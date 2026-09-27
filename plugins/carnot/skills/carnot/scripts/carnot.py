#!/usr/bin/env python3
"""carnot: find the Carnot limit of an AI coding agent fleet from a repository's own history.

Model (Polymorphism, "The Heat Death of the Codebase?", 2026):
  X(N) = N / (1 + a(N-1) + b N(N-1))            coordination drag (Gunther USL)
  r(N) = 1 - (1 - r0)(1 - p)^(N-1)             rework rises with concurrent changes
  U(N) = (1 - r(N)) * min(lam X(N), V / h)      finished changes/day, capped by review
  Rule of thumb: q = review capacity / one agent's output; N* ~ q / (1 - a q - b q^2),
  capped at (1 - a) / (p + sqrt(b)); finished ~ (1 - r0) x review capacity when review binds.

Subcommands:
  calibrate  measure r0, p, lam, h (and review evidence) from git / GitHub history
  model      run the model for given parameters
  run        calibrate then model (the usual entry point)
  fit        fit a and b from a fleet sweep (CSV of agents,finished_per_day)
  compare    measure before and after a date, e.g. adopting a verification tool

Standard library only. Needs `git`; uses `gh` (GitHub CLI) when available.
Nothing leaves the machine except the gh API calls the user's own gh makes.
"""
import argparse, json, math, os, re, subprocess, sys, statistics, itertools, random
from datetime import datetime, timezone, timedelta

DEFAULTS = {
    "a": 0.10, "b": 0.01, "r0": 0.40, "p": 0.01,
    "lam": 6.0, "loc": 150.0,
    "reviewers": 2, "rate": 200.0, "hours": 4.0, "auto": 0.5, "rho": 0.75,
}
SOURCES = {
    "a": "default (Khailo 2026 fit 0.12; Cursor lock anecdote ~0.37)",
    "b": "default (Khailo 2026 fit 0.032; range 0.002-0.035)",
    "r0": "default for autonomous agents (METR 2026: ~half of test-passing agent PRs not mergeable)",
    "p": "default (chance a concurrent change forces a redo; AIDev test: under 1% even though 20-42% of concurrent agent PRs conflict textually)",
    "lam": "default (assumed changes per agent per day)",
    "loc": "default",
    "reviewers": "default (ask the user)",
    "rate": "default (Kemerer & Paulk: review quality falls above 200 lines/hour)",
    "hours": "default (focused review hours per reviewer-day)",
    "auto": "default (share of verification handled by tests/types/tooling; ask the user)",
    "rho": "target reviewer utilisation (queueing: waits blow up above ~0.85)",
}

FIX_RE = re.compile(r"^(fix|hotfix|bugfix|bug|revert)(\(|:|!|/|-|\s)|\b(fix(es|ed)?|bug|regression|hotfix|broke|broken|revert(s|ed)?)\b", re.I)
REVERT_SHA_RE = re.compile(r"This reverts commit ([0-9a-f]{7,40})", re.I)
REVERT_PR_RE = re.compile(r"^Revert\b.*?(?:#(\d+))?", re.I)
AGENT_RE = re.compile(r"co-authored-by:\s*(claude|copilot|codex|cursor|devin|gemini|aider|openhands|jules)|generated with \[?claude|\[bot\]|devin-ai|copilot-swe-agent|codex", re.I)
AGENT_BRANCH_RE = re.compile(r"^(codex|copilot|claude|cursor|devin|jules|agent)[/-]", re.I)
NOISE_RE = re.compile(r"(^|/)(package-lock\.json|yarn\.lock|pnpm-lock\.yaml|Cargo\.lock|poetry\.lock|go\.sum|uv\.lock|Gemfile\.lock|CHANGELOG[^/]*|.*\.md|.*\.snap)$|^docs?/", re.I)
PLURAL = {"pull request": "pull requests", "merged branch": "merged branches", "commit": "commits"}
BOTS_RE = re.compile(r"dependabot|renovate|github-actions|pre-commit-ci", re.I)


# ----------------------------------------------------------------------------- helpers
def sh(args, cwd, check=True):
    r = subprocess.run(args, cwd=cwd, capture_output=True, text=True)
    if check and r.returncode != 0:
        raise RuntimeError(f"{' '.join(args)} failed: {r.stderr.strip()[:300]}")
    return r


def iso(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00")) if s else None


def code_files(files):
    return {f for f in files if f and not NOISE_RE.search(f)}


def median(xs, default=None):
    xs = [x for x in xs if x is not None]
    return statistics.median(xs) if xs else default


# ----------------------------------------------------------------------------- model
def model_point(s, n):
    X = n / (1 + s["a"] * (n - 1) + s["b"] * n * (n - 1))
    r = 1 - (1 - s["r0"]) * (1 - s["p"]) ** (n - 1)
    cap_lines = s["reviewers"] * s["hours"] * s["rate"] * s["rho"] / max(1e-9, 1 - s["auto"])
    cap = cap_lines / s["loc"]
    raw = s["lam"] * X
    U = (1 - r) * min(raw, cap)
    return {"agents": n, "X": X, "rework": r, "submitted": min(raw, cap), "review_cap": cap,
            "finished_per_day": U, "review_limited": raw >= cap}


def run_model(s, nmax=30, backlog=None, plan=None):
    pts = [model_point(s, n) for n in range(1, nmax + 1)]
    best = max(pts, key=lambda q: q["finished_per_day"] + 1e-12 * (nmax - q["agents"]))
    one = pts[0]["finished_per_day"]
    q = pts[0]["review_cap"] / s["lam"]
    dq = 1 - s["a"] * q - s["b"] * q * q
    ceil_n = (1 - s["a"]) / (s["p"] + math.sqrt(s["b"])) if (s["p"] + s["b"]) > 0 else float("inf")
    rule_n = min(q / dq if dq > 0 else float("inf"), ceil_n)
    out = {
        "params": s,
        "best_agents": best["agents"],
        "finished_per_day_at_best": round(best["finished_per_day"], 2),
        "speedup_vs_one_agent": round(best["finished_per_day"] / one, 2) if one else None,
        "limited_by": "review" if best["review_limited"] else "coordination",
        "review_keeps_up_with_agents": round(pts[0]["review_cap"] / s["lam"], 1),
        "rework_at_best": round(best["rework"], 3),
        "rule_of_thumb_agents": round(rule_n, 1) if math.isfinite(rule_n) else None,
        "rule_of_thumb_output": (round((1 - s["r0"]) * pts[0]["review_cap"], 2) if rule_n < ceil_n else
                                 round((1 - s["r0"]) * min(pts[0]["review_cap"], s["lam"] * rule_n / (2 + s["a"] * rule_n)), 2))
                                if math.isfinite(rule_n) else None,
        "curve": [{"agents": q["agents"], "finished_per_day": round(q["finished_per_day"], 2),
                   "rework": round(q["rework"], 3), "review_limited": q["review_limited"]} for q in pts[:16]],
    }
    # Levers: what each improvement buys at its own best size
    levers = {}
    for name, change in {
        "halve collision chance p (partition work)": {"p": s["p"] / 2},
        "halve coordination cost b (share less context)": {"b": s["b"] / 2},
        "add one reviewer": {"reviewers": s["reviewers"] + 1},
        "automate 20 more points of verification": {"auto": min(0.9, s["auto"] + 0.2)},
        "cut baseline rework r0 by a third": {"r0": s["r0"] * 2 / 3},
    }.items():
        s2 = dict(s, **change)
        b2 = max((model_point(s2, n) for n in range(1, nmax + 1)), key=lambda q: q["finished_per_day"])
        levers[name] = {"best_agents": b2["agents"], "finished_per_day": round(b2["finished_per_day"], 2),
                        "gain_pct": round(100 * (b2["finished_per_day"] / best["finished_per_day"] - 1), 1)}
    out["levers"] = levers
    if backlog:
        n = plan or best["agents"]
        q = model_point(s, n)
        days = backlog / q["finished_per_day"]
        out["estimate"] = {"backlog_changes": backlog, "agents": n,
                           "finished_per_day": round(q["finished_per_day"], 2),
                           "working_days": round(days, 1), "agent_days": round(days * n, 1),
                           "working_days_one_agent": round(backlog / one, 1),
                           "rework_rate": round(q["rework"], 3),
                           "review_limited": q["review_limited"]}
    return out


# ----------------------------------------------------------------------------- git calibration
def git_changes(repo, since, until=None):
    fmt = "%x1e%H%x1f%an%x1f%ae%x1f%at%x1f%ct%x1f%s%x1f%B%x1d"
    r = sh(["git", "log", f"--since={since.isoformat()}"] + ([f"--until={until.isoformat()}"] if until else [])
           + ["--no-merges", f"--format={fmt}", "--numstat", "-M"], repo)
    out = []
    for rec in r.stdout.split("\x1e")[1:]:
        head, _, stat = rec.partition("\x1d")
        f = head.split("\x1f")
        if len(f) < 7:
            continue
        sha, an, ae, at, ct, subj, body = f[:7]
        files, lines = set(), 0
        for line in stat.strip().splitlines():
            parts = line.split("\t")
            if len(parts) == 3:
                add, dele, path = parts
                if "=>" in path:  # rename: take the new path
                    path = re.sub(r"\{[^}]*=> ([^}]*)\}", r"\1", path).split(" => ")[-1]
                files.add(path)
                if add.isdigit() and dele.isdigit() and not NOISE_RE.search(path):
                    lines += int(add) + int(dele)
        out.append({"id": sha, "author": an, "email": ae,
                    "start": datetime.fromtimestamp(int(at), timezone.utc),
                    "end": datetime.fromtimestamp(int(ct), timezone.utc),
                    "title": subj, "body": body, "files": code_files(files), "lines": lines,
                    "agent": bool(AGENT_RE.search(body) or AGENT_RE.search(ae) or AGENT_RE.search(an)),
                    "bot": bool(BOTS_RE.search(an) or BOTS_RE.search(ae))})
    out.sort(key=lambda c: c["end"])
    return out


def merge_changes(repo, since, until=None):
    """Treat each merged branch (second parent of a merge commit) as one change, like a PR."""
    r = sh(["git", "log", "--merges", "--first-parent", f"--since={since.isoformat()}"]
           + ([f"--until={until.isoformat()}"] if until else []) + ["--format=%H%x1f%P%x1f%ct%x1f%s"], repo)
    out = []
    for line in r.stdout.splitlines():
        sha, parents, ct, subj = line.split("\x1f", 3)
        ps = parents.split()
        if len(ps) != 2:
            continue
        p1, p2 = ps
        base = sh(["git", "merge-base", p1, p2], repo, check=False).stdout.strip()
        if not base:
            continue
        times = [int(t) for t in sh(["git", "log", "--format=%at", f"{p1}..{p2}"], repo).stdout.split()]
        if not times:
            continue
        files, lines = set(), 0
        for l in sh(["git", "diff", "--numstat", "-M", base, p2], repo).stdout.splitlines():
            parts = l.split("\t")
            if len(parts) == 3:
                files.add(parts[2].split(" => ")[-1])
                if parts[0].isdigit() and parts[1].isdigit() and not NOISE_RE.search(parts[2]):
                    lines += int(parts[0]) + int(parts[1])
        m = re.search(r"Merged? (?:in |branch '?|pull request #\d+ from [^/\s]+/)([^\s']+)", subj)
        title = m.group(1) if m else subj
        body = sh(["git", "log", "--format=%an %ae%n%B", f"{p1}..{p2}"], repo).stdout
        out.append({"id": sha, "tip": p2, "author": "", "start": datetime.fromtimestamp(min(times), timezone.utc),
                    "end": datetime.fromtimestamp(int(ct), timezone.utc), "title": title, "body": subj,
                    "files": code_files(files), "lines": lines, "merged": True,
                    "agent": bool(AGENT_RE.search(body) or AGENT_BRANCH_RE.search(title)),
                    "bot": bool(BOTS_RE.search(body.split("\n", 1)[0]))})
    out.sort(key=lambda c: c["end"])
    return out


def attribute_rework(changes, window_days, explicit_reverts):
    """SZZ-lite at file level: each fix change marks the most recent earlier change
    that touched one of its files within the window as needing rework."""
    window = timedelta(days=window_days)
    reworked = set(explicit_reverts)
    last_touch = {}  # file -> list of (time, id), append-only in time order
    by_id = {c["id"]: c for c in changes}
    fixes = 0
    for c in changes:
        if c["bot"]:
            continue
        if FIX_RE.search(c["title"]):
            fixes += 1
            cand = None
            for f in c["files"]:
                for t, cid in reversed(last_touch.get(f, [])):
                    if cid == c["id"]:
                        continue
                    if c["end"] - t > window:
                        break
                    if cand is None or t > cand[0]:
                        cand = (t, cid)
                    break
            if cand:
                reworked.add(cand[1])
        for f in c["files"]:
            last_touch.setdefault(f, []).append((c["end"], c["id"]))
    return reworked, fixes


def overlap_rate(changes, min_gap_minutes=0, sample=4000, seed=7):
    """Share of pairs of changes that were in flight at the same time and touched a common file."""
    iv = [c for c in changes if c["files"] and not c["bot"]
          and (c["end"] - c["start"]).total_seconds() >= min_gap_minutes * 60]
    iv.sort(key=lambda c: c["start"])
    pairs = []
    for i, a in enumerate(iv):
        for b in iv[i + 1:]:
            if b["start"] >= a["end"]:
                break
            pairs.append((a, b))
    if not pairs:
        return None, 0, []
    rnd = random.Random(seed)
    if len(pairs) > sample:
        pairs = rnd.sample(pairs, sample)
    hits = [(a, b) for a, b in pairs if a["files"] & b["files"]]
    return len(hits) / len(pairs), len(pairs), pairs


def replay_conflicts(repo, pairs, limit=150):
    """Textual conflict rate by replaying pairs of PR heads with `git merge-tree`."""
    heads = {}
    tested = conflicts = skipped = 0
    for a, b in pairs[:limit]:
        for c in (a, b):
            if c["id"] not in heads:
                if c.get("tip"):
                    heads[c["id"]] = c["tip"]
                    continue
                ref = f"refs/carnot/pr/{c['num']}"
                r = sh(["git", "fetch", "-q", "origin", f"pull/{c['num']}/head:{ref}"], repo, check=False)
                heads[c["id"]] = ref if r.returncode == 0 else None
        if not heads[a["id"]] or not heads[b["id"]]:
            continue
        ha, hb = heads[a["id"]], heads[b["id"]]
        # A branch rebased onto (or stacked on) the other already contains it and cannot conflict.
        if (sh(["git", "merge-base", "--is-ancestor", ha, hb], repo, check=False).returncode == 0 or
                sh(["git", "merge-base", "--is-ancestor", hb, ha], repo, check=False).returncode == 0):
            skipped += 1
            continue
        r = sh(["git", "merge-tree", "--write-tree", "--no-messages", heads[a["id"]], heads[b["id"]]], repo, check=False)
        if r.returncode in (0, 1):
            tested += 1
            conflicts += r.returncode == 1
    for ref in {v for v in heads.values() if v and v.startswith("refs/carnot/")}:
        sh(["git", "update-ref", "-d", ref], repo, check=False)
    return conflicts, tested, skipped


def gh_available(repo):
    try:
        r = sh(["gh", "repo", "view", "--json", "nameWithOwner"], repo, check=False)
        return r.returncode == 0
    except FileNotFoundError:
        return False


def github_changes(repo, since, until=None, limit=1000):
    rng = f"created:{since.date().isoformat()}..{until.date().isoformat()}" if until else f"created:>={since.date().isoformat()}"
    fields = "number,title,author,createdAt,mergedAt,closedAt,state,additions,deletions,files,headRefName,body,reviews,isDraft"
    r = sh(["gh", "pr", "list", "--state", "all", "--limit", str(limit), "--search", rng,
            "--json", fields], repo)
    prs = json.loads(r.stdout)
    out = []
    for pr in prs:
        if pr.get("isDraft") and pr["state"] == "OPEN":
            continue
        login = (pr.get("author") or {}).get("login", "")
        end = iso(pr.get("mergedAt")) or iso(pr.get("closedAt"))
        files = {f["path"] for f in (pr.get("files") or [])}
        states = [rv.get("state") for rv in (pr.get("reviews") or [])]
        reviewers = {(rv.get("author") or {}).get("login") for rv in (pr.get("reviews") or [])} - {login, None}
        out.append({"id": f"pr{pr['number']}", "num": pr["number"], "author": login,
                    "start": iso(pr["createdAt"]), "end": end or datetime.now(timezone.utc),
                    "state": pr["state"], "merged": bool(pr.get("mergedAt")),
                    "title": pr["title"], "body": pr.get("body") or "", "files": code_files(files),
                    "lines": (pr.get("additions") or 0) + (pr.get("deletions") or 0),
                    "changes_requested": "CHANGES_REQUESTED" in states,
                    "reviewed": bool(reviewers), "reviewers": reviewers,
                    "agent": bool(AGENT_RE.search(login) or AGENT_BRANCH_RE.search(pr.get("headRefName") or "")
                                  or AGENT_RE.search(pr.get("body") or "")),
                    "bot": bool(BOTS_RE.search(login))})
    out.sort(key=lambda c: c["end"])
    return out


def collect(repo, since, until=None, source="auto", limit=1000):
    """Fetch changes in [since, until) from the best available source."""
    repo = os.path.abspath(repo)
    sh(["git", "rev-parse", "--git-dir"], repo)
    note = {}
    use_gh = source == "github" or (source == "auto" and gh_available(repo))
    changes, mode = [], None
    if use_gh:
        try:
            changes = github_changes(repo, since, until, limit)
        except Exception as e:  # fall back to git
            note["github_error"] = str(e)[:300]
            changes = []
        if len([c for c in changes if not c["bot"]]) < 15:
            note["github_note"] = "fewer than 15 non-bot PRs in range; using git history instead"
        else:
            mode = "github"
    if mode is None and source in ("auto", "merges"):
        merged = merge_changes(repo, since, until)
        if len(merged) >= 15 or source == "merges":
            changes, mode = merged, "merges"
    if mode is None:
        changes, mode = git_changes(repo, since, until), "commits"
    return repo, changes, mode, note


def calibrate(repo, days=90, window=14, source="auto", replay=150, limit=1000, agents_used=None):
    since = datetime.now(timezone.utc) - timedelta(days=days)
    repo, changes, mode, note = collect(repo, since, None, source, limit)
    s, src, ev = measure(repo, changes, mode, None, window, replay, agents_used)
    ev.update(note)
    ev["days"] = days
    return s, src, ev


def measure(repo, changes, mode, period=None, window=14, replay=150, agents_used=None):
    """Measure parameters for changes finishing within period=(start, end); None means all.
    Rework attribution always uses the whole history, so a fix landing after the
    period still counts against the change it fixes."""
    s = dict(DEFAULTS)
    src = dict(SOURCES)
    use_gh = mode == "github"
    unit = {"github": "pull request", "merges": "merged branch", "commits": "commit"}[mode]
    all_real = [c for c in changes if not c["bot"]]
    real = [c for c in all_real if period is None or period[0] <= c["end"] < period[1]]
    ev = {"repo": repo, "rework_window_days": window, "unit": unit, "source": mode}
    if period:
        ev["period"] = [period[0].date().isoformat(), period[1].date().isoformat()]
    ev["changes"] = len(real)
    ev["agent_share"] = round(sum(c["agent"] for c in real) / len(real), 2) if real else 0
    if len(real) < 15:
        ev["warning"] = "too little history to calibrate; defaults used throughout"
        return s, src, ev
    span_days = max(1, (max(c["end"] for c in real) - min(c["end"] for c in real)).days)

    now = max(c["end"] for c in all_real)
    # ---- rework r0
    if use_gh:
        merged = [c for c in all_real if c["merged"]]
        reverts = set()
        by_num = {c["num"]: c for c in merged}
        for c in merged:
            m = REVERT_PR_RE.match(c["title"])
            if m:
                nums = re.findall(r"#(\d+)", c["title"] + " " + c["body"][:500])
                for n in nums:
                    if int(n) in by_num:
                        reverts.add(by_num[int(n)]["id"])
        post, fixes = attribute_rework(merged, window, reverts)
        observable = [c for c in real if (now - c["end"]).days >= window or not c["merged"]]
        closed_unmerged = [c for c in observable if c["state"] == "CLOSED" and not c["merged"]]
        rejected_after_review = [c for c in closed_unmerged if c["reviewed"]]
        needs = {c["id"] for c in observable if c["id"] in post or c["changes_requested"]} | {c["id"] for c in rejected_after_review}
        denom = [c for c in observable if c["merged"] or c in rejected_after_review]
        k_r0 = len(needs & {c["id"] for c in denom})
        ev["rework"] = {
            "observed_changes": len(denom), "reworked": k_r0,
            "fixed_or_reverted_after_merge": len(post & {c["id"] for c in denom}),
            "changes_requested_in_review": sum(c["changes_requested"] for c in denom),
            "closed_unmerged_after_review": len(rejected_after_review),
            "closed_unmerged_without_review_excluded": len(closed_unmerged) - len(rejected_after_review),
            "fix_prs_seen": fixes,
        }
        is_rework = lambda c: c["id"] in needs
    else:
        explicit = set()
        ids = [c["id"] for c in all_real]
        for c in all_real:
            for sha in REVERT_SHA_RE.findall(c["body"]):
                explicit.update(i for i in ids if i.startswith(sha))
        post, fixes = attribute_rework(all_real, window, explicit)
        denom = [c for c in real if (now - c["end"]).days >= window]
        k_r0 = sum(c["id"] in post for c in denom)
        ev["rework"] = {"observed_changes": len(denom), "reworked": k_r0,
                        "fix_commits_seen": fixes, "explicit_reverts": len(explicit),
                        "method": "fix/revert commits attributed to the latest earlier commit touching the same file within the window"}
        is_rework = lambda c: c["id"] in post
    for label, grp in (("agent", [c for c in denom if c["agent"]]), ("human", [c for c in denom if not c["agent"]])):
        if len(grp) >= 10:
            ev["rework"][f"r0_{label}"] = round(sum(map(is_rework, grp)) / len(grp), 3)
    if denom:
        ev["rework"]["r0_ci95"] = [round(x, 3) for x in wilson(k_r0, len(denom))]
    if len(denom) >= 15:
        s["r0"] = round(min(0.8, k_r0 / len(denom)), 3)
        src["r0"] = f"measured: share of {PLURAL[unit]} needing rework within {window} days (n={len(denom)})"

    # ---- collision chance p
    if mode in ("github", "merges"):
        merged_real = [c for c in real if c["merged"]]
        p_files, npairs, pairs = overlap_rate(merged_real)
        ev["collisions"] = {"overlapping_pairs": npairs, "file_overlap_rate": round(p_files, 3) if p_files is not None else None}
        p_replay = None
        if pairs and replay:
            rnd = random.Random(11)
            sample = pairs if len(pairs) <= replay else rnd.sample(pairs, replay)
            k, tested, skipped = replay_conflicts(repo, sample, replay)
            if tested:
                p_replay = (k + 1) / (tested + 2)  # Laplace: zero conflicts in n pairs is not p = 0
            ev["collisions"].update({"replayed_pairs": tested, "conflicting_pairs": k,
                                     "skipped_already_rebased_or_stacked": skipped,
                                     "textual_conflict_rate_raw": round(k / tested, 3) if tested else None,
                                     "textual_conflict_rate_smoothed": round(p_replay, 3) if p_replay is not None else None,
                                     "ci95": [round(x, 3) for x in wilson(k, tested)] if tested else None,
                                     "note": "textual conflicts only; semantic conflicts (clean merge, broken tests) are not counted"})
        if p_replay is not None and ev["collisions"]["replayed_pairs"] >= 20:
            s["p"] = round(p_replay, 3)
            src["p"] = (f"measured: {ev['collisions']['conflicting_pairs']} textual conflicts in {ev['collisions']['replayed_pairs']} "
                        f"replayed pairs of concurrent {PLURAL[unit]} (git merge-tree, smoothed)")
        elif p_files is not None and npairs >= 20:
            s["p"] = round(p_files, 3)
            src["p"] = f"measured (upper bound): share of {npairs} overlapping pairs of {PLURAL[unit]} touching a common file"
    else:
        gap = [c for c in real if (c["end"] - c["start"]).total_seconds() >= 600]
        ev["collisions"] = {"commits_landed_10min_after_authoring": len(gap)}
        if len(gap) >= 20:
            p_files, npairs, _ = overlap_rate(real, min_gap_minutes=10)
            ev["collisions"].update({"overlapping_pairs": npairs,
                                     "file_overlap_rate": round(p_files, 3) if p_files is not None else None})
            if p_files is not None and npairs >= 20:
                s["p"] = round(p_files, 3)
                src["p"] = (f"measured (rough upper bound): share of {npairs} pairs of commits in flight at the same time "
                            "(authored before landing) that touched a common file")
        else:
            ev["collisions"]["note"] = ("linear history with no sign of concurrent work: p cannot be measured. "
                                        "Ask the user how often parallel agents touched the same files, or run a sweep.")

    # ---- lines per change and changes per agent-day
    lines = [c["lines"] for c in real if c["lines"] and (not use_gh or c["merged"])]
    if len(lines) >= 15:
        s["loc"] = float(max(10, round(median(lines))))
        src["loc"] = f"measured: median lines changed per {unit} (n={len(lines)})"
    per_day = {}
    for c in real:
        if use_gh and not c["merged"]:
            continue
        per_day.setdefault((c["author"], c["end"].date()), 0)
        per_day[(c["author"], c["end"].date())] += 1
    rates = {}
    for (a, d), k in per_day.items():
        rates.setdefault(a, []).append(k)
    author_rates = [statistics.mean(v) for a, v in rates.items() if len(v) >= 3]
    active_days = len({d for (_, d) in per_day})
    total = sum(per_day.values())
    ev["throughput"] = {"finished_per_active_day": round(total / active_days, 2) if active_days else None,
                        "active_days": active_days, "authors": len(rates),
                        "median_per_author_active_day": round(median(author_rates, 0), 2) if author_rates else None}
    if agents_used and ev["throughput"]["finished_per_active_day"]:
        # invert the model at the fleet size actually used: finished = lam * X(N) * (1 - r(N))
        n = agents_used
        X = n / (1 + s["a"] * (n - 1) + s["b"] * n * (n - 1))
        r = 1 - (1 - s["r0"]) * (1 - s["p"]) ** (n - 1)
        s["lam"] = round(ev["throughput"]["finished_per_active_day"] / (X * (1 - r)), 1)
        src["lam"] = f"measured: {ev['throughput']['finished_per_active_day']} finished/active day with ~{n} agents, back-solved through the model"

    # ---- review evidence
    if use_gh:
        weeks = max(1, span_days / 7)
        counts = {}
        for c in real:
            for rv in c["reviewers"]:
                counts[rv] = counts.get(rv, 0) + 1
        regular = [r for r, k in counts.items() if k / weeks >= 1]
        ev["review"] = {"reviewers_seen": len(counts), "regular_reviewers_1_per_week": len(regular),
                        "reviewed_share": round(sum(c["reviewed"] for c in real if c["merged"]) / max(1, sum(c["merged"] for c in real)), 2)}
        if regular:
            s["reviewers"] = len(regular)
            src["reviewers"] = "measured: people reviewing at least one PR a week"
    ci = [p for p in (".github/workflows", ".gitlab-ci.yml", "bitbucket-pipelines.yml", ".circleci", "Jenkinsfile", ".buildkite")
          if os.path.exists(os.path.join(repo, p))]
    ev.setdefault("review", {})["ci_detected"] = ci
    try:
        tracked = sh(["git", "ls-files"], repo).stdout.splitlines()
        tests = [f for f in tracked if re.search(r"(^|/)(tests?|spec|__tests__)(/|$)|[._-](test|spec)\.[a-z]+$|_test\.go$|Test\.java$", f)]
        ev["review"]["test_file_share"] = round(len(tests) / max(1, len(tracked)), 3)
    except RuntimeError:
        pass
    return s, src, ev


def wilson(k, n, z=1.96):
    """95% Wilson score interval for a proportion k/n."""
    if not n:
        return (0.0, 1.0)
    ph = k / n
    d = 1 + z * z / n
    c = (ph + z * z / (2 * n)) / d
    h = z * math.sqrt(ph * (1 - ph) / n + z * z / (4 * n * n)) / d
    return (max(0.0, c - h), min(1.0, c + h))


def two_prop_p(k1, n1, k2, n2):
    """Two-sided p-value for a difference in proportions (normal approximation)."""
    if not n1 or not n2:
        return None
    pp = (k1 + k2) / (n1 + n2)
    se = math.sqrt(pp * (1 - pp) * (1 / n1 + 1 / n2))
    if se == 0:
        return 1.0
    z = abs(k1 / n1 - k2 / n2) / se
    return math.erfc(z / math.sqrt(2))


def parse_date(v):
    d = datetime.fromisoformat(v)
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def compare(repo, pivot, before_days=90, after_days=None, window=14, source="auto", replay=150, limit=1000,
            overrides=None, before_set=None, after_set=None, nmax=30):
    """Measure the same repo before and after a date (e.g. adopting a verification tool)."""
    now = datetime.now(timezone.utc)
    start = pivot - timedelta(days=before_days)
    end = min(now, pivot + timedelta(days=after_days)) if after_days else now
    repo, changes, mode, note = collect(repo, start, end, source, limit)
    out = {"repo": repo, "pivot": pivot.date().isoformat(), "source": mode, "note": note}
    sides = {}
    for name, period, extra in (("before", (start, pivot), before_set), ("after", (pivot, end + timedelta(seconds=1)), after_set)):
        s, src, ev = measure(repo, changes, mode, period, window, replay)
        for k, v in (overrides or {}).items():
            s[k] = v; src[k] = "set by user"
        for k, v in (extra or {}).items():
            s[k] = v; src[k] = f"set by user for the {name} period"
        sides[name] = {"params": s, "sources": src, "evidence": ev, "result": run_model(s, nmax)}
    out.update(sides)
    b, a = sides["before"]["evidence"], sides["after"]["evidence"]
    tests = {}
    rb, ra = b.get("rework", {}), a.get("rework", {})
    if rb.get("observed_changes") and ra.get("observed_changes"):
        tests["r0"] = {"before": [rb["reworked"], rb["observed_changes"]], "after": [ra["reworked"], ra["observed_changes"]],
                       "p_value": round(two_prop_p(rb["reworked"], rb["observed_changes"], ra["reworked"], ra["observed_changes"]), 3)}
    cb, ca = b.get("collisions", {}), a.get("collisions", {})
    if cb.get("replayed_pairs") and ca.get("replayed_pairs"):
        tests["p"] = {"before": [cb["conflicting_pairs"], cb["replayed_pairs"]], "after": [ca["conflicting_pairs"], ca["replayed_pairs"]],
                      "p_value": round(two_prop_p(cb["conflicting_pairs"], cb["replayed_pairs"], ca["conflicting_pairs"], ca["replayed_pairs"]), 3)}
    out["tests"] = tests
    unobs = (now - pivot).days < window + 14
    out["warnings"] = ([f"the after period is short: changes in the last {window} days can't show rework yet"] if unobs else []) + \
        ["before/after is not a controlled experiment: team, workload and agents may also have changed. "
         "Compare with a repo that didn't change, if you have one."]
    return out


def compare_markdown(c):
    b, a = c["before"], c["after"]
    L = [f"## Carnot compare for `{os.path.basename(c['repo'])}`: before and after {c['pivot']}\n",
         f"Source: {PLURAL[b['evidence']['unit']]}. Before: {b['evidence'].get('period', ['?', '?'])[0]} to {c['pivot']} "
         f"({b['evidence']['changes']} changes). After: {c['pivot']} to {a['evidence'].get('period', ['?', '?'])[1]} ({a['evidence']['changes']} changes).\n"]
    rb, ra = b["result"], a["result"]
    L.append("| | Before | After | Change |\n|---|---|---|---|")
    def row(label, x, y, fmt="{}", pct=False):
        if isinstance(x, (int, float)) and isinstance(y, (int, float)) and x:
            ch = f"{100 * (y / x - 1):+.0f}%"
        else:
            ch = ""
        L.append(f"| {label} | {fmt.format(x)} | {fmt.format(y)} | {ch} |")
    def ci(ev, key):
        v = ev.get("rework", {}).get("r0_ci95") if key == "r0" else ev.get("collisions", {}).get("ci95")
        return f" ({round(100 * v[0])}–{round(100 * v[1])}%)" if v else ""
    L.append(f"| Rework r₀ (95% range) | {round(100 * b['params']['r0'])}%{ci(b['evidence'], 'r0')} | "
             f"{round(100 * a['params']['r0'])}%{ci(a['evidence'], 'r0')} | {100 * (a['params']['r0'] - b['params']['r0']):+.0f} pts |")
    L.append(f"| Collision chance p (95% range) | {round(100 * b['params']['p'], 1)}%{ci(b['evidence'], 'p')} | "
             f"{round(100 * a['params']['p'], 1)}%{ci(a['evidence'], 'p')} | {100 * (a['params']['p'] - b['params']['p']):+.1f} pts |")
    row("Lines per change", b["params"]["loc"], a["params"]["loc"], "{:.0f}")
    row("Finished changes per active day", b["evidence"].get("throughput", {}).get("finished_per_active_day"),
        a["evidence"].get("throughput", {}).get("finished_per_active_day"))
    L.append(f"| Verification automated | {b['params']['auto']:.0%} | {a['params']['auto']:.0%} | {100 * (a['params']['auto'] - b['params']['auto']):+.0f} pts |")
    row("Best fleet size", rb["best_agents"], ra["best_agents"])
    row("Finished changes/day at best", rb["finished_per_day_at_best"], ra["finished_per_day_at_best"])
    L.append(f"| Limited by | {rb['limited_by']} | {ra['limited_by']} | |")
    L.append("")
    for k, t in c["tests"].items():
        verdict = "unlikely to be noise" if t["p_value"] < 0.05 else "could be noise"
        L.append(f"- {k}: {t['before'][0]}/{t['before'][1]} before vs {t['after'][0]}/{t['after'][1]} after, p = {t['p_value']} ({verdict}).")
    if b["sources"]["p"].split(":")[0] != a["sources"]["p"].split(":")[0] or ("replayed" in b["sources"]["p"]) != ("replayed" in a["sources"]["p"]):
        L.append("- ⚠ collision chance was measured differently in the two periods (too few concurrent pairs in one); don't read the change in p as real.")
    for side, x in (("before", b), ("after", a)):
        n = x["evidence"].get("rework", {}).get("observed_changes", 0)
        if n < 50:
            L.append(f"- ⚠ only {n} changes in the {side} period could be checked for rework; differences smaller than about 15 points won't be distinguishable from noise.")
    for w in c["warnings"]:
        L.append(f"- ⚠ {w}")
    return "\n".join(L)


# ----------------------------------------------------------------------------- fit
def fit_usl(rows):
    """Least-squares fit of a, b (and single-agent rate g) to (N, throughput) rows."""
    best = None
    for a in [i / 200 for i in range(0, 121)]:
        for b in [i / 2000 for i in range(0, 121)]:
            xs = [n / (1 + a * (n - 1) + b * n * (n - 1)) for n, _ in rows]
            num = sum(x * y for x, (_, y) in zip(xs, rows)); den = sum(x * x for x in xs)
            g = num / den if den else 0
            err = sum((g * x - y) ** 2 for x, (_, y) in zip(xs, rows))
            if best is None or err < best[0]:
                best = (err, a, b, g)
    err, a, b, g = best
    return {"a": a, "b": b, "single_agent_rate": round(g, 3), "sse": round(err, 4),
            "usl_peak_agents": round(math.sqrt((1 - a) / b), 1) if b > 0 else None}


# ----------------------------------------------------------------------------- output
def to_markdown(res, src, ev):
    s = res["params"]
    L = []
    L.append(f"## Carnot limit for `{os.path.basename(ev.get('repo', '')) or 'this project'}`\n")
    L.append(f"**Best fleet size: {res['best_agents']} agents**, finishing about **{res['finished_per_day_at_best']} changes/day** "
             f"({res['speedup_vs_one_agent']}× one agent). Limited by **{res['limited_by']}**. "
             f"Rework at that size: {round(100 * res['rework_at_best'])}%.\n")
    rule = res['rule_of_thumb_agents']
    L.append((f"Rule of thumb q ÷ (1 − αq − βq²), capped at (1 − α) ÷ (p + √β): {rule} agents, about {res['rule_of_thumb_output']} finished/day. " if rule else "Rule of thumb: no review, collision or coordination limit. ")
             + f"Review capacity keeps up with about {res['review_keeps_up_with_agents']} agents' worth of raw output.\n")
    if "estimate" in res:
        e = res["estimate"]
        L.append(f"**Estimate:** {e['backlog_changes']} changes with {e['agents']} agents ≈ **{e['working_days']} working days** "
                 f"({e['agent_days']} agent-days; one agent: {e['working_days_one_agent']} days).\n")
    L.append("| Parameter | Value | Source |\n|---|---|---|")
    names = {"r0": "Baseline rework r₀", "p": "Collision chance p", "a": "One-at-a-time share α", "b": "Coordination per pair β",
             "lam": "Changes per agent per day λ", "loc": "Lines per change", "reviewers": "Reviewers",
             "rate": "Review speed (lines/h)", "hours": "Focused review hours/day", "auto": "Verification automated", "rho": "Target reviewer load"}
    for k, label in names.items():
        L.append(f"| {label} | {s[k]} | {src.get(k, '')} |")
    L.append("\n| Agents | Finished/day | Rework | Review-limited |\n|---|---|---|---|")
    for q in res["curve"][:12]:
        L.append(f"| {q['agents']} | {q['finished_per_day']} | {round(100 * q['rework'])}% | {'yes' if q['review_limited'] else ''} |")
    L.append("\n**What would help most** (best output after the change):\n")
    for name, v in sorted(res["levers"].items(), key=lambda kv: -kv[1]["gain_pct"]):
        L.append(f"- {name}: {v['finished_per_day']}/day at {v['best_agents']} agents ({v['gain_pct']:+}%)")
    return "\n".join(L)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    def add_params(p):
        for k, v in DEFAULTS.items():
            p.add_argument(f"--{k}", type=float, default=None, help=f"override (default {v})")
        p.add_argument("--backlog", type=int, help="changes to deliver, for a time estimate")
        p.add_argument("--plan", type=int, help="agents you plan to run, for the estimate (default: best)")
        p.add_argument("--nmax", type=int, default=30)
        p.add_argument("--format", choices=["json", "md"], default="md")

    pc = sub.add_parser("calibrate"); pr = sub.add_parser("run"); pm = sub.add_parser("model"); pf = sub.add_parser("fit")
    pcmp = sub.add_parser("compare", help="measure before and after a date, e.g. adopting a verification tool")
    for p in (pc, pr):
        p.add_argument("--days", type=int, default=90)
    for p in (pc, pr, pcmp):
        p.add_argument("--repo", default=".")
        p.add_argument("--window", type=int, default=14, help="days after a change in which a fix counts as its rework")
        p.add_argument("--source", choices=["auto", "github", "merges", "commits"], default="auto",
                       help="auto: GitHub PRs if gh works, else merged branches, else commits")
        p.add_argument("--replay", type=int, default=150, help="pairs to replay with git merge-tree (0 to skip)")
        p.add_argument("--limit", type=int, default=1000, help="max PRs to fetch")
        p.add_argument("--agents-used", type=int, help="typical concurrent agents during the window (to back-solve λ)")
    add_params(pr); add_params(pm)
    pcmp.add_argument("--pivot", required=True, help="date the change took effect (YYYY-MM-DD)")
    pcmp.add_argument("--before", type=int, default=90, help="days before the pivot to measure")
    pcmp.add_argument("--after", type=int, help="days after the pivot to measure (default: up to today)")
    pcmp.add_argument("--before-set", action="append", default=[], metavar="KEY=VALUE",
                      help="parameter for the before period only, e.g. auto=0.5 (repeatable)")
    pcmp.add_argument("--after-set", action="append", default=[], metavar="KEY=VALUE",
                      help="parameter for the after period only, e.g. auto=0.7 (repeatable)")
    for k, v in DEFAULTS.items():
        pcmp.add_argument(f"--{k}", type=float, default=None, help=f"override for both periods (default {v})")
    pcmp.add_argument("--nmax", type=int, default=30)
    pcmp.add_argument("--format", choices=["json", "md"], default="md")
    pc.add_argument("--format", choices=["json"], default="json")
    pf.add_argument("csv", help="file with lines: agents,finished_per_day")
    a = ap.parse_args()

    if a.cmd == "fit":
        rows = []
        for line in open(a.csv):
            parts = [x.strip() for x in line.split(",")]
            if len(parts) >= 2 and parts[0].replace(".", "").isdigit():
                rows.append((float(parts[0]), float(parts[1])))
        print(json.dumps(fit_usl(rows), indent=2)); return

    if a.cmd == "compare":
        def kv(items):
            out = {}
            for it in items:
                k, _, v = it.partition("=")
                if k not in DEFAULTS:
                    ap.error(f"unknown parameter {k!r}; choose from {', '.join(DEFAULTS)}")
                out[k] = int(float(v)) if k == "reviewers" else float(v)
            return out
        both = {k: (int(getattr(a, k)) if k == "reviewers" else getattr(a, k)) for k in DEFAULTS if getattr(a, k) is not None}
        c = compare(a.repo, parse_date(a.pivot), a.before, a.after, a.window, a.source, a.replay, a.limit,
                    both, kv(a.before_set), kv(a.after_set), a.nmax)
        print(json.dumps(c, indent=2, default=str) if a.format == "json" else compare_markdown(c)); return

    if a.cmd == "calibrate":
        s, src, ev = calibrate(a.repo, a.days, a.window, a.source, a.replay, a.limit, a.agents_used)
        print(json.dumps({"params": s, "sources": src, "evidence": ev}, indent=2, default=str)); return

    if a.cmd == "run":
        s, src, ev = calibrate(a.repo, a.days, a.window, a.source, a.replay, a.limit, a.agents_used)
    else:
        s, src, ev = dict(DEFAULTS), dict(SOURCES), {}
    for k in DEFAULTS:
        v = getattr(a, k)
        if v is not None:
            s[k] = int(v) if k == "reviewers" else v
            src[k] = "set by user"
    res = run_model(s, a.nmax, a.backlog, a.plan)
    if a.format == "json":
        print(json.dumps({"result": res, "sources": src, "evidence": ev}, indent=2, default=str))
    else:
        print(to_markdown(res, src, ev))
        if ev:
            print("\n<details><summary>Evidence</summary>\n\n```json\n" + json.dumps(ev, indent=2, default=str) + "\n```\n</details>")


if __name__ == "__main__":
    main()
