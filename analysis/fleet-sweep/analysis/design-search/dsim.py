#!/usr/bin/env python3
"""Design-search simulation core for study 2 (imports synth / derive / predict / score unchanged).

One call of `study(cfg)` simulates one complete study under a chosen truth:

    T1 (1 worker 15 min, then N_max at once 15 min; its PRs reviewed or not)
    T2 pilot (n_p workers, L_p minutes, pw separate windows)
    [optional free reviewer calibration: K extra reviews at no load, local, no credits]
    -> pilot_params -> Params(rival_rework="completion") -> [gate, if the design uses one]
    -> sweep windows (sizes x replicates, window length L) -> derive -> score pieces

and returns the family picked, the V-constancy codings (S4n, O1n and two variants), S1r/S2r,
the escaped-defect and collision tests, and the design's credit cost.

Analysis decisions adopted (README "Decisions"): rival rework reading `completion`, S1r/S2r, S4n/O1n,
escape model `logit_cluster_task`, NB likelihood with fixed CV 0.3. Ties between rivals count as a
failure to recover the family.
"""
from __future__ import annotations

import math
import random
import sys
from dataclasses import dataclass, field, asdict
from pathlib import Path

import numpy as np
from scipy import stats

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from derive import derive_window, pilot_params  # noqa: E402
from predict import Params, gate  # noqa: E402
from score import (rival_scores, score, escape_analysis, collision_analysis, ratio_ci)  # noqa: E402
from synth import T0, make_truth, make_task_pool, simulate  # noqa: E402

BUDGET = 200.0          # $250 credits minus the $50 floor
FLOOR = 50.0
COST = {"sonnet": 4.20, "haiku": 2.10}
BASE_DEFECT = {"sonnet": 0.35, "haiku": 0.49}   # haiku: r0 about 1.3x sonnet's (0.42 -> 0.54)


class StepLoad(float):
    """reviewer_load that acts as a step: rate x (1 + delta) whenever depth >= 1, else x 1.
    synth computes 1 + reviewer_load * depth, so overriding __mul__ gives the step without editing synth."""
    def __mul__(self, depth):
        return float(self) if depth >= 1 else 0.0
    __rmul__ = __mul__


@dataclass(frozen=True)
class Design:
    worker: str = "sonnet"          # sonnet | haiku
    lam: float = 4.0                # task-size target: single-agent attempts per agent-hour (for this worker)
    q: float = 2.5                  # reviewer capacity target V / lambda1 (V chosen at design time)
    sizes: tuple = (1, 6)           # fixed sizes; ("gate",) = PLAN-v3 pilot gate chooses (N_low, N_high)
    L: int = 90                     # sweep window minutes
    reps: int = 2                   # windows per size
    n_p: int = 2                    # T2 workers
    L_p: int = 60                   # T2 minutes per pilot window
    pw: int = 1                     # number of T2 windows
    t1_reviewed: bool = True
    vcal: int = 0                   # free offline calibration reviews (reviewer only; no credit)

    @property
    def gated(self):
        return self.sizes and self.sizes[0] == "gate"

    def label(self):
        s = "gate" if self.gated else "/".join(map(str, self.sizes))
        return (f"{self.worker[0].upper()} lam{self.lam:g} q{self.q:g} N{s} {self.L}m x{self.reps} "
                f"| T2 {self.n_p}w {self.pw}x{self.L_p}m{' T1r' if self.t1_reviewed else ''}"
                f"{f' cal{self.vcal}' if self.vcal else ''}")


def n_max(d: Design):
    return 8 if d.gated else max(d.sizes)


def pilot_hours(d: Design):
    t1 = 0.5 + 0.25 * (n_max(d) - 1)
    t2 = d.n_p * d.L_p / 60 * d.pw
    return t1, t2


def sweep_hours(sizes, reps, L):
    return reps * sum(sizes) * L / 60


def fit_budget(sizes, reps, L, rate, pilot_cost, budget=BUDGET):
    """Pre-registered degrade rule: keep the design if it fits; otherwise the largest sweep (in worker-hours)
    over fewer replicates and/or windows shortened in 15-min steps (not below 60 min) that fits; ties -> more reps.
    Returns (reps, L) or None."""
    room = budget - pilot_cost
    best = None
    for r in range(reps, 0, -1):
        for LL in range(L, 59, -15):
            c = rate * sweep_hours(sizes, r, LL)
            if c <= room + 1e-9:
                key = (sweep_hours(sizes, r, LL), r)
                if best is None or key > best[0]:
                    best = (key, (r, LL))
    return None if best is None else best[1]


def design_cost(d: Design, burn_mult=1.0, sizes=None):
    rate = COST[d.worker] * burn_mult
    t1, t2 = pilot_hours(d)
    sz = sizes or ((1, 8) if d.gated else d.sizes)
    return dict(rate=rate, pilot_h=t1 + t2, pilot_cost=rate * (t1 + t2), sweep_h=sweep_hours(sz, d.reps, d.L),
                sweep_cost=rate * sweep_hours(sz, d.reps, d.L),
                total=rate * (t1 + t2 + sweep_hours(sz, d.reps, d.L)),
                agent_h=t1 + t2 + sweep_hours(sz, d.reps, d.L))


# ------------------------------------------------------------------------------------------ truths
FAMILY = {"skimN": "carnot", "slowN": "carnot", "carnot": "carnot", "usl": "usl", "amdahl": "amdahl", "linear": "linear",
          "skim": "carnot", "slow": "carnot", "escape": "carnot", "coll01": "carnot", "coll05": "carnot",
          "null": "carnot"}


def build_truth(d: Design, tname: str, lam_scale=1.0, haiku_lam=None, service_cv=1.0, cv_window=0.3,
                V_scale=1.0, truth_over=None, ci_slow=False):
    """lam_scale: true lambda / assumed (robustness). haiku_lam: Haiku's lambda as a share of the task-size
    target (scenario 0.7 or 1.0); the design assumes 0.85 for Haiku when it sets V."""
    if d.worker == "haiku":
        lam_assumed = 0.85 * d.lam
        lam_true = (haiku_lam if haiku_lam is not None else 0.85) * d.lam * lam_scale
    else:
        lam_assumed = d.lam
        lam_true = d.lam * lam_scale
    V0 = d.q * lam_assumed * V_scale
    over = dict(lam1=lam_true, V0=V0, defect_p=BASE_DEFECT[d.worker], cv_window=cv_window,
                service_cv=service_cv, rework_min=6.0 * 4.0 / lam_true,
                ci_hidden_min=0.25, ci_post_min=0.5)   # harness requirement: ~0.75 min serial merge-queue time
    if ci_slow:
        over.update(ci_hidden_min=1.0, ci_post_min=1.5)   # synth default (~2.6 min per change)
    base = {"carnot": "carnot", "usl": "usl", "amdahl": "amdahl", "linear": "linear",
            "skim": "carnot", "slow": "carnot", "escape": "carnot", "coll01": "carnot", "coll05": "carnot",
            "null": "carnot", "skimN": "carnot", "slowN": "carnot"}[tname]
    if tname == "skim":
        over["reviewer_load"] = StepLoad(0.25)
    elif tname == "slow":
        over["reviewer_load"] = StepLoad(-0.25)
    elif tname == "escape":
        over["escape_depth"] = 0.2
    elif tname == "coll01":
        over["p"] = 0.01
    elif tname == "coll05":
        over["p"] = 0.05
    elif tname == "null":
        over["p"] = 0.0
    over.update(truth_over or {})
    return make_truth(base, **over)


# ------------------------------------------------------------------------------------------ pieces
pil_derived_cache = {}
cal_cache = {}


def run_pilot(d: Design, truth, seed, pool, vcal_shift_sd=0.10):
    runs = []
    nm = n_max(d)
    t0 = T0 + (seed % 100000) * 86400.0
    sched = [(0.0, 30.0)] + [(15.0, 30.0)] * (nm - 1)
    t1 = simulate(truth, nm, seed=seed * 1000 + 1, window_min=30, warmup_min=0, grace_min=60, schedule=sched,
                  task_pool=pool, run_id=f"s{seed}-T1", kind="trial", t0=t0)
    if d.t1_reviewed:
        runs.append(t1)
    for i in range(d.pw):
        runs.append(simulate(truth, d.n_p, seed=seed * 1000 + 2 + i, window_min=d.L_p, warmup_min=10, grace_min=10,
                             task_pool=pool, run_id=f"s{seed}-T2-{i}", kind="pilot", t0=t0 + (3 + 3 * i) * 3600))
    der = [derive_window(r, e) for r, e in runs]
    pil_derived_cache[seed] = der
    pil = pilot_params(der)
    if d.vcal:
        rng = np.random.default_rng(seed + 424242)
        shift = math.exp(rng.normal(0, vcal_shift_sd))
        mean_h = 1.0 / (truth.V0 * shift)
        cv = truth.service_cv
        k = 1 / cv ** 2
        durs = rng.gamma(k, mean_h / k, size=d.vcal)
        pil["cal_durations_s"] = list(durs * 3600.0)
        cal_cache[seed] = (float(durs.sum()), pil["cal_durations_s"])
        pil["reviews"] = pil["reviews"] + d.vcal
        pil["busy_hours"] = pil["busy_hours"] + float(durs.sum())
        pil["V"] = pil["reviews"] / pil["busy_hours"]
    return pil


def sweep_order(sizes, reps):
    order = []
    for r in range(reps):
        order += list(sizes) if r % 2 == 0 else list(reversed(sizes))
    return order


def review_durations(events):
    return [e["duration_s"] for e in events if e["type"] == "review_end" and e.get("duration_s")]


def welch_log(a, b):
    a, b = np.log(np.asarray(a, float)), np.log(np.asarray(b, float))
    if len(a) < 3 or len(b) < 3:
        return math.nan
    return float(stats.ttest_ind(a, b, equal_var=False).pvalue)


def study(cfg):
    """cfg: design (dict), truth (name), seed, lam_scale, haiku_lam, burn_mult, service_cv, extra (bool:
    escape/collision tests), vcal_shift_sd."""
    pil_derived_cache.clear()
    cal_cache.clear()
    d = Design(**cfg["design"])
    tname = cfg["truth"]
    seed = cfg["seed"]
    truth = build_truth(d, tname, lam_scale=cfg.get("lam_scale", 1.0), haiku_lam=cfg.get("haiku_lam"),
                        service_cv=cfg.get("service_cv", 1.0), cv_window=cfg.get("cv_window", 0.3),
                        V_scale=cfg.get("V_scale", 1.0), truth_over=cfg.get("truth_over"),
                        ci_slow=cfg.get("ci_slow", False))
    out = dict(seed=seed, truth=tname)
    pool = make_task_pool(truth, seed * 31 + 5)
    try:
        pil = run_pilot(d, truth, seed, pool, cfg.get("vcal_shift_sd", 0.10))
        P = Params.from_pilot(pil, window_min=d.L, warmup_min=10, rival_rework="completion")
        if not (P.lambda_pilot > 0) or P.completion is None or not (P.completion > 0):
            raise ValueError
    except (ValueError, KeyError, ZeroDivisionError, TypeError):
        out["status"] = "PILOT_UNUSABLE"
        return out
    out["pilot_V_ratio"] = pil["V"] / truth.V0
    out["pilot_lam_ratio"] = pil["lambda_pilot"] / truth.lam1
    out["pilot_reviews"] = pil["reviews"]
    bm = cfg.get("burn_mult", 1.0)
    cost = design_cost(d, bm)
    g = gate(P)
    out["gate"] = g["decision"]
    if d.gated:
        if g["decision"] != "RUN":
            out["status"] = "GATE_" + g["decision"]
            return out
        sizes = (g["N_low"], g["N_high"])
    else:
        sizes = tuple(d.sizes)
    fb = fit_budget(sizes, d.reps, d.L, cost["rate"], cost["pilot_cost"])
    if fb is None:
        out["status"] = "OVER_BUDGET"
        return out
    reps, L = fb
    if L != d.L:
        P = Params.from_pilot(pil, window_min=L, warmup_min=10, rival_rework="completion")
    out["sizes"] = sizes
    out["reps_L"] = (reps, L)
    out["cost"] = cost["pilot_cost"] + cost["rate"] * sweep_hours(sizes, reps, L)
    runs = []
    t0 = T0 + (seed % 100000) * 86400.0
    import dataclasses
    truth_hi = truth
    if tname in ("skimN", "slowN"):
        # reviewer pace differs by +/-25% at the high fleet size (e.g. shared rate limits), not with queue depth
        truth_hi = dataclasses.replace(truth, V0=truth.V0 * (1.25 if tname == "skimN" else 0.75))
    order = sweep_order(sizes, reps)
    if cfg.get("anchor_low"):
        # variant: every N_low window runs first and is pooled into the anchor (lambda, completion, V, b)
        order = sorted(order, key=lambda n: n != min(sizes))
    for i, n in enumerate(order):
        runs.append(simulate(truth_hi if n == max(sizes) else truth, n, seed=seed * 1000 + 10 + i, window_min=L, warmup_min=10, grace_min=10,
                             task_pool=pool, run_id=f"s{seed}-N{n}-w{i + 1}", kind="sweep",
                             t0=t0 + (10 + 2.5 * i) * 3600))
    derived = [derive_window(r, e) for r, e in runs]
    S = [x["summary"] for x in derived]
    if cfg.get("anchor_low"):
        if min(sizes) != d.n_p:
            raise ValueError("anchor_low needs n_p == N_low")
        extra_p = []
        for (r, e) in runs:
            if r["n_workers"] == min(sizes):
                rr = dict(r, kind="pilot")
                extra_p.append(derive_window(rr, e))
        pil = pilot_params(pil_derived_cache[seed] + extra_p)
        if d.vcal:
            pil["reviews"] += d.vcal
            pil["busy_hours"] += cal_cache[seed][0]
            pil["V"] = pil["reviews"] / pil["busy_hours"]
            pil["cal_durations_s"] = cal_cache[seed][1]
        P = Params.from_pilot(pil, window_min=L, warmup_min=10, rival_rework="completion")
    rv = rival_scores(S, P)
    out["best"] = rv["best"]
    out["correct"] = rv["best"] == FAMILY[tname]
    out["finished"] = [s["finished"] for s in S]
    lo, hi = min(sizes), max(sizes)
    ext = [x for x in derived if x["summary"]["n_workers"] in (lo, hi)]
    try:
        R = score(ext, pil, rival_rework="completion", escape_model="logit_cluster_task", do_escape=False,
                  do_collision=False, do_fit=False, window_min=L, warmup_min=10)
        oc = {o["id"]: o["code"] for o in R["outcomes"]}
        out["S4n"] = oc.get("S4n")
        out["O1n"] = oc.get("O1n")
        out["S1r"] = oc.get("S1r")
        out["S2r"] = oc.get("S2r")
        V = R["V"]
        out["V_hi_lo"] = V["high_over_low"]
        ci = V["high_over_low_ci"]
        out["Vratio_sig"] = bool(ci[0] > 1 or ci[1] < 1)            # variant: exact CI of V(high)/V(low) excludes 1
    except Exception as e:  # pragma: no cover
        out["score_error"] = repr(e)[:200]
    dl = [x for (r, e) in runs if r["n_workers"] == lo for x in review_durations(e)]
    dh = [x for (r, e) in runs if r["n_workers"] == hi for x in review_durations(e)]
    out["Vdur_p"] = welch_log(dl, dh)                                  # variant: Welch t on log review durations
    if pil.get("cal_durations_s"):
        out["Vcal_p"] = welch_log(pil["cal_durations_s"], dh)           # variant: N_high durations vs free calibration
    out["n_rev"] = (len(dl), len(dh))
    if cfg.get("extra"):
        apprs = [a for x in derived for a in x["approvals"]]
        e = escape_analysis(apprs, "logit_cluster_task")
        out["esc_events"] = e["events"]
        out["esc_p"] = e.get("p_one_sided") if not e["descriptive_only"] else None
        prs = [p for x in derived for p in x["prs"]]
        c = collision_analysis(prs, with_model_form=False)
        out["coll_p"] = c.get("p_k_one_sided") if c.get("fit_ok") else None
    out["status"] = "SCORED"
    return out
