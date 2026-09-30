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
* ``per_task_sessions = true`` (``--sessions``) switches to the harness's current worker model, one cloud
  session per task: N slots, each running one session at a time; the harness hands tasks out in the seeded
  order (``slot_busy`` + ``session_launch``); a session pushes its branch (``claim``) after
  ``session_startup_min`` and READY after its work time; READY frees the slot; a bounce queues rework, which
  goes to the next free slot before any new task (``session_message``, kind rework) and to the same session;
  a session with no READY within ``task_timeout_min`` is abandoned (``session_timeout``). The default
  (False) is the long-running-worker model the operating characteristics were computed with; its random
  streams are unchanged.

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
    per_task_sessions: bool = False   # True: one session per task in N slots (the harness's worker model)
    session_startup_min: float = 1.0  # sessions: launch -> branch pushed (provisioning, clone), exp. mean
    task_timeout_min: float = 25.0    # sessions: no READY this long after launch / rework message: abandoned
    # ---- PLAN-v5 process (all defaults leave the v4 behaviour and its random streams unchanged)
    service_dist: str = "gamma"       # gamma (mean 1/V0, CV service_cv) | uniform (service_lo_s..service_hi_s seconds)
    service_lo_s: float = 10.0        # v5: the measured default-effort review, 10-30 s per change
    service_hi_s: float = 30.0
    collision_model: str = "inflight"  # inflight (v4: p per change in flight at submit) | lifetime (p per other change
                                       # merged since this change's base) | census (a fixed pair-conflict graph)
    census_manifest: float = 1.0      # census: chance a structurally conflicting pair conflicts when exposed
    census_given_share: float = 0.083  # census: P(pair conflicts | pair shares a file) for the synthetic graph
    pairs_json: str = ""              # census: optional path to a private pair census (files + textual_conflicts only)
    integration_bg: float = 0.0       # lifetime / census: integration failures unrelated to collisions, per merge-queue pass
    escape_N: float = 0.0             # reviewer miss probability x (1 + escape_N (N - 1) / 11): escapes rising with N
    escape_k: float = 0.0             # reviewer miss probability x (1 + escape_k k): escapes rising with changes in flight
    file_zipf: float = 0.8            # file popularity exponent in make_task_pool
    files_per_task: tuple = (1, 1, 2, 2, 3)
    # ---- PLAN-v6 process (T1-measured; all defaults leave v4 / v5 behaviour and their random streams unchanged)
    v6: bool = False                  # the T1-calibrated slot cycle below (sessions only)
    n_reviewers: int = 1              # K parallel reviewers sharing one FIFO queue (v6 logs carry `reviewer`)
    service_quantiles: tuple = ()     # service_dist = "empirical": review seconds at the 0, 5, ..., 100th percentiles
    review_contention: float = 0.0    # review time x (1 + review_contention (K - 1)): parallel local calls slowing each other
    rearm_s: float = 20.1             # v6: slot freed -> session_launch (the routine re-arm), gamma CV rearm_cv
    rearm_cv: float = 0.11
    rework_msg_s: float = 1.5         # v6: slot freed -> session_message (rework follow-up)
    startup_median_s: float = 85.0    # v6: session_launch -> branch pushed, lognormal
    startup_logsd: float = 0.28
    work_median_s: float = 83.0       # v6: branch pushed -> first READY (coding), lognormal
    work_logsd: float = 0.45
    rework_median_s: float = 70.0     # v6: rework message -> next READY, lognormal
    rework_logsd: float = 0.33
    throttle_factor: float = 1.0      # v6: service-side slowdown of start-up, coding and rework when N > 1 (rule 1 OCs)
    throttle_startup: bool = True     # v6: whether the throttle also slows start-up (False: coding / rework only)
    drag_startup: bool = False        # v6: False (default): coordination drag stretches only the work (coding, rework),
                                      # by enough that the per-agent cycle scales as N / X(N); True: every leg / (X / N)
    # ---- PLAN-v7 process: `claude --cloud` command launcher, ONE SESSION PER SLOT (T0d / T0e, 2026-09-29). All
    # defaults leave v4-v6 behaviour and their random streams unchanged.
    v7: bool = False                  # one session per slot: a slot's session gets tasks_per_session tasks, then retires
    tasks_per_session: int = 4        # v7: [launcher] tasks_per_session (4 since T0e's compaction at the 6th task)
    messages_per_session: int = 0     # v7: [launcher] messages_per_session: retire once the session has had this many
                                      # messages (launch prompt, kind=task and rework messages); 0 = off
    launch_delay_s: float = 4.1       # v7: slot freed -> session_launch (the launch command returns the session id)
    followup_delay_s: float = 1.6     # v7: slot freed -> session_message kind=task (the follow-up command returns)
    startup_launch_median_s: float = 28.7   # v7: session_launch -> branch pushed (provisioning, clone, first push)
    startup_launch_logsd: float = 0.05
    startup_follow_median_s: float = 15.0   # v7: session_message kind=task -> branch pushed (no provisioning)
    startup_follow_logsd: float = 0.05
    stall_p: float = 0.0              # v7: a hand-out that never reaches READY (e.g. context compaction, a mis-named branch): the session timeout
    throttle_launch_only: bool = False  # v7: the throttle slows only launches (provisioning under concurrency)
    task_time_sd: float = 0.0         # v7: per-task log-sd of a coding / rework time multiplier (the same task is slower
                                      # or faster in every window that uses it; tasks are reused across windows)
    follow_code_factor: float = 1.0   # v7: coding time of a follow-up task (positions 2.. in its session) x this factor
                                      # (shared context: T0e's follow-ups coded in 62 s against T0d's 95 s, n = 5)

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


# PLAN-v5 process (design-search/DESIGN-SEARCH-v5.md): one Haiku session per task in N slots (1.3-min start-up, 25-min
# timeout), the measured fast reviewer (10-30 s per change, uniform, never load-dependent), a ~2 s serial merge queue,
# collisions per other change merged since a change's base (its launch, or its last rework message), background
# integration failures, 220 tasks on 28 files with the census's sharing rate (15% of pairs share a file), window CV
# 0.3. lam1 is set so that lambda at N = 1 is about 6 first attempts per slot-hour (dry runs); defect_p / catch0 /
# false_reject give an escape share of about 0.10 of approvals and a review-bounce share of about 0.3 at N = 1.
V5_TRUTH = dict(per_task_sessions=True, session_startup_min=1.3, task_timeout_min=25.0, lam1=11.0, work_cv=1.0,
                rework_min=5.0, service_dist="uniform", service_lo_s=10.0, service_hi_s=30.0, V0=180.0, reviewer_load=0.0,
                ci_hidden_min=0.01, ci_post_min=0.0167, rebase_s=0.4, collision_model="lifetime", p=0.005, p_m=0.0,
                conflict_share=0.8, integration_bg=0.01, defect_p=0.35, catch0=0.77, false_reject=0.05,
                cv_window=0.3, n_tasks=220, n_files=28, file_zipf=0.85,
                files_per_task=(1,) * 28 + (2,) * 13 + (3,) * 2 + (4,), usage_every_min=0.0, claim_race_p=0.0)
# PLAN-v5 worker truths (rival families): drag on the per-agent rate, and p for Carnot's collision term.
V5_FAMILIES = {
    "linear": dict(family="linear", p=0.0),
    "mild": dict(family="amdahl", alpha=0.03, p=0.0),       # a milder bend than any rival (sensitivity)
    "amdahl": dict(family="amdahl", alpha=0.1, p=0.0),
    "usl": dict(family="usl", alpha=0.1, beta=0.01, p=0.0),
    "carnot": dict(family="carnot", alpha=0.1, beta=0.01, p=0.005),
}


def make_truth_v5(name="carnot", **overrides) -> Truth:
    kw = dict(V5_TRUTH)
    kw.update(V5_FAMILIES[name])
    kw.update(overrides)
    return Truth(**kw)


# PLAN-v6 process (design-search/DESIGN-SEARCH-v6.md), calibrated on the live trials T1 and T0c (2026-09-28;
# design-search/t1_params_public.json, from t1_params.py, numbers only). Per task on a slot: re-arm 20 s (the routine
# re-arm call), start-up (launch -> branch pushed) lognormal median 85 s, coding (pushed -> READY) lognormal median
# 83 s, rework (message -> READY) lognormal median 70 s, the rework follow-up sent 1.5 s after a slot frees. Review:
# the pooled T1 + T0c durations (134 reviews, median 20.1 s, p90 29.3 s, max 38.9 s; no dependence on queue depth),
# K reviewers on one FIFO queue. 39% of reviews request changes on first and rework attempts alike (46 / 117), and
# 2 of 71 approvals failed hidden tests (defect_p 0.20, catch0 0.915, false_reject 0.26, no rework discount). Merge
# queue ~5.8 s per change. Collisions per other change merged since the change's base at p = 0.0075 (T1: 5 collided
# first passes, all rebase conflicts, over 658 merges of exposure); 0.5% background integration failures. lambda at
# N = 1 comes out at about 14-15 first submissions per slot-hour (T1 phase A 16, T0c 13.3; 12-slot phase 13.6).
T1_REVIEW_Q = (10.4, 13.09, 14.06, 14.88, 15.31, 16.19, 17.43, 18.2, 18.87, 19.56, 20.13, 20.7, 21.47, 22.43, 23.22,
               24.19, 25.66, 27.84, 29.34, 31.59, 38.92)   # fallback (same numbers); read from t1_params_public.json below
V6_TRUTH = dict(V5_TRUTH, v6=True, service_dist="empirical", service_quantiles=T1_REVIEW_Q, n_reviewers=1,
                rearm_s=20.1, rearm_cv=0.11, rework_msg_s=1.5, startup_median_s=85.0, startup_logsd=0.28,
                work_median_s=83.0, work_logsd=0.45, rework_median_s=70.0, rework_logsd=0.33,
                defect_p=0.20, catch0=0.915, false_reject=0.26, rework_defect_factor=1.0, review_error_p=0.015,
                ci_hidden_min=0.3 / 60, rebase_s=1.4, ci_post_min=4.1 / 60, p=0.0075, conflict_share=0.9,
                integration_bg=0.005)
V6_FAMILIES = {
    "linear": dict(family="linear", p=0.0),
    "mild": dict(family="amdahl", alpha=0.03, p=0.0),
    "amdahl": dict(family="amdahl", alpha=0.1, p=0.0),
    "usl": dict(family="usl", alpha=0.1, beta=0.01, p=0.0),
    "carnot": dict(family="carnot", alpha=0.1, beta=0.01, p=0.0075),
    "measured": dict(family="linear", p=0.0075),   # linear workers with T1's collision rate (not a SCALE null)
}


def _load_t1_quantiles():
    f = Path(__file__).resolve().parent / "design-search" / "t1_params_public.json"
    try:
        d = json.loads(f.read_text())
        q = d.get("review_pooled_quantiles_s") or d["review"]["quantiles_s"]
        return tuple(float(q[f"p{i}"]) for i in range(0, 101, 5))
    except Exception:
        return T1_REVIEW_Q


V6_TRUTH["service_quantiles"] = _load_t1_quantiles()


def make_truth_v6(name="carnot", **overrides) -> Truth:
    kw = dict(V6_TRUTH)
    kw.update(V6_FAMILIES[name])
    kw.update(overrides)
    return Truth(**kw)


# PLAN-v7 process (design-search/DESIGN-SEARCH-v7.md), calibrated on the command-launcher trials T0d and T0e
# (2026-09-29; design-search/t0de_params_public.json, from t0de_params.py, numbers only). One session per slot: the
# slot's session is launched with its first task (launch command 4.1 s, then 28.7 s to the first push: provisioning
# and clone), later tasks go to it as follow-up messages (1.6 s, then 15.0 s to the first push), and it retires after
# tasks_per_session = 4 tasks. Coding (pushed -> READY) lognormal median 82 s (log-sd 0.44; T0d + T0e, T1 83 s);
# rework (message -> READY) median 48 s (0.37); rework goes to the session that did the task. Review: the pooled
# T1 + T0c + T0d + T0e durations (168 reviews, median 20.5 s); 41% of reviews request changes (69 / 168: T1 0.39,
# T0d / T0e 0.44), escapes about 3% of approvals. Start-up log-sds: measured 0.02 (launches) and 0.04 (follow-ups) at
# one slot; 0.05 assumed (0.25 as a sensitivity; the measured figures are the 15-s watcher poll's quantisation, not the
# sessions' spread: PLAN-v7 rule 1). A session also retires after 6 messages of any kind (launch prompt, kind=task,
# rework; PLAN-v7 rule 6: T0e compacted after 8). Session timeout 10 min (PLAN-v7 rule 6; was 25). lambda at N = 1
# comes out at about 23 first submissions per slot-hour (T0d, every task a launch: 21.3; T0e before its stall: faster).
def _load_t0de():
    f = Path(__file__).resolve().parent / "design-search" / "t0de_params_public.json"
    try:
        d = json.loads(f.read_text())
        q = d["review_pooled_all_quantiles_s"]
        return tuple(float(q[f"p{i}"]) for i in range(0, 101, 5))
    except Exception:
        return V6_TRUTH["service_quantiles"]


V7_TRUTH = dict(V6_TRUTH, v7=True, service_quantiles=_load_t0de(), tasks_per_session=4, messages_per_session=6,
                launch_delay_s=4.1, followup_delay_s=1.6, rework_msg_s=1.6,
                startup_launch_median_s=28.7, startup_launch_logsd=0.05,
                startup_follow_median_s=15.0, startup_follow_logsd=0.05,
                work_median_s=82.0, work_logsd=0.44, rework_median_s=48.0, rework_logsd=0.37,
                false_reject=0.285, task_timeout_min=10.0)
V7_FAMILIES = V6_FAMILIES


def make_truth_v7(name="carnot", **overrides) -> Truth:
    kw = dict(V7_TRUTH)
    kw.update(V7_FAMILIES[name])
    kw.update(overrides)
    return Truth(**kw)


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
    conflicts: set = field(default_factory=set)   # census collision model only


def make_task_pool(truth: Truth, seed: int):
    rng = random.Random(seed)
    weights = [1 / (i + 1) ** truth.file_zipf for i in range(truth.n_files)]  # a few popular files
    files = [f"src/sandbox/mod{i:02d}.py" for i in range(truth.n_files)]
    base = math.log(truth.defect_p / (1 - truth.defect_p))
    pool = []
    fpt = list(truth.files_per_task)
    for i in range(truth.n_tasks):
        nf = rng.choice(fpt)
        fs = set()
        while len(fs) < nf:
            fs.add(rng.choices(files, weights)[0])
        pool.append(Task(f"{i + 1:03d}", sorted(fs), base + rng.gauss(0, truth.task_sd), rng.randint(20, 150)))
    if truth.collision_model == "census":
        _conflict_graph(truth, pool, seed)
    return pool


def _conflict_graph(truth: Truth, pool, seed):
    """Pair-conflict structure for collision_model = "census". With truth.pairs_json (a private census of the real
    task set: only its `files` and `textual_conflicts` keys are read, nothing is copied), the real graph is mapped
    onto the pool in id order; otherwise a synthetic graph: a pair sharing a file conflicts with probability
    census_given_share (the 220-task census: 1.2% of pairs conflict, all of them among the 15% that share a file)."""
    for t in pool:
        t.conflicts = set()
    if truth.pairs_json:
        d = json.loads(Path(truth.pairs_json).read_text())
        ids = sorted(d["files"])
        idx = {tid: i for i, tid in enumerate(ids)}
        fidx = {f: j for j, f in enumerate(sorted({f for v in d["files"].values() for f in v}))}
        for i, tid in enumerate(ids[:len(pool)]):
            pool[i].files = sorted(f"src/sandbox/file{fidx[f]:02d}.py" for f in d["files"][tid])  # opaque names
        for a, b in d["textual_conflicts"]:
            ia, ib = idx.get(a), idx.get(b)
            if ia is not None and ib is not None and ia < len(pool) and ib < len(pool):
                pool[ia].conflicts.add(pool[ib].id)
                pool[ib].conflicts.add(pool[ia].id)
        return
    rng = random.Random(seed * 7 + 3)
    for i, a in enumerate(pool):
        fa = set(a.files)
        for b in pool[i + 1:]:
            if fa & set(b.files) and rng.random() < truth.census_given_share:
                a.conflicts.add(b.id)
                b.conflicts.add(a.id)


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
    merged_log = []   # (t, task id) of every merge in this window (v5 collision models)
    ptr = [0]
    last_claimed = [None]
    reviewq = deque()
    K = max(1, int(truth.n_reviewers))
    tag_rev = truth.v6 or K > 1       # v6 / K > 1 logs name the reviewer on its events
    revs = [dict(id=f"r{i + 1}", busy=False) for i in range(K)]
    rev = revs[0]                     # K = 1: the single reviewer (v4 / v5 code path, unchanged)
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
        if ptr[0] == len(order):  # as the harness logs it: the last unclaimed task is gone
            emit(t, "note", text=f"tasks_exhausted n={len(order)}")
        changes[task.id] = dict(task=task, owner=w, attempt=0, head=None, inflight=False, defective=False,
                                collided=False, merged=False, depth=0, base=t)
        emit(t, "claim", worker=w["id"], task=task.id, branch=f"claude/task-{task.id}")
        rate = truth.lam1 * drag(t) * mult / 3600.0
        dur = gamma_time(1 / rate, truth.work_cv)
        if t + dur < w["end"]:
            at(t + dur, do_submit, w, task.id)

    def do_submit(t, w, task_id, token=None):
        if truth.per_task_sessions:
            if w.get("token") != token or t >= w["end"]:
                return  # timed out, or the window closed first
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
        if truth.collision_model == "inflight":
            p_none = (1 - truth.p) ** k * (1 - truth.p_m) ** m
            ch["collided"] = rng.random() > p_none
        else:
            ch["collided"] = False     # decided at rebase from the merges since the change's base (v5)
            ch["k_submit"] = k
        emit(t, "submit", worker=w["id"], task=task_id, branch=f"claude/task-{task_id}", head=ch["head"],
             attempt_no=ch["attempt"], lines_changed=ch["task"].lines if ch["attempt"] == 1 else rng.randint(5, 60),
             files=list(ch["task"].files), k=k, m=m)
        reviewq.append(task_id)
        if any(not r["busy"] for r in revs):
            start_review(t)
        if truth.per_task_sessions:
            slot_free(t, w)
            return
        if ch["attempt"] == 1 or truth.rework_uses_worker:
            worker_next(w, t)

    # ---------------------------------------------------------------- reviewer
    def rv_f(r):
        return dict(reviewer=r["id"]) if tag_rev else {}

    def empirical_s():
        q = truth.service_quantiles
        u = rng.random() * (len(q) - 1)
        i = min(int(u), len(q) - 2)
        return q[i] + (q[i + 1] - q[i]) * (u - i)

    def start_review(t, r=None):
        if r is None:
            r = next(x for x in revs if not x["busy"]) if K > 1 else rev
        task_id = reviewq.popleft()
        ch = changes[task_id]
        depth = len(reviewq)
        ch["depth"] = depth
        if not r["busy"]:
            emit(t, "reviewer_busy", **rv_f(r))
            r["busy"] = True
        emit(t, "review_start", task=task_id, head=ch["head"], queue_depth=depth, **rv_f(r))
        rate = truth.V0 * max(truth.reviewer_min_factor, 1 + truth.reviewer_load * depth) / 3600.0
        if truth.service_dist == "uniform":
            svc = lambda: rng.uniform(truth.service_lo_s, truth.service_hi_s)
        elif truth.service_dist == "empirical":
            svc = lambda: empirical_s() * (1 + truth.review_contention * (K - 1))
        else:
            svc = lambda: gamma_time(1 / rate, truth.service_cv)
        dur = svc()
        if rng.random() < truth.review_error_p:
            t_err = t + dur * rng.random()
            at(t_err, review_error, task_id, ch["head"], r)
            dur = (t_err - t) + svc()
        at(t + dur, review_end, task_id, ch["head"], t, r)

    def review_error(t, task_id, head, r=None):
        f = rv_f(r) if r is not None else {}
        emit(t, "review_error", task=task_id, head=head, error="synthetic: reviewer call failed, retried", **f)
        emit(t, "review_start", task=task_id, head=head, queue_depth=len(reviewq), **f)

    def review_end(t, task_id, head, t_start, r=None):
        r = r or rev
        ch = changes[task_id]
        depth = ch["depth"]
        if ch["defective"]:
            catch = truth.catch0 * math.exp(-truth.escape_depth * depth)
            if truth.escape_N or truth.escape_k:
                miss = (1 - catch) * (1 + truth.escape_N * (W - 1) / 11.0) * (1 + truth.escape_k * ch.get("k_submit", 0))
                catch = max(0.0, 1 - miss)
            reject = rng.random() < catch
        else:
            reject = rng.random() < truth.false_reject
        verdict = "request_changes" if reject else "approve"
        emit(t, "review_end", task=task_id, head=head, verdict=verdict,
             reason="acceptance criterion not met" if reject else "looks good",
             tokens_in=int(4000 + 30 * ch["task"].lines + rng.randint(0, 3000)),
             tokens_out=int(300 + rng.randint(0, 900)), duration_s=round(t - t_start, 3), **rv_f(r))
        if reject:
            bounce(t, task_id, "review")
        else:
            mq.append(task_id)
            if not mqs["busy"]:
                emit(t, "queue_busy")
                mqs["busy"] = True
                mq_start(t)
        if reviewq:
            start_review(t, r)   # the same reviewer takes the next change: it stays busy (no idle/busy pair)
        else:
            emit(t, "reviewer_idle", **rv_f(r))
            r["busy"] = False

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
        if truth.collision_model != "inflight":
            base = ch.get("base", 0.0)
            since = [tid for (tm, tid) in merged_log if tm > base and tid != task_id]
            if truth.collision_model == "lifetime":
                ch["collided"] = bool(since) and rng.random() > (1 - truth.p) ** len(since)
            else:
                nconf = sum(1 for tid in since if tid in ch["task"].conflicts)
                ch["collided"] = nconf > 0 and rng.random() > (1 - truth.census_manifest) ** nconf
            ch["bg_fail"] = truth.integration_bg > 0 and rng.random() < truth.integration_bg
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
        hid = not ch["collided"] and not ch.get("bg_fail", False)
        emit(t, "tests_post", task=task_id, head=ch["head"], visible_passed=vis, hidden_passed=hid)
        if not vis:
            bounce(t, task_id, "visible_fail")
        elif not hid:
            bounce(t, task_id, "integration_failure")
        else:
            emit(t, "merge", task=task_id, head=ch["head"], main_sha=_hex(rng, 40))
            ch["inflight"] = False
            ch["merged"] = True
            merged_log.append((t, task_id))
        mq_next(t)

    # ---------------------------------------------------------------- feedback
    def bounce(t, task_id, cause):
        ch = changes[task_id]
        emit(t, "bounce", task=task_id, head=ch["head"], cause=cause)
        if truth.per_task_sessions:
            if not truth.rework_returns or ch.get("abandoned"):
                ch["inflight"] = False
                return
            if task_id not in rework_q:
                rework_q.append(task_id)
            dispatch(t)
            return
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

    # ---------------------------------------------------------------- one session per task (slots)
    rework_q = deque()

    def slot_free(t, w):
        w["busy"], w["token"] = None, None
        if truth.v7 and w.get("busy_sess") is not None:
            w["busy_sess"]["busy"] = False
            w["busy_sess"] = None
        emit(t, "slot_idle", slot=w["id"])
        dispatch(t)

    def occupy(t, w, task_id):
        seq[0] += 1
        tok = seq[0]
        w["busy"], w["token"] = task_id, tok
        emit(t, "slot_busy", slot=w["id"])
        at(t + truth.task_timeout_min * 60, session_timeout, w, tok)
        return tok

    def session_timeout(t, w, token):
        if w.get("token") != token or t >= w["end"]:
            return
        task_id = w["busy"]
        ch = changes[task_id]
        emit(t, "session_timeout", slot=w["id"], task=task_id, session_id=ch["session"])
        ch["abandoned"] = True
        if ch["attempt"] == 0 or not any(x == task_id for x in reviewq):
            ch["inflight"] = False
        if truth.v7:   # the session is retired (still reachable for its earlier tasks' rework); the slot's next task
            s = sessions[ch["session"]]     # goes to a fresh session
            if w.get("sess") is s:
                v7_retire(t, w, "timeout")
        slot_free(t, w)

    # ---------------------------------------------------------------- v7: one session per slot
    sessions = {}

    def v7_retire(t, w, reason):
        s = w.get("sess")
        if s is None:
            return
        s["slot"] = None
        w["sess"] = None
        emit(t, "note", text=f"session_retired slot={w['id']} session={s['id']} tasks={s['n']} reason={reason} reachable=true")

    def v7_dispatch(t):
        for w in workers:
            if not (w["start"] <= t < w["end"]) or w.get("busy") or not w.get("open"):
                continue
            while rework_q and changes[rework_q[0]].get("abandoned"):
                rework_q.popleft()
            pick = None
            for i, tid in enumerate(rework_q):
                ch = changes[tid]
                if ch.get("abandoned"):
                    continue
                s = sessions[ch["session"]]
                if s["slot"] is w or (s["slot"] is None and not s["busy"]):
                    pick = i
                    break
            if pick is not None:
                tid = rework_q[pick]
                del rework_q[pick]
                s = sessions[changes[tid]["session"]]
                s["m"] = s.get("m", 0) + 1
                tok = occupy(t, w, tid)
                s["busy"] = True
                w["busy_sess"] = s
                at(t + truth.rework_msg_s, v6_rework, w, tid, tok)
                continue
            if ptr[0] >= len(order):
                continue
            task = order[ptr[0]]
            ptr[0] += 1
            s = w.get("sess")
            by_msgs = (s is not None and truth.messages_per_session > 0 and s.get("m", 0) >= truth.messages_per_session
                       and s["n"] < truth.tasks_per_session)
            launch = s is None or s["n"] >= truth.tasks_per_session or by_msgs
            if launch:
                if s is not None:
                    v7_retire(t, w, "messages_per_session" if by_msgs else "tasks_per_session")
                s = dict(id=f"session_{_hex(rng, 8)}", slot=w, n=0, m=0, busy=False)
                sessions[s["id"]] = s
                w["sess"] = s
            s["n"] += 1
            s["m"] = s.get("m", 0) + 1
            changes[task.id] = dict(task=task, owner=w, attempt=0, head=None, inflight=False, defective=False,
                                    collided=False, merged=False, depth=0, session=s["id"])
            tok = occupy(t, w, task.id)
            s["busy"] = True
            w["busy_sess"] = s
            if ptr[0] == len(order):
                emit(t, "note", text=f"tasks_exhausted n={len(order)}")
            at(t + (truth.launch_delay_s if launch else truth.followup_delay_s), v7_handout, w, task.id, tok, launch)

    def v7_handout(t, w, task_id, token, launch):
        if w.get("token") != token or t >= w["end"]:
            return
        ch = changes[task_id]
        if launch:
            emit(t, "session_launch", slot=w["id"], task=task_id, session_id=ch["session"], attempt_no=1)
            up = lognorm(truth.startup_launch_median_s, truth.startup_launch_logsd) * v6_slow(t, startup="launch")
        else:
            emit(t, "session_message", slot=w["id"], task=task_id, session_id=ch["session"], kind="task")
            up = lognorm(truth.startup_follow_median_s, truth.startup_follow_logsd) * v6_slow(t, startup="follow")
        ch["base"] = t
        if truth.stall_p > 0 and rng.random() < truth.stall_p:
            return     # the session never pushes or sends READY: the session timeout frees the slot
        t_up = t + up
        at(t_up, session_branch, w, task_id, token)
        cf = 1.0 if launch else truth.follow_code_factor
        at(t_up + lognorm(truth.work_median_s, truth.work_logsd) * v6_slow(t) * task_f(task_id) * cf, do_submit, w, task_id, token)

    def task_f(task_id):
        if truth.task_time_sd <= 0:
            return 1.0
        task = changes[task_id]["task"]
        return math.exp(truth.task_time_sd * random.Random(f"{task.id}|{task.logit:.12f}").gauss(0.0, 1.0))

    def dispatch(t):
        if truth.v7:
            return v7_dispatch(t)
        for w in workers:
            if not (w["start"] <= t < w["end"]) or w.get("busy") or not w.get("open"):
                continue
            while rework_q and changes[rework_q[0]].get("abandoned"):
                rework_q.popleft()
            if rework_q:
                task_id = rework_q.popleft()
                ch = changes[task_id]
                tok = occupy(t, w, task_id)
                if truth.v6:
                    at(t + truth.rework_msg_s, v6_rework, w, task_id, tok)
                    continue
                emit(t, "session_message", slot=w["id"], task=task_id, session_id=ch["session"], kind="rework")
                ch["base"] = t    # the rework prompt says to merge origin/main first
                mean = truth.rework_min * 60
                dur = mean if truth.rework_dist == "fixed" else gamma_time(mean, 1.0)
                dur /= (drag(t) * mult)
                at(t + dur, do_submit, w, task_id, tok)
                continue
            if ptr[0] >= len(order):
                return
            task = order[ptr[0]]
            ptr[0] += 1
            changes[task.id] = dict(task=task, owner=w, attempt=0, head=None, inflight=False, defective=False,
                                    collided=False, merged=False, depth=0, session=f"session_{_hex(rng, 8)}")
            tok = occupy(t, w, task.id)
            if truth.v6:
                if ptr[0] == len(order):
                    emit(t, "note", text=f"tasks_exhausted n={len(order)}")
                at(t + gamma_time(truth.rearm_s, truth.rearm_cv), v6_launch, w, task.id, tok)
                continue
            emit(t, "session_launch", slot=w["id"], task=task.id, session_id=changes[task.id]["session"], attempt_no=1)
            changes[task.id]["base"] = t
            if ptr[0] == len(order):
                emit(t, "note", text=f"tasks_exhausted n={len(order)}")
            t_up = t + rng.expovariate(1 / max(truth.session_startup_min * 60, 1e-9))
            at(t_up, session_branch, w, task.id, tok)
            rate = truth.lam1 * drag(t) * mult / 3600.0
            at(t_up + gamma_time(1 / rate, truth.work_cv), do_submit, w, task.id, tok)

    def lognorm(median, logsd):
        return median * math.exp(logsd * rng.gauss(0.0, 1.0))

    # v6 work stretch: with drag_startup = False, provisioning (re-arm, start-up) is not slowed by coordination, so the
    # work legs are stretched by s(g) chosen to make the whole per-task cycle scale as 1 / g, g = X(N) / N (the rival's
    # per-agent share): s = (C / g - (C - P)) / P, C the mean cycle per task at N = 1 and P its work part.
    b_rev = truth.defect_p * truth.catch0 + (1 - truth.defect_p) * truth.false_reject
    legs = b_rev / max(1 - b_rev, 1e-9)
    e_up = truth.startup_median_s * math.exp(truth.startup_logsd ** 2 / 2)
    P_work = truth.work_median_s * math.exp(truth.work_logsd ** 2 / 2) + legs * truth.rework_median_s * math.exp(truth.rework_logsd ** 2 / 2)
    C_cyc = truth.rearm_s + e_up + legs * truth.rework_msg_s + P_work
    if truth.v7:   # one session per slot: 1 launch in tasks_per_session hand-outs, the rest follow-ups
        fl = 1.0 / max(truth.tasks_per_session, 1)
        e_ho = (fl * (truth.launch_delay_s + truth.startup_launch_median_s * math.exp(truth.startup_launch_logsd ** 2 / 2))
                + (1 - fl) * (truth.followup_delay_s + truth.startup_follow_median_s * math.exp(truth.startup_follow_logsd ** 2 / 2)))
        C_cyc = e_ho + legs * truth.rework_msg_s + P_work

    def work_stretch(g):
        if truth.drag_startup or g >= 1.0:
            return 1.0 / g
        return (C_cyc / g - (C_cyc - P_work)) / P_work

    def v6_slow(t, startup=False):
        """Duration multiplier for worker-side legs: the window's speed multiplier, coordination drag (work legs only,
        unless drag_startup), and a service-side throttle when more than one slot is active."""
        f = 1.0 / mult
        g = drag(t)
        if startup:
            if truth.drag_startup:
                f /= g
        else:
            f *= work_stretch(g)
        if truth.throttle_factor != 1.0 and n_active(t) > 1 and (not startup or truth.throttle_startup):
            if not truth.throttle_launch_only or startup == "launch":
                f *= truth.throttle_factor
        return f

    def v6_launch(t, w, task_id, token):
        if w.get("token") != token or t >= w["end"]:
            return
        ch = changes[task_id]
        emit(t, "session_launch", slot=w["id"], task=task_id, session_id=ch["session"], attempt_no=1)
        ch["base"] = t
        t_up = t + lognorm(truth.startup_median_s, truth.startup_logsd) * v6_slow(t, startup=True)
        at(t_up, session_branch, w, task_id, token)
        at(t_up + lognorm(truth.work_median_s, truth.work_logsd) * v6_slow(t), do_submit, w, task_id, token)

    def v6_rework(t, w, task_id, token):
        if w.get("token") != token or t >= w["end"]:
            return
        ch = changes[task_id]
        emit(t, "session_message", slot=w["id"], task=task_id, session_id=ch["session"], kind="rework")
        ch["base"] = t
        at(t + lognorm(truth.rework_median_s, truth.rework_logsd) * v6_slow(t) * task_f(task_id), do_submit, w, task_id, token)

    def session_branch(t, w, task_id, token):
        if w.get("token") == token and t < w["end"]:
            emit(t, "claim", worker=w["id"], task=task_id, branch=f"claude/task-{task_id}")

    def open_slot(t, w):
        w["open"] = True
        emit(t, "worker_start", worker=w["id"], session_id=None)
        if truth.usage_every_min > 0:
            at(t + truth.usage_every_min * 60, usage, w)
        dispatch(t)

    def close_slot(t, w):
        if w.get("busy"):
            w["busy"], w["token"] = None, None
            emit(t, "slot_idle", slot=w["id"])

    emit(0.0, "note", text=f"task_supply n={len(order)}")
    if truth.per_task_sessions:
        for w in workers:
            w["id"] = "s" + w["id"][1:]
            at(w["start"], open_slot, w)
            at(w["end"], close_slot, w)
    else:
        for w in workers:
            at(w["start"] + rng.uniform(0, 5), start_worker, w)

    while heap:
        t, _, fn, args = heapq.heappop(heap)
        if t > end_all:
            break
        fn(t, *args)

    out.sort(key=lambda x: x[0])  # stable: same-time events keep emission order
    if truth.per_task_sessions:
        # the harness logs worker_start at the slot's opening, before anything happens on it
        out.sort(key=lambda x: (x[0], x[1]["type"] != "worker_start"))
    run = {"run_id": run_id, "kind": kind, "n_workers": n_workers, "window_start": iso(t0),
           "window_end": iso(t0 + end_s), "warmup_min": warmup_min, "grace_min": grace_min,
           "task_order_seed": int(order_seed), "sandbox_commit": "synthetic", "harness_commit": "synthetic",
           "worker_model": "synthetic", "reviewer_model": "synthetic",
           "notes": "synth.py truth: " + json.dumps(dataclasses.asdict(truth), sort_keys=True)}
    if tag_rev:
        run["n_reviewers"] = K
    if truth.v7:
        tps = [s["n"] for s in sessions.values()]
        run["notes"] = (f"session_per=slot sessions_launched={len(tps)} tasks_handed_out={sum(tps)} "
                        f"tasks_per_session={','.join(map(str, tps))}; " + run["notes"])
    return run, [e for _, e in out]


def simulate_study(truth: Truth, sizes, *, seed: int, reps=2, window_min=90.0, warmup_min=10.0,
                   grace_min=10.0, with_pilot=True, pilot_design="v3"):
    """T1 trial, T2 pilot, then sweep windows in ABBA order (low, high, high, low for reps=2; ABBAAB for
    reps=3). pilot_design="v4" is PLAN-v4's pilot: T1 = 1 worker for 30 min plus 11 more for the last
    15 min (12 at once), T2 = eight separate 60-min windows of one worker. pilot_design="v5" is PLAN-v5's: T1 only
    (1 slot for 30 min, then 12 for 30 min), and `reps` may be a dict {size: windows} (order: v5_order).
    Returns a list of (run_json, events)."""
    pool = make_task_pool(truth, seed * 31 + 5)
    runs = []
    t0 = T0 + (seed % 100000) * 86400.0
    if with_pilot and pilot_design == "v4":
        sched = [(0.0, 30.0)] + [(15.0, 30.0)] * 11
        runs.append(simulate(truth, 12, seed=seed * 1000 + 1, window_min=30, warmup_min=0, grace_min=60,
                             schedule=sched, task_pool=pool, run_id=f"synth-{seed}-T1", kind="trial", t0=t0))
        for j in range(8):
            runs.append(simulate(truth, 1, seed=seed * 1000 + 2 + j * 100, window_min=60, warmup_min=warmup_min,
                                 grace_min=grace_min, task_pool=pool, run_id=f"synth-{seed}-T2-{j + 1}", kind="pilot",
                                 t0=t0 + (3 + 1.5 * j) * 3600))
        t0 += 12 * 3600
    elif with_pilot and pilot_design == "v3":
        # T1: 1 worker alone 15 min, then 9 at once 15 min; the reviewer keeps going (long grace) so
        # every T1 PR is reviewed (PLAN-v3 section 3).
        sched = [(0.0, 30.0)] + [(15.0, 30.0)] * 8
        runs.append(simulate(truth, 9, seed=seed * 1000 + 1, window_min=30, warmup_min=0, grace_min=60,
                             schedule=sched, task_pool=pool, run_id=f"synth-{seed}-T1", kind="trial", t0=t0))
        runs.append(simulate(truth, 2, seed=seed * 1000 + 2, window_min=60, warmup_min=warmup_min,
                             grace_min=grace_min, task_pool=pool, run_id=f"synth-{seed}-T2", kind="pilot",
                             t0=t0 + 3 * 3600))
    if with_pilot and pilot_design == "v5":
        # PLAN-v5 T1: one slot for 30 min, then twelve for 30 min (no separate T2 pilot)
        sched = [(0.0, 60.0)] + [(30.0, 60.0)] * 11
        runs.append(simulate(truth, 12, seed=seed * 1000 + 1, window_min=60, warmup_min=0, grace_min=10,
                             schedule=sched, task_pool=pool, run_id=f"synth-{seed}-T1", kind="trial", t0=t0))
        t0 += 3 * 3600
    if isinstance(reps, dict) or pilot_design == "v5":
        order = v5_order(sizes, reps)
    else:
        lo, hi = sizes
        order = []
        for r in range(reps):
            order += [lo, hi] if r % 2 == 0 else [hi, lo]
    for i, n in enumerate(order):
        runs.append(simulate(truth, n, seed=seed * 1000 + 10 + i, window_min=window_min, warmup_min=warmup_min,
                             grace_min=grace_min, task_pool=pool, run_id=f"synth-{seed}-N{n}-w{i + 1}",
                             kind="sweep", t0=t0 + (6 + 2.5 * i) * 3600))
    return runs


def simulate_study_v6(family="measured", *, seed: int, cells=None, window_min=None, warmup_min=None, with_t1b=True,
                      **overrides):
    """PLAN-v6: T1b (one slot for 90 min, then twelve for 30 min, K = 3) and the sweep's (N, K) cells in v6_order.
    Returns a list of (run_json, events)."""
    from v6 import DESIGN_V6, v6_order, cells_of
    cells = cells_of(cells or DESIGN_V6["cells"])
    L = window_min or DESIGN_V6["window_min"]
    W = DESIGN_V6["warmup_min"] if warmup_min is None else warmup_min
    t0 = T0 + (seed % 100000) * 86400.0
    runs = []
    base = make_truth_v6(family, **overrides)
    pool = make_task_pool(base, seed * 31 + 5)
    if with_t1b:
        tb = DESIGN_V6["t1b"]
        L1, L2 = tb["one_slot_min"], tb["twelve_slot_min"]
        sched = [(0.0, L1 + L2)] + [(L1, L1 + L2)] * 11
        truth = make_truth_v6(family, n_reviewers=tb["K"], **overrides)
        runs.append(simulate(truth, 12, seed=seed * 1000 + 1, window_min=L1 + L2, warmup_min=0, grace_min=10,
                             schedule=sched, task_pool=pool, run_id=f"synth-{seed}-T1b", kind="trial", t0=t0))
    for i, (n, k) in enumerate(v6_order(cells)):
        truth = make_truth_v6(family, n_reviewers=k, **overrides)
        runs.append(simulate(truth, n, seed=seed * 1000 + 10 + i, window_min=L, warmup_min=W, grace_min=10,
                             task_pool=pool, run_id=f"synth-{seed}-N{n}K{k}-w{i + 1}", kind="sweep",
                             t0=t0 + (6 + 1.5 * i) * 3600))
    return runs


def simulate_study_v7(family="measured", *, seed: int, budget="400", cells=None, window_min=None, warmup_min=None,
                      with_t1b=True, **overrides):
    """PLAN-v7: T1b (one slot for 30 min, then twelve for 15 min, K = K_hi, one session per slot) and the chosen design's
    (N, K) cells in v6_order, 15-min windows. One task pool reused by every window, each with its own order (the
    real sweep's reset). Returns a list of (run_json, events)."""
    from v7 import DESIGNS_V7, T1B_V7, v6_order, cells_of
    D = DESIGNS_V7[budget]
    cells = cells_of(cells or D["cells"])
    L = window_min or D["window_min"]
    W = D["warmup_min"] if warmup_min is None else warmup_min
    t0 = T0 + (seed % 100000) * 86400.0
    runs = []
    base = make_truth_v7(family, **overrides)
    pool = make_task_pool(base, seed * 31 + 5)
    if with_t1b:
        L1, L2 = T1B_V7["one_slot_min"], T1B_V7["twelve_slot_min"]
        sched = [(0.0, L1 + L2)] + [(L1, L1 + L2)] * 11
        truth = make_truth_v7(family, n_reviewers=T1B_V7["K"], **overrides)
        runs.append(simulate(truth, 12, seed=seed * 1000 + 1, window_min=L1 + L2, warmup_min=0, grace_min=10,
                             schedule=sched, task_pool=pool, run_id=f"synth-{seed}-T1b", kind="trial", t0=t0))
    for i, (n, k) in enumerate(v6_order(cells)):
        truth = make_truth_v7(family, n_reviewers=k, **overrides)
        runs.append(simulate(truth, n, seed=seed * 1000 + 10 + i, window_min=L, warmup_min=W, grace_min=10,
                             task_pool=pool, run_id=f"synth-{seed}-N{n}K{k}-w{i + 1}", kind="sweep",
                             t0=t0 + (6 + 1.0 * i) * 3600))
    return runs


def v5_order(sizes, reps):
    """Sweep order for unequal replicates (PLAN-v5 section 5): windows of every size spread evenly through the
    sequence, the largest size never first or last, starting and ending with the smallest. reps: dict size -> windows,
    or an int (same count per size)."""
    sizes = sorted(sizes)
    if not isinstance(reps, dict):
        reps = {n: int(reps) for n in sizes}
    slots = []
    for n in sizes:
        r = reps.get(n, 0)
        for i in range(r):
            slots.append(((i + 0.5) / r, -n if n == sizes[0] else n, n))
    slots.sort()
    order = [n for _, _, n in slots]
    lo = sizes[0]
    if order and order[0] != lo and lo in order:
        order.remove(lo)
        order.insert(0, lo)
    if order and order[-1] != lo and order.count(lo) > 1:
        idx = max(i for i, n in enumerate(order[:-1]) if n == lo)
        order.append(order.pop(idx))
    return order


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
    ap.add_argument("--v4", action="store_true",
                    help="PLAN-v4 design: sizes 1 12, 3 windows each (ABBAAB) of 120 min, v4 pilot (T1 1->12, 8 x 60-min T2)")
    ap.add_argument("--v5", action="store_true",
                    help="PLAN-v5 process and design: V5_TRUTH (sessions, fast reviewer, lifetime collisions), T1 1 -> 12, "
                         "sweep sizes / reps / window from --sizes --reps-v5 --window-min (defaults: the recommended design)")
    ap.add_argument("--v6", action="store_true",
                    help="PLAN-v6 process (T1-calibrated, K reviewers) and design: T1b + the (N, K) cells of v6.DESIGN_V6")
    ap.add_argument("--v7", action="store_true",
                    help="PLAN-v7 process (T0d / T0e-calibrated, one session per slot, K reviewers) and design: T1b + the "
                         "cells of v7.DESIGNS_V7[--budget]")
    ap.add_argument("--budget", default="400", choices=["300", "400", "500"], help="--v7: which design (default 400)")
    ap.add_argument("--family", default=None, choices=["linear", "mild", "amdahl", "usl", "carnot", "measured"],
                    help="--v5 / --v6: worker truth (default carnot for v5, measured for v6)")
    ap.add_argument("--reps-v5", nargs="*", default=None, metavar="N=R", help="--v5 only: windows per size, e.g. 1=8 12=3")
    ap.add_argument("--sessions", action="store_true",
                    help="one cloud session per task in N slots (the harness's worker model; sets per_task_sessions)")
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    if a.v4:
        a.sizes, a.reps, a.window_min = [1, 12], 3, 120.0
    ov = {}
    for kv in a.set:
        k, v = kv.split("=", 1)
        ov[k] = _coerce(v)
    if a.sessions:
        ov["per_task_sessions"] = True
    if a.v7:
        runs = simulate_study_v7(a.family or "measured", seed=a.seed, budget=a.budget, **ov)
    elif a.v6:
        runs = simulate_study_v6(a.family or "measured", seed=a.seed, **ov)
    elif a.v5:
        from v5 import DESIGN_V5
        truth = make_truth_v5(a.family or "carnot", **ov)
        sizes = sorted(set(a.sizes)) if a.sizes != [1, 5] else list(DESIGN_V5["sizes"])
        reps = ({int(k): int(v) for k, v in (x.split("=") for x in a.reps_v5)} if a.reps_v5
                else dict(DESIGN_V5["reps"]))
        wmin = a.window_min if a.window_min != 90 else DESIGN_V5["window_min"]
        runs = simulate_study(truth, sizes, seed=a.seed, reps=reps, window_min=wmin,
                              with_pilot=not a.no_pilot, pilot_design="v5")
    else:
        truth = make_truth(a.truth, **ov)
        runs = simulate_study(truth, a.sizes, seed=a.seed, reps=a.reps, window_min=a.window_min,
                              with_pilot=not a.no_pilot, pilot_design="v4" if a.v4 else "v3")
    for run, ev in runs:
        d = write_run(Path(a.out), run, ev)
        print(f"{d}  kind={run['kind']} N={run['n_workers']} events={len(ev)}")


if __name__ == "__main__":
    main()
