"""Exploratory, written after study 1 and not pre-registered: the shape of rework in AIDev.

    .venv/bin/python rework.py            # writes rework.json and prints a summary

Two questions from the heat-death paper's model, which treats rework as a constant rate r:
  A. Repeat returns. Does a change that has been sent back once ("changes requested") come back
     again at the same rate as any change comes back the first time? If so, rounds per PR follow a
     geometric series and total rework is r / (1 - r). If not, returns cluster.
  B. Knock-on. When a change that needed rework merges into files another open PR also touches, is
     that other PR then sent back or abandoned more often than when a change that needed no rework
     merges there? Compared within repositories, with a placebo: the same PR's send-backs BEFORE
     that merge, which a knock-on cannot explain but a strict reviewer can.

Sample: review-gated repositories as exploratory.py defines them (median PR life at least 1 hour),
with at least MIN_PRS agent PRs, as in analyze.py. A wider sample without the MIN_PRS floor is
reported as a check. AIDev has no commit timestamps or line counts, so the SIZE of rework cannot be
measured here.
"""
import json, math
import numpy as np, pandas as pd
import analyze as A

END = pd.Timestamp("2025-09-01", tz="UTC")

def load(min_prs):
    pr = pd.read_parquet("data/pull_request.parquet", columns=["id", "created_at", "closed_at", "merged_at", "repo_id"])
    for c in ("created_at", "closed_at", "merged_at"):
        pr[c] = pd.to_datetime(pr[c], utc=True, errors="coerce")
    pr["life_h"] = (pr.closed_at.fillna(END) - pr.created_at).dt.total_seconds() / 3600
    pr = pr[pr.groupby("repo_id").life_h.transform("median") >= 1.0]
    pr = pr[pr.groupby("repo_id").id.transform("size") >= min_prs].copy()
    rv = pd.read_parquet("data/pr_reviews.parquet", columns=["pr_id", "state", "submitted_at"])
    rv["submitted_at"] = pd.to_datetime(rv.submitted_at, utc=True, errors="coerce")
    rv = rv[rv.pr_id.isin(pr.id)]
    cr = rv[rv.state == "CHANGES_REQUESTED"]
    pr["cr"] = pr.id.map(cr.groupby("pr_id").size()).fillna(0).astype(int)
    files = pd.read_parquet("data/pr_files.parquet")
    files = files[files.pr_id.isin(pr.id) & ~files.filename.fillna("").str.contains(A.NOISE_RE)]
    return pr, set(rv.pr_id), cr.groupby("pr_id").submitted_at.apply(list).to_dict(), files.groupby("pr_id").filename.agg(frozenset).to_dict()

def rounds_within(crt, ids, window_min):
    """Rounds of 'changes requested' per PR, counting requests less than window_min apart as one:
    AIDev has no commit times, so two requests with no push between them cannot be told apart."""
    out = {}
    for i in ids:
        n, last = 0, None
        for t in sorted(x for x in crt.get(i, []) if pd.notna(x)):
            if last is None or (t - last).total_seconds() > window_min * 60:
                n += 1
            last = t
        out[i] = n
    return pd.Series(out)

def shape(cnt):
    cond = {j: {"n": int((cnt >= j).sum()), "p_again": float((cnt >= j + 1).sum() / (cnt >= j).sum())}
            for j in range(5) if (cnt >= j).sum() >= 30}
    r1 = cond[0]["p_again"]
    return cond, float(cnt.mean()), r1 / (1 - r1)

def repeat_returns(pr, reviewed, crt):
    d = pr[pr.closed_at.notna() & pr.id.isin(reviewed)]
    cond = {}
    for j in range(5):
        at, nxt = int((d.cr >= j).sum()), int((d.cr >= j + 1).sum())
        if at >= 30:
            cond[j] = {"n": at, "p_again": nxt / at}
    r1 = cond[0]["p_again"]
    mean_rounds = float(d.cr.mean())
    sens = {}
    for w in (10, 60):
        c2, m2, g2 = shape(rounds_within(crt, d.id, w))
        sens[f"merge_within_{w}min"] = {"p_again_given_rounds": {j: v["p_again"] for j, v in c2.items()}, "excess_over_geometric": m2 / g2 - 1}
    return {"prs": int(len(d)), "rounds": {str(k): int(v) for k, v in d.cr.clip(upper=6).value_counts().sort_index().items()},
            "p_again_given_rounds": cond, "mean_rounds": mean_rounds, "geometric_mean_rounds": r1 / (1 - r1),
            "excess_over_geometric": mean_rounds / (r1 / (1 - r1)) - 1,
            # per review rather than per change: the share of all reviews-with-outcome that send back,
            # which already counts the repeats (section 9 of the paper counts this way)
            "per_review_rate": mean_rounds / (1 + mean_rounds), "sensitivity": sens}

def mh(df, y):
    num = den = 0.0
    for _, s in df.groupby("repo"):
        a, c = s[s.x == 1], s[s.x == 0]
        if len(a) and len(c):
            w = len(a) * len(c) / len(s); num += w * (a[y].mean() - c[y].mean()); den += w
    return num / den if den else float("nan")

def knock_on(pr, crt, fs, boots=400):
    rows = []
    for repo, g in pr.groupby("repo_id"):
        g = g[g.id.isin(fs.keys())]; merged = g[g.merged_at.notna()]
        for b in g.itertuples():
            if pd.isna(b.closed_at):
                continue
            ov = [a for a in merged.itertuples()
                  if a.id != b.id and b.created_at < a.merged_at < b.closed_at and (fs[a.id] & fs[b.id])]
            if len(ov) != 1:
                continue
            a, ts = ov[0], crt.get(b.id, [])
            rows.append(dict(repo=repo, x=int(a.cr > 0), unmerged=float(pd.isna(b.merged_at)),
                             after=float(any(t > a.merged_at for t in ts)), before=float(any(t <= a.merged_at for t in ts))))
    S = pd.DataFrame(rows)
    out = {"prs": int(len(S)), "exposed_to_reworked": int(S.x.sum()),
           "raw": {y: {"reworked": float(S[S.x == 1][y].mean()), "clean": float(S[S.x == 0][y].mean())} for y in ("unmerged", "after", "before")},
           "within_repo_rd": {y: mh(S, y) for y in ("unmerged", "after", "before")}}
    both = S.groupby("repo").x.agg(lambda v: v.nunique() == 2)
    out["repos_with_both_arms"] = int(both.sum())
    rng, repos = np.random.default_rng(1), S.repo.unique()
    reps = {k: [] for k in ("unmerged", "after", "before", "after_minus_before")}
    for _ in range(boots):
        pick = rng.choice(repos, len(repos))
        B = pd.concat([S[S.repo == r].assign(repo=f"{r}_{i}") for i, r in enumerate(pick)])
        v = {y: mh(B, y) for y in ("unmerged", "after", "before")}
        v["after_minus_before"] = v["after"] - v["before"]
        for k, x in v.items():
            if not math.isnan(x):
                reps[k].append(x)
    out["ci95"] = {k: [float(np.percentile(v, 2.5)), float(np.percentile(v, 97.5))] for k, v in reps.items()}
    out["after_minus_before"] = {"mean": float(np.mean(reps["after_minus_before"])), "ci95": out["ci95"]["after_minus_before"]}
    return out

if __name__ == "__main__":
    out = {}
    for name, floor in (("primary", A.MIN_PRS), ("all_gated_repos", 1)):
        pr, reviewed, crt, fs = load(floor)
        out[name] = {"repos": int(pr.repo_id.nunique()), "prs": int(len(pr)),
                     "A_repeat_returns": repeat_returns(pr, reviewed, crt), "B_knock_on": knock_on(pr, crt, fs)}
    json.dump(out, open("rework.json", "w"), indent=2)
    print(json.dumps(out, indent=2))
