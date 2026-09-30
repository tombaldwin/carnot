#!/usr/bin/env python3
"""PLAN-v7 design-search simulation core (synth V7 process -> derive -> the v6 / v7 codings; no network, API or harness).

The v7 process (synth.V7_TRUTH): `claude --cloud` command launcher, one session per slot (4 tasks per session), legs
calibrated on T0d / T0e. The budget is counted in **tasks**: hand-outs (session_launch + session_message kind=task),
each a first attempt with its rework, T1b included; the credit meter charged about $0.45 per task (T0d / T0e).

Windows are independent given the truth, so the search simulates a *bank* of windows per cell (truth, N, K, window
length L) once and builds each simulated study by drawing its windows from the banks. The final operating
characteristics (oc_v7.py) simulate every window afresh, with one task pool per study reused by every window (as the
real sweep reuses its 220 tasks through `reset`).

Truth names: "<family>[/<mod>...]" with family in synth.V7_FAMILIES and modifiers as dsim6 (cv=, p=, fastrev,
slowrev, cont=, thr=, thrcode=, esc2x, nobg) plus: lam20 (every worker leg x 1.17: lambda about 20 at N = 1, the
round figure of T0d), lam28 (legs / 1.2: lambda about 28), upsd=<x> (start-up log-sd x for launches and follow-ups;
measured 0.02-0.04 at one slot, 0.05 assumed), thrlaunch=<x> (a service throttle on launches only: provisioning under
concurrency), stall=<x> (share of hand-outs that never reach READY: the session timeout frees the slot), tasksd=<x> (per-task log-sd of
coding / rework time, the same in every window that uses the task), tps=<n> (tasks per session), mps=<n> (messages per
session before retirement; 0 = off; the v7 process retires after 6), posfx=<x> (coding of a
follow-up task, i.e. positions 2.. in its session, x this factor: shared context).
"""
from __future__ import annotations

import os
import random
import sys
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))
from derive import derive_window  # noqa: E402
from synth import T0, V7_TRUTH, make_task_pool, make_truth_v7, simulate  # noqa: E402
import dsim6  # noqa: E402
import v6  # noqa: E402

USD_PER_TASK = 0.45          # T0d $0.50, T0e $0.43 (whole-dollar meter): $0.30-0.60
SPENDABLE = 236.0 - 50.0     # credits left after T0e less the $50 floor


@dataclass(frozen=True)
class D7:
    cells: tuple            # ((N, K, windows), ...)
    L: int = 20
    W: int = 3              # warm-up minutes (grace is always 10)

    def label(self):
        return " + ".join(f"{n}K{k}x{w}" for n, k, w in self.cells if w) + f" @{self.L}m" + ("" if self.W == 3 else f" w{self.W}")

    def cdict(self):
        return {(n, k): w for n, k, w in self.cells if w}

    def windows(self):
        return sum(w for _, _, w in self.cells)

    def slot_hours(self):
        return sum(n * w for n, k, w in self.cells) * self.L / 60.0


# ------------------------------------------------------------------------------------------------ truths
def build_truth7(tname, K=1, over=None):
    fam = tname.split("/")[0]
    kw = dict(n_reviewers=K)
    legs = ("launch_delay_s", "followup_delay_s", "startup_launch_median_s", "startup_follow_median_s", "work_median_s",
            "rework_median_s")
    for part in tname.split("/")[1:]:
        if part.startswith("cv="):
            kw["cv_window"] = float(part[3:])
        elif part.startswith("p="):
            kw["p"] = float(part[2:])
        elif part == "fastrev":
            kw["service_quantiles"] = tuple(x / 3 for x in V7_TRUTH["service_quantiles"])
        elif part == "slowrev":
            kw["service_quantiles"] = tuple(x * 1.5 for x in V7_TRUTH["service_quantiles"])
        elif part.startswith("cont="):
            kw["review_contention"] = float(part[5:])
        elif part in ("lam20", "lam28"):
            f = 1.17 if part == "lam20" else 1 / 1.2
            kw.update({k: V7_TRUTH[k] * f for k in legs})
        elif part.startswith("upsd="):
            kw.update(startup_launch_logsd=float(part[5:]), startup_follow_logsd=float(part[5:]))
        elif part.startswith("thrlaunch="):
            kw.update(throttle_factor=float(part[10:]), throttle_launch_only=True)
        elif part.startswith("thrcode="):
            kw.update(throttle_factor=float(part[8:]), throttle_startup=False)
        elif part.startswith("thr="):
            kw["throttle_factor"] = float(part[4:])
        elif part.startswith("stall="):
            kw["stall_p"] = float(part[6:])
        elif part.startswith("tasksd="):
            kw["task_time_sd"] = float(part[7:])
        elif part.startswith("tps="):
            kw["tasks_per_session"] = int(part[4:])
        elif part.startswith("mps="):
            kw["messages_per_session"] = int(part[4:])
        elif part.startswith("posfx="):
            kw["follow_code_factor"] = float(part[6:])
        elif part == "esc2x":
            kw["escape_N"] = 1.1
        elif part == "nobg":
            kw["integration_bg"] = 0.0
        else:
            raise ValueError(part)
    kw.update(over or {})
    return make_truth_v7(fam, **kw)


# ------------------------------------------------------------------------------------------------ one window
def handouts(ev):
    return sum(1 for e in ev if e["type"] == "session_launch" or (e["type"] == "session_message" and e.get("kind") == "task"))


def _compact(run, ev, K):
    c = dsim6._compact(derive_window(run, ev), K)
    c["S"]["tasks"] = handouts(ev)
    c["S"]["sessions"] = sum(1 for e in ev if e["type"] == "session_launch")
    return c


LITE = dsim6.LITE + ("tasks", "sessions")


def sim_window(args):
    """args: (tname, N, K, L, seed[, W[, lite[, pool_seed]]]). pool_seed: the task pool (one per study when given)."""
    tname, N, K, L, seed = args[:5]
    W = args[5] if len(args) > 5 else 3
    lite = args[6] if len(args) > 6 else False
    pool_seed = args[7] if len(args) > 7 and args[7] is not None else seed * 31 + 5
    order_seed = args[8] if len(args) > 8 else None
    truth = build_truth7(tname, K)
    pool = make_task_pool(truth, pool_seed)
    run, ev = simulate(truth, N, seed=seed, window_min=L, warmup_min=W, grace_min=10, task_pool=pool,
                       run_id=f"b{seed}", kind="sweep", t0=T0 + (seed % 10000) * 3600.0, task_order_seed=order_seed)
    c = _compact(run, ev, K)
    if lite:
        return dict(S={k: c["S"].get(k) for k in LITE}, pr=np.zeros((0, 5)), ap=np.zeros((0, 2)))
    return c


_POOL = None


def pool():
    global _POOL
    if _POOL is None:
        _POOL = ProcessPoolExecutor(max_workers=max(1, (os.cpu_count() or 2) - 1))
    return _POOL


def cell_seed(tname, N, K, L):
    return (sum(ord(c) * (i + 7) for i, c in enumerate(f"v7|{tname}|{N}|{K}|{L}")) % 99991) * 10_000


def make_banks(cells, sizes, W=3, lite=False):
    jobs = []
    for c in cells:
        base = cell_seed(*c)
        for i in range(sizes.get(c[1], sizes.get("default", 500))):
            jobs.append((c, (c[0], c[1], c[2], c[3], base + i, W, lite)))
    res = list(pool().map(sim_window, [j[1] for j in jobs], chunksize=16))
    banks = {}
    for (c, _), r in zip(jobs, res):
        banks.setdefault(c, []).append(r)
    return banks


# ------------------------------------------------------------------------------------------------ one study
def score_study(windows, parts=("scale", "cap", "coll", "esc", "family", "util")):
    out = dsim6.score_study(windows, parts)
    out["tasks"] = int(sum(w["S"].get("tasks") or 0 for w in windows))
    out["reviews_total"] = int(sum(w["S"].get("reviews") or 0 for w in windows))
    out["sessions"] = int(sum(w["S"].get("sessions") or 0 for w in windows))
    return out


# ------------------------------------------------------------------------------------------------ T1b
def t1b_sim(tname, L1, L2, K, seed, pool_seed=None):
    """T1b in slot mode: one slot for L1 min, then twelve for L2 min (s1 keeps its session), grace 10."""
    truth = build_truth7(tname, K)
    sched = [(0.0, L1 + L2)] + [(float(L1), L1 + L2)] * 11
    run, ev = simulate(truth, 12, seed=seed, window_min=L1 + L2, warmup_min=0, grace_min=10, schedule=sched,
                       task_pool=make_task_pool(truth, pool_seed if pool_seed is not None else seed * 31 + 5),
                       run_id=f"t1b{seed}", kind="trial", t0=T0)
    return run, ev


def study_fresh(cfg):
    """cfg: design (D7), truth, seed, parts[, shared_pool=True]. Every window freshly simulated in v6_order; with
    shared_pool (default) one task pool per study, reused by every window with its own order (the real reset)."""
    d, tname, seed = cfg["design"], cfg["truth"], cfg["seed"]
    parts = cfg.get("parts", ("scale", "cap", "coll", "esc", "family", "util"))
    ps = seed * 31 + 5 if cfg.get("shared_pool", True) else None
    os_ = seed * 7 + 3 if cfg.get("fixed_order") else None     # the same task order in every window (one reset seed)
    wins = []
    for i, (n, k) in enumerate(v6.v6_order(d.cdict())):
        wins.append(sim_window((tname, n, k, d.L, seed * 1000 + i, d.W, False, ps, os_)))
    r = score_study(wins, parts)
    r["truth"] = tname
    r["seed"] = seed
    return r
