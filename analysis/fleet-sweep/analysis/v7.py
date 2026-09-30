#!/usr/bin/env python3
"""PLAN-v7 analysis codings: the v6 study under a hard budget counted in cloud tasks (PLAN-v7 section 5).

Imported by score.py (the default `--plan v7`), predict.py (the default path) and design-search/{dsim7,oc_v7}.py, so
the design search, the self-test and the real analysis run one implementation. The tests are the v6 codings
(v6.py: SCALE, CAP, COLL and the descriptive results), re-graded for the v7 design and printed with the v7
operating characteristics; abort rule 1 is rebuilt for one session per slot. Nothing here uses the network.

Why v7 (PLAN-v7 section 0): the harness now launches workers with `claude --cloud` (routines billed plan usage), which
draws cloud-session credits, and keeps one session per slot. T0d and T0e (2026-09-29) measured about $0.45 of credits
per task (a first attempt with its rework), and $186 is spendable above the $50 floor: about 400 tasks in all, T1b
included. At about 26 hand-outs per slot-hour v6's design would use about 2,300 tasks. v7 keeps N = 12 but runs
**15-minute windows** for every cell (one length, so end effects cancel between N = 1 and N = 12), raises the
non-binding reviewer count to K = 5 (lambda is now about 23, so three reviewers would bind at N = 12), adds one N = 12,
K = 1 window for CAP only in the 500-task design, and replaces T1b by a short slot-mode trial whose rule 1 reads
follow-up start-ups only. Three nested designs (300, 400, 500 tasks); rule 2 picks one after T1b.

Tests (grades fixed in advance by the v7 design search, DESIGN-SEARCH-v7.md; OPERATING-CHARACTERISTICS-v7.md):

  SCALE    As v6: per-agent finished output at N_hi (K_hi windows) against every N = 1 window, one-sided NB LR test of
           gamma < 0 with CV 0.3 fixed. BEND if p < 0.05. Confirmatory, primary.
  CAP      As v6 (N_hi, K = 1 against K_hi). Confirmatory, secondary, in the 500-task design; the 300- and 400-task
           designs have no K = 1 window at N_hi, so CAP codes N/A there.
  COLL     As v6. Confirmatory, secondary.
  K1, CAPFIT, ESC-N, ESC, FAMILY, UTIL, THROTTLE (rule 1b), COLL-m, COLL-k, LAMBDA, BOUNCE, EFFORT: descriptive, as v6.
  COST     Descriptive: tasks handed out, sessions launched and reviews per window and in all (the budget ledger).
  RULE 1   (T1b, slot mode) `throttle_v7`: **follow-up start-up** (session_message kind=task -> branch first pushed),
           twelve slots against one, ratio of geometric means with a Welch 90% interval on the log scale, tolerance
           1.25. Launch start-ups (provisioning + clone) and the mixed start-up are reported beside it.
"""
from __future__ import annotations

import math

import numpy as np

from common import ALPHA, BETA, CONFIRMATORY, CV_OVERDISPERSION, DESCRIPTIVE, X_amdahl, X_linear, X_usl, nb_interval, pooled_interval
import v5
import v6
from v5 import RIVALS_V5, SECONDARY, _f, bend_test, collision_v5, effort_compare, escape_v5, exposure_hours, family_fit, utilisation_v5
from v6 import (K_of, THROTTLE_LEVEL, THROTTLE_TOL, _welch_log_ratio, cap_fit, cells_of, k_test, scale_windows, task_legs,
                v6_order)

ALPHA_TEST = 0.05
P_V7 = v6.P_V6

# ---------------------------------------------------------------------------------------------- budget
USD_PER_TASK = dict(low=0.30, central=0.45, high=0.60)   # T0d $8 / 16 tasks, T0e $3 / 7 tasks (whole-dollar meter)
BALANCE_USD = 236.0
FLOOR_USD = 50.0
SPENDABLE_USD = BALANCE_USD - FLOOR_USD                 # $186: about 413 tasks at $0.45, 310 at $0.60, 620 at $0.30
TASKS_PER_SLOT_HOUR = 26.5                              # simulated hand-outs per slot-hour at K_hi (oc_v7.json; lambda about 23-24)
TASKS_PER_SLOT_HOUR_K1 = 32.0                           # at N_hi with one reviewer: rework is held up, slots take new tasks


# ---------------------------------------------------------------------------------------------- rule 1 (slot mode)
def throttle_v7(run, events, split_min=None, exclude=(), tol=THROTTLE_TOL, measure="followup"):
    """Rule 1 on a slot-mode T1b log: one slot, then twelve. measure: "followup" (the rule: follow-up start-ups only,
    session_message kind=task -> first push, like with like), "launch" (launch start-ups only: provisioning + clone)
    or "mixed" (every hand-out, as v6). All three are returned; the decision uses `measure`."""
    if split_min is None:
        mins = [float(m.group(1)) for e in events if e["type"] == "note" for m in [v6.SCHED_RE.match(e.get("text", ""))] if m]
        split_min = sorted(mins)[1] if len(mins) > 1 else None
    if split_min is None:
        raise ValueError("no second start_schedule entry: give split_min")
    rows = [x for x in task_legs_v7(run, events) if x["slot"] not in set(exclude)]
    A = [x for x in rows if x["launch"] < split_min * 60]
    B = [x for x in rows if x["launch"] >= split_min * 60]

    def ratio(sel, key="startup_s"):
        return _welch_log_ratio([x[key] for x in A if x[key] and sel(x)], [x[key] for x in B if x[key] and sel(x)])
    up = dict(followup=ratio(lambda x: x["followup"]), launch=ratio(lambda x: not x["followup"]), mixed=ratio(lambda x: True))
    code = ratio(lambda x: True, "coding_s")
    dec, flag = v6.throttle_decision(up[measure], code, tol)
    lf = up["launch"]["ci"][0]
    launch_flag = bool(lf == lf and lf > tol)
    return dict(split_min=split_min, excluded=list(exclude), tolerance=tol, level=THROTTLE_LEVEL, measure=measure,
                startup=up[measure], startup_by=up, coding=code, decision=dec, coding_flag=flag, launch_flag=launch_flag,
                n_followup=(sum(x["followup"] for x in A), sum(x["followup"] for x in B)),
                n_launch=(sum(not x["followup"] for x in A), sum(not x["followup"] for x in B)))


CODING_CUTOFF_S = 300.0   # rule 1: a coding leg counts only for hand-outs at least 5 min before window end (both phases)


def task_legs_v7(run, events, coding_cutoff_s=CODING_CUTOFF_S):
    """Per task: slot, hand-out time, kind (followup: session_message kind=task; else session_launch), start-up s
    (hand-out -> claim; a routine launch from its run_once_at, as v6) and coding s (claim -> first READY). A coding leg
    counts only if the task was handed out at least `coding_cutoff_s` before window end (and its READY came by then):
    without the cut-off, slow tasks handed out late in the short twelve-slot phase are dropped (READY after window
    end) and the phase's geometric mean is pulled down, masking a coding slowdown (review of PLAN-v7)."""
    t0 = v6.parse_t(run["window_start"])
    we = v6.parse_t(run["window_end"]) - t0
    rows, once = {}, {}
    for e in events:
        t = v6.parse_t(e["t"]) - t0
        ty = e["type"]
        if ty == "note":
            m = v6.LEAD_RE.match(e.get("text", ""))
            if m:
                once.setdefault(m.group(2), v6.parse_t(m.group(3)) - t0)
        elif ty == "session_launch" or (ty == "session_message" and e.get("kind") == "task"):
            r = rows.setdefault(e["task"], {})
            if "launch" not in r:
                r.update(launch=t, slot=e["slot"], followup=ty == "session_message")
        elif ty == "claim":
            rows.setdefault(e["task"], {}).setdefault("claim", t)
        elif ty == "submit" and e["attempt_no"] == 1:
            rows.setdefault(e["task"], {}).setdefault("ready", t)
    out = []
    for task, r in rows.items():
        if "launch" not in r or "claim" not in r:
            continue
        up = r["claim"] - (r["launch"] if r["followup"] else max(r["launch"], once.get(task, r["launch"])))
        code = (r["ready"] - r["claim"] if "ready" in r and r["ready"] <= we and r["launch"] <= we - coding_cutoff_s
                else None)
        out.append(dict(task=task, slot=r["slot"], launch=r["launch"], followup=r["followup"],
                        startup_s=up if up > 0 else None, coding_s=code))
    return out


def throttle_sweep_v7(derived_runs, tol=THROTTLE_TOL):
    """Rule 1b on the sweep (descriptive): each N_hi window's follow-up start-up and coding against all N = 1 windows
    pooled (follow-ups only, as rule 1)."""
    lo = [x for run, ev in derived_runs if run["n_workers"] == 1 for x in task_legs_v7(run, ev)]
    res = []
    for run, ev in derived_runs:
        if run["n_workers"] == 1:
            continue
        legs = task_legs_v7(run, ev)
        up = _welch_log_ratio([x["startup_s"] for x in lo if x["startup_s"] and x["followup"]],
                              [x["startup_s"] for x in legs if x["startup_s"] and x["followup"]])
        code = _welch_log_ratio([x["coding_s"] for x in lo if x["coding_s"]], [x["coding_s"] for x in legs if x["coding_s"]])
        dec, flag = v6.throttle_decision(up, code, tol)
        res.append(dict(run_id=run["run_id"], N=run["n_workers"], K=int(run.get("n_reviewers") or 1), startup=up, coding=code,
                        decision=dec, coding_flag=flag))
    return res


def cost_ledger(raw):
    """Tasks handed out (session_launch + session_message kind=task: each a first attempt with its rework, the budget
    unit), sessions launched and reviews, per window."""
    rows = []
    for run, ev in raw:
        tasks = sum(1 for e in ev if e["type"] == "session_launch" or (e["type"] == "session_message" and e.get("kind") == "task"))
        rows.append(dict(run_id=run["run_id"], N=run["n_workers"], K=int(run.get("n_reviewers") or 1), tasks=tasks,
                         sessions=sum(1 for e in ev if e["type"] == "session_launch"),
                         reviews=sum(1 for e in ev if e["type"] == "review_end"),
                         meter=[e.get("credits_left_usd") for e in ev if e["type"] == "meter"]))
    return rows


# ---------------------------------------------------------------------------------------------- the designs
# PLAN-v7 section 5. cells: (N, K) -> windows; every window 15 min (3 min warm-up, 10 min grace). The design run is
# chosen after T1b from the measured cost per task (abort rule 2): DESIGNS_V7["400"] is the recommended design.
# Filled from design-search/oc_v7.json (tasks: simulated hand-outs, "measured" workers; reviews likewise).
# After the review of PLAN-v7: T1b is 30 + 15 min (its twelve-slot phase includes the first concurrent session
# retirement); every N = 1 window runs at K = 5 in the 300- and 400-task designs (the K contrast at N = 1 predicts
# nothing and would make half the N = 1 windows differ in protocol from the N = 12 arm); only the 500-task design keeps
# the 2 x 2 (6 + 6 at N = 1, and one N = 12, K = 1 window for CAP).
T1B_V7 = dict(one_slot_min=30.0, twelve_slot_min=15.0, K=5, tasks=87, tasks_p90=120, reviews=130)
DESIGNS_V7 = {
    "300": dict(cells={(1, 5): 10, (12, 5): 2}, window_min=15.0, warmup_min=3.0, grace_min=10.0),
    "400": dict(cells={(1, 5): 12, (12, 5): 3}, window_min=15.0, warmup_min=3.0, grace_min=10.0),
    "500": dict(cells={(1, 1): 6, (1, 5): 6, (12, 5): 3, (12, 1): 1}, window_min=15.0, warmup_min=3.0, grace_min=10.0),
}
DESIGN_V7 = dict(DESIGNS_V7["400"], name="400", N_hi=12, K_hi=5, K_lo=1, t1b=T1B_V7, designs=DESIGNS_V7,
                 balance_usd=BALANCE_USD, balance_floor_usd=FLOOR_USD, usd_per_task=USD_PER_TASK, cv=CV_OVERDISPERSION,
                 alpha=ALPHA, beta=BETA, p=P_V7, lambda_floor=10.0, review_util_alarm=0.8, mq_time_alarm_s=30.0,
                 throttle_tol=THROTTLE_TOL, tasks_per_session=4, messages_per_session=6, task_timeout_min=10.0,
                 task_budget_min=5.0, poll_interval_s=2.0, max_downtime_min=3.0, plan_usage_headroom=0.30,
                 meter_read_delay_min=10.0, days=2)


def window_tasks(n, k, window_min, n_hi=12, tph=TASKS_PER_SLOT_HOUR, tph_k1=TASKS_PER_SLOT_HOUR_K1):
    """Expected hand-outs in one window (warm-up included): N x window hours x hand-outs per slot-hour (more at N_hi
    with one reviewer, where rework is held up in the review queue and the slots take new tasks instead)."""
    return n * window_min / 60.0 * (tph_k1 if (n > 1 and k == 1) else tph)


def design_tasks(cells, window_min, t1b_tasks=None, **kw):
    t1b = T1B_V7["tasks"] if t1b_tasks is None else t1b_tasks
    sw = sum(w * window_tasks(n, k, window_min, **kw) for (n, k), w in cells_of(cells).items())
    return dict(sweep_tasks=sw, t1b_tasks=t1b, total_tasks=sw + t1b,
                usd={k: (sw + t1b) * v for k, v in USD_PER_TASK.items()})


def usd_per_task_t1b(meter_before_t1b, meter_after_t1b, t1b_tasks):
    """Rule 2's one formula: c = (meter immediately before T1b's first launch - meter 10 min after T1b's grace end) /
    T1b's tasks (hand-outs). No correction for the whole-dollar meter: two readings are each within $1, so c is known to
    about +-$2 / tasks (+-$0.023 at 87 tasks)."""
    return (meter_before_t1b - meter_after_t1b) / max(t1b_tasks, 1)


def choose_design(usd_per_task, balance=BALANCE_USD, floor=FLOOR_USD, spent_t1b=0.0, tasks_per_slot_hour=None):
    """Abort rule 2 after T1b: the largest design whose predicted sweep cost at the measured cost per task c
    (`usd_per_task_t1b`) and hand-outs per slot-hour h (T1b's twelve-slot phase, if measured) leaves at least the
    floor: sweep tasks x c <= balance - floor, with balance = the meter after T1b (or the balance before it less
    spent_t1b). Returns the design name or None (stop and report T1b)."""
    tph = tasks_per_slot_hour or TASKS_PER_SLOT_HOUR
    room = balance - spent_t1b - floor
    for name in ("500", "400", "300"):
        d = DESIGNS_V7[name]
        sw = design_tasks(d["cells"], d["window_min"], t1b_tasks=0, tph=tph, tph_k1=tph * TASKS_PER_SLOT_HOUR_K1 / TASKS_PER_SLOT_HOUR)
        if sw["sweep_tasks"] * usd_per_task <= room:
            return name
    return None


def rule2_before_window(meter_before_t1b, meter_now, tasks_since_t1b, remaining_cells, window_min=15.0, floor=FLOOR_USD,
                        tasks_per_slot_hour=None):
    """Rule 2 before every sweep window: the running cost per task is cumulative, c = (meter before T1b - meter now) /
    (every hand-out since T1b began), never a per-window delta of a few dollars; the rest of the design must cost
    <= meter now - floor at c. If not, windows are dropped in the pre-registered order (N = 1 beyond eight,
    alternately by K; then the N_hi, K = 1 window; then N_hi, K_hi windows beyond two) until it fits. Returns
    dict(c, need, room, fits, drop=[(N, K), ...], cells=the cells left)."""
    tph = tasks_per_slot_hour or TASKS_PER_SLOT_HOUR
    c = (meter_before_t1b - meter_now) / max(tasks_since_t1b, 1)
    cells = dict(cells_of(remaining_cells))
    room = meter_now - floor

    def need(cc):
        return design_tasks(cc, window_min, t1b_tasks=0, tph=tph,
                            tph_k1=tph * TASKS_PER_SLOT_HOUR_K1 / TASKS_PER_SLOT_HOUR)["sweep_tasks"] * c
    drop = []
    k_lo_turn = [5, 1]
    while need(cells) > room:
        n1 = sum(w for (n, k), w in cells.items() if n == 1)
        if n1 > 8:
            ks = [k for k in k_lo_turn if cells.get((1, k), 0) > 0]
            k = ks[0]
            k_lo_turn.reverse()
            cells[(1, k)] -= 1
            drop.append((1, k))
        elif cells.get((12, 1), 0) > 0:
            cells[(12, 1)] -= 1
            drop.append((12, 1))
        elif cells.get((12, 5), 0) > 2:
            cells[(12, 5)] -= 1
            drop.append((12, 5))
        else:
            break
    cells = {k: w for k, w in cells.items() if w > 0}
    return dict(c=c, need=need(cells), room=room, fits=need(cells) <= room, drop=drop, cells=cells)


# Simulated operating characteristics (design-search/oc_v7.py, OPERATING-CHARACTERISTICS-v7.md), filled from
# oc_v7.json by hand; printed by predict.py and score.py next to the results, never recomputed by them.
V7_OC = dict(
    source="OPERATING-CHARACTERISTICS-v7.md, design-search/oc_v7.py, 1000 freshly simulated studies per cell (2026-09-30, "
           "after the review: N = 1 at K = 5, T1b 30 + 15, sessions retired after 4 tasks or 6 messages)",
    design="400 tasks: N = 1 x 12 at K = 5, N = 12 x 3 at K = 5, 15 min each (3 min warm-up); 300: N = 1 x 10, N = 12 x 2, "
           "all K = 5; 500: N = 1 x 6 at K = 1 and 6 at K = 5, N = 12 x 3 at K = 5 and one at K = 1",
    tasks=dict(t1b=87, t1b_p90=120, sweep={"300": 225, "400": 317, "500": 412}, sweep_p90={"300": 266, "400": 367, "500": 474},
               total={"300": 312, "400": 404, "500": 499}, fits_up_to_usd_per_task={"300": 0.59, "400": 0.46, "500": 0.37}),
    reviews=dict(t1b=135, sweep={"300": 325, "400": 458, "500": 522}, per_window=dict(n1=9.5, n12k5=115, n12k1=64)),
    SCALE=dict(fpr_linear=0.029, fpr_measured=0.038, power_amdahl=0.89, power_usl=1.00, power_carnot=1.00, power_mild=0.26,
               mde_ratio_80=0.55, fpr_linear_cv015=0.001, fpr_linear_cv05=0.10, power_amdahl_cv05=0.82, lam20_fpr=0.024,
               lam20_power_amdahl=0.89, fpr_contention_0_1=0.023, fpr_contention_0_2=0.035, power_amdahl_contention=0.91,
               fpr_slowrev=0.033, bend_under_coding_throttle_1_3=0.19, fpr_stall=0.029, fpr_stall_0_1=0.023,
               power_amdahl_stall_0_1=0.60, fpr_task_reuse=0.027, fpr_tps8=0.026, fpr_tps8_no_msg_limit=0.037,
               fpr_shared_context=0.017, fpr_upsd=0.028, fpr_welch=0.083, fpr_estcv=0.085,
               d300=dict(fpr=0.026, power_amdahl=0.78, power_usl=1.00, power_mild=0.19, fpr_stall_0_1=0.028),
               d500=dict(fpr=0.028, power_amdahl=0.89, power_usl=1.00, power_mild=0.23)),
    CAP=dict(design="500 only", fpr_fastrev=0.036, power_linear=0.52, power_measured=0.58, rate_usl=0.12, rate_carnot=0.12,
             rate_amdahl=0.060, rate_mild=0.124, power_linear_cv05=0.56, power_contention_0_1=0.50, power_lam20=0.22,
             ratio_linear=0.51, ratio_measured=0.48),
    COLL=dict(fpr=0.001, fpr_linear=0.003, power={"0.0035": 0.05, "0.0075": 0.20, "0.015": 0.46},
              power_linear={"0.0035": 0.45, "0.0075": 0.76, "0.015": 0.96}, p_ci_coverage=0.96,
              d300=dict(power_linear_0075=0.64, power_0075=0.09), d500=dict(power_linear_0075=0.85, power_0075=0.25)),
    ESC_N=dict(fpr=0.032, fpr_measured=0.059, power_2x=0.30, d300_power_2x=0.20, d500_power_2x=0.31),
    ESC=dict(rate=0.032, halfwidth_median=0.022, approvals_median=265),
    FAMILY=dict(correct=dict(linear=0.96, amdahl=0.79, usl=0.32, carnot=0.64), binary_correct=dict(linear=0.96, amdahl=0.94)),
    UTIL=dict(review_util_12x5=dict(linear=0.61, measured=0.62, amdahl=0.28, usl=0.14, contention_0_1=0.80, contention_0_2=0.90),
              review_util_12x1=dict(linear=1.00, amdahl=0.92, usl=0.66), flag_rule4_12x5_linear=0.41,
              flag_rule4_12x5_contention_0_1=0.89),
    THROTTLE=dict(t1b="30 + 15 min", measure="follow-up start-up", poll_s=2,
                  # pre-registered (the start-up log-sd 0.25 sensitivity: T0d / T0e's 0.02-0.04 were the 15-s poll's
                  # quantisation, not the sessions' spread)
                  clear_no_throttle=0.75, stop_no_throttle=0.000, stop_1_5=0.54, stop_2=0.985, ci_factor_median=1.17,
                  stop_drag=0.000, clear_amdahl=0.71, launch_flag_launch_only_2=0.78,
                  # at the tight spread the simulator was calibrated with (log-sd 0.05), for comparison only
                  tight=dict(clear_no_throttle=1.00, stop_1_25=0.04, stop_1_5=1.00, stop_2=1.00, ci_factor_median=1.03,
                             launch_flag_launch_only_2=1.00),
                  coding_flag=dict(coding_only_1_5=0.34, amdahl=0.95, usl=0.99, none=0.00),
                  stall_0_1=dict(clear=0.95, stop=0.000), mixed_clear_no_throttle=0.78, mixed_clear_usl=0.36,
                  upsd_0_25=dict(clear=0.75, stop_1_5=0.54, stop_2=0.985)),
    K1=dict(flagged=0.011, design="500 only"),
)

V7_ORDER = ("SCALE", "SCALE-nf", "CAP", "COLL", "K1", "CAPFIT", "ESC-N", "ESC", "FAMILY", "UTIL", "THROTTLE", "COST",
            "COLL-m", "COLL-k", "LAMBDA", "BOUNCE", "EFFORT")
GRADES_V7 = dict(v6.GRADES_V6, COST=(DESCRIPTIVE, None))


# ---------------------------------------------------------------------------------------------- scoring
def score_v7(derived, *, cv=CV_OVERDISPERSION, effort_rows=None, raw=None, alpha=ALPHA, beta=BETA, p=P_V7):
    """PLAN-v7 codings: the v6 codings (v6.score_v6) re-graded and printed with the v7 operating characteristics,
    rule 1b on follow-up start-ups, and the task ledger (COST)."""
    R = v6.score_v6(derived, cv=cv, effort_rows=effort_rows, raw=None, alpha=alpha, beta=beta, p=p)
    R["plan"] = "v7"
    R["throttle_sweep"] = throttle_sweep_v7(raw) if raw else None
    R["cost"] = cost_ledger(raw) if raw else None
    R["operating_characteristics"] = V7_OC
    O = v6.code_outcomes_v6(R, oc=V7_OC, grades=GRADES_V7, order=V7_ORDER)
    c = R["cost"]
    if c:
        tot = sum(x["tasks"] for x in c)
        O.append(v6._res("COST", "The budget ledger: tasks handed out (first attempts with their rework), sessions and reviews",
                         dict(windows=c, tasks=tot, sessions=sum(x["sessions"] for x in c), reviews=sum(x["reviews"] for x in c)),
                         "reported; about $0.45 of cloud credits per task (T0d / T0e)", "REPORTED",
                         f"{tot} tasks, {sum(x['sessions'] for x in c)} sessions, {sum(x['reviews'] for x in c)} reviews over "
                         f"{len(c)} windows (about ${tot * USD_PER_TASK['central']:.0f} at $0.45 per task)", grades=GRADES_V7))
    else:
        O.append(v6._res("COST", "The budget ledger", None, "needs the raw logs", "N/A", "not computed", grades=GRADES_V7))
    rank = {k: i for i, k in enumerate(V7_ORDER)}
    R["outcomes"] = sorted(O, key=lambda o: rank.get(o["id"], len(rank)))
    return R


def render_md_v7(R):
    md = v6.render_md_v6(R)
    md = md.replace("# Fleet sweep (study 2, PLAN-v6): results draft", "# Fleet sweep (study 2, PLAN-v7): results draft")
    md = md.replace("Generated by `analysis/score.py` (PLAN-v6; the default `--plan v6`)",
                    "Generated by `analysis/score.py` (PLAN-v7; the default `--plan v7`)")
    md = md.replace("as fixed before the sweep (PLAN-v6 section 5)", "as fixed before the sweep (PLAN-v7 section 5)")
    if R.get("cost"):
        L = ["\n## Budget ledger [descriptive]\n", "| Window | N | K | tasks | sessions | reviews |", "|---|---|---|---|---|---|"]
        for x in R["cost"]:
            L.append(f"| {x['run_id']} | {x['N']} | {x['K']} | {x['tasks']} | {x['sessions']} | {x['reviews']} |")
        md += "\n".join(L) + "\n"
    return md


# ---------------------------------------------------------------------------------------------- predictions
def predictions_v7(cells, window_min=15.0, warmup_min=3.0, lam=23.0, completion=0.88, b_review=0.41, b_mq=0.11,
                   review_s=21.6, alpha=ALPHA, beta=BETA, p=P_V7):
    """As v6.predictions_v6 (U = (1 - r) min(lambda X(N), K x 3600 / review_s)), at the v7 rates."""
    return v6.predictions_v6(cells, window_min, warmup_min, lam, completion, b_review, b_mq, review_s, alpha, beta, p)


def reviews_estimate(cells, window_min, lam=23.0, b_review=0.41, review_s=21.6, grace_min=10.0):
    """Opus reviews per window and in all (the owner's Max plan, not credits): demand lambda N / (1 - b) per hour over
    the window, capped at K x 3600 / review_s per hour over window + grace."""
    out = {}
    for (n, k), w in cells_of(cells).items():
        demand = lam * n / max(1 - b_review, 1e-9) * window_min / 60.0
        cap = k * 3600.0 / review_s * (window_min + grace_min) / 60.0
        out[(n, k)] = dict(windows=w, per_window=min(demand, cap), total=w * min(demand, cap))
    return out


def oc_statement_v7():
    oc = V7_OC
    s, c, cl = oc.get("SCALE") or {}, oc.get("CAP") or {}, oc.get("COLL") or {}
    L = [f"### Operating characteristics (simulated; {oc['source']})", ""]
    L.append(f"- **Confirmatory, primary (SCALE, N = 12, K = {DESIGN_V7['K_hi']} vs N = 1):** false alarm {_f(s.get('fpr_linear'))} under linear "
             f"workers (window CV 0.3; {_f(s.get('fpr_linear_cv05'))} at CV 0.5); power {_f(s.get('power_amdahl'))} (Amdahl), "
             f"{_f(s.get('power_usl'))} (USL), {_f(s.get('power_mild'))} (mild bend). **The verdict is one p-value:** the fixed-CV "
             "one-sided NB LR test on all N = 1 windows and the N = 12, K = 5 windows (SCALE-nf, the Welch and CV-estimated "
             "versions and the versions without flagged windows are sensitivities, never the verdict). Minimum detectable "
             f"effect at 80% power: a per-agent ratio of about {_f(s.get('mde_ratio_80'))} (log-SE about 0.25, set by the window CV "
             "0.3 over three N = 12 windows), so the study tests bends the size of the paper's default Amdahl (0.48) and "
             "USL (0.29) curves, not any bend; a NO-BEND result is consistent with anything from linear to a 25-30% bend. "
             "The CV 0.3 is assumed, not measured (at 0.5 the false alarm is "
             f"{_f(s.get('fpr_linear_cv05'))}, the honest bound); with 10% of hand-outs lost (stalls, mis-named branches) "
             f"the power against Amdahl falls to {_f(s.get('power_amdahl_stall_0_1'))}.")
    L.append(f"- **Confirmatory, secondary (CAP, N = 12, K = 1 vs {DESIGN_V7['K_hi']}; 500-task design only):** false alarm {_f(c.get('fpr_fastrev'))} when review never "
             f"binds; power {_f(c.get('power_measured'))} (T1-like workers), {_f(c.get('power_linear'))} (linear); rate "
             f"{_f(c.get('rate_usl'))} under USL workers, where the model predicts no ceiling.")
    L.append(f"- **Confirmatory, secondary (COLL):** false alarm {_f(cl.get('fpr'), '{:.3f}')}-{_f(cl.get('fpr_linear'), '{:.3f}')}; "
             "power under near-linear (T1-like) workers " +
             ", ".join(f"{_f(v)} at p = {k}" for k, v in (cl.get("power_linear") or {}).items()) +
             "; under USL-like workers (few merges) " +
             ", ".join(f"{_f(v)} at p = {k}" for k, v in (cl.get("power") or {}).items()) + ". The false alarm far below "
             "0.05 means the test is under-sized (about one background event per study under p = 0), not a virtue; and "
             "its power is coupled to SCALE: if SCALE reads a strong bend, the slowed fleet merges little and COLL is "
             "close to uninformative.")
    t = oc.get("tasks") or {}
    if t:
        L.append(f"- **Budget:** T1b {t['t1b']} tasks; sweep " + ", ".join(f"{k}-task design {v}" for k, v in t["sweep"].items()) +
                 " tasks (simulated means, near-linear workers; drag makes every window cheaper).")
    L.append("- **Descriptive:** K1, CAPFIT, ESC-N, ESC, FAMILY, UTIL, THROTTLE (rule 1b, follow-ups), COST, COLL-m, COLL-k, "
             "LAMBDA, BOUNCE, EFFORT.")
    return L


ABORT_V7 = (
    "Rule 1 (throttling, T1b in slot mode): T1b runs one slot for 30 min, then twelve for 15 min (K = K_hi, one session "
    "per slot, retired after 4 tasks or 6 messages; the twelve-slot phase includes each slot's first retirement and "
    "relaunch). The watcher polls every 2 s (at 15 s every start-up leg was quantised to the poll). The statistic is "
    "the **follow-up start-up** (session_message kind=task -> branch first pushed) of every task handed out by "
    "follow-up in each phase, twelve slots against one: ratio of geometric means with a Welch 90% interval on the log "
    "scale; tolerance 1.25. STOP if the interval lies wholly above 1.25; CLEAR if wholly below; otherwise "
    "INCONCLUSIVE: proceed, with the ratio reported beside SCALE. Launch start-ups (provisioning + clone) are compared "
    "the same way and a launch interval wholly above 1.25 is a flag, not a stop; so is a coding interval wholly above "
    "1.25 (coding contains the drag SCALE measures; a coding leg counts only for hand-outs at least 5 min before "
    "window end, in both phases, so late slow tasks are not dropped selectively). The mixed start-up is reported only. "
    "Slots lost to operator-logged failures unrelated to the service are excluded first. Pre-registered operating "
    "characteristics: those at a start-up log-sd of 0.25 (CLEAR 0.75 with no throttling, STOP 0.54 at 1.5x and 0.985 "
    "at 2x, never under drag), not the tight figures of the T0d / T0e calibration, whose log-sds were the poll's.",
    "Rule 1b (throttling in the sweep, descriptive): each N_hi window's follow-up start-up and coding against the pooled "
    "N = 1 windows (THROTTLE).",
    "Rule 2 (budget, in cloud tasks; one formula): meter readings are taken immediately before T1b's first launch and "
    "10 min after T1b's grace end (if the meter has not moved from the before-reading, wait 10 min and read again), and "
    "likewise immediately before and 10 min after every sweep window, each logged as a `meter` event. After T1b: "
    "c = (meter before T1b - meter after T1b) / T1b's tasks (hand-outs), with no correction for the whole-dollar meter "
    "(c to about +-$0.023 at 87 tasks), and h = hand-outs per slot-hour of its twelve-slot phase. Run the largest of the "
    "500-, 400- and 300-task designs whose sweep costs c x its tasks at h no more than the meter after T1b less $50 "
    "(v7.choose_design); if none fits, stop and report T1b. Before every window, c is cumulative: (meter before T1b - "
    "meter now) / every hand-out since T1b began (v7.rule2_before_window), and the rest of the chosen design must "
    "still fit at it; if not, drop N = 1 windows beyond eight (alternately by K in the 500-task design), then the "
    "N_hi, K = 1 window, then N_hi, K_hi windows beyond two. **The drop rule is expected to fire in a sizeable share "
    "of futures** (the 400-task design at $0.45 ends about $4 above the floor on average, and a window's cost varies "
    "by more than that); a truncated design is analysed as run.",
    "Rule 3 (lambda floor): after T1b's one-slot phase and the first two N = 1 sweep windows, pooled lambda below 10 "
    "first submissions per slot-hour (under half of the ~24 simulated, 21 in T0d) -> stop before the first N_hi window "
    "and report.",
    "Rule 4 (reviewers): 4a, before T1b (free): the reviewer calibration at --parallel K_hi against --parallel 1 on the "
    "same heads; if the mean review time at K_hi is more than 1.35x that at 1, K_hi reviewers would bind at N = 12 "
    "(predicted busy share >= 0.85): do not start T1b; re-plan. **Plan usage (the local Opus reviewers bill the owner's "
    "Max plan):** the operator reads the plan's usage indicators (5-hour and weekly) before and after the 4a "
    "calibration and before and after T1b, logs each as `note plan_usage ...` (`harness log --plan-usage`), projects "
    "the sweep's reviews from the usage per review measured there, and does not start the sweep if the projection "
    "leaves less than 30% of either allowance; if it is tight, the N = 12 windows are scheduled so that no two fall in "
    "one 5-hour block. A reviewer call that fails on a rate or usage limit is logged as a `reviewer_rate_limited` note. "
    "4b, in the sweep: an N_hi, K_hi window with the reviewers busy >= 80% of its counted time is flagged and SCALE "
    "is reported with and without it (not a stop); an N_hi, K = 1 window with its reviewer busy < 80% did not bind "
    "(reported beside CAP). A mean review above 60 s in any window is reported.",
    "Rule 5 (merge queue): mean merge-queue time per change above 30 s -> stop and fix the harness before the next window.",
    "Rule 6 (stalled sessions): a session with no READY within task_timeout_min (10 min, from 25; the prompts state a "
    "5-min budget and say to push what they have after 8) of a hand-out or rework message is retired and the slot gets "
    "a fresh session. Sessions are also retired after 4 tasks or 6 messages of any kind (launch prompt, next tasks, "
    "rework), whichever comes first: context, not tasks, drives compaction (T0e compacted after 6 tasks + 2 reworks). "
    "Stalls and branch losses are reported per window and per hand-out by N (LAMBDA); more than two in one window -> "
    "note it and report SCALE without that window beside the pre-registered result.",
    "Rule 7 (task order): every window's reset uses a fresh order seed (recorded in reset.json); the 220 tasks are "
    "reused across windows, each window taking the first tasks of its own order. A repeated seed is a protocol "
    "deviation: the window is reported and SCALE is shown without it.",
)
