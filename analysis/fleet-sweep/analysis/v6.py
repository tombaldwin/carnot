#!/usr/bin/env python3
"""PLAN-v6 analysis codings: agent-side scaling and the review ceiling, with K parallel reviewers (PLAN-v6 section 5).

Imported by score.py (the default `--plan v6`), predict.py (the default path) and design-search/dsim6.py, so the
design search, the self-test and the real analysis run one implementation. Reuses the v5 codings (v5.py) wherever
they carry over. Nothing here uses the network.

Why v6 (PLAN-v6 section 0): in T1 (2026-09-28) twelve Haiku slots made about 14 first submissions per slot-hour
(the v5 design assumed 6), 39% of reviews asked for changes, and one serial reviewer at a median 20 s per review
was busy 88% of the twelve-slot phase with its queue growing to 29 by the window's end. At N = 12 with one reviewer,
review binds, so v5's SCALE would have measured the review ceiling, not the workers. v6 varies the number of
parallel reviewers K as a factor.

The design is a 2 x 2 of fleet size N in {1, 12} and reviewers K in {1, 3} (the N = 1 windows split between K = 1
and K = 3 at no extra cost; at N = 1 the reviewer is busy about 15% of the time, so K should not matter there).

Tests (grades fixed in advance by the v6 design search, DESIGN-SEARCH-v6.md; OPERATING-CHARACTERISTICS-v6.md):

  SCALE    Agent-side scaling with review not binding: per-agent finished output falls from N = 1 to N = 12, using
           the N = 12 windows with K = 3 and every N = 1 window (v5.bend_test: one-sided NB likelihood-ratio test of
           gamma < 0, CV 0.3 fixed). BEND if p < 0.05. Confirmatory, primary.
  CAP      The review ceiling: at N = 12, finished output with one reviewer is lower than with three (one-sided NB LR
           test of delta < 0 in finished ~ theta x slot-hours x exp(delta [K = 1]), CV 0.3). CAPPED if p < 0.05. The
           paper's U = (1 - r) min(lambda X(N), V / h): with near-linear workers lambda X(12) exceeds one reviewer's
           capacity, so the model predicts CAPPED; under drag (USL) it does not. Confirmatory, secondary.
  COLL     As v5 (collisions against j, merges since a change's base), all windows. Confirmatory, secondary.
  K1       Descriptive: the same K contrast at N = 1 (the model predicts none); with CAP it is the 2 x 2 interaction.
  CAPFIT   Descriptive: finished output at N = 12, K = 1 against the ceiling predicted from the reviewer's own
           service rate, (1 - b) x K x 3600 / mean review seconds x counted hours.
  FAMILY, ESC, ESC-N, UTIL, COLL-m, COLL-k, LAMBDA, BOUNCE, EFFORT: as v5 (FAMILY on the SCALE windows).
  THROTTLE Abort rule 1 as revised (PLAN-v6 section 5.3): start-up (launch -> branch pushed) and coding
           (pushed -> first READY) times, twelve slots against one, as ratios of geometric means with Welch 90%
           intervals on the log scale; tolerance 1.25. `throttle_v6` codes T1b (the decision); `score_v6` repeats the
           comparison on the sweep (every N = 12 window against the pooled N = 1 windows), descriptive only.
"""
from __future__ import annotations

import math
import re
from pathlib import Path

import numpy as np
from scipy import optimize, stats

from common import (ALPHA, BETA, CONFIRMATORY, CV_OVERDISPERSION, DESCRIPTIVE, X_amdahl, X_linear, X_usl, lr_one_sided,
                    nb_interval, nb_logpmf, parse_t, pooled_interval)
import v5
from v5 import (RIVALS_V5, RIVAL_LABEL_V5, SECONDARY, _f, bend_test, collision_v5, effort_compare, escape_v5,
                exposure_hours, family_fit, utilisation_v5)

ALPHA_TEST = 0.05
P_V6 = 0.0075          # T1: 5 collided first passes over 658 merges of exposure (naive per-merge rate)
THROTTLE_TOL = 1.25    # rule 1: a service-side slowdown of 25% or more at twelve slots is throttling
THROTTLE_LEVEL = 0.90  # two-sided 90% interval (each bound a one-sided 5% test)

# ---------------------------------------------------------------------------------------------- the design
# PLAN-v6 section 5. cells: (N, K) -> windows. Session-hours count worker slots only (the reviewer runs locally).
DESIGN_V6 = dict(
    cells={(1, 1): 6, (1, 3): 6, (12, 3): 3, (12, 1): 4}, window_min=45.0, warmup_min=5.0, grace_min=10.0,
    N_hi=12, K_hi=3, K_lo=1,
    t1b=dict(one_slot_min=90.0, twelve_slot_min=30.0, K=3, session_hours=7.5),
    sweep_session_hours=72.0, balance_usd=249.0, balance_floor_usd=50.0,
    degrade=dict(cells={(1, 1): 5, (1, 3): 5, (12, 3): 2, (12, 1): 3}, sweep_session_hours=52.5,
                 note="one N = 12 window of each K fewer, and two N = 1 windows fewer"),
    burn_assumed=2.10, cv=CV_OVERDISPERSION, alpha=ALPHA, beta=BETA, p=P_V6,
    lambda_floor=7.0, review_util_alarm=0.8, mq_time_alarm_s=30.0, throttle_tol=THROTTLE_TOL)


def cells_of(cells):
    """cells: {(N, K): windows} or [{"N":, "K":, "windows":}] -> {(N, K): windows}."""
    if isinstance(cells, dict):
        return {tuple(map(int, k)) if not isinstance(k, str) else tuple(int(x) for x in k.split("x")): int(v)
                for k, v in cells.items()}
    return {(int(c["N"]), int(c["K"])): int(c["windows"]) for c in cells}


def sweep_hours(cells, window_min):
    return sum(n * w for (n, _), w in cells_of(cells).items()) * window_min / 60.0


def design_cost_v6(cells, window_min, burn, t1b_h=None):
    t1b = DESIGN_V6["t1b"]["session_hours"] if t1b_h is None else t1b_h
    sw = sweep_hours(cells, window_min)
    return dict(sweep_session_hours=sw, t1b_session_hours=t1b, total_session_hours=sw + t1b, burn=burn,
                total_usd=burn * (sw + t1b))


def fits_up_to(cells, window_min, room=None, t1b_h=None):
    room = DESIGN_V6["balance_usd"] - DESIGN_V6["balance_floor_usd"] if room is None else room
    return room / design_cost_v6(cells, window_min, 1.0, t1b_h)["total_session_hours"]


def v6_order(cells):
    """Sweep order (PLAN-v6 section 5.1): the N = 12 windows spread evenly through the sequence, alternating K,
    never first or last; the N = 1 windows alternate K = 3, K = 1 and fill the gaps; the sequence
    starts with two N = 1 windows (rule 3 is checked there before any N = 12 window). Where one K has more windows at
    a size, it leads the alternation."""
    c = cells_of(cells)
    ns = sorted({n for n, _ in c})
    lo, hi = ns[0], ns[-1]

    def alt(n):
        ks = sorted({k for (nn, k) in c if nn == n}, key=lambda k: (-c[(n, k)], -k))   # the more numerous K first
        rem = {k: c[(n, k)] for k in ks}
        out = []
        while any(rem.values()):
            for k in ks:
                if rem[k]:
                    out.append((n, k))
                    rem[k] -= 1
        return out
    his = alt(hi)
    los = alt(lo)
    mids = [n for n in ns if n not in (lo, hi)]
    mid = [x for n in mids for x in alt(n)]
    total = len(his) + len(los) + len(mid)
    order = [None] * total
    big = his + mid
    for i, x in enumerate(big):
        pos = int(round(2 + (i + 0.5) * (total - 3) / len(big)))
        pos = min(max(pos, 2), total - 2)
        while order[pos] is not None:
            pos += 1
        order[pos] = x
    it = iter(los)
    for i in range(total):
        if order[i] is None:
            order[i] = next(it)
    return order


# Simulated operating characteristics (design-search/oc_v6.py, OPERATING-CHARACTERISTICS-v6.md), filled from
# oc_v6.json by hand; printed by predict.py and score.py next to the results, never recomputed by them.
V6_OC = dict(
    source="OPERATING-CHARACTERISTICS-v6.md, design-search/oc_v6.py, 1000 freshly simulated studies per cell",
    design="N = 1 x 12 windows (6 at K = 1, 6 at K = 3), N = 12 x 3 at K = 3 and x 4 at K = 1, 45 min each (5 min warm-up); "
           "degrade: N = 1 x 10, N = 12 x 2 (K = 3) and x 3 (K = 1)",
    SCALE=dict(fpr_linear=0.020, fpr_measured=0.026, power_amdahl=0.97, power_usl=1.00, power_carnot=1.00, power_mild=0.32,
               fpr_linear_cv015=0.000, fpr_linear_cv05=0.12, power_amdahl_cv05=0.87, lam70_power_amdahl=0.93, lam70_fpr=0.017,
               fpr_contention=0.020, power_amdahl_contention=0.96, bend_under_coding_throttle_1_3=0.10,
               degrade_fpr=0.030, degrade_power_amdahl=0.885, degrade_power_usl=1.00),
    CAP=dict(fpr_fastrev=0.025, rate_usl=0.048, rate_amdahl=0.030, rate_mild=0.089, power_linear=0.51, power_measured=0.74,
             power_slowrev=0.995, power_lam70=0.08, power_linear_cv05=0.59, ratio_linear=0.68, ratio_measured=0.61,
             degrade_power_measured=0.54, degrade_power_linear=0.38, degrade_fpr=0.036),
    COLL=dict(fpr=0.015, fpr_linear=0.035, power={"0.0035": 0.48, "0.0075": 0.78, "0.015": 0.98},
              power_linear={"0.0035": 0.99, "0.0075": 1.00}, p_ci_coverage=0.95, degrade_power_0075=0.69),
    ESC_N=dict(fpr=0.058, fpr_linear=0.064, power_2x=0.44, degrade_power_2x=0.40),
    ESC=dict(rate=0.031, halfwidth_median=0.012, approvals_median=858),
    FAMILY=dict(correct=dict(linear=0.98, amdahl=0.86, usl=0.48, carnot=0.50), binary_correct=dict(linear=0.98, amdahl=0.98)),
    UTIL=dict(review_util_12x3=dict(linear=0.57, measured=0.59, amdahl=0.27, usl=0.16, linear_busiest_p95=0.94,
                                    contention_0_2=0.77), review_util_12x1=dict(linear=0.99, amdahl=0.77, usl=0.48),
              flag_rule4_12x3_linear=0.23, flag_rule4_12x3_contention=0.84,
              reviews_per_window=dict(n1=17, n12k1=145, n12k3_linear=208, n12k3_measured=215)),
    THROTTLE=dict(one_slot_min=90, clear_no_throttle=0.94, stop_no_throttle=0.000, stop_1_25=0.04, stop_1_5=0.77, stop_2=1.00,
                  ci_factor_median=1.12, one_slot_30=dict(clear=0.57, stop_1_5=0.44), one_slot_60=dict(clear=0.84, stop_1_5=0.65),
                  one_slot_120=dict(clear=0.96, stop_1_5=0.81)),
)


def plan_usage(cells, window_min, per_day=None, lam=14.0, b_review=0.39, review_s=21.0, tokens_per_review=48_000):
    """Budget (b), PLAN-v6 section 5.2: the design in plan-usage terms. Worker session-hours (Haiku cloud sessions:
    at most N at once), wall-clock, the local reviewer's Opus calls (reviews per window from lambda and K, capped by
    capacity) and, with per_day, the days needed at that many session-hours per day."""
    c = cells_of(cells)
    t1b = DESIGN_V6["t1b"]
    t1b_h = t1b["session_hours"]
    sw = sweep_hours(c, window_min)
    out = {"sweep worker session-hours": f"{sw:.1f}", "T1b worker session-hours": f"{t1b_h:.1f}",
           "total worker session-hours": f"{sw + t1b_h:.1f}"}
    for (n, k), w in sorted(c.items()):
        if not w:
            continue
        demand = lam * n / max(1 - b_review, 1e-9)
        rev_h = min(demand, k * 3600.0 / review_s)
        rv = rev_h * (window_min + 10) / 60.0
        out[f"cell N = {n}, K = {k}"] = (f"{w} windows x {n * window_min / 60:.2f} session-h ({n} concurrent sessions for "
                                          f"{window_min:g} min); about {rv:.0f} Opus reviews per window "
                                          f"(~{rv * tokens_per_review / 1e6:.1f}M input tokens), up to {k} at once")
    n_win = sum(c.values())
    out["windows / wall-clock"] = f"{n_win} windows, about {n_win * (window_min + 25) / 60:.0f} h of operator time (window + grace + reset)"
    hi = [(n, k) for (n, k), w in c.items() if n == max(n for n, _ in c) and w]
    per12 = max(n for n, _ in c) * window_min / 60.0
    out["heaviest day"] = (f"an N = {max(n for n, _ in c)} window is {per12:.0f} session-hours in {window_min + 10:g} min; "
                           f"with two N = 1 windows the same day about {per12 + 2 * window_min / 60:.0f}")
    if per_day:
        out["days at the allowance"] = (f"{(sw + t1b_h) / per_day:.1f} days at {per_day:g} session-hours per day"
                                        + ("" if per12 <= per_day else f" (an N = 12 window alone exceeds {per_day:g})"))
    out["decision needed"] = ("owner: which budget applies ((a) credits at an assumed burn, or (b) plan usage) and, under (b), "
                              "the allowance per day and per 5-hour block")
    return out


ABORT_V6 = (
    "Rule 1 (throttling, T1b; revised): T1b runs one slot for 90 min, then twelve for 30 min (K = 3). Per task launched "
    "in each phase: start-up (routine fire -> branch first pushed; launch -> push if the fire time is not logged) and "
    "coding (branch pushed -> first READY). Ratios twelve : one of the geometric means with Welch 90% intervals on the "
    "log scale; tolerance 1.25. STOP if the start-up interval lies wholly above 1.25; CLEAR if wholly below; otherwise "
    "INCONCLUSIVE: proceed, with the ratio and its interval reported beside SCALE. A coding interval wholly above 1.25 "
    "with start-up not stopped is a flag, not a stop (coding contains the drag SCALE measures); it is reported with "
    "SCALE. Slots lost to operator-logged failures unrelated to the service (e.g. the CLI) are excluded before the "
    "ratios are computed. Both phases share one window, so day-to-day speed differences cancel.",
    "Rule 1b (throttling in the sweep, descriptive, not a stop): each N = 12 window's start-up and coding against the "
    "pooled N = 1 windows (THROTTLE). Across windows the comparison carries the window-to-window speed noise (CV 0.3 "
    "assumed), so it cannot decide anything on its own.",
    "Rule 2 (budget; UNRESOLVED which applies, the owner decides before T1b): (a) credits: run the full design if the "
    "predicted balance after it stays >= $50 at the measured burn (<= $2.50 per session-hour); else the degrade design "
    "(<= $3.32); else stop and report T1b. Before every window the rest of the chosen design must still fit; if not, drop "
    "N = 1 windows beyond ten first, then N = 12 windows alternately by K, keeping at least two of each. (b) plan usage: "
    "the sweep is paced to the owner's daily allowance; the order is kept and the analysis is unchanged. An interrupted "
    "sequence is analysed as run.",
    "Rule 3 (lambda floor): after the first two N = 1 sweep windows, pooled lambda < 7 first submissions per slot-hour "
    "(half of T1's 14) -> stop before the first N = 12 window and report.",
    "Rule 4 (reviewer binding, per K): an N = 12, K = 3 window with the reviewers busy >= 80% of its counted time (per "
    "reviewer) is flagged and SCALE is reported with and without it (not a stop). An N = 12, K = 1 window with the "
    "reviewer busy < 80% means the ceiling did not bind: reported beside CAP (not a stop). A mean review above 60 s in "
    "any window is reported (T1: 21 s).",
    "Rule 5 (merge queue): mean merge-queue time per change above 30 s (T1: 5.8 s) -> stop and fix the harness before "
    "the next window.",
)


# ---------------------------------------------------------------------------------------------- window helpers
def K_of(s):
    return int(s.get("n_reviewers") or 1)


def scale_windows(S, n_hi=None, k_hi=None):
    n_hi = n_hi or max(s["n_workers"] for s in S)
    ks = {K_of(s) for s in S if s["n_workers"] == n_hi}
    k_hi = k_hi if k_hi is not None else max(ks)
    return [s for s in S if s["n_workers"] != n_hi or K_of(s) == k_hi]


# ---------------------------------------------------------------------------------------------- CAP
def k_test(S, n, k_lo, k_hi, cv=CV_OVERDISPERSION, ci=True):
    """At fleet size n: finished_w ~ NB(theta x slot-hours_w x exp(delta [K_w = k_lo])), one-sided LR test of
    delta < 0 (fewer finished with fewer reviewers). Returns p, the ratio exp(delta) and its profile 95% interval."""
    W = [s for s in S if s["n_workers"] == n and K_of(s) in (k_lo, k_hi)]
    out = dict(N=n, K_lo=k_lo, K_hi=k_hi, windows={k_lo: sum(K_of(s) == k_lo for s in W), k_hi: sum(K_of(s) == k_hi for s in W)})
    if not out["windows"][k_lo] or not out["windows"][k_hi]:
        return dict(out, p=math.nan, ratio=math.nan)
    y = np.array([s["finished"] for s in W], float)
    a = np.array([exposure_hours(s) * n for s in W], float)
    x = np.array([1.0 if K_of(s) == k_lo else 0.0 for s in W])
    rates = {k: y[x == (1.0 if k == k_lo else 0.0)].sum() / a[x == (1.0 if k == k_lo else 0.0)].sum() for k in (k_lo, k_hi)}
    out["per_slot_hour"] = {int(k): float(v) for k, v in rates.items()}
    out["ratio"] = float(rates[k_lo] / rates[k_hi]) if rates[k_hi] > 0 else math.nan
    lt0 = math.log(max(y.sum(), 0.5) / a.sum())
    ll = lambda lt, d: float(np.sum(nb_logpmf(y, np.exp(lt + d * x) * a, cv)))
    r1 = optimize.minimize(lambda th: -ll(th[0], th[1]), np.array([lt0, 0.0]), method="L-BFGS-B",
                           bounds=[(lt0 - 6, lt0 + 6), (-4, 4)])
    _, ll0 = v5._fit_level(y, a, cv)
    d = float(r1.x[1])
    p2, p1 = lr_one_sided(-r1.fun, ll0, -d)
    out.update(p=p1, p_two_sided=p2, delta=d, ratio_model=math.exp(d))
    if ci:
        crit = stats.chi2.ppf(0.95, 1) / 2

        def prof(dv):
            r = optimize.minimize_scalar(lambda lt: -ll(lt, dv), bounds=(lt0 - 8, lt0 + 8), method="bounded")
            return (-r1.fun + r.fun) - crit
        lo, hi = -4.0, 4.0
        try:
            if prof(-4.0) > 0:
                lo = optimize.brentq(prof, -4.0, d)
            if prof(4.0) > 0:
                hi = optimize.brentq(prof, d, 4.0)
        except ValueError:
            pass
        out["ratio_ci"] = [math.exp(lo), math.exp(hi)]
    return out


def cap_fit(S, n, k):
    """Observed finished per counted hour at (n, k) against the ceiling from the reviewer's own service rate:
    (1 - b_review) x (1 - b_mq) x K x 3600 / mean review seconds (descriptive)."""
    W = [s for s in S if s["n_workers"] == n and K_of(s) == k]
    if not W:
        return None
    fin = sum(s["finished"] for s in W)
    hrs = sum(s["hours"] for s in W)
    rev = sum(s["reviews"] for s in W)
    rc = sum(s["bounces"].get("review", 0) for s in W)
    mqb = sum(s["bounces"].get(c, 0) for s in W for c in ("rebase_conflict", "escaped_defect", "integration_failure", "visible_fail"))
    ar = sum(s["approvals_resolved"] for s in W)
    durs = [x for s in W for x in s.get("review_durations_s", [])]
    mean_s = float(np.mean(durs)) if durs else (sum(s["busy_hours"] for s in W) * 3600 / rev if rev else math.nan)
    b = rc / rev if rev else math.nan
    bmq = mqb / ar if ar else 0.0
    cap_h = (1 - b) * (1 - bmq) * k * 3600.0 / mean_s if mean_s == mean_s and mean_s > 0 else math.nan
    return dict(N=n, K=k, windows=len(W), finished_per_hour=fin / hrs if hrs else math.nan, cap_per_hour=cap_h,
                attainment=(fin / hrs) / cap_h if hrs and cap_h == cap_h and cap_h > 0 else math.nan,
                b_review=b, b_mq=bmq, review_s_mean=mean_s,
                reviewer_util_mean=float(np.mean([s["reviewer_util"] for s in W])))


# ---------------------------------------------------------------------------------------------- THROTTLE (rule 1)
LEAD_RE = re.compile(r"launch_detail slot=(\S+) task=(\S+) .*run_once_at=(\S+)")
SCHED_RE = re.compile(r"start_schedule minute=([\d.]+) slots=(\S+)")


def _welch_log_ratio(a, b, level=THROTTLE_LEVEL):
    """Ratio of geometric means b / a with a Welch interval on the log scale."""
    a = np.log(np.asarray(a, float))
    b = np.log(np.asarray(b, float))
    if len(a) < 2 or len(b) < 2:
        return dict(ratio=math.nan, ci=[math.nan, math.nan], n_a=len(a), n_b=len(b))
    va, vb = a.var(ddof=1) / len(a), b.var(ddof=1) / len(b)
    se = math.sqrt(va + vb)
    df = (va + vb) ** 2 / (va ** 2 / (len(a) - 1) + vb ** 2 / (len(b) - 1)) if va + vb > 0 else 1.0
    tq = stats.t.ppf(0.5 + level / 2, df)
    d = b.mean() - a.mean()
    return dict(ratio=math.exp(d), ci=[math.exp(d - tq * se), math.exp(d + tq * se)], se_log=se, df=df, n_a=len(a), n_b=len(b))


def task_legs(run, events):
    """Per task: (slot, launch t, start-up s, coding s) from session_launch, claim, first submit; start-up measured
    from the routine's run_once_at when a launch_detail note gives it (removes the harness's re-arm lead)."""
    t0 = parse_t(run["window_start"])
    we = parse_t(run["window_end"]) - t0
    rows, once = {}, {}
    for e in events:
        t = parse_t(e["t"]) - t0
        ty = e["type"]
        if ty == "note":
            m = LEAD_RE.match(e.get("text", ""))
            if m:
                once.setdefault(m.group(2), parse_t(m.group(3)) - t0)
        elif ty == "session_launch":
            rows.setdefault(e["task"], {}).setdefault("launch", t)
            rows[e["task"]].setdefault("slot", e["slot"])
        elif ty == "session_message" and e.get("kind") == "task":   # one session per slot: a follow-up hand-out
            rows.setdefault(e["task"], {}).setdefault("launch", t)
            rows[e["task"]].setdefault("slot", e["slot"])
            rows[e["task"]]["followup"] = True
        elif ty == "claim":
            rows.setdefault(e["task"], {}).setdefault("claim", t)
        elif ty == "submit" and e["attempt_no"] == 1:
            rows.setdefault(e["task"], {}).setdefault("ready", t)
    out = []
    for task, r in rows.items():
        if "launch" not in r or "claim" not in r:
            continue
        up = r["claim"] - (r["launch"] if r.get("followup") else max(r["launch"], once.get(task, r["launch"])))
        code = r["ready"] - r["claim"] if "ready" in r and r["ready"] <= we else None
        out.append(dict(slot=r.get("slot"), launch=r["launch"], startup_s=up if up > 0 else None, coding_s=code))
    return out


def throttle_decision(up, code, tol=THROTTLE_TOL):
    """up / code: _welch_log_ratio results (twelve slots over one). Rule 1 (PLAN-v6 section 5.3)."""
    lo, hi = up["ci"]
    if lo == lo and lo > tol:
        dec = "STOP"
    elif hi == hi and hi < tol:
        dec = "CLEAR"
    else:
        dec = "INCONCLUSIVE"
    flag = bool(code["ci"][0] == code["ci"][0] and code["ci"][0] > tol)
    return dec, flag


def throttle_v6(run, events, split_min=None, exclude=(), tol=THROTTLE_TOL):
    """Rule 1 on a T1-style log: one slot, then many. Phase A = launches before the second start_schedule minute
    (or split_min) on the first slot; phase B = launches after it on the other slots too, excluding `exclude`
    (slots lost for reasons unrelated to throttling, logged by the operator)."""
    if split_min is None:
        mins = [float(m.group(1)) for e in events if e["type"] == "note" for m in [SCHED_RE.match(e.get("text", ""))] if m]
        split_min = sorted(mins)[1] if len(mins) > 1 else None
    if split_min is None:
        raise ValueError("no second start_schedule entry: give split_min")
    legs = [x for x in task_legs(run, events) if x["slot"] not in set(exclude)]
    A = [x for x in legs if x["launch"] < split_min * 60]
    B = [x for x in legs if x["launch"] >= split_min * 60]
    up = _welch_log_ratio([x["startup_s"] for x in A if x["startup_s"]], [x["startup_s"] for x in B if x["startup_s"]])
    code = _welch_log_ratio([x["coding_s"] for x in A if x["coding_s"]], [x["coding_s"] for x in B if x["coding_s"]])
    dec, flag = throttle_decision(up, code, tol)
    return dict(split_min=split_min, excluded=list(exclude), tolerance=tol, level=THROTTLE_LEVEL, startup=up, coding=code,
                decision=dec, coding_flag=flag)


def throttle_sweep(derived_runs, tol=THROTTLE_TOL):
    """Rule 1b on the sweep (descriptive): each N_hi window's start-up / coding against all N = 1 windows pooled."""
    lo = [x for run, ev in derived_runs if run["n_workers"] == 1 for x in task_legs(run, ev)]
    res = []
    for run, ev in derived_runs:
        if run["n_workers"] == 1:
            continue
        legs = task_legs(run, ev)
        up = _welch_log_ratio([x["startup_s"] for x in lo if x["startup_s"]], [x["startup_s"] for x in legs if x["startup_s"]])
        code = _welch_log_ratio([x["coding_s"] for x in lo if x["coding_s"]], [x["coding_s"] for x in legs if x["coding_s"]])
        dec, flag = throttle_decision(up, code, tol)
        res.append(dict(run_id=run["run_id"], N=run["n_workers"], K=int(run.get("n_reviewers") or 1), startup=up, coding=code,
                        decision=dec, coding_flag=flag))
    return res


# ---------------------------------------------------------------------------------------------- scoring
V6_ORDER = ("SCALE", "SCALE-nf", "CAP", "COLL", "K1", "CAPFIT", "ESC-N", "ESC", "FAMILY", "UTIL", "THROTTLE", "COLL-m",
            "COLL-k", "LAMBDA", "BOUNCE", "EFFORT")
GRADES_V6 = {"SCALE": (CONFIRMATORY, "primary"), "SCALE-nf": (CONFIRMATORY, "primary"), "CAP": (CONFIRMATORY, SECONDARY),
             "COLL": (CONFIRMATORY, SECONDARY), "K1": (DESCRIPTIVE, None), "CAPFIT": (DESCRIPTIVE, None),
             "ESC-N": (DESCRIPTIVE, None), "ESC": (DESCRIPTIVE, None), "FAMILY": (DESCRIPTIVE, None),
             "UTIL": (DESCRIPTIVE, None), "THROTTLE": (DESCRIPTIVE, None), "COLL-m": (DESCRIPTIVE, None),
             "COLL-k": (DESCRIPTIVE, None), "LAMBDA": (DESCRIPTIVE, None), "BOUNCE": (DESCRIPTIVE, None),
             "EFFORT": (DESCRIPTIVE, None)}


def _res(id_, statement, value, rule, code, note=""):
    g, role = GRADES_V6[id_]
    return dict(id=id_, grade=g, counts_as=g, role=role, statement=statement, value=value, rule=rule, code=code, note=note)


def score_v6(derived, *, cv=CV_OVERDISPERSION, effort_rows=None, raw=None, alpha=ALPHA, beta=BETA, p=P_V6):
    """PLAN-v6 codings. derived: derive.py outputs (summary with n_reviewers). raw (optional): [(run, events)] of the
    same windows, for the in-sweep throttling check."""
    S = [d["summary"] for d in derived]
    sizes = sorted({s["n_workers"] for s in S})
    if len(sizes) < 2:
        raise ValueError(f"expected at least two fleet sizes, got {sizes}")
    nlo, nhi = sizes[0], sizes[-1]
    ks_hi = sorted({K_of(s) for s in S if s["n_workers"] == nhi})
    k_hi, k_lo = max(ks_hi), min(ks_hi)
    R = dict(plan="v6", sizes=sizes, N_low=nlo, N_high=nhi, K_hi=k_hi, K_lo=k_lo, cv=cv, windows=S,
             cells={f"{n}x{k}": sum(1 for s in S if s["n_workers"] == n and K_of(s) == k)
                    for n in sizes for k in sorted({K_of(s) for s in S})},
             constants=dict(alpha=alpha, beta=beta, p=p))
    SW = scale_windows(S, nhi, k_hi)
    R["scale"] = bend_test(SW, cv, "lr_fixed")
    R["scale_sensitivity"] = {m: bend_test(SW, cv, m) for m in ("lr_estcv", "welch")}
    lo_khi = [s for s in SW if s["n_workers"] != nlo or K_of(s) == k_hi]
    R["scale_sensitivity"]["N1_Khi_only"] = bend_test(lo_khi, cv, "lr_fixed") if len({s["n_workers"] for s in lo_khi}) > 1 else None
    flagged_rev = [s["run_id"] for s in SW if s["n_workers"] == nhi and (s["reviewer_util"] >= DESIGN_V6["review_util_alarm"])]
    R["review_flagged"] = flagged_rev
    keep_rev = [s for s in SW if s["run_id"] not in flagged_rev]
    R["scale_sensitivity"]["without_review_flagged"] = (bend_test(keep_rev, cv, "lr_fixed")
                                                         if flagged_rev and len({s["n_workers"] for s in keep_rev}) > 1 else None)
    flagged = [s["run_id"] for s in SW if s.get("supply_flagged")]
    R["supply_flagged"] = flagged
    keep = [s for s in SW if not s.get("supply_flagged")]
    R["scale_nf"] = bend_test(keep, cv, "lr_fixed") if flagged and len({s["n_workers"] for s in keep}) >= 2 else None
    R["cap"] = k_test(S, nhi, k_lo, k_hi, cv) if k_lo != k_hi else None
    ks_lo = sorted({K_of(s) for s in S if s["n_workers"] == nlo})
    R["k1"] = k_test(S, nlo, min(ks_lo), max(ks_lo), cv) if len(ks_lo) > 1 else None
    R["capfit"] = {f"{n}x{k}": cap_fit(S, n, k) for n in sizes for k in sorted({K_of(s) for s in S if s["n_workers"] == n})}
    R["family"] = family_fit(SW, cv, alpha, beta, p)
    apprs = [a for d in derived for a in d["approvals"]]
    prs = [x for d in derived for x in d["prs"]]
    R["escape"] = escape_v5(apprs, n_max=nhi)
    R["collision"] = collision_v5(prs)
    R["util"] = {f"{n}x{k}": utilisation_v5([s for s in S if s["n_workers"] == n and K_of(s) == k])[n]
                 for n in sizes for k in sorted({K_of(s) for s in S if s["n_workers"] == n})}
    for key, u in R["util"].items():
        SS = [s for s in S if f"{s['n_workers']}x{K_of(s)}" == key]
        u["queue_nonempty_share_mean"] = float(np.mean([s["queue_nonempty_share"] for s in SS]))
        u["mean_waiting_depth_mean"] = float(np.mean([s["mean_waiting_depth"] for s in SS]))
    R["per_cell"] = {}
    for key in R["util"]:
        SS = [s for s in S if f"{s['n_workers']}x{K_of(s)}" == key]
        n = SS[0]["n_workers"]
        wh = sum(s["worker_hours"] for s in SS)
        R["per_cell"][key] = dict(
            windows=len(SS), finished=sum(s["finished"] for s in SS), attempts=sum(s["attempts"] for s in SS),
            lam=sum(s["attempts"] for s in SS) / wh if wh else math.nan,
            finished_per_slot_hour=sum(s["finished"] for s in SS) / sum(exposure_hours(s) * n for s in SS),
            completion=(sum(s["finished"] for s in SS) / sum(s["attempts_full"] for s in SS)) if sum(s["attempts_full"] for s in SS) else math.nan,
            timeouts=sum(s.get("session_timeouts", 0) for s in SS),
            startup_min=float(np.nanmean([s.get("startup_min_mean", math.nan) for s in SS])),
            rework_wait_min=float(np.nanmean([s.get("rework_wait_min_mean", math.nan) for s in SS])),
            censored=sum(s["censored"] for s in SS), rework_open=sum(s["rework_open"] for s in SS),
            bounces=v5._pool_bounces(SS))
    R["throttle_sweep"] = throttle_sweep(raw) if raw else None
    if effort_rows is not None:
        R["effort"] = effort_compare(derived, effort_rows)
    R["operating_characteristics"] = V6_OC
    R["outcomes"] = code_outcomes_v6(R)
    return R


def _oc(path, key, default=None):
    d = V6_OC
    for k in path:
        d = (d or {}).get(k)
    return d if d is not None else default


def code_outcomes_v6(R):
    O = []
    nlo, nhi, khi, klo = R["N_low"], R["N_high"], R["K_hi"], R["K_lo"]
    sc = R["scale"]
    code = "N/A" if sc["p"] != sc["p"] else ("BEND" if sc["p"] < ALPHA_TEST else "LINEAR-NOT-REJECTED")
    ci = sc.get("per_agent_ratio_ci") or [math.nan, math.nan]
    so = V6_OC.get("SCALE") or {}
    O.append(_res("SCALE", f"Agent-side scaling: with {khi} reviewers (review not binding), per-agent finished output at "
                  f"N = {nhi} is lower than at N = {nlo}", dict(p=sc["p"], per_agent_ratio=sc["per_agent_ratio"], ci95=ci),
                  f"one-sided NB LR test of gamma < 0 on the N = {nhi}, K = {khi} windows and every N = {nlo} window "
                  "(CV 0.3 fixed); BEND iff p < 0.05", code,
                  f"per-agent ratio {_f(sc['per_agent_ratio'])} (95% {_f(ci[0])}-{_f(ci[1])}), p = {_f(sc['p'], '{:.3g}')}; "
                  f"simulated false alarm {_f(so.get('fpr_linear'))}, power {_f(so.get('power_amdahl'))} (Amdahl) / "
                  f"{_f(so.get('power_usl'))} (USL) / {_f(so.get('power_mild'))} (mild bend)"
                  + (f"; windows with the reviewers busy >= 80%: {', '.join(R['review_flagged'])} (SCALE without them: p = "
                     f"{_f(R['scale_sensitivity']['without_review_flagged']['p'], '{:.3g}')})"
                     if R["review_flagged"] and R["scale_sensitivity"].get("without_review_flagged") else "")))
    if R.get("scale_nf") is not None:
        s2 = R["scale_nf"]
        c2 = "BEND" if s2["p"] < ALPHA_TEST else "LINEAR-NOT-REJECTED"
        n2 = f"without {', '.join(R['supply_flagged'])}: p = {_f(s2['p'], '{:.3g}')}"
    else:
        s2, c2, n2 = None, "N/A", "no window ran out of tasks more than 10 min before its end: identical to SCALE"
    O.append(_res("SCALE-nf", "SCALE without the windows that ran out of tasks more than 10 min before their end",
                  None if s2 is None else dict(p=s2["p"], per_agent_ratio=s2["per_agent_ratio"]), "as SCALE", c2, n2))
    cp = R.get("cap")
    co = V6_OC.get("CAP") or {}
    if cp is None or cp.get("p") != cp.get("p"):
        O.append(_res("CAP", f"The review ceiling: at N = {nhi}, fewer reviewers finish less", None,
                      "needs N_high windows at two reviewer counts", "N/A", "not in this design"))
    else:
        cc = "CAPPED" if cp["p"] < ALPHA_TEST else "NOT-DETECTED"
        cci = cp.get("ratio_ci") or [math.nan, math.nan]
        O.append(_res("CAP", f"The review ceiling: at N = {nhi}, finished output with {klo} reviewer(s) is lower than with {khi}",
                      dict(p=cp["p"], ratio=cp["ratio"], ci95=cci, per_slot_hour=cp.get("per_slot_hour")),
                      f"one-sided NB LR test of delta < 0 in finished ~ theta x slot-hours x exp(delta [K = {klo}]) over the "
                      f"N = {nhi} windows (CV 0.3 fixed); CAPPED iff p < 0.05", cc,
                      f"ratio K = {klo} : {khi} = {_f(cp['ratio'])} (95% {_f(cci[0])}-{_f(cci[1])}), p = {_f(cp['p'], '{:.3g}')}; "
                      f"simulated false alarm {_f(co.get('fpr_fastrev'))} (review never binds), power "
                      f"{_f(co.get('power_linear'))} (linear workers) / {_f(co.get('power_amdahl'))} (Amdahl); "
                      f"under USL workers the model predicts no ceiling (rate {_f(co.get('rate_usl'))})"))
    c = R["collision"]
    c_oc = V6_OC.get("COLL") or {}
    if not c["fit_ok"]:
        cc = "NOT-DETECTED" if c["events"] < v5.MIN_COLLISIONS_V5 else "N/A"
        nc = f"{c['events']} collisions in {c['n']} first merge-queue passes: not modelled, coded as not detected"
    else:
        cc = "DETECTED" if c["p_j"] < ALPHA_TEST else "NOT-DETECTED"
        nc = (f"OR per merge since base {_f(c['or_j'], '{:.3f}')}, one-sided p = {_f(c['p_j'], '{:.3g}')}; p-hat "
              f"{_f(c.get('p_hat'), '{:.4f}')} (95% {_f((c.get('p_ci') or [None])[0], '{:.4f}')}-"
              f"{_f((c.get('p_ci') or [None, None])[1], '{:.4f}')}); {c['events']} collisions in {c['n']} passes")
    nc += (f"; simulated false alarm {_f(c_oc.get('fpr'))}, power " +
           ", ".join(f"{_f(v)} at p = {k}" for k, v in (c_oc.get("power") or {}).items()))
    O.append(_res("COLL", "Collisions force rework: a change's first merge-queue pass fails more often the more other "
                  "changes merged since its base", dict(p=c.get("p_j"), or_j=c.get("or_j"), p_hat=c.get("p_hat"),
                                                        p_ci=c.get("p_ci"), events=c["events"], n=c["n"]),
                  "logistic collided ~ j, one-sided LR p < 0.05 -> DETECTED", cc, nc))
    k1 = R.get("k1")
    if k1 and k1.get("p") == k1.get("p"):
        O.append(_res("K1", f"The same reviewer contrast at N = {nlo} (the model predicts none; with CAP, the 2 x 2 interaction)",
                      dict(p_two_sided=k1.get("p_two_sided"), ratio=k1["ratio"], ci95=k1.get("ratio_ci")), "reported",
                      "REPORTED", f"ratio K = {k1['K_lo']} : {k1['K_hi']} = {_f(k1['ratio'])} (95% "
                      f"{_f((k1.get('ratio_ci') or [None])[0])}-{_f((k1.get('ratio_ci') or [None, None])[1])}), two-sided p = "
                      f"{_f(k1.get('p_two_sided'), '{:.3g}')}"))
    else:
        O.append(_res("K1", f"The reviewer contrast at N = {nlo}", None, "reported", "N/A", "one reviewer count at N = 1"))
    cf = R["capfit"]
    O.append(_res("CAPFIT", "Finished output against the ceiling from the reviewers' own service rate, per cell",
                  cf, "reported: attainment = finished per counted hour / ((1 - b) (1 - b_mq) K 3600 / mean review s)", "REPORTED",
                  "; ".join(f"{k}: {_f(v['finished_per_hour'], '{:.0f}')}/h of a ceiling {_f(v['cap_per_hour'], '{:.0f}')}/h "
                            f"(attainment {_f(v['attainment'])}, reviewers busy {_f(v['reviewer_util_mean'])})"
                            for k, v in cf.items() if v)))
    e = R["escape"]
    eo = V6_OC.get("ESC_N") or {}
    if not e["trend_fit_ok"]:
        ce, ne = "N/A", f"{e['events']} escapes (< {v5.MIN_ESCAPES_V5}) or one size only: not modelled"
    else:
        ce = "RISES" if e["p_trend"] < ALPHA_TEST else "NOT-DETECTED"
        ne = f"OR N = 1 -> {nhi}: {_f(e['or_1_to_Nmax'])}, one-sided p = {_f(e['p_trend'], '{:.3g}')}"
    ne += "; by size: " + ", ".join(f"N = {k}: {v['escaped']}/{v['approvals']} = {_f(v['rate'])}" for k, v in e["by_size"].items())
    ne += f"; simulated power {_f(eo.get('power_2x'))} for a doubling, false alarm {_f(eo.get('fpr'))}"
    O.append(_res("ESC-N", f"The escape rate rises with fleet size (N = {nlo} to {nhi})",
                  dict(p=e["p_trend"], by_size={k: v["rate"] for k, v in e["by_size"].items()}),
                  "logistic escaped ~ (N - 1) / (N_max - 1), one-sided LR p < 0.05; >= 8 escapes", ce, ne))
    O.append(_res("ESC", "The escape rate: approved changes that fail their hidden tests on the approved head",
                  dict(rate=e["rate"], wilson95=e["wilson95"], cluster95=e["cluster95"], n=e["n"]),
                  "estimate with Wilson and task-clustered 95% intervals", "REPORTED",
                  f"{e['events']}/{e['n']} = {_f(e['rate'], '{:.3f}')} (Wilson {_f(e['wilson95'][0], '{:.3f}')}-"
                  f"{_f(e['wilson95'][1], '{:.3f}')})"))
    f = R["family"]
    order = sorted(RIVALS_V5, key=lambda r: -f["ll"][r])
    O.append(_res("FAMILY", "Which rival fits the SCALE windows best (linear, Amdahl, USL, Carnot uncapped), free levels",
                  dict(best=f["best"], best3=f["best3"], ll=f["ll"], binary=f["binary"]), "highest NB likelihood", "REPORTED",
                  "order " + " > ".join(f"{r} ({f['ll'][r]:.2f})" for r in order) + f"; linear vs bending: {f['binary']}"))
    u = R["util"]
    hk = f"{nhi}x{khi}"
    flag = "n/a"
    if hk in u:
        mx = u[hk]["reviewer_util_max"]
        flag = "SATURATING" if mx >= 0.8 else "APPROACHING" if mx >= 0.5 else "FAR-FROM-SATURATION"
    O.append(_res("UTIL", f"Where the limit moves: reviewer and merge-queue utilisation per cell (rule 4 on N = {nhi}, K = {khi})",
                  {k: dict(reviewer_util_mean=v["reviewer_util_mean"], reviewer_util_max=v["reviewer_util_max"],
                           queue_nonempty=v["queue_nonempty_share_mean"], mq_util_max=v["mq_util_max"],
                           review_s_mean=v["review_s_mean"]) for k, v in u.items()},
                  "reported; per-reviewer busy share >= 0.8 in an N_high, K_high window flags it (rule 4)", flag,
                  "; ".join(f"{k}: reviewers busy {_f(v['reviewer_util_mean'])} (max {_f(v['reviewer_util_max'])}), queue non-empty "
                            f"{_f(v['queue_nonempty_share_mean'])}, merge queue {_f(v['mq_util_max'], '{:.3f}')}" for k, v in u.items())))
    ts = R.get("throttle_sweep")
    if ts:
        O.append(_res("THROTTLE", "Rule 1b in the sweep: start-up and coding at N_high against the pooled N = 1 windows",
                      [dict(run_id=x["run_id"], startup=x["startup"]["ratio"], startup_ci=x["startup"]["ci"],
                            coding=x["coding"]["ratio"], decision=x["decision"]) for x in ts],
                      f"ratio of geometric means, Welch 90% interval, tolerance {THROTTLE_TOL}", "REPORTED",
                      "; ".join(f"{x['run_id']}: start-up x{_f(x['startup']['ratio'])} ({_f(x['startup']['ci'][0])}-"
                                f"{_f(x['startup']['ci'][1])}), coding x{_f(x['coding']['ratio'])} -> {x['decision']}"
                                + (" (coding flag)" if x["coding_flag"] else "") for x in ts)))
    else:
        O.append(_res("THROTTLE", "Rule 1b in the sweep", None, "needs the raw logs", "N/A", "not computed"))
    O.append(_res("COLL-m", "H3: collisions come from changes sharing a file (jm, given j)",
                  dict(p=c.get("p_jm"), or_jm=c.get("or_jm")), "logistic collided ~ j + jm, one-sided LR p for jm", "REPORTED",
                  f"OR {_f(c.get('or_jm'), '{:.3f}')}, p = {_f(c.get('p_jm'), '{:.3g}')}" if c["fit_ok"] else "not modelled"))
    O.append(_res("COLL-k", "Study 1's H1 under control: collisions vs k and m", dict(p=c.get("p_k")),
                  "logistic collided ~ k + m, one-sided LR p for k", "REPORTED",
                  f"p = {_f(c.get('p_k'), '{:.3g}')}" if c["fit_ok"] else "not modelled"))
    pc = R["per_cell"]
    O.append(_res("LAMBDA", "Per-cell lambda (first submissions per slot-hour), completion, timeouts, start-up, rework wait",
                  pc, "reported", "REPORTED",
                  "; ".join(f"{k}: lambda {_f(v['lam'])}, completion {_f(v['completion'])}, start-up {_f(v['startup_min'])} min, "
                            f"rework wait {_f(v['rework_wait_min'])} min" for k, v in pc.items())))
    O.append(_res("BOUNCE", "Bounce causes by cell", {k: v["bounces"] for k, v in pc.items()}, "reported", "REPORTED",
                  "; ".join(f"{k}: " + ", ".join(f"{c_}: {n_}" for c_, n_ in v["bounces"]["causes"].items()) for k, v in pc.items())))
    ef = R.get("effort")
    if ef is None:
        O.append(_res("EFFORT", "Escapes by review effort (post-hoc max-effort re-review)", None, "descriptive; needs --effort-log",
                      "N/A", "no effort log given"))
    else:
        O.append(_res("EFFORT", "Escapes by review effort (post-hoc max-effort re-review)", ef["matched"], "descriptive", "REPORTED",
                      "; ".join(f"{eff}: {v['heads']} heads, agreement {_f(v['agreement'])}" for eff, v in ef["matched"].items())))
    rank = {k: i for i, k in enumerate(V6_ORDER)}
    return sorted(O, key=lambda o: rank.get(o["id"], len(rank)))


def _gtxt(o):
    return f"{o['grade']}" + (f", {o['role']}" if o.get("role") and o["role"] != SECONDARY else
                              (", secondary" if o.get("role") == SECONDARY else ""))


def render_md_v6(R):
    nlo, nhi, khi, klo = R["N_low"], R["N_high"], R["K_hi"], R["K_lo"]
    L = ["# Fleet sweep (study 2, PLAN-v6): results draft", "",
         "Generated by `analysis/score.py` (PLAN-v6; the default `--plan v6`). Every result is labelled **[confirmatory]** or "
         "**[descriptive]** as fixed before the sweep (PLAN-v6 section 5). The design crosses fleet size N with the number of "
         "parallel reviewers K: SCALE asks whether the workers' own output bends when review does not bind (K = "
         f"{khi}); CAP asks whether one reviewer caps finished work at N = {nhi} (the paper's min(lambda X, V/h)).", ""]
    L.append("Cells: " + ", ".join(f"N x K = {k}: {v} windows" for k, v in R["cells"].items() if v) +
             f". Over-dispersion CV fixed at {R['cv']}.")
    L += ["", "## Summary of pre-registered results\n", "| ID | Grade | Statement | Code | Detail |", "|---|---|---|---|---|"]
    for o in R["outcomes"]:
        L.append(f"| {o['id']} | {_gtxt(o)} | {o['statement']} | **{o['code']}** | {o['rule']}. {o['note']} |")
    L.append("\n## Windows\n")
    L.append("| Window | N | K | slot-hours | finished | per slot-hour | reviewers busy | queue non-empty | attempts | censored |")
    L.append("|---|---|---|---|---|---|---|---|---|---|")
    for s in R["windows"]:
        h = exposure_hours(s) * s["n_workers"]
        L.append(f"| {s['run_id']} | {s['n_workers']} | {K_of(s)} | {h:.2f} | {s['finished']} | {s['finished'] / h if h else math.nan:.2f} | "
                 f"{_f(s['reviewer_util'])} | {_f(s['queue_nonempty_share'])} | {s['attempts']} | {s['censored']} |")
    sc = R["scale"]
    L.append(f"\nSCALE sensitivity [descriptive]: " + "; ".join(
        f"`{m}` p = {_f(v['p'], '{:.3g}')}" for m, v in R["scale_sensitivity"].items() if v) + ".")
    f = R["family"]
    L.append("\nRival predictions per SCALE window (free levels) [descriptive]:\n")
    L.append("| Window | N | finished | " + " | ".join(RIVAL_LABEL_V5[r] for r in RIVALS_V5) + " |")
    L.append("|---|---|---|" + "---|" * len(RIVALS_V5))
    for w in f["windows"]:
        L.append(f"| {w['run_id']} | {w['N']} | {w['finished']} | " + " | ".join(f"{w['pred_' + r]:.1f}" for r in RIVALS_V5) + " |")
    L.append("\n## Per cell [descriptive]\n")
    L.append("| cell N x K | windows | lambda /slot-h | finished /slot-h | completion | start-up min | rework wait min | reviewers busy | "
             "review s | ceiling /h | attainment |")
    L.append("|---|---|---|---|---|---|---|---|---|---|---|")
    for k, v in R["per_cell"].items():
        u = R["util"][k]
        cf = R["capfit"].get(k) or {}
        L.append(f"| {k} | {v['windows']} | {_f(v['lam'])} | {_f(v['finished_per_slot_hour'])} | {_f(v['completion'])} | "
                 f"{_f(v['startup_min'])} | {_f(v['rework_wait_min'])} | {_f(u['reviewer_util_mean'])} | {_f(u['review_s_mean'], '{:.0f}')} | "
                 f"{_f(cf.get('cap_per_hour'), '{:.0f}')} | {_f(cf.get('attainment'))} |")
    L.append("")
    return "\n".join(L) + "\n"


# ---------------------------------------------------------------------------------------------- predictions
def predictions_v6(cells, window_min=60.0, warmup_min=10.0, lam=14.0, completion=0.8, b_review=0.39, b_mq=0.11,
                   review_s=21.0, alpha=ALPHA, beta=BETA, p=P_V6):
    """Per rival and cell: the paper's U(N) = (1 - r) min(lambda X(N), cap), cap = K x 3600 / review_s reviews per
    hour, of which (1 - b_review)(1 - b_mq) merge. Demand in reviews per hour = first submissions / (1 - b_review)."""
    h = (window_min - warmup_min) / 60.0
    rows = []
    for r in RIVALS_V5:
        for (n, k), w in sorted(cells_of(cells).items()):
            g = v5.shape(r, n, alpha, beta, p)
            first_h = lam * {"linear": X_linear(n), "amdahl": X_amdahl(n, alpha), "usl": X_usl(n, alpha, beta),
                             "carnot": X_usl(n, alpha, beta)}[r]
            demand = first_h / max(1 - b_review, 1e-9)
            cap_rev = k * 3600.0 / review_s
            util = demand / cap_rev
            unc = lam * completion * g * h
            capped = cap_rev * (1 - b_review) * (1 - b_mq) * h
            mu = min(unc, capped)
            rows.append(dict(rival=r, N=n, K=k, windows=w, ratio_to_1=g, uncapped=unc, ceiling=capped, finished=mu,
                             finished_95=list(nb_interval(mu, CV_OVERDISPERSION)), reviews_demand_per_hour=demand,
                             reviewer_capacity_per_hour=cap_rev, reviewer_util=float(min(util, 1.0)), binds=bool(util >= 1.0),
                             finished_total=mu * w,
                             finished_total_95=list(pooled_interval([mu] * w, CV_OVERDISPERSION)) if w else None))
    return rows


def oc_statement_v6():
    oc = V6_OC
    s, c, cl = oc.get("SCALE") or {}, oc.get("CAP") or {}, oc.get("COLL") or {}
    L = [f"### Operating characteristics (simulated; {oc['source']})", ""]
    L.append(f"- **Confirmatory, primary (SCALE, K = 3):** false alarm {_f(s.get('fpr_linear'))} under linear workers (window CV 0.3; "
             f"{_f(s.get('fpr_linear_cv05'))} at CV 0.5); power {_f(s.get('power_amdahl'))} (Amdahl), {_f(s.get('power_usl'))} (USL), "
             f"{_f(s.get('power_mild'))} (mild bend).")
    L.append(f"- **Confirmatory, secondary (CAP, N = 12, K = 1 vs 3):** false alarm {_f(c.get('fpr_fastrev'))} when review never "
             f"binds; power {_f(c.get('power_linear'))} (linear workers), {_f(c.get('power_amdahl'))} (Amdahl); rate "
             f"{_f(c.get('rate_usl'))} under USL workers, where the model predicts no ceiling.")
    L.append(f"- **Confirmatory, secondary (COLL):** false alarm {_f(cl.get('fpr'))}; power " +
             ", ".join(f"{_f(v)} at p = {k}" for k, v in (cl.get("power") or {}).items()) + ".")
    L.append("- **Descriptive:** K1 (the K contrast at N = 1), CAPFIT, ESC-N, ESC, FAMILY, UTIL, THROTTLE (rule 1b), COLL-m, "
             "COLL-k, LAMBDA, BOUNCE, EFFORT.")
    return L
