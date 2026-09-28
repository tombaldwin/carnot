#!/usr/bin/env python3
"""PLAN-v6 design-search simulation core (synth V6 process -> derive -> v6 / v5 codings; no network, API or harness).

Windows are independent given the truth, so the search simulates a *bank* of windows per cell (truth, N, K, window
length L) once and builds each simulated study by drawing its windows from the banks (without replacement within a
study). The final operating characteristics (oc_v6.py) use fresh simulations for the recommended design.

Truth names: "<family>[/<mod>...]" with family in synth.V6_FAMILIES (linear, mild, amdahl, usl, carnot, measured)
and modifiers: cv=<x> (window CV), p=<x> (collisions per merge since base), fastrev (reviews 3x faster: review
never binds, the CAP null), slowrev (reviews 1.5x longer), cont=<x> (review contention: x (1 + x (K - 1))),
lam70 (worker legs 1/0.7 longer: lambda -30%), thr=<x> (service-side throttle at N > 1 on start-up, coding and
rework), thrcode=<x> (throttle on coding / rework only), esc2x (reviewer misses rise with N: escapes about double at
N = 12), nobg (no background integration failures).

Costs (budget (a), PLAN-v6 section 5.2): burn x (T1b + sweep) session-hours against the $249 balance less the $50
floor; T1b = 90 min at one slot + 30 min at twelve = 7.5 session-hours. A window at N slots of L minutes is N L / 60.
"""
from __future__ import annotations

import dataclasses
import math
import os
import random
import sys
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from derive import derive_window  # noqa: E402
from synth import T0, make_task_pool, make_truth_v6, simulate  # noqa: E402
import v5  # noqa: E402
import v6  # noqa: E402

ROOM = 249.0 - 50.0       # $ spendable under budget (a)
BURN = 2.10
T1B_H = 7.5


@dataclass(frozen=True)
class D6:
    cells: tuple            # ((N, K, windows), ...)
    L: int = 60
    W: int = 5              # warm-up minutes (grace is always 10)

    def label(self):
        return " + ".join(f"{n}K{k}x{w}" for n, k, w in self.cells if w) + f" @{self.L}m" + ("" if self.W == 5 else f" w{self.W}")

    def cdict(self):
        return {(n, k): w for n, k, w in self.cells if w}

    def sweep_h(self):
        return sum(n * w for n, k, w in self.cells) * self.L / 60.0

    def total_h(self, t1b=T1B_H):
        return self.sweep_h() + t1b

    def cost(self, burn=BURN):
        return burn * self.total_h()

    def fits_up_to(self):
        return ROOM / self.total_h()

    def windows(self):
        return sum(w for _, _, w in self.cells)

    def wall_h(self):
        return self.windows() * (self.L + 10 + 15) / 60.0


# ------------------------------------------------------------------------------------------------ truths
def build_truth6(tname, K=1, over=None):
    fam = tname.split("/")[0]
    kw = dict(n_reviewers=K)
    from synth import V6_TRUTH
    for part in tname.split("/")[1:]:
        if part.startswith("cv="):
            kw["cv_window"] = float(part[3:])
        elif part.startswith("p="):
            kw["p"] = float(part[2:])
        elif part == "fastrev":
            kw["service_quantiles"] = tuple(x / 3 for x in V6_TRUTH["service_quantiles"])
        elif part == "slowrev":
            kw["service_quantiles"] = tuple(x * 1.5 for x in V6_TRUTH["service_quantiles"])
        elif part.startswith("cont="):
            kw["review_contention"] = float(part[5:])
        elif part == "lam70":
            kw.update(startup_median_s=V6_TRUTH["startup_median_s"] / 0.7, work_median_s=V6_TRUTH["work_median_s"] / 0.7,
                      rework_median_s=V6_TRUTH["rework_median_s"] / 0.7)
        elif part.startswith("thr="):
            kw["throttle_factor"] = float(part[4:])
        elif part.startswith("thrcode="):
            kw.update(throttle_factor=float(part[8:]), throttle_startup=False)
        elif part == "esc2x":
            kw["escape_N"] = 1.1
        elif part == "nobg":
            kw["integration_bg"] = 0.0
        else:
            raise ValueError(part)
    kw.update(over or {})
    return make_truth_v6(fam, **kw)


# ------------------------------------------------------------------------------------------------ one window
def _compact(d, K):
    s = d["summary"]
    keep = ("run_id", "n_workers", "finished", "hours", "worker_hours_full", "worker_hours", "attempts", "attempts_full",
            "lam", "reviews", "busy_hours", "reviewer_util", "queue_nonempty_share", "mean_waiting_depth", "mq_util",
            "supply_flagged", "t_exhausted_min", "censored", "rework_open", "session_timeouts", "approvals_resolved",
            "startup_min_mean", "review_s_mean", "j_mean", "escaped", "collisions_first", "bounces", "ci_time_min",
            "rework_wait_min_mean", "timeouts_before_submit", "b_review")
    S = {k: s.get(k) for k in keep}
    S["n_reviewers"] = K
    pr = np.array([(float(p["collided_first"]), p["j"], p["jm"], p.get("k") or 0, p.get("m") or 0)
                   for p in d["prs"] if p.get("collided_first") is not None and p.get("j") is not None], float).reshape(-1, 5)
    ap = np.array([(float(a["escaped"]), int(a["task"])) for a in d["approvals"] if a.get("escaped") is not None], float).reshape(-1, 2)
    return dict(S=S, pr=pr, ap=ap)


LITE = ("n_workers", "n_reviewers", "finished", "hours", "worker_hours_full", "reviewer_util", "queue_nonempty_share",
        "supply_flagged", "t_exhausted_min", "lam", "reviews", "busy_hours", "attempts", "censored")


def sim_window(args):
    """args: (tname, N, K, L, seed[, W[, lite]]). lite: keep only the summary fields SCALE / CAP / UTIL need."""
    tname, N, K, L, seed = args[:5]
    W = args[5] if len(args) > 5 else 5
    lite = args[6] if len(args) > 6 else False
    truth = build_truth6(tname, K)
    pool = make_task_pool(truth, seed * 31 + 5)
    run, ev = simulate(truth, N, seed=seed, window_min=L, warmup_min=W, grace_min=10, task_pool=pool,
                       run_id=f"b{seed}", kind="sweep", t0=T0 + (seed % 10000) * 3600.0)
    c = _compact(derive_window(run, ev), K)
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
    return (sum(ord(c) * (i + 7) for i, c in enumerate(f"{tname}|{N}|{K}|{L}")) % 99991) * 10_000


def make_banks(cells, sizes, W=5, lite=False):
    """cells: iterable of (tname, N, K, L); sizes: dict N -> bank size. Returns {(tname, N, K, L): [window, ...]}."""
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
def _S_of(w, i):
    s = dict(w["S"])
    s["run_id"] = f"w{i}"
    s["worker_hours_full"] = s.get("worker_hours_full")
    return s


def score_study(windows, parts=("scale", "cap", "coll", "esc", "family", "util"), n_hi=None, k_hi=None, ci=False):
    S = [_S_of(w, i) for i, w in enumerate(windows)]
    n_hi = n_hi or max(s["n_workers"] for s in S)
    ks = sorted({v6.K_of(s) for s in S if s["n_workers"] == n_hi})
    k_hi = k_hi or max(ks)
    k_lo = min(ks)
    out = {}
    SW = v6.scale_windows(S, n_hi, k_hi)
    if "scale" in parts:
        b = v5.bend_test(SW, method="lr_fixed")
        out["bend_p"], out["pa_ratio"] = b["p"], b["per_agent_ratio"]
        if "scale_sens" in parts:
            out["bend_p_estcv"] = v5.bend_test(SW, method="lr_estcv")["p"]
            out["bend_p_welch"] = v5.bend_test(SW, method="welch")["p"]
        out["flag_rev"] = any(s["reviewer_util"] >= 0.8 for s in SW if s["n_workers"] == n_hi)
        out["flag_supply"] = any(s.get("supply_flagged") for s in S)
    if "cap" in parts and k_lo != k_hi:
        c = v6.k_test(S, n_hi, k_lo, k_hi, ci=ci)
        out["cap_p"], out["cap_ratio"] = c["p"], c["ratio"]
        los = sorted({v6.K_of(s) for s in S if s["n_workers"] == 1})
        if len(los) > 1:
            k1 = v6.k_test(S, 1, min(los), max(los), ci=False)
            out["k1_p2"], out["k1_ratio"] = k1.get("p_two_sided"), k1["ratio"]
    if "family" in parts:
        f = v5.family_fit(SW, p=v6.P_V6)
        out["best"], out["best3"], out["binary"] = f["best"], f["best3"], f["binary"]
    if "coll" in parts:
        prs = [dict(collided_first=bool(r[0]), j=r[1], jm=r[2], k=r[3], m=r[4], n_workers=w["S"]["n_workers"])
               for w in windows for r in w["pr"]]
        c = v5.collision_v5(prs, model_form="p_hat" in parts)
        out["coll_p"], out["coll_events"], out["coll_n"] = c["p_j"], c["events"], c["n"]
        if "p_hat" in parts and c.get("p_ci"):
            out["p_hat"], out["p_ci"] = c.get("p_hat"), c.get("p_ci")
    if "esc" in parts:
        apprs = [dict(escaped=bool(r[0]), task=f"{int(r[1]):03d}", n_workers=w["S"]["n_workers"]) for w in windows for r in w["ap"]]
        e = v5.escape_v5(apprs, n_max=n_hi)
        out["esc_p"], out["esc_rate"], out["esc_n"], out["esc_events"], out["esc_hw"] = (e["p_trend"], e["rate"], e["n"],
                                                                                         e["events"], e["halfwidth"])
    if "util" in parts:
        for (n, k) in sorted({(s["n_workers"], v6.K_of(s)) for s in S}):
            SS = [s for s in S if s["n_workers"] == n and v6.K_of(s) == k]
            out[f"util_{n}x{k}"] = float(np.mean([s["reviewer_util"] for s in SS]))
            out[f"utilmax_{n}x{k}"] = float(np.max([s["reviewer_util"] for s in SS]))
            out[f"qne_{n}x{k}"] = float(np.mean([s["queue_nonempty_share"] for s in SS]))
            out[f"fin_{n}x{k}"] = float(np.mean([s["finished"] for s in SS]))
            out[f"lam_{n}x{k}"] = float(np.mean([s["lam"] for s in SS]))
            out[f"rev_{n}x{k}"] = float(np.mean([s["reviews"] for s in SS]))
            out[f"busyh_{n}x{k}"] = float(np.mean([s["busy_hours"] for s in SS]))
            out[f"exh_{n}x{k}"] = float(np.mean([1.0 if s.get("t_exhausted_min") is not None else 0.0 for s in SS]))
            out[f"supflag_{n}x{k}"] = float(np.mean([1.0 if s.get("supply_flagged") else 0.0 for s in SS]))
    return out


def study_from_bank(args):
    design, tname, banks_for, rep, parts = args
    rng = random.Random(hash((design.label(), tname, rep)) & 0xFFFFFFFF)
    wins = []
    for n, k, w in design.cells:
        if not w:
            continue
        b = banks_for[(n, k)]
        idx = rng.sample(range(len(b)), w)
        wins += [b[i] for i in idx]
    r = score_study(wins, parts)
    r["truth"] = tname
    return r


def study_fresh(cfg):
    """cfg: design (D6), truth, seed, parts. Every window freshly simulated (the final OCs)."""
    d, tname, seed = cfg["design"], cfg["truth"], cfg["seed"]
    parts = cfg.get("parts", ("scale", "cap", "coll", "esc", "family", "util"))
    wins = []
    order = v6.v6_order(d.cdict())
    for i, (n, k) in enumerate(order):
        wins.append(sim_window((tname, n, k, d.L, seed * 1000 + i, d.W)))
    r = score_study(wins, parts)
    r["truth"] = tname
    r["seed"] = seed
    return r
