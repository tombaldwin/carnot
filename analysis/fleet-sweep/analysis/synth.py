#!/usr/bin/env python3
"""Synthetic event logs for the fleet sweep, conforming to SCHEMA.md, from a chosen "truth".

This is a small discrete-event simulation of the harness in PLAN-v3 section 5, not a count
generator:

* N workers (per-worker schedule), each claiming tasks from a seeded task order, working for a
  random time, pushing `READY:` (submit), then claiming the next task without waiting. Between tasks
  a worker first reworks any of its branches that were sent back (FEEDBACK.md), and re-submits.
  Worker speed follows the truth's throughput curve: per-worker rate = lam1 * X(n_active) / n_active
  (USL for "carnot"/"usl", alpha-only for "amdahl", n for "linear").
* One FIFO reviewer, one change at a time, service time ~ Gamma(mean 1/rate, CV service_cv), where
  rate = V0 * max(min_factor, 1 + reviewer_load * queue_depth). reviewer_load = 0 is a fixed-capacity
  reviewer (the Carnot assumption); > 0 skims under load; < 0 slows under load. Every
  re-submission is reviewed again. Review errors are retried once.
* Latent defects: each submitted head is defective with a task-specific probability. The reviewer
  catches a defect with probability catch0 * exp(-escape_depth * queue_depth) and wrongly rejects a
  good change with probability false_reject. An approved defective head fails its hidden tests in
  the merge queue: an escaped defect.
* Collisions: at each submit the change collides with each of the k others in flight with
  probability p (p + p_m if they share a file). A collided change bounces in the merge queue, as a
  rebase conflict (share conflict_share) or an integration failure (hidden tests fail only after
  rebase).
* Serial merge queue: hidden_pre -> rebase -> tests_post -> merge, with CI times.
* Workers stop at window_end; the reviewer and merge queue run to window_end + grace; whatever is
  still open then is simply left open (censoring happens in derive.py, as it will for real logs).

Usage (writes run directories under --out):

    python synth.py --truth carnot --sizes 1 5 --out /tmp/synth-carnot --seed 1
    python synth.py --truth usl --sizes 1 5 --out /tmp/synth-usl --set V0=14 escape_depth=0.1

Each call writes a T1 trial, a T2 pilot and four sweep windows in the order low, high, high, low.
"""
from __future__ import annotations

import argparse
import dataclasses
import heapq
import json
import math
import random
import sys
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import X_amdahl, X_linear, X_usl, iso  # noqa: E402

T0 = 1790845200.0  # 2026-10-01T09:00:00Z; window start of the first synthetic run


@dataclass
class Truth:
    family: str = "carnot"            # worker curve: carnot|usl (USL X), amdahl (alpha only), linear
    lam1: float = 4.0                 # single-agent first-attempt rate per hour, no rework drag
    alpha: float = 0.1
    beta: float = 0.01
    V0: float = 12.7                  # reviewer service rate at queue depth 0 (reviews per busy hour)
    reviewer_load: float = 0.0        # rate multiplier 1 + reviewer_load * depth
    reviewer_min_factor: float = 0.25
    service_cv: float = 1.0           # 1 = exponential service time
    defect_p: float = 0.35            # mean probability a first-attempt head is defective
    task_sd: float = 0.5              # task heterogeneity, sd on the logit scale
    rework_defect_factor: float = 0.5  # defect probability multiplier for re-submissions
    catch0: float = 0.7
    escape_depth: float = 0.0         # catch = catch0 * exp(-escape_depth * depth)
    false_reject: float = 0.10
    p: float = 0.005
    p_m: float = 0.0
    conflict_share: float = 0.5
    visible_fail: float = 0.02
    rework_min: float = 6.0
    rework_dist: str = "exp"          # exp | fixed
    rework_uses_worker: bool = True
    rework_returns: bool = True       # False: bounced changes are abandoned (rival formulas' reading)
    startup_min: float = 2.0
    work_cv: float = 1.0
    cv_window: float = 0.0            # common-mode Gamma(mean 1, CV) multiplier on worker speed per run
    claim_race_p: float = 0.02
    ci_hidden_min: float = 1.0
    ci_post_min: float = 1.5
    rebase_s: float = 5.0
    review_error_p: float = 0.01
    usage_every_min: float = 10.0
    n_files: int = 40
    n_tasks: int = 120

    def X(self, n):
        if self.family in ("carnot", "usl"):
            return float(X_usl(n, self.alpha, self.beta))
        if self.family == "amdahl":
            return float(X_amdahl(n, self.alpha))
        return float(X_linear(n))


FAST = dict(reviewer_load=2.0)  # "no review limit": the reviewer speeds up to meet any demand
TRUTHS = {
    "carnot": dict(family="carnot"),
    "usl": dict(family="usl", **FAST),
    "amdahl": dict(family="amdahl", **FAST),
    "linear": dict(family="linear", **FAST),
    "carnot-skim": dict(family="carnot", reviewer_load=0.08),
    "carnot-slow": dict(family="carnot", reviewer_load=-0.04),
}


def make_truth(name="carnot", **overrides) -> Truth:
    kw = dict(TRUTHS[name])
    kw.update(overrides)
    return Truth(**kw)


@dataclass
class Task:
    id: str
    files: list
    logit: float
    lines: int


def make_task_pool(truth: Truth, seed: int):
    rng = random.Random(seed)
    weights = [1 / (i + 1) ** 0.8 for i in range(truth.n_files)]  # a few popular files
    files = [f"src/sandbox/mod{i:02d}.py" for i in range(truth.n_files)]
    base = math.log(truth.defect_p / (1 - truth.defect_p))
    pool = []
    for i in range(truth.n_tasks):
        nf = rng.choice([1, 1, 2, 2, 3])
        fs = set()
        while len(fs) < nf:
            fs.add(rng.choices(files, weights)[0])
        pool.append(Task(f"{i + 1:03d}", sorted(fs), base + rng.gauss(0, truth.task_sd), rng.randint(20, 150)))
    return pool


def _hex(rng, n=12):
    return "%0*x" % (n, rng.getrandbits(4 * n))


def simulate(truth: Truth, n_workers: int, *, seed: int, window_min=90.0, warmup_min=10.0,
             grace_min=10.0, schedule=None, task_pool=None, run_id="synth", kind="sweep",
             t0=T0, task_order_seed=None):
    """Simulate one window. Returns (run_json, events) with events as SCHEMA.md dicts."""
    rng = random.Random(seed)
    pool = task_pool or make_task_pool(truth, seed + 7919)
    order_seed = task_order_seed if task_order_seed is not None else seed
    order = list(pool)
    random.Random(order_seed).shuffle(order)
    sched = schedule or [(0.0, window_min)] * n_workers
    W = len(sched)
    end_s = window_min * 60
    end_all = end_s + grace_min * 60
    mult = 1.0 if truth.cv_window <= 0 else rng.gammavariate(1 / truth.cv_window ** 2, truth.cv_window ** 2)

    heap = []
    seq = [0]
    out = []

    def at(t, fn, *args):
        seq[0] += 1
        heapq.heappush(heap, (t, seq[0], fn, args))

    def emit(t, typ, **f):
        out.append((t, {"t": iso(t0 + t), "type": typ, **f}))

    workers = [dict(id=f"w{i + 1}", start=s * 60, end=e * 60, pending=deque(), last_claim=None)
               for i, (s, e) in enumerate(sched)]

    def n_active(t):
        return max(1, sum(1 for w in workers if w["start"] <= t < w["end"]))

    def drag(t):
        n = n_active(t)
        return truth.X(n) / n  # per-worker share of single-agent speed

    def gamma_time(mean, cv):
        if mean <= 0:
            return 0.0
        if cv <= 0:
            return mean
        k = 1 / cv ** 2
        return rng.gammavariate(k, mean / k)

    changes = {}      # task id -> state
    ptr = [0]
    last_claimed = [None]
    reviewq = deque()
    rev = dict(busy=False)
    mq = deque()
    mqs = dict(busy=False)

    # ---------------------------------------------------------------- workers
    def worker_next(w, t):
        if t >= w["end"]:
            return
        if truth.rework_returns and truth.rework_uses_worker and w["pending"]:
            task = w["pending"].popleft()
            mean = truth.rework_min * 60
            dur =mean if truth.rework_dist == "fixed" else gamma_time(mean, 1.0)
            dur /= (drag(t) * mult)  # coordination drag slows rework as it slows new work
            if t + dur < w["end"]:
                at(t + dur, do_submit, w, task)
            return
        if ptr[0] >= len(order):
            return
        if last_claimed[0] is not None and rng.random() < truth.claim_race_p:
            emit(t, "claim_race", worker=w["id"], task=last_claimed[0])
        task = order[ptr[0]]
        ptr[0] += 1
        last_claimed[0] = task.id
        changes[task.id] = dict(task=task, owner=w, attempt=0, head=None, inflight=False, defective=False,
                                collided=False, merged=False, depth=0)
        emit(t, "claim", worker=w["id"], task=task.id, branch=f"claude/task-{task.id}")
        rate = truth.lam1 * drag(t) * mult / 3600.0
        dur = gamma_time(1 / rate, truth.work_cv)
        if t + dur < w["end"]:
            at(t + dur, do_submit, w, task.id)

    def do_submit(t, w, task_id):
        ch = changes[task_id]
        ch["attempt"] += 1
        ch["head"] = _hex(rng)
        others = [c for tid, c in changes.items() if c["inflight"] and tid != task_id]
        fs = set(ch["task"].files)
        k = len(others)
        m = sum(1 for c in others if fs & set(c["task"].files))
        ch["inflight"] = True
        d = 1 / (1 + math.exp(-ch["task"].logit))
        if ch["attempt"] > 1:
            d *= truth.rework_defect_factor
        ch["defective"] = rng.random() < d
        p_none = (1 - truth.p) ** k * (1 - truth.p_m) ** m
        ch["collided"] = rng.random() > p_none
        emit(t, "submit", worker=w["id"], task=task_id, branch=f"claude/task-{task_id}", head=ch["head"],
             attempt_no=ch["attempt"], lines_changed=ch["task"].lines if ch["attempt"] == 1 else rng.randint(5, 60),
             files=list(ch["task"].files), k=k, m=m)
        reviewq.append(task_id)
        if not rev["busy"]:
            start_review(t)
        if ch["attempt"] == 1 or truth.rework_uses_worker:
            worker_next(w, t)

    # ---------------------------------------------------------------- reviewer
    def start_review(t):
        task_id = reviewq.popleft()
        ch = changes[task_id]
        depth = len(reviewq)
        ch["depth"] = depth
        if not rev["busy"]:
            emit(t, "reviewer_busy")
            rev["busy"] = True
        emit(t, "review_start", task=task_id, head=ch["head"], queue_depth=depth)
        rate = truth.V0 * max(truth.reviewer_min_factor, 1 + truth.reviewer_load * depth) / 3600.0
        dur = gamma_time(1 / rate, truth.service_cv)
        if rng.random() < truth.review_error_p:
            t_err = t + dur * rng.random()
            at(t_err, review_error, task_id, ch["head"])
            dur = (t_err - t) + gamma_time(1 / rate, truth.service_cv)
        at(t + dur, review_end, task_id, ch["head"], t)

    def review_error(t, task_id, head):
        emit(t, "review_error", task=task_id, head=head, error="synthetic: reviewer call failed, retried")
        emit(t, "review_start", task=task_id, head=head, queue_depth=len(reviewq))

    def review_end(t, task_id, head, t_start):
        ch = changes[task_id]
        depth = ch["depth"]
        if ch["defective"]:
            catch = truth.catch0 * math.exp(-truth.escape_depth * depth)
            reject = rng.random() < catch
        else:
            reject = rng.random() < truth.false_reject
        verdict = "request_changes" if reject else "approve"
        emit(t, "review_end", task=task_id, head=head, verdict=verdict,
             reason="acceptance criterion not met" if reject else "looks good",
             tokens_in=int(4000 + 30 * ch["task"].lines + rng.randint(0, 3000)),
             tokens_out=int(300 + rng.randint(0, 900)), duration_s=round(t - t_start, 3))
        if reject:
            bounce(t, task_id, "review")
        else:
            mq.append(task_id)
            if not mqs["busy"]:
                emit(t, "queue_busy")
                mqs["busy"] = True
                mq_start(t)
        if reviewq:
            start_review(t)
        else:
            emit(t, "reviewer_idle")
            rev["busy"] = False

    # ---------------------------------------------------------------- merge queue
    def mq_start(t):
        task_id = mq.popleft()
        at(t + gamma_time(truth.ci_hidden_min * 60, 0.2), hidden_pre, task_id)

    def mq_next(t):
        if mq:
            mq_start(t)
        else:
            emit(t, "queue_idle")
            mqs["busy"] = False

    def hidden_pre(t, task_id):
        ch = changes[task_id]
        ok = not ch["defective"]
        emit(t, "hidden_pre", task=task_id, head=ch["head"], passed=ok)
        if not ok:
            bounce(t, task_id, "escaped_defect")
            mq_next(t)
        else:
            at(t + truth.rebase_s, rebase, task_id)

    def rebase(t, task_id):
        ch = changes[task_id]
        conflict = ch["collided"] and rng.random() < truth.conflict_share
        ch["conflict"] = conflict
        emit(t, "rebase", task=task_id, head=ch["head"], new_head=None if conflict else _hex(rng), conflict=conflict)
        if conflict:
            bounce(t, task_id, "rebase_conflict")
            mq_next(t)
        else:
            at(t + gamma_time(truth.ci_post_min * 60, 0.2), tests_post, task_id)

    def tests_post(t, task_id):
        ch = changes[task_id]
        vis = rng.random() >= truth.visible_fail
        hid = not ch["collided"]
        emit(t, "tests_post", task=task_id, head=ch["head"], visible_passed=vis, hidden_passed=hid)
        if not vis:
            bounce(t, task_id, "visible_fail")
        elif not hid:
            bounce(t, task_id, "integration_failure")
        else:
            emit(t, "merge", task=task_id, head=ch["head"], main_sha=_hex(rng, 40))
            ch["inflight"] = False
            ch["merged"] = True
        mq_next(t)

    # ---------------------------------------------------------------- feedback
    def bounce(t, task_id, cause):
        ch = changes[task_id]
        emit(t, "bounce", task=task_id, head=ch["head"], cause=cause)
        if not truth.rework_returns:
            ch["inflight"] = False  # abandoned
            return
        w = ch["owner"]
        if truth.rework_uses_worker:
            w["pending"].append(task_id)
        else:
            mean = truth.rework_min * 60
            dur = mean if truth.rework_dist == "fixed" else gamma_time(mean, 1.0)
            if t + dur < w["end"]:
                at(t + dur, do_submit, w, task_id)

    def usage(t, w):
        if t >= w["end"]:
            return
        tin = int(rng.gauss(90000, 15000) * truth.usage_every_min / 10)
        emit(t, "usage", worker=w["id"], tokens_in=max(tin, 0), tokens_out=int(tin * 0.05),
             cost_usd_est=round(4.2 * truth.usage_every_min / 60, 3))
        at(t + truth.usage_every_min * 60, usage, w)

    def start_worker(t, w):
        emit(t, "worker_start", worker=w["id"], session_id=f"session_{_hex(rng, 8)}")
        at(t + truth.startup_min * 60, lambda tt, ww: worker_next(ww, tt), w)
        if truth.usage_every_min > 0:
            at(t + truth.usage_every_min * 60, usage, w)

    for w in workers:
        at(w["start"] + rng.uniform(0, 5), start_worker, w)

    while heap:
        t, _, fn, args = heapq.heappop(heap)
        if t > end_all:
            break
        fn(t, *args)

    out.sort(key=lambda x: x[0])  # stable: same-time events keep emission order
    run = {"run_id": run_id, "kind": kind, "n_workers": n_workers, "window_start": iso(t0),
           "window_end": iso(t0 + end_s), "warmup_min": warmup_min, "grace_min": grace_min,
           "task_order_seed": int(order_seed), "sandbox_commit": "synthetic", "harness_commit": "synthetic",
           "worker_model": "synthetic", "reviewer_model": "synthetic",
           "notes": "synth.py truth: " + json.dumps(dataclasses.asdict(truth), sort_keys=True)}
    return run, [e for _, e in out]


def simulate_study(truth: Truth, sizes, *, seed: int, reps=2, window_min=90.0, warmup_min=10.0,
                   grace_min=10.0, with_pilot=True):
    """T1 trial, T2 pilot, then sweep windows in ABBA order (low, high, high, low for reps=2).
    Returns a list of (run_json, events)."""
    pool = make_task_pool(truth, seed * 31 + 5)
    runs = []
    t0 = T0 + (seed % 100000) * 86400.0
    if with_pilot:
        # T1: 1 worker alone 15 min, then 9 at once 15 min; the reviewer keeps going (long grace) so
        # every T1 PR is reviewed (PLAN-v3 section 3).
        sched = [(0.0, 30.0)] + [(15.0, 30.0)] * 8
        runs.append(simulate(truth, 9, seed=seed * 1000 + 1, window_min=30, warmup_min=0, grace_min=60,
                             schedule=sched, task_pool=pool, run_id=f"synth-{seed}-T1", kind="trial", t0=t0))
        runs.append(simulate(truth, 2, seed=seed * 1000 + 2, window_min=60, warmup_min=warmup_min,
                             grace_min=grace_min, task_pool=pool, run_id=f"synth-{seed}-T2", kind="pilot",
                             t0=t0 + 3 * 3600))
    lo, hi = sizes
    order = []
    for r in range(reps):
        order += [lo, hi] if r % 2 == 0 else [hi, lo]
    for i, n in enumerate(order):
        runs.append(simulate(truth, n, seed=seed * 1000 + 10 + i, window_min=window_min, warmup_min=warmup_min,
                             grace_min=grace_min, task_pool=pool, run_id=f"synth-{seed}-N{n}-w{i + 1}",
                             kind="sweep", t0=t0 + (6 + 2.5 * i) * 3600))
    return runs


def write_run(base: Path, run, events):
    d = Path(base) / run["run_id"]
    d.mkdir(parents=True, exist_ok=True)
    (d / "run.json").write_text(json.dumps(run, indent=2) + "\n")
    with open(d / "events.jsonl", "w") as f:
        for e in events:
            f.write(json.dumps(e, separators=(",", ":")) + "\n")
    return d


def _coerce(v: str):
    for f in (int, float):
        try:
            return f(v)
        except ValueError:
            pass
    if v.lower() in ("true", "false"):
        return v.lower() == "true"
    return v


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--truth", default="carnot", choices=sorted(TRUTHS))
    ap.add_argument("--set", nargs="*", default=[], metavar="KEY=VALUE", help="override Truth fields")
    ap.add_argument("--sizes", nargs=2, type=int, default=[1, 5], metavar=("N_LOW", "N_HIGH"))
    ap.add_argument("--reps", type=int, default=2)
    ap.add_argument("--no-pilot", action="store_true")
    ap.add_argument("--window-min", type=float, default=90)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    ov = {}
    for kv in a.set:
        k, v = kv.split("=", 1)
        ov[k] = _coerce(v)
    truth = make_truth(a.truth, **ov)
    runs = simulate_study(truth, a.sizes, seed=a.seed, reps=a.reps, window_min=a.window_min,
                          with_pilot=not a.no_pilot)
    for run, ev in runs:
        d = write_run(Path(a.out), run, ev)
        print(f"{d}  kind={run['kind']} N={run['n_workers']} events={len(ev)}")


if __name__ == "__main__":
    main()
