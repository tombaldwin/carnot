"""Offline reviewer calibration through the live review path (PLAN-v4 section 7; abort rule 4).

    python -m harness calibrate --config config.t2.toml --tasks T001,T002,... --variants reference,mutant \
        --out ../analysis/calibration.jsonl --job v2-checkout [--parallel 4] [--mutant-dir DIR]

For each task and variant, a commit is made in the orchestrator's local clone (never pushed):
the task's reference patch applied to ``base_ref`` ("reference", expected APPROVE), or a
mechanically broken copy of it ("mutant", expected REQUEST_CHANGES). The commit goes through
the SAME ``CommandReviewer`` as a live window: same command template, same prompt template,
same packet builder (``review.make_packet``), same checkout job. One line per review is
appended to the calibration-review log that ``analysis/predict.py --calibration`` reads for
abort rule 4 (format: analysis/README.md, "Calibration-review log").

Mutants. The reference patch is edited, never the task: one of
  * ``invert``: the first comparison / boolean token on one added line is inverted
    (== / !=, < / >=, > / <=, and / or, True / False, if / if not);
  * ``return_none``: one added ``return <expr>`` becomes ``return None``;
  * ``drop_hunk``: one hunk of a file the patch modifies is removed (patches with 2+ hunks);
  * ``drop_file``: one new file is removed (patches that touch 2+ files).
Candidates are tried in an order seeded by (seed, task id). A candidate is kept only if it applies
to ``base_ref``, differs from the reference, and (unless ``--no-check-hidden``) the task's hidden
tests FAIL on it, so every mutant is really broken. Mutants are written to ``--mutant-dir``
(default: a ``mutants/`` directory beside ``[tasks] reference_dir``), which must be a private path:
they contain task code, so the command refuses a directory inside the public repo. An existing
``<dir>/<id>.patch`` is reused, so reruns review the same mutants.

Durations. ``duration_s`` is the wall-clock time of the reviewer call(s) for that item, from
hand-over to parsed verdict, including an immediate retry (as the log format asks); export of the
checkout is inside the call, as it is live. ``--parallel k`` runs k reviews at once; calibration uses only
each call's duration and verdict (V = verdicts / summed call time), never queueing. Each row records
``parallel``. Parallel calls can be slower if they compete for CPU (each may run pytest): PLAN-v6 section 6.10's
contention check runs the same reference and mutant items with ``--parallel 1`` and ``--parallel 3`` under two
job labels (e.g. ``--job v6-par1`` / ``--job v6-par3``; mutants are reused from ``--mutant-dir``) and compares
the mean ``duration_s`` (the summary's ``mean_duration_s``). Live windows run K reviewers too since PLAN-v6
(``[reviewer] parallel``).
"""
from __future__ import annotations

import dataclasses as dc
import datetime as dt
import json
import os
import random
import re
import subprocess
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from .clock import iso
from .config import Config
from .gitops import Repo
from .review import CommandReviewer, ReviewError, make_packet
from .tasks import Task, TestRunner, load_tasks

VARIANTS = ("reference", "mutant")
EXPECTED = {"reference": "approve", "mutant": "request_changes"}
SOURCE = {"reference": "reference", "mutant": "broken"}   # analysis/predict.py CAL_SOURCES

_INVERT = [
    (re.compile(r"=="), "!="), (re.compile(r"!="), "=="),
    (re.compile(r"<="), ">"), (re.compile(r">="), "<"),
    (re.compile(r"(?<![<>=!\-])<(?![<=])"), ">="), (re.compile(r"(?<![<>=\-])>(?![>=])"), "<="),
    (re.compile(r"\band\b"), "or"), (re.compile(r"\bor\b"), "and"),
    (re.compile(r"\bTrue\b"), "False"), (re.compile(r"\bFalse\b"), "True"),
    (re.compile(r"\b(if|elif|while) not\b"), r"\1"), (re.compile(r"\b(if|elif|while)\b(?! not\b)"), r"\1 not"),
]
_RETURN = re.compile(r"^(\+\s*)return\s+(?!None\s*$)\S.*$")


# ----------------------------------------------------------------------------- patch editing
@dc.dataclass
class FileDiff:
    header: list[str]
    hunks: list[list[str]]

    @property
    def new_file(self) -> bool:
        return any(h.startswith("--- /dev/null") or h.startswith("new file mode") for h in self.header)

    def text(self) -> list[str]:
        return self.header + [ln for h in self.hunks for ln in h]


def parse_patch(text: str) -> list[FileDiff]:
    files: list[FileDiff] = []
    cur: FileDiff | None = None
    for ln in text.splitlines():
        if ln.startswith("diff --git "):
            cur = FileDiff([ln], [])
            files.append(cur)
        elif cur is None:
            continue
        elif ln.startswith("@@"):
            cur.hunks.append([ln])
        elif cur.hunks:
            cur.hunks[-1].append(ln)
        else:
            cur.header.append(ln)
    return files


def render_patch(files: list[FileDiff]) -> str:
    return "\n".join(ln for f in files for ln in f.text()) + "\n"


def mutant_candidates(text: str) -> list[tuple[str, str]]:
    """Every mechanical mutant of a patch as (description, patch text). Descriptions name the
    operator and position only (no code), so they can go in the public calibration log."""
    files = parse_patch(text)
    out = []
    n_hunks = sum(len(f.hunks) for f in files)
    for fi, f in enumerate(files):
        for hi, h in enumerate(f.hunks):
            for li, ln in enumerate(h):
                if not ln.startswith("+") or ln.startswith("+++"):
                    continue
                code = ln[1:].split("#", 1)[0]
                for rx, rep in _INVERT:
                    m = rx.search(code)
                    if m:
                        new = "+" + code[:m.start()] + m.expand(rep) + code[m.end():] + ln[1 + len(code):]
                        g = [dc.replace(x, hunks=[list(y) for y in x.hunks]) for x in files]
                        g[fi].hunks[hi][li] = new
                        out.append((f"invert file={fi} hunk={hi} line={li} op={rx.pattern}", render_patch(g)))
                        break
                if _RETURN.match(ln):
                    g = [dc.replace(x, hunks=[list(y) for y in x.hunks]) for x in files]
                    g[fi].hunks[hi][li] = _RETURN.sub(r"\1return None", ln)
                    out.append((f"return_none file={fi} hunk={hi} line={li}", render_patch(g)))
            if n_hunks > 1 and not f.new_file:
                g = [dc.replace(x, hunks=[list(y) for y in x.hunks]) for x in files]
                del g[fi].hunks[hi]
                g = [x for x in g if x.hunks or not x.new_file]
                out.append((f"drop_hunk file={fi} hunk={hi}", render_patch(g)))
        if len(files) > 1 and f.new_file:
            out.append((f"drop_file file={fi}", render_patch([x for j, x in enumerate(files) if j != fi])))
    return out


def commit_patch(repo: Repo, base: str, patch_text: str, msg: str) -> str | None:
    """``patch_text`` applied to ``base`` as a loose commit in ``repo`` (no ref, never pushed);
    None if it does not apply or changes nothing."""
    with tempfile.TemporaryDirectory(prefix="fleet-cal-") as td:
        pf = Path(td) / "p.patch"
        pf.write_text(patch_text)
        env = dict(repo.env, GIT_INDEX_FILE=os.path.join(td, "index"))

        def g(*a, check=True):
            with repo.lock:
                return subprocess.run(["git", *a], cwd=repo.path, env=env, capture_output=True, text=True,
                                      check=check)
        g("read-tree", base)
        if g("apply", "--cached", str(pf), check=False).returncode != 0:
            return None
        tree = g("write-tree").stdout.strip()
    if tree == repo.out("rev-parse", base + "^{tree}"):
        return None
    return repo.commit_tree(tree, [base], msg)


# ----------------------------------------------------------------------------- task lists and paths
def parse_task_list(arg: str, catalogue: dict[str, Task]) -> list[str]:
    """``all``, ``@file`` (one id per line, # comments), or a comma-separated list."""
    if arg == "all":
        ids = sorted(catalogue)
    elif arg.startswith("@"):
        ids = [ln.split("#", 1)[0].strip() for ln in Path(arg[1:]).read_text().splitlines()]
        ids = [i for i in ids if i]
    else:
        ids = [i.strip() for i in arg.split(",") if i.strip()]
    missing = [i for i in ids if i not in catalogue]
    if missing:
        raise SystemExit(f"calibrate: task ids not in the catalogue: {missing[:10]}")
    if len(set(ids)) != len(ids):
        raise SystemExit("calibrate: duplicate task ids in --tasks")
    return ids


def public_repo_root() -> Path | None:
    here = Path(__file__).resolve().parent
    p = subprocess.run(["git", "rev-parse", "--show-toplevel"], cwd=here, capture_output=True, text=True)
    return Path(p.stdout.strip()).resolve() if p.returncode == 0 else None


def refuse_if_public(path: Path, what: str) -> None:
    root = public_repo_root()
    if root is not None and (path.resolve() == root or root in path.resolve().parents):
        raise SystemExit(f"calibrate: {what} {path} is inside the public repo {root}; it holds task code, "
                         "so keep it in a private path (the tasks repo's gitignored dryrun/)")


# ----------------------------------------------------------------------------- the run
@dc.dataclass
class Item:
    task: str
    variant: str
    head: str
    mutation: str | None
    visible_passed: bool
    visible_output: str


def make_mutant(repo: Repo, runner: TestRunner | None, tid: str, base: str, ref_text: str, seed: int,
                max_tries: int = 25) -> tuple[str, str] | None:
    cands = mutant_candidates(ref_text)
    random.Random(f"{seed}-{tid}").shuffle(cands)
    for desc, text in cands[:max_tries]:
        if text == ref_text:
            continue
        head = commit_patch(repo, base, text, f"calibration mutant {tid}")
        if head is None:
            continue
        if runner is not None:
            _, hid = runner.run(head, visible=False, hidden_tasks=[tid])
            if hid.passed:
                continue
        return desc, text
    return None


def calibrate(cfg: Config, repo: Repo, task_ids: list[str], variants: list[str], out: Path, job: str,
              parallel: int = 1, mutant_dir: Path | None = None, check_hidden: bool = True, seed: int = 1,
              reviewer=None, detail_out: Path | None = None, echo=print) -> dict:
    bad = [v for v in variants if v not in VARIANTS]
    if bad or not variants:
        raise SystemExit(f"calibrate: --variants must be from {VARIANTS}, got {variants}")
    if parallel < 1:
        raise SystemExit("calibrate: --parallel must be >= 1")
    catalogue = load_tasks(cfg)
    ref_dir = cfg.path(cfg.tasks.reference_dir)
    mutant_dir = Path(mutant_dir) if mutant_dir else ref_dir.parent / "mutants"
    if "mutant" in variants:
        refuse_if_public(mutant_dir, "--mutant-dir")
        mutant_dir.mkdir(parents=True, exist_ok=True)
    if detail_out:
        refuse_if_public(Path(detail_out), "--detail-out")
    reviewer = reviewer or CommandReviewer(cfg, repo)
    runner = TestRunner(cfg, repo)

    repo.run("fetch", "-q", "--tags", "origin")
    base = repo.try_sha(f"refs/tags/{cfg.repo.base_ref}") or repo.sha(cfg.repo.base_ref)

    done: set[str] = set()
    if out.exists():
        for ln in out.read_text().splitlines():
            if ln.strip():
                done.add(json.loads(ln)["review_id"])

    # 1. prepare every item serially (commits, mutants, visible tests)
    index_path = mutant_dir / "index.json"
    index = json.loads(index_path.read_text()) if "mutant" in variants and index_path.exists() else {}
    items: list[Item] = []
    skipped: list[str] = []
    for tid in task_ids:
        ref_text = (ref_dir / f"{tid}.patch").read_text()
        for v in variants:
            rid = f"cal-{job}-{tid}-{v}"
            if rid in done:
                continue
            mutation = None
            if v == "reference":
                text = ref_text
            else:
                mp = mutant_dir / f"{tid}.patch"
                if mp.exists():
                    text, mutation = mp.read_text(), index.get(tid, {}).get("mutation", "existing file")
                else:
                    m = make_mutant(repo, runner if check_hidden else None, tid, base, ref_text, seed)
                    if m is None:
                        skipped.append(f"{tid}: no mutant that applies{' and fails the hidden tests' if check_hidden else ''}")
                        continue
                    mutation, text = m
                    mp.write_text(text)
                    index[tid] = dict(mutation=mutation, seed=seed, hidden_checked=check_hidden)
                    index_path.write_text(json.dumps(index, indent=2, sort_keys=True) + "\n")
            head = commit_patch(repo, base, text, f"calibration {v} {tid}")
            if head is None:
                skipped.append(f"{tid} {v}: patch does not apply to {cfg.repo.base_ref}")
                continue
            vis, _ = runner.run(head, visible=True, hidden_tasks=[])
            items.append(Item(tid, v, head, mutation, vis.passed, vis.output))
    echo(f"calibrate: {len(items)} reviews to run ({len(done)} already in {out.name}), "
         f"{len(skipped)} skipped, parallel {parallel}")

    # 2. review them through the live reviewer path
    lock = threading.Lock()
    rc = cfg.reviewer
    rows = []
    details = []

    def one(it: Item):
        packet = make_packet(cfg, repo, catalogue[it.task], it.head, base, it.visible_passed, it.visible_output)
        t0 = time.monotonic()
        res, err = None, None
        for _ in range(1 + rc.retries):
            try:
                res = reviewer.review(packet)
                break
            except Exception as e:  # ReviewError or anything unexpected, as live
                err = str(e) if isinstance(e, ReviewError) else f"{type(e).__name__}: {e}"
        dur = time.monotonic() - t0
        row = dict(review_id=f"cal-{job}-{it.task}-{it.variant}", duration_s=round(max(dur, 0.001), 3),
                   verdict=res.verdict if res else "error", source=SOURCE[it.variant], task=it.task,
                   expected=EXPECTED[it.variant], job=job, reviewer_model=cfg.run.reviewer_model,
                   t=iso(dt.datetime.now(dt.timezone.utc)), review_job=rc.job, mutation=it.mutation,
                   tokens_in=res.tokens_in if res else None, tokens_out=res.tokens_out if res else None,
                   parallel=parallel)
        with lock:
            with open(out, "a") as f:
                f.write(json.dumps(row) + "\n")
            rows.append(row)
            details.append(dict(review_id=row["review_id"], head=it.head, reason=res.reason if res else None,
                                error=None if res else err))
            echo(f"  {row['review_id']}: {row['verdict']} in {row['duration_s']:.1f}s (expected {row['expected']})")

    out.parent.mkdir(parents=True, exist_ok=True)
    if parallel == 1:
        for it in items:
            one(it)
    else:
        with ThreadPoolExecutor(parallel) as ex:
            list(ex.map(one, items))
    if detail_out:
        with open(detail_out, "a") as f:
            for d in details:
                f.write(json.dumps(d) + "\n")

    n = [r for r in rows if r["verdict"] != "error"]
    busy = sum(r["duration_s"] for r in rows)
    summary = dict(out=str(out), job=job, reviews=len(n), errors=len(rows) - len(n), skipped=skipped,
                   busy_hours=busy / 3600, V=(len(n) / (busy / 3600)) if busy > 0 else None, parallel=parallel,
                   mean_duration_s=(busy / len(rows)) if rows else None,
                   catch_rate_mutant=_rate(rows, "mutant", "request_changes"),
                   false_reject_reference=_rate(rows, "reference", "request_changes"))
    return summary


def _rate(rows, variant, verdict):
    r = [x for x in rows if x["source"] == SOURCE[variant] and x["verdict"] != "error"]
    return (sum(x["verdict"] == verdict for x in r) / len(r)) if r else None
