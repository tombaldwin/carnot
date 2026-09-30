#!/usr/bin/env python3
"""Point predictions and the pre-registration table. Default: PLAN-v7; `--plan v6` / `v5` / `v4` give the superseded tables.

PLAN-v7 (the default; v7.py holds the design and codings; the tests are v6's):

    python predict.py --md prediction.md --json prediction.json        # the recommended 400-task design
    python predict.py --budget 300                                     # the 300- or 500-task design
    python predict.py --usd-per-task 0.52 --balance 214 --tasks-per-slot-hour 27   # abort rule 2 after T1b

  The budget is counted in cloud tasks (hand-outs: a first attempt with its rework, about $0.45 of credits each,
  T0d / T0e), T1b included. Printed: the design (cells, 15-min windows, order), the tasks per window and in all with
  the dollar cost at $0.30 / 0.45 / 0.60 per task, which design fits a given cost per task and balance (rule 2),
  the Opus reviews needed (plan usage), the model's prediction per cell at lambda 23, review 21.6 s, b_review 0.41,
  the abort rules and the operating characteristics.

PLAN-v6 (superseded; `--plan v6`):

    python predict.py --md prediction.md --json prediction.json        # the recommended 2 x 2 design (N x reviewers K)
    python predict.py --lambda-pilot 14 --review-s 21 --burn 2.10 --balance 249 --session-h-per-day 24
    python predict.py --cells 1x1=5 1x3=5 12x3=2 12x1=2                 # another design (e.g. the degrade design)

  Each rival (linear, Amdahl, USL, Carnot uncapped) is turned into the paper's U = (1 - r) min(lambda X(N), cap) per
  cell, cap = K x 3600 / review seconds reviews per hour of which (1 - b_review)(1 - b_mq) merge: which cells the
  model says are review-bound, the reviewer utilisation, illustrative finished counts, the SCALE ratio (K = 3) and the
  CAP ratio (K = 1 : 3 at N = 12). Defaults are T1's measurements (lambda 14 per slot-hour, review 21 s, b_review
  0.39, merge-queue bounces 0.11, 5.8 s per change). Also printed: the budget under (a) the credit assumption and (b)
  plan usage (session-hours per day), the abort rules (rule 1 revised) and the operating characteristics.

PLAN-v5 (superseded; `--plan v5`):

    python predict.py --md prediction.md --json prediction.json                   # the recommended design, assumptions
    python predict.py --lambda-pilot 5.4 --completion 0.88 --burn 2.3 --balance 236 --task-supply 220
    python predict.py --sizes 1 12 --reps-v5 1=6 12=2 --burn 3.1 --balance 236    # the degrade design

  The rivals are linear, Amdahl (alpha), USL (alpha, beta) and Carnot uncapped (USL x (1 - p)^(N - 1)); none is
  review-capped (the reviewer runs at 10-30 s per change). What SCALE and FAMILY test is each rival's ratio
  finished(N) / finished(1) = g_R(N), which needs no pilot: every rival's level is fitted to the sweep's own windows.
  lambda (per slot-hour) and completion (from T1, or the design assumptions 6 and 0.9) only turn the ratios into
  illustrative counts, reviewer / merge-queue utilisation and the task-supply check. Also printed: the design's
  session-hours and cost at the given burn, the degrade rule, the abort rules and the operating characteristics.

PLAN-v4 (superseded; `--plan v4`, unchanged below):

    python predict.py --plan v4 --pilot pilot.json --md prediction.md --json prediction.json
    python predict.py --pilot pilot.json --burn 2.10 --balance 240     # adds abort rule 5 (credits)
    python predict.py --lambda-pilot 7 --n-pilot 1 --V 14 --b-review 0.25 --b-hidden 0.1 --r0 0.3 \
                      --completion 0.7 --ci-time-min 0.5              # parameters by hand
    python predict.py --pilot pilot.json --calibration calibration.jsonl   # abort rule 4 from the calibration log
    python predict.py --pilot pilot.json --v3-gate                    # SUPERSEDED PLAN-v3 gate, for reference only

PLAN-v4 (the default path): fleet sizes N = 1 and N = 12, fixed in advance; three windows per size of
120 min with 10 min warm-up and 10 min grace (ABBAAB); the `completion` reading of the rivals' rework
term; no pilot gate. The output is the pre-registration table: pilot inputs, design-point loads, abort
rule 4 (calibrated V within +/-30% of 2 x pilot lambda), point predictions (finished and attempts per
window, 95% predictive intervals) for Carnot, USL, Amdahl and linear at each size, the O2 interval for
the total finished over the three N = 12 windows, and the operating-characteristics statement with the
confirmatory / conditional / descriptive split of PLAN-v4 section 1 (as amended in section 6, v4.1).

Calibration log (PLAN-v4.1 section 6.3; format in README "Calibration-review log"): the offline reviews
of pilot PRs, reference and deliberately broken solutions. predict.py reads it for abort rule 4 only:
calibrated V = verdicts / (sum of all call durations, errors included) per hour, for one review-job
version. It never enters the pilot's V, b or review-time CV, which come from live T1 + T2 reviews only
(derive.py --pilot). Without --calibration, rule 4 is shown as NOT EVALUATED, with the live pilot V as a
preview only.

`--v3-gate` runs PLAN-v3's q-gate (N_low, N_high, REDESIGN_* decisions). PLAN-v4 section 2 dropped it
("No pilot gate"); it is kept only so the design search and older outputs can be reproduced, and its
decision is labelled SUPERSEDED. It is never computed on the default path.

Inputs: the pilot's lambda (first attempts per worker-hour after warm-up, at n_pilot workers), V
(reviews per reviewer-busy hour), b_review, b_hidden (and b_other: rebase conflicts + visible fails per
approval, 0 if absent), r0, the completion share c (finished / attempts in the pilot windows),
merge-queue time per change; the model constants alpha = 0.1, beta = 0.01, p = 0.005.

Formulas (hours = window - warm-up = 110 min):

* single-agent rate for rival R, anchored at the pilot:  lam1_R = lambda_pilot * n_pilot / X_R(n_pilot)
  so every rival reproduces the pilot's attempt rate exactly (X_R = USL for Carnot and USL,
  alpha-only for Amdahl, N for linear).
* Carnot, review-capped: b_hidden(N) = 1 - (1 - b_hidden)(1 - p)^(N - n_pilot)  (collisions grow with N);
  1 - b(N) = (1 - b_review)(1 - b_hidden(N))(1 - b_other);
  reviews/h = min(lam1 X(N) / (1 - b(N)), V, merge-queue capacity / (1 - b_review));
  finished = (1 - b_hidden(N))(1 - b_other)(1 - b_review) * reviews/h * hours.
* USL: (1 - r0) lam1 X(N) hours;  Amdahl: (1 - r0) lam1_A X_A(N) hours;  Linear: (1 - r0) lam1_L N hours.
  Readings of the rework term (--rival-rework; README "decisions"):
    completion  (PLAN-v4, default) every rival, and Carnot's uncapped branch, use the pilot's measured
                completion share c: rival = c lam1_R X_R(N) hours and
                Carnot = min(c lam1 X(N), (1 - b_hidden(N))(1 - b_other)(1 - b_review) V_cap) hours,
                so Carnot and USL coincide below the knee and differ only by the review cap.
    plan        (1 - r0) as PLAN-v3 section 2 writes it.
    recovered   factor 1: bounced changes are reworked and finish.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import (UNCAPPED_TRUTH_FOOTNOTE, ALPHA, BETA, CV_OVERDISPERSION, P_COLLISION, PLAN_V4, RIVAL_LABEL, RIVALS,  # noqa: E402
                    V4_OC, X_for, X_usl, nb_interval, pooled_interval, rate_ci)

N_MAX_BUDGET = 8   # PLAN-v3 gate only (superseded)
N_SEARCH = 20


def _num(x, default=math.nan):
    return default if x is None else float(x)


class Params:
    def __init__(self, lambda_pilot, n_pilot, V, b_review, b_hidden, r0, ci_time_min=0.0, b_other=0.0,
                 window_min=PLAN_V4["window_min"], warmup_min=PLAN_V4["warmup_min"], alpha=ALPHA, beta=BETA,
                 p=P_COLLISION, rival_rework=PLAN_V4["rival_rework"], mq_cap=True, completion=None):
        self.lambda_pilot = float(lambda_pilot)
        self.n_pilot = int(n_pilot)
        self.V = float(V)
        self.b_review = float(b_review)
        self.b_hidden = float(b_hidden)
        self.b_other = 0.0 if b_other is None or (isinstance(b_other, float) and math.isnan(b_other)) else float(b_other)
        self.r0 = float(r0)
        ci = _num(ci_time_min, 0.0)
        self.ci_time_min = 0.0 if math.isnan(ci) else ci
        self.window_min = float(window_min)
        self.warmup_min = float(warmup_min)
        self.alpha, self.beta, self.p = alpha, beta, p
        self.rival_rework = rival_rework
        for name in ("b_review", "b_hidden", "b_other"):
            v = getattr(self, name)
            if not (0.0 <= v < 1.0):
                raise ValueError(f"{name} = {v} must be in [0, 1): the pilot has too few reviews to predict from")
        if not (self.lambda_pilot > 0 and self.V > 0):
            raise ValueError("lambda_pilot and V must be positive")
        self.completion = None if completion is None or (isinstance(completion, float) and math.isnan(completion)) \
            else float(completion)
        if rival_rework == "completion" and self.completion is None:
            raise ValueError("rival_rework='completion' needs the pilot's completion share")
        self.mq_cap = mq_cap

    @classmethod
    def from_pilot(cls, pilot: dict, **kw):
        return cls(lambda_pilot=pilot["lambda_pilot"], n_pilot=pilot["n_pilot"], V=pilot["V"],
                   b_review=pilot["b_review"], b_hidden=pilot["b_hidden"], r0=pilot["r0"],
                   ci_time_min=pilot.get("ci_time_min"), b_other=pilot.get("b_other", 0.0),
                   completion=pilot.get("completion"), **kw)

    def as_dict(self):
        return {k: v for k, v in self.__dict__.items()}

    @property
    def hours(self):
        return (self.window_min - self.warmup_min) / 60.0

    def X(self, rival, n):
        return float(X_for(rival, n, self.alpha, self.beta))

    def lam1(self, rival):
        return self.lambda_pilot * self.n_pilot / self.X(rival, self.n_pilot)

    def b_hidden_N(self, n):
        return 1 - (1 - self.b_hidden) * (1 - self.p) ** (n - self.n_pilot)

    def one_minus_b(self, n):
        return (1 - self.b_review) * (1 - self.b_hidden_N(n)) * (1 - self.b_other)

    def demand(self, n):
        """Review demand, reviews per hour, including re-reviews (Carnot's X)."""
        return self.lam1("carnot") * self.X("carnot", n) / self.one_minus_b(n)

    def attempts_per_hour(self, rival, n):
        return self.lam1(rival) * self.X(rival, n)


def predict_window(P: Params, n, hours=None):
    """Point predictions for one window at N workers: finished and attempts per window, per rival."""
    h = P.hours if hours is None else hours
    out = {}
    omb = P.one_minus_b(n)
    dem = P.demand(n)
    caps = {"review": P.V}
    if P.mq_cap and P.ci_time_min > 0:
        caps["merge_queue"] = (60.0 / P.ci_time_min) / (1 - P.b_review)
    rev_h = min(dem, *caps.values())
    binding = "demand" if rev_h == dem else min(caps, key=caps.get)
    fin_c = (1 - P.b_hidden_N(n)) * (1 - P.b_other) * (1 - P.b_review) * rev_h * h
    if P.rival_rework == "completion":
        fin_c = min(fin_c, P.completion * P.attempts_per_hour("carnot", n) * h)
    out["carnot"] = dict(finished=fin_c, attempts=P.attempts_per_hour("carnot", n) * h, reviews_per_hour=rev_h,
                         demand_per_hour=dem, binding=binding, b=1 - omb, V=P.V)
    for r in ("usl", "amdahl", "linear"):
        att = P.attempts_per_hour(r, n)
        # "plan": (1 - r0) as PLAN-v3 writes it. "recovered": bounced work is reworked and finishes,
        # the same completion factor Carnot has when review does not bind (= 1).
        factor = {"plan": 1 - P.r0, "recovered": 1.0, "completion": P.completion}[P.rival_rework]
        out[r] = dict(finished=factor * att * h, attempts=att * h, reviews_per_hour=att / omb,
                      demand_per_hour=att / omb, binding="none", b=1 - omb, V=None)
    return out


def gate(P: Params):
    """SUPERSEDED (PLAN-v3 section 3). PLAN-v4 fixes N = 1 and 12 with no gate; this is kept only for
    reproducing the design search and PLAN-v3 outputs, and runs only behind predict.py --v3-gate."""
    dem = {n: P.demand(n) for n in range(1, N_SEARCH + 1)}
    lows = [n for n in dem if n <= N_MAX_BUDGET and dem[n] <= 0.7 * P.V]
    n_low = max(lows) if lows else None
    highs = [n for n in dem if dem[n] >= 1.5 * P.V]
    n_high = min(highs) if highs else None
    x8 = float(X_usl(N_MAX_BUDGET, P.alpha, P.beta))
    lam1 = P.lam1("carnot")
    omb = P.one_minus_b(1)
    # feasible V: demand(1) <= 0.7 V (N_low >= 1) and demand(8) >= 1.5 V (N_high <= 8)
    v_lo = lam1 / omb / 0.7
    v_hi = lam1 * x8 / omb / 1.5
    if dem[N_MAX_BUDGET] < P.V:
        decision = "REDESIGN_UNREACHABLE"
        reason = (f"demand at N = 8 ({dem[8]:.1f}/h) is below V ({P.V:.1f}/h): the review limit cannot be "
                  "reached within budget. In order: shrink tasks to raise lambda; give the reviewer a heavier, "
                  "pre-registered job; or do not run the sweep.")
    elif n_high is None or n_high > N_MAX_BUDGET:
        decision = "RECUT_OR_ABORT"
        reason = f"N_high = {n_high} > 8: re-cut the budget before running, or do not run the sweep."
    elif n_low is None:
        decision = "REDESIGN_SATURATED_AT_1"
        reason = (f"demand at N = 1 ({dem[1]:.1f}/h) exceeds 0.7 V ({0.7 * P.V:.1f}/h): the reviewer is "
                  "saturated even with one worker, so no size sits below the knee. Lighten the review job or "
                  "enlarge tasks (pre-registered), or run as a plateau-only study.")
    elif n_low >= n_high:
        decision = "REDESIGN_NONMONOTONE"
        reason = "N_low >= N_high (demand not monotone in N over the range); inspect the curve."
    else:
        decision = "RUN"
        reason = f"run N_low = {n_low}, N_high = {n_high}."
    return dict(decision=decision, reason=reason, N_low=n_low, N_high=n_high,
                demand={n: dem[n] for n in range(1, 11)}, V=P.V, q=P.V / lam1, lam1=lam1,
                V_feasible=[v_lo, v_hi], q_feasible=[v_lo / lam1, v_hi / lam1],
                x_ratio_needed=1.5 / 0.7, x_ratio_8_over_2=x8 / float(X_usl(2, P.alpha, P.beta)))


def budget_rule(n_low, n_high, burn, pilot_spend):
    """SUPERSEDED (PLAN-v3 section 4; --v3-gate only): full sweep if burn * 3 (N_low + N_high) <= 200 - P,
    else 75-min windows, else one replicate of each size reported as a pilot."""
    room = 200.0 - pilot_spend
    full = burn * 3.0 * (n_low + n_high)
    short = burn * 2.5 * (n_low + n_high)
    if full <= room:
        return dict(plan="full sweep, 90-min windows, 2 replicates", cost=full, room=room)
    if short <= room:
        return dict(plan="75-min windows, 2 replicates", cost=short, room=room)
    return dict(plan="one replicate of each size, reported as a pilot", cost=burn * 1.5 * (n_low + n_high), room=room)


def prediction_table(P: Params, sizes):
    rows = []
    for n in sizes:
        pr = predict_window(P, n)
        for r in RIVALS:
            d = pr[r]
            lo, hi = nb_interval(d["finished"], CV_OVERDISPERSION, 0.95)
            rows.append(dict(N=n, rival=r, finished=d["finished"], finished_95=[lo, hi], attempts=d["attempts"],
                             demand_per_hour=d["demand_per_hour"], reviews_per_hour=d["reviews_per_hour"],
                             binding=d["binding"], b=d["b"], V=d["V"]))
    return rows


def to_markdown_v3(P: Params, g, rows, pilot=None, budget=None):
    """SUPERSEDED PLAN-v3 table with the gate (--v3-gate only)."""
    L = []
    L.append("## SUPERSEDED: PLAN-v3 gate and point predictions (reference only; PLAN-v4 has no gate)\n")
    L.append(f"Constants: alpha = {P.alpha}, beta = {P.beta}, p = {P.p}; over-dispersion CV = {CV_OVERDISPERSION} "
             f"(fixed). Window {P.window_min:g} min, warm-up {P.warmup_min:g} min, so hours = {P.hours:.3f} per "
             f"window. Rival rework reading: `{P.rival_rework}`.\n")
    L.append("### Pilot inputs\n")
    L.append("| Input | Value |\n|---|---|")
    L.append(f"| lambda (first attempts per worker-hour at N = {P.n_pilot}) | {P.lambda_pilot:.3f} |")
    if pilot and pilot.get("lambda_ci"):
        L.append(f"| lambda 95% interval | {pilot['lambda_ci'][0]:.2f} - {pilot['lambda_ci'][1]:.2f} |")
    L.append(f"| lambda1 (single-agent, USL-anchored) | {P.lam1('carnot'):.3f} |")
    L.append(f"| V (reviews per reviewer-busy hour) | {P.V:.2f} |")
    if pilot and pilot.get("V_ci"):
        L.append(f"| V 95% interval (n = {pilot.get('reviews')}) | {pilot['V_ci'][0]:.1f} - {pilot['V_ci'][1]:.1f} |")
    L.append(f"| b_review | {P.b_review:.3f} |")
    L.append(f"| b_hidden (escaped + integration per approval) | {P.b_hidden:.3f} |")
    L.append(f"| b_other (rebase conflict + visible fail per approval) | {P.b_other:.3f} |")
    L.append(f"| b (implied, at N = {P.n_pilot}) | {1 - P.one_minus_b(P.n_pilot):.3f} |")
    if pilot and pilot.get("b") is not None:
        L.append(f"| b (measured directly) | {pilot['b']:.3f} |")
    L.append(f"| r0 (share of first attempts bounced at least once) | {P.r0:.3f} |")
    if P.completion is not None:
        L.append(f"| completion share (finished / attempts in the pilot window) | {P.completion:.3f} |")
    L.append(f"| merge-queue time per change (min) | {P.ci_time_min:.2f} |")
    if pilot:
        for k, lab in (("escape_rate", "escape rate (escaped / approvals)"), ("review_min_mean", "review time (min)"),
                       ("review_tokens_mean", "review tokens"), ("worker_tokens_per_attempt", "worker tokens per attempt"),
                       ("startup_min", "session start-up (min)")):
            if pilot.get(k) is not None:
                L.append(f"| {lab} | {pilot[k]:.3g} |")
    L.append("\n### Gate\n")
    L.append(f"q = V / lambda1 = {g['q']:.2f}. Review demand lambda1 X(N) / (1 - b), reviews per hour:\n")
    L.append("| N | " + " | ".join(str(n) for n in g["demand"]) + " |")
    L.append("|---|" + "---|" * len(g["demand"]))
    L.append("| demand | " + " | ".join(f"{v:.1f}" for v in g["demand"].values()) + " |")
    L.append("| demand / V | " + " | ".join(f"{v / g['V']:.2f}" for v in g["demand"].values()) + " |")
    L.append(f"\nN_low = {g['N_low']}, N_high = {g['N_high']}. PLAN-v3 decision (SUPERSEDED, not acted on): {g['decision']} - {g['reason']}\n")
    L.append(f"The gate can pass only if V lies in [{g['V_feasible'][0]:.1f}, {g['V_feasible'][1]:.1f}] reviews/h "
             f"(q in [{g['q_feasible'][0]:.2f}, {g['q_feasible'][1]:.2f}]). With these alpha, beta, "
             f"X(8)/X(2) = {g['x_ratio_8_over_2']:.2f} < 1.5/0.7 = {g['x_ratio_needed']:.2f}, so N_low = 1 is the only "
             "size that can sit below the knee with N_high <= 8.\n")
    if budget:
        L.append(f"Budget rule: {budget['plan']} (predicted ${budget['cost']:.0f} against ${budget['room']:.0f} available).\n")
    L.append("### Finished changes per window (point prediction and 95% predictive interval, NB with CV 0.3)\n")
    L.append("| Rival | N | Finished | 95% interval | Attempts | Review demand /h | Reviews /h | Binding | b | V |")
    L.append("|---|---|---|---|---|---|---|---|---|---|")
    for r in rows:
        vtxt = "" if r["V"] is None else f"{r['V']:.1f}"
        L.append(f"| {RIVAL_LABEL[r['rival']]} | {r['N']} | {r['finished']:.1f} | {r['finished_95'][0]}-{r['finished_95'][1]} "
                 f"| {r['attempts']:.1f} | {r['demand_per_hour']:.1f} | {r['reviews_per_hour']:.1f} | {r['binding']} "
                 f"| {r['b']:.3f} | {vtxt} |")
    sizes = sorted({r["N"] for r in rows})
    if len(sizes) == 2:
        f = {(r["rival"], r["N"]): r["finished"] for r in rows}
        L.append("\nPredicted ratio finished(N_high) / finished(N_low) (descriptive only): " +
                 ", ".join(f"{RIVAL_LABEL[r]} {f[(r, sizes[1])] / f[(r, sizes[0])]:.2f}" for r in RIVALS) + ".\n")
    L.append("Also predicted, all rivals: V equal to the pilot's within +/-25% at both sizes and in both half-windows "
             "(Carnot; the uncapped rivals imply a reviewer that keeps up, i.e. V rising with load); b and the escape "
             "rate as in the pilot, with the Carnot model predicting escapes rising with queue depth.\n")
    return "\n".join(L)



# ---------------------------------------------------------------------- PLAN-v4
def pilot_inputs_md(P: Params, pilot):
    L = ["| Input | Value |", "|---|---|"]
    L.append(f"| lambda (first attempts per worker-hour at N = {P.n_pilot}) | {P.lambda_pilot:.3f} |")
    if pilot.get("lambda_ci"):
        L.append(f"| lambda 95% interval | {pilot['lambda_ci'][0]:.2f} - {pilot['lambda_ci'][1]:.2f} |")
    L.append(f"| lambda1 (single-agent, USL-anchored) | {P.lam1('carnot'):.3f} |")
    L.append(f"| V (reviews per reviewer-busy hour) | {P.V:.2f} |")
    if pilot.get("V_ci"):
        L.append(f"| V 95% interval (n = {pilot.get('reviews')}) | {pilot['V_ci'][0]:.1f} - {pilot['V_ci'][1]:.1f} |")
    L.append(f"| b_review | {P.b_review:.3f} |")
    L.append(f"| b_hidden (escaped + integration per approval) | {P.b_hidden:.3f} |")
    L.append(f"| b_other (rebase conflict + visible fail per approval) | {P.b_other:.3f} |")
    L.append(f"| b (implied, at N = {P.n_pilot}) | {1 - P.one_minus_b(P.n_pilot):.3f} |")
    if pilot.get("b") is not None:
        L.append(f"| b (measured directly) | {pilot['b']:.3f} |")
    L.append(f"| r0 (share of first attempts bounced at least once) | {P.r0:.3f} |")
    if P.completion is not None:
        L.append(f"| completion share c (finished / attempts in the pilot windows) | {P.completion:.3f} |")
    L.append(f"| merge-queue time per change (min) | {P.ci_time_min:.2f} |")
    for k, lab in (("escape_rate", "escape rate (escaped / approvals)"), ("review_min_mean", "review time (min)"),
                   ("review_time_cv", "review-time CV"), ("review_tokens_mean", "review tokens"),
                   ("worker_tokens_per_attempt", "worker tokens per attempt"), ("startup_min", "session start-up (min)")):
        if pilot.get(k) is not None:
            L.append(f"| {lab} | {pilot[k]:.3g} |")
    if "review_cv_ok" in pilot:
        L.append(f"| review_cv_ok (review-time CV <= {PLAN_V4['review_cv_max']}; fixes whether V constancy is confirmatory) "
                 f"| **{bool(pilot['review_cv_ok'])}** |")
    if pilot.get("supply_truncated_windows"):
        L.append(f"| pilot windows that ran out of tasks | {', '.join(pilot['supply_truncated_windows'])} |")
    return L


CAL_VERDICTS = ("approve", "request_changes")
CAL_SOURCES = ("pilot_pr", "reference", "broken", "other")


def load_calibration(path, job=None):
    """Read a calibration-review log (JSONL, or CSV with a header) and summarise one review-job version.

    Required per row: review_id (unique), duration_s (> 0, wall-clock seconds of the reviewer call including
    any retry), verdict (approve | request_changes | error). Optional: source (pilot_pr | reference | broken |
    other), task, expected (approve | request_changes), job (the review-job version; default the job of the
    last row), reviewer_model, t (ISO time). Rows of other jobs are ignored. Busy time is the sum of every
    row's duration_s (errors included, as live busy time includes crash-and-retry time); calibrated V =
    verdict rows / busy hours."""
    import csv
    path = Path(path)
    text = path.read_text()
    if path.suffix.lower() == ".csv":
        rows = list(csv.DictReader(text.splitlines()))
    else:
        rows = [json.loads(ln) for ln in text.splitlines() if ln.strip()]
    errs = []
    seen = set()
    for i, r in enumerate(rows, 1):
        for k in ("review_id", "duration_s", "verdict"):
            if r.get(k) in (None, ""):
                errs.append(f"row {i}: missing {k}")
        try:
            r["duration_s"] = float(r["duration_s"])
            if not r["duration_s"] > 0:
                errs.append(f"row {i}: duration_s must be > 0")
        except (TypeError, ValueError, KeyError):
            errs.append(f"row {i}: duration_s not a number")
        v = str(r.get("verdict", "")).strip().lower()
        r["verdict"] = v
        if v not in (*CAL_VERDICTS, "error"):
            errs.append(f"row {i}: verdict {v!r} not approve / request_changes / error")
        if r.get("source") not in (None, "") and r["source"] not in CAL_SOURCES:
            errs.append(f"row {i}: source {r['source']!r} not in {CAL_SOURCES}")
        if r.get("expected") not in (None, "") and r["expected"] not in CAL_VERDICTS:
            errs.append(f"row {i}: expected {r['expected']!r} not approve / request_changes")
        if r.get("review_id") in seen:
            errs.append(f"row {i}: duplicate review_id {r.get('review_id')!r}")
        seen.add(r.get("review_id"))
    if errs:
        raise SystemExit(f"{path}: calibration log invalid:\n  " + "\n  ".join(errs[:20]))
    if not rows:
        raise SystemExit(f"{path}: calibration log is empty")
    jobs = [str(r.get("job") or "") for r in rows]
    job = jobs[-1] if job is None else str(job)
    rows = [r for r, j in zip(rows, jobs) if j == job]
    if not rows:
        raise SystemExit(f"{path}: no rows for job {job!r}")
    n = sum(1 for r in rows if r["verdict"] in CAL_VERDICTS)
    busy_h = sum(r["duration_s"] for r in rows) / 3600.0
    d = [r["duration_s"] for r in rows if r["verdict"] in CAL_VERDICTS]
    mean = sum(d) / len(d) if d else math.nan
    cv = (math.sqrt(sum((x - mean) ** 2 for x in d) / (len(d) - 1)) / mean) if len(d) > 1 else math.nan
    lab = [r for r in rows if r.get("expected") in CAL_VERDICTS and r["verdict"] in CAL_VERDICTS]
    broken = [r for r in lab if r["expected"] == "request_changes"]
    good = [r for r in lab if r["expected"] == "approve"]
    lo, hi = rate_ci(n, busy_h) if busy_h > 0 else (math.nan, math.nan)
    return dict(path=str(path), job=job, rows=len(rows), reviews=n, errors=len(rows) - n, busy_hours=busy_h,
                V=n / busy_h if busy_h > 0 else math.nan, V_ci=[lo, hi], review_s_mean=mean, review_time_cv=cv,
                by_source={s_: sum(1 for r in rows if (r.get("source") or "other") == s_) for s_ in CAL_SOURCES},
                catch_rate_broken=(sum(r["verdict"] == "request_changes" for r in broken) / len(broken)) if broken else None,
                false_reject_reference=(sum(r["verdict"] == "request_changes" for r in good) / len(good)) if good else None,
                note="descriptive except V, which is used for abort rule 4 only; never pooled into the pilot")


def abort_rule_4(P: Params, cal=None):
    """PLAN-v4 section 4 rule 4 (as amended in 6.3): the calibrated V, from the calibration log, within +/-30% of
    2 x pilot lambda (per agent-hour at N = 1). Without a calibration log the rule is not evaluated; the live
    pilot V is shown as a preview only."""
    target = PLAN_V4["q"] * P.lam1("carnot")
    if cal is None:
        ratio = P.V / target
        return dict(evaluated=False, V=None, V_live_preview=P.V, target=target, ratio=None,
                    ratio_live_preview=ratio, ok=None, source="not evaluated: no calibration log (--calibration)")
    ratio = cal["V"] / target
    return dict(evaluated=True, V=cal["V"], V_ci=cal["V_ci"], target=target, ratio=ratio, ok=bool(0.7 <= ratio <= 1.3),
                source=f"calibration log {Path(cal['path']).name}, job {cal['job']!r}, {cal['reviews']} reviews",
                V_live_preview=P.V)


def credit_rule(burn, balance):
    """PLAN-v4 sections 3-4 rule 5: predicted balance after the sweep >= $50."""
    cost = burn * PLAN_V4["sweep_session_hours"]
    after = balance - cost
    return dict(burn=burn, balance=balance, sweep_cost=cost, balance_after=after,
                ok=after >= PLAN_V4["balance_floor_usd"])


def supply_check(P: Params, supply, sizes):
    """Minute at which each rival's predicted claim rate (first attempts per hour, from minute 0) would use up
    `supply` tasks in one window; None if it would not within the window. Claims run slightly ahead of
    first submissions, so this is a lower bound on the supply needed."""
    out = []
    for n in sizes:
        for r in RIVALS:
            rate = P.attempts_per_hour(r, n)  # per hour
            t_ex = 60.0 * supply / rate if rate > 0 else math.inf
            out.append(dict(N=n, rival=r, claims_per_window=rate * P.window_min / 60.0,
                            exhausted_min=t_ex if t_ex < P.window_min else None))
    return out


def v4_predictions(P: Params, sizes=None, reps=None):
    sizes = tuple(sizes or PLAN_V4["sizes"])
    reps = reps or PLAN_V4["reps"]
    rows = prediction_table(P, sizes)
    totals = []
    for n in sizes:
        pr = predict_window(P, n)
        for r in RIVALS:
            mu = pr[r]["finished"]
            lo, hi = pooled_interval([mu] * reps, CV_OVERDISPERSION)
            totals.append(dict(N=n, rival=r, windows=reps, finished_total=mu * reps, finished_total_95=[lo, hi],
                               attempts_total=pr[r]["attempts"] * reps))
    loads = {n: P.demand(n) / P.V for n in sizes}
    o2 = next(t for t in totals if t["N"] == max(sizes) and t["rival"] == "carnot")
    return dict(sizes=list(sizes), reps=reps, rows=rows, totals=totals, loads=loads, q=P.V / P.lam1("carnot"),
                O2_interval=o2["finished_total_95"], O2_point=o2["finished_total"])


def oc_statement_md():
    oc = V4_OC
    pc = oc["primary_correct"]
    cm = oc["confusion"]
    L = [f"### Operating characteristics (simulated; PLAN-v4.1 section 6.8, {oc['source']})", ""]
    L.append("Framing (PLAN-v4 section 7, v4.2): a measurement and calibration study. O2 is the primary quantitative "
             "confirmatory test; P1 is a confirmatory manipulation check; Vdur is conditionally confirmatory; everything else "
             "is descriptive.")
    L.append("")
    L.append(f"- **Confirmatory, primary quantitative test (O2):** finished at N = 12 inside Carnot's 95% predictive interval, "
             f"(1 - b_review)(1 - b_hidden(12))(1 - b_other) x V x hours with V and b from the pilot at N = 1. False-alarm rate "
             f"under Carnot's own truth {oc['O2_false_alarm']['service_cv_1']:.2f} at review-time CV 1, "
             f"{oc['O2_false_alarm']['service_cv_0_5']:.2f} at CV 0.5 (above the nominal 0.05); it fails under USL / Amdahl / "
             f"linear truth with probability {oc['O2_power']['usl']:.2f} / {oc['O2_power']['amdahl']:.2f} / "
             f"{oc['O2_power']['linear']:.2f}*.")
    L.append(f"- **Confirmatory, manipulation check (P1): the harness's fixed-capacity reviewer caps output.** Carnot's "
             f"review-capped prediction has a higher likelihood than the best uncapped rival (USL, Amdahl, linear); the "
             f"likelihood ratio is reported. The reviewer is the binding limit by design, so a FAIL indicates a harness or "
             f"calibration fault, not support for a rival. Correct {pc['carnot']:.2f} under Carnot truth; {pc['usl']:.2f} / "
             f"{pc['amdahl']:.2f} / {pc['linear']:.2f} under USL / Amdahl / linear truth*. If agents are 30% slower than "
             f"assumed: {pc['carnot_lambda_30pct_low']:.2f} under Carnot, {pc['usl_lambda_30pct_low']:.2f} under USL*. At 1.5x "
             f"credit burn with the degrade design (2 x 120-min windows per size, about $199): {pc['burn_1_5x']:.2f} under "
             f"Carnot, {pc['usl_burn_1_5x']:.2f} under USL*.")
    L.append(f"  - *{UNCAPPED_TRUTH_FOOTNOTE}")
    L.append(f"- **Conditionally confirmatory: the reviewer's pace does not change with load (Vdur).** Welch test on log "
             f"review durations, N = 1 against N = 12; fails iff two-sided p < 0.05. Confirmatory only if the pilot's live "
             f"(T1 + T2) review-time CV <= {PLAN_V4['review_cv_max']} (`review_cv_ok`), else descriptive. Power against a "
             f"+/-25% reviewer: {oc['Vdur_power']['service_cv_0_5']:.2f} at review-time CV 0.5, "
             f"{oc['Vdur_power']['service_cv_1']:.2f} at CV 1; false-positive rate {oc['Vdur_fpr']['service_cv_0_5']:.2f} / "
             f"{oc['Vdur_fpr']['service_cv_1']:.2f}. Probability that `review_cv_ok` is set: "
             f"{oc['review_cv_ok_prob']['cv_0_3']:.2f} / {oc['review_cv_ok_prob']['cv_0_5']:.2f} / "
             f"{oc['review_cv_ok_prob']['cv_0_7']:.2f} at true CV 0.3 / 0.5 / 0.7 (near 0.5 it is close to a coin toss).")
    L.append("- **Descriptive** (reported whatever they show; a null result is not evidence):")
    L.append(f"  - S3, the review-bounce share b_review does not rise with N (one-sided Fisher exact; false-alarm rate "
             f"{oc['S3_false_alarm']:.2f}); O3, attempts rise with N (exact rate-ratio test; passes under every truth, "
             f"{oc['O3_power']:.2f}, so it is uninformative);")
    L.append("  - V(12)/V(1) with its exact 95% interval; no equivalence claim (the interval is about 0.7-1.45 at this design);")
    L.append("  - four-way ranking of the rivals (USL vs Amdahl is not claimed). Simulated confusion matrix, rows = truth, "
             "columns = family with the highest likelihood:")
    L.append("")
    L.append("    | truth | Carnot | USL | Amdahl | linear | tie |")
    L.append("    |---|---|---|---|---|---|")
    for t in RIVALS:
        row = cm[t]
        L.append(f"    | {t} | " + " | ".join(f"{row[c]:.2f}" for c in (*RIVALS, "tie")) + " |")
    L.append("")
    L.append(f"  - escaped defects against reviewer queue depth (`logit_cluster_task`, >= {PLAN_V4['min_escape_events']} events "
             f"or not modelled): power {oc['escape_power']['a05']:.2f} at alpha 0.05;")
    L.append(f"  - collisions against in-flight changes (k-slope): power {oc['collision_power']['p01_a05']:.2f} at p = 0.01, "
             f"{oc['collision_power']['p05_a05']:.2f} at p = 0.05;")
    L.append(f"  - S1r / S2r surprise ratios: false-alarm rates {oc['S1r_false_alarm']:.2f} / {oc['S2r_false_alarm']:.2f} under "
             "the model's own truth. S2r fires about half the time under Carnot at N = 12, so it is not a criterion.")
    L.append(f"- **Circularity.** At the design point the reviewer is {oc['loads']['N1']:.2f} loaded with one agent and about "
             f"{oc['loads']['N12']:.1f}x overloaded with twelve, so a flat finished count at N = 12 is expected by construction "
             "and is not itself evidence; the non-identity claims are the ones listed above.")
    return L


def to_markdown_v4(P: Params, pred, pilot, ar4, credits=None):
    L = ["## Point predictions (pre-registration, PLAN-v4)\n"]
    L.append(f"Design (PLAN-v4 section 2, fixed in advance, no pilot gate): N = {' and '.join(str(n) for n in pred['sizes'])}, "
             f"{pred['reps']} windows per size in {PLAN_V4['order']} order, {P.window_min:g} min each ({P.warmup_min:g} min "
             f"warm-up, {PLAN_V4['grace_min']:g} min grace), so hours = {P.hours:.3f} per window. Constants: alpha = {P.alpha}, "
             f"beta = {P.beta}, p = {P.p}; over-dispersion CV = {CV_OVERDISPERSION} (fixed). Rival rework reading: "
             f"`{P.rival_rework}`.\n")
    L.append("### Pilot inputs\n")
    L += pilot_inputs_md(P, pilot)
    L.append("\n### Design-point load and abort rules\n")
    L.append(f"q = V / lambda1 = {pred['q']:.2f} (target {PLAN_V4['q']:g}). Review demand lambda1 X(N) / (1 - b) as a share "
             "of V: " + ", ".join(f"N = {n}: {v:.2f}" for n, v in pred["loads"].items()) + ".\n")
    if ar4["evaluated"]:
        L.append(f"- Abort rule 4 (calibrated V within +/-30% of 2 x pilot lambda = {ar4['target']:.1f}/h; {ar4['source']}): "
                 f"calibrated V = {ar4['V']:.2f} ({ar4['V_ci'][0]:.1f}-{ar4['V_ci'][1]:.1f}), V / target = {ar4['ratio']:.2f} -> "
                 f"**{'OK' if ar4['ok'] else 'FAIL: apply the pre-registered consequence (PLAN-v4 section 7.3): accept the measured q and re-run oc_v41.py at that q before any sweep window, or redefine the review job AND re-run the pilot; never recalibrate offline and keep the old pilot'}**. "
                 f"(Live pilot V = {ar4['V_live_preview']:.2f}; the calibration reviews are not pooled into it.)")
    else:
        L.append(f"- Abort rule 4 (calibrated V within +/-30% of 2 x pilot lambda = {ar4['target']:.1f}/h): **NOT EVALUATED**, "
                 f"no calibration log given (--calibration). Preview only: live pilot V / target = "
                 f"{ar4['ratio_live_preview']:.2f}.")
    lam_floor = 0.5 * PLAN_V4["lambda_target"]
    L.append(f"- Abort rule 3 (pilot lambda >= 0.5 x target = {lam_floor:g}/agent-hour): lambda1 = {P.lam1('carnot'):.2f} -> "
             f"**{'OK' if P.lam1('carnot') >= lam_floor else 'FAIL: report as a pilot, no sweep'}**.")
    if pred.get("supply"):
        S = pred["supply"]
        ex = [x for x in S["by_rival"] if x["exhausted_min"] is not None]
        txt = "; ".join(f"{RIVAL_LABEL[x['rival']]} at N = {x['N']} would claim all {S['n']} by minute "
                        f"{x['exhausted_min']:.0f}" for x in ex) or "no rival's predicted claim rate uses it up within a window"
        L.append(f"- Task supply ({S['n']} tasks per window): {txt}. A window that runs out is flagged by derive.py and its "
                 "attempt-based measures stop at that minute (README decision 19).")
    if credits:
        L.append(f"- Abort rule 5 (credits): sweep {PLAN_V4['sweep_session_hours']:g} session-h x ${credits['burn']:.2f} = "
                 f"${credits['sweep_cost']:.0f}; balance ${credits['balance']:.0f} -> ${credits['balance_after']:.0f} after "
                 f"(floor ${PLAN_V4['balance_floor_usd']:.0f}) -> **{'OK' if credits['ok'] else 'FAIL: apply the degrade rule'}**.")
    L.append("\n### Finished changes and attempts per window (point prediction, 95% predictive interval: NB with CV 0.3)\n")
    L.append("| Rival | N | Finished | 95% interval | Attempts | Review demand /h | Reviews /h | Binding | b | V |")
    L.append("|---|---|---|---|---|---|---|---|---|---|")
    for r in pred["rows"]:
        vtxt = "" if r["V"] is None else f"{r['V']:.1f}"
        L.append(f"| {RIVAL_LABEL[r['rival']]} | {r['N']} | {r['finished']:.1f} | {r['finished_95'][0]}-{r['finished_95'][1]} "
                 f"| {r['attempts']:.1f} | {r['demand_per_hour']:.1f} | {r['reviews_per_hour']:.1f} | {r['binding']} "
                 f"| {r['b']:.3f} | {vtxt} |")
    L.append(f"\n### Totals over the {pred['reps']} windows per size\n")
    L.append("| Rival | N | Finished total | 95% interval | Attempts total |\n|---|---|---|---|---|")
    for t in pred["totals"]:
        L.append(f"| {RIVAL_LABEL[t['rival']]} | {t['N']} | {t['finished_total']:.1f} | {t['finished_total_95'][0]}-"
                 f"{t['finished_total_95'][1]} | {t['attempts_total']:.1f} |")
    hi = max(pred["sizes"])
    L.append(f"\n**O2 (confirmatory, primary quantitative test):** the total finished over the {pred['reps']} N = {hi} windows is predicted "
             f"at {pred['O2_point']:.1f}, 95% predictive interval {pred['O2_interval'][0]}-{pred['O2_interval'][1]} "
             "(score.py recomputes it from each window's actual hours).\n")
    f = {(r["rival"], r["N"]): r["finished"] for r in pred["rows"]}
    lo = min(pred["sizes"])
    L.append(f"Predicted ratio finished(N = {hi}) / finished(N = {lo}) (descriptive only; S1r/S2r compare the observed ratio "
             "with Carnot's): " + ", ".join(f"{RIVAL_LABEL[r]} {f[(r, hi)] / f[(r, lo)]:.2f}" for r in RIVALS) + ".\n")
    L.append("Completion share (PLAN-v4 sections 6.7 and 7.6): c is measured in the 60-min pilot windows and applied to "
             "120-min sweep windows. Censoring at the window end is heavier in a 60-min window, so c is biased low, and every "
             "prediction that uses c is expected to be LOW: all four rivals at N = 1 (the N = 1 sweep windows are expected to "
             "run above every prediction) and the uncapped rivals at N = 12 (which moves them towards the cap, so it works "
             "against a P1 PASS). Carnot's capped branch at N = 12, and so O2, does not use c.\n")
    L.append("Also predicted by Carnot: V is a property of the reviewer (V(12)/V(1) = 1, review durations unchanged); "
             "b_review the same at both sizes; attempts rising with N as lambda1 X(N). The uncapped rivals imply a reviewer "
             "that keeps up with demand, i.e. V rising with load.\n")
    L += oc_statement_md()
    return "\n".join(L)


# ---------------------------------------------------------------------- PLAN-v5
def to_markdown_v5(rows, design, cost, cost15, degrade, credits, a):
    import v5
    L = ["## Point predictions and design (pre-registration, PLAN-v5)\n"]
    sizes = design["sizes"]
    reps = design["reps"]
    L.append(f"Design (PLAN-v5 section 5): N = {', '.join(str(n) for n in sizes)}; windows " +
             ", ".join(f"{reps.get(n, 0)} at N = {n}" for n in sizes) + f", {design['window_min']:g} min each "
             f"({a.warmup_min:g} min warm-up, 10 min grace), order {v5_order_txt(sizes, reps)}. No T2 pilot: every rival's "
             "level is fitted to the sweep's own windows (so its N = 1 windows anchor it); T1 checks throttling only. "
             f"Constants alpha = {a.alpha}, beta = {a.beta}, p = {a.p}; over-dispersion CV {CV_OVERDISPERSION} per window.\n")
    L.append("### Budget\n")
    L.append("| | session-hours | at the given burn | at 1.5x |\n|---|---|---|---|")
    L.append(f"| T0 + T1 + sweep | {cost['t1_session_hours'] + cost['sweep_session_hours']:.1f} (+ T0 ~$1) | ${cost['total_usd']:.0f} "
             f"at ${cost['burn']:.2f}/h | ${cost15['total_usd']:.0f} |")
    L.append(f"| degrade design ({', '.join(f'{v} x N = {k}' for k, v in degrade['reps'].items())}) | "
             f"{degrade['cost']['t1_session_hours'] + degrade['cost']['sweep_session_hours']:.1f} | ${degrade['cost']['total_usd']:.0f} | "
             f"${degrade['cost15']['total_usd']:.0f} |")
    if credits:
        L.append(f"\nCredits: balance ${credits['balance']:.0f}; the full sweep ({cost['sweep_session_hours']:.0f} session-h x "
                 f"${credits['burn']:.2f}) leaves ${credits['after_full']:.0f}, the degrade sweep ${credits['after_degrade']:.0f} "
                 f"(floor $50) -> **{credits['decision']}**.")
    L.append("\n### What each rival predicts (finished per window relative to N = 1; no pilot needed)\n")
    L.append("| Rival | " + " | ".join(f"N = {n}: ratio to 1 | per agent" for n in sizes) + " |")
    L.append("|---|" + "---|---|" * len(sizes))
    for r in v5.RIVALS_V5:
        rr = {x["N"]: x for x in rows if x["rival"] == r}
        L.append(f"| {v5.RIVAL_LABEL_V5[r]} | " + " | ".join(f"{rr[n]['ratio_to_1']:.2f} | {rr[n]['per_agent_ratio']:.2f}" for n in sizes) + " |")
    L.append(f"\nSCALE (confirmatory, primary) reads BEND iff the one-sided LR test of per-agent output falling with N gives p < 0.05. "
             "Linear predicts a per-agent ratio of 1 (LINEAR-NOT-REJECTED); every other rival predicts BEND.\n")
    L.append(f"### Illustrative counts and loads (lambda {a.lam:.2f} per slot-hour, completion {a.comp:.2f}, b_review {a.b_review_v5:.2f}, "
             f"review {a.review_s:.0f} s, merge queue {a.mq_s:.0f} s per change)\n")
    L.append("| Rival | N | finished / window | 95% | windows | total | 95% | reviews /h | reviewer util | at 1.5x review time | merge-queue util | supply out at min |")
    L.append("|---|---|---|---|---|---|---|---|---|---|---|---|")
    for x in rows:
        tot = x["finished_total_95"] or [None, None]
        L.append(f"| {v5.RIVAL_LABEL_V5[x['rival']]} | {x['N']} | {x['finished']:.1f} | {x['finished_95'][0]}-{x['finished_95'][1]} | "
                 f"{x['windows']} | {x['finished_total']:.1f} | {tot[0]}-{tot[1]} | {x['reviews_per_hour']:.0f} | {x['reviewer_util']:.2f} | "
                 f"{x['reviewer_util_slow']:.2f} | {x['mq_util']:.3f} | {'-' if x['supply_exhausted_min'] is None else round(x['supply_exhausted_min'])} |")
    hi = max(sizes)
    lin = next(x for x in rows if x["rival"] == "linear" and x["N"] == hi)
    L.append(f"\nReviewer: a single serial call per change. Under linear workers at N = {hi} it would be busy about "
             f"{lin['reviewer_util']:.0%} of the time ({lin['reviewer_util_slow']:.0%} if reviews take 1.5x as long): approaching "
             "saturation, so UTIL is reported beside SCALE and a window with the reviewer busy >= 80% is flagged. The merge "
             "queue stays below 5% busy under every rival.\n")
    L.append("### Abort rules (PLAN-v5 section 5)\n")
    for t in ABORT_V5:
        L.append(f"- {t}")
    L.append("")
    L += v5.oc_statement_v5()
    return "\n".join(L)


def v5_order_txt(sizes, reps):
    from synth import v5_order
    return ", ".join(str(n) for n in v5_order(sizes, reps))


ABORT_V5 = (
    "Rule 1 (throttling, T1, unchanged from PLAN-v4 section 7.5): twelve-slot activity rate / one-slot rate < 0.8 -> stop and report T1.",
    "Rule 2 (credits): burn = (M0 - M1) / T1 session-hours. Run the full design if the predicted balance after it is >= $50; "
    "else the degrade design (the same without one N = 12 window) if that is; else stop and report T0/T1. Before every window "
    "the predicted balance after the rest of the chosen design must stay >= $50; if not, drop the remaining N = 12 window(s) "
    "first, then stop. An interrupted sequence is analysed as run (every test is defined for >= 1 window per size); an N = 12 "
    "window is never run without at least two N = 1 windows before it.",
    "Rule 3 (lambda floor): after the first two N = 1 sweep windows, pooled lambda < 3 first submissions per slot-hour (half the "
    "dry runs' 6) -> stop before the first N = 12 window and report; the operating characteristics were computed down to "
    "lambda -30%.",
    "Rule 4 (review must not bind): reviewer utilisation >= 0.8 in an N = 12 window, or the mean review above 60 s -> the window "
    "is flagged in UTIL and SCALE is reported with and without it (not a stop).",
    "Rule 5 (merge queue): mean merge-queue time per change above 30 s (dry runs: about 2 s) -> stop and fix the harness before "
    "the next window.",
)


def main_v5(a):
    import v5
    D = v5.DESIGN_V5
    sizes = sorted(a.sizes) if a.sizes else list(D["sizes"])
    reps = ({int(k): int(v) for k, v in (x.split("=") for x in a.reps_v5)} if a.reps_v5 else dict(D["reps"]))
    wmin = a.window_min if a.window_min is not None else D["window_min"]
    pilot = json.loads(Path(a.pilot).read_text()) if a.pilot else {}
    a.lam = a.lambda_pilot if a.lambda_pilot is not None else pilot.get("lambda_pilot") or 6.0
    a.comp = a.completion if a.completion is not None else pilot.get("completion") or 0.9
    a.b_review_v5 = a.b_review if a.b_review is not None else pilot.get("b_review") or 0.3
    rows = v5.predictions_v5(sizes, reps, wmin, a.warmup_min, a.lam, a.comp, a.b_review_v5, a.review_s, a.mq_s,
                             a.alpha, a.beta, a.p, CV_OVERDISPERSION, a.task_supply)
    burn = a.burn if a.burn is not None else D["burn_assumed"]
    cost = v5.design_cost(sizes, reps, wmin, burn)
    cost15 = v5.design_cost(sizes, reps, wmin, 1.5 * burn)
    dg = D["degrade"]
    degrade = dict(reps=dg["reps"], cost=v5.design_cost(dg["sizes"], dg["reps"], dg["window_min"], burn),
                   cost15=v5.design_cost(dg["sizes"], dg["reps"], dg["window_min"], 1.5 * burn))
    credits = None
    if a.burn is not None and a.balance is not None:
        full = a.balance - burn * cost["sweep_session_hours"]
        deg = a.balance - burn * degrade["cost"]["sweep_session_hours"]
        dec = ("run the full design" if full >= D["balance_floor_usd"] else
               "run the degrade design" if deg >= D["balance_floor_usd"] else "stop: report T1")
        credits = dict(burn=burn, balance=a.balance, after_full=full, after_degrade=deg, decision=dec)
    design = dict(sizes=sizes, reps=reps, window_min=wmin)
    md = to_markdown_v5(rows, design, cost, cost15, degrade, credits, a)
    out = dict(plan="PLAN-v5", design=design, predictions=rows, cost=cost, cost_1_5x=cost15, degrade=degrade, credits=credits,
               assumptions=dict(lambda_per_slot_hour=a.lam, completion=a.comp, b_review=a.b_review_v5, review_s=a.review_s,
                                mq_s=a.mq_s, alpha=a.alpha, beta=a.beta, p=a.p),
               abort_rules=list(ABORT_V5), operating_characteristics=v5.V5_OC)
    print(md)
    if a.md:
        Path(a.md).write_text(md + "\n")
    if a.json:
        Path(a.json).write_text(json.dumps(out, indent=2, default=str) + "\n")


# ---------------------------------------------------------------------- PLAN-v6
def to_markdown_v6(rows, design, cost, cost15, degrade, budget_b, credits, a):
    import v5
    import v6
    cells = design["cells"]
    L = ["## Point predictions and design (pre-registration, PLAN-v6)\n"]
    L.append(f"Design (PLAN-v6 section 5): cells N x K = " + ", ".join(f"{n}x{k}: {w}" for (n, k), w in sorted(cells.items())) +
             f"; {design['window_min']:g}-min windows ({design['warmup_min']:g} min warm-up, 10 min grace); order " +
             ", ".join(f"{n}K{k}" for n, k in v6.v6_order(cells)) + ". Before the sweep: T1b (one slot for "
             f"{v6.DESIGN_V6['t1b']['one_slot_min']:g} min, then twelve for {v6.DESIGN_V6['t1b']['twelve_slot_min']:g} min, K = "
             f"{v6.DESIGN_V6['t1b']['K']}) for abort rule 1. Constants alpha = {a.alpha}, beta = {a.beta}, p = {a.p}; "
             f"over-dispersion CV {CV_OVERDISPERSION} per window.\n")
    L.append("### Budget\n")
    L.append("**(a) Credit assumption** (the $2.10 per session-hour of the v5 plan, against the $249 balance less the $50 floor). "
             "UNRESOLVED: the meter did not move over T0c + T1 (about 7 session-hours); the owner decides which budget "
             "applies.\n")
    L.append("| | session-hours | at the given burn | at 1.5x | fits up to |\n|---|---|---|---|---|")
    L.append(f"| T1b + sweep | {cost['total_session_hours']:.1f} | ${cost['total_usd']:.0f} at ${cost['burn']:.2f}/h | "
             f"${cost15['total_usd']:.0f} | ${v6.fits_up_to(cells, design['window_min']):.2f}/h |")
    L.append(f"| degrade design ({', '.join(f'{n}x{k}: {w}' for (n, k), w in sorted(degrade['cells'].items()))}) | "
             f"{degrade['cost']['total_session_hours']:.1f} | ${degrade['cost']['total_usd']:.0f} | ${degrade['cost15']['total_usd']:.0f} | "
             f"${v6.fits_up_to(degrade['cells'], design['window_min']):.2f}/h |")
    if credits:
        L.append(f"\nCredits: balance ${credits['balance']:.0f}; the full design leaves ${credits['after_full']:.0f}, the degrade "
                 f"design ${credits['after_degrade']:.0f} (floor $50) -> **{credits['decision']}**.")
    L.append("\n**(b) Plan usage** (if cloud sessions draw the Max plan rather than credits): worker session-hours and their "
             "intensity, and the local reviewer's Opus calls.\n")
    L.append("| | value |\n|---|---|")
    for k, v in budget_b.items():
        L.append(f"| {k} | {v} |")
    L.append("\n### What the model predicts per cell (U = (1 - r) min(lambda X(N), cap))\n")
    L.append("| Rival | N | K | reviews demanded /h | reviewer capacity /h | reviewer util | binds | finished / window | 95% | "
             "uncapped | ceiling |")
    L.append("|---|---|---|---|---|---|---|---|---|---|---|")
    for x in rows:
        L.append(f"| {v5.RIVAL_LABEL_V5[x['rival']]} | {x['N']} | {x['K']} | {x['reviews_demand_per_hour']:.0f} | "
                 f"{x['reviewer_capacity_per_hour']:.0f} | {x['reviewer_util']:.2f} | {'yes' if x['binds'] else 'no'} | "
                 f"{x['finished']:.1f} | {x['finished_95'][0]}-{x['finished_95'][1]} | {x['uncapped']:.1f} | {x['ceiling']:.1f} |")
    hi = max(n for n, _ in cells)
    L.append("\nSCALE (confirmatory, primary) uses the N = 1 windows and the N = %d, K = 3 windows: linear predicts a per-agent "
             "ratio of 1, every other rival BEND. CAP (confirmatory, secondary) compares N = %d windows with K = 1 and K = 3: "
             "the model predicts CAPPED wherever lambda X(%d) exceeds one reviewer's capacity (the rows marked 'binds' at "
             "K = 1).\n" % (hi, hi, hi))
    L.append("### Abort rules (PLAN-v6 section 5.3)\n")
    for t in v6.ABORT_V6:
        L.append(f"- {t}")
    L.append("")
    L += v6.oc_statement_v6()
    return "\n".join(L)


def main_v6(a):
    import v6
    D = v6.DESIGN_V6
    cells = ({tuple(int(x) for x in k.split("x")): int(v) for k, v in (c.split("=") for c in a.cells)} if a.cells
             else dict(D["cells"]))
    wmin = a.window_min if a.window_min is not None else D["window_min"]
    wu = D["warmup_min"]
    lam = a.lambda_pilot if a.lambda_pilot is not None else 14.0
    comp = a.completion if a.completion is not None else 0.8
    brev = a.b_review if a.b_review is not None else 0.39
    rs = a.review_s if a.review_s is not None else 21.0
    rows = v6.predictions_v6(cells, wmin, wu, lam, comp, brev, 0.11, rs, a.alpha, a.beta, a.p)
    burn = a.burn if a.burn is not None else D["burn_assumed"]
    cost = v6.design_cost_v6(cells, wmin, burn)
    cost15 = v6.design_cost_v6(cells, wmin, 1.5 * burn)
    dg = D["degrade"]
    degrade = dict(cells=v6.cells_of(dg["cells"]), cost=v6.design_cost_v6(dg["cells"], wmin, burn),
                   cost15=v6.design_cost_v6(dg["cells"], wmin, 1.5 * burn))
    credits = None
    if a.burn is not None and a.balance is not None:
        full = a.balance - burn * cost["sweep_session_hours"]
        deg = a.balance - burn * degrade["cost"]["sweep_session_hours"]
        dec = ("run the full design" if full >= D["balance_floor_usd"] else
               "run the degrade design" if deg >= D["balance_floor_usd"] else "stop: report T1b")
        credits = dict(burn=burn, balance=a.balance, after_full=full, after_degrade=deg, decision=dec)
    budget_b = v6.plan_usage(cells, wmin, a.session_h_per_day)
    design = dict(cells=cells, window_min=wmin, warmup_min=wu)
    md = to_markdown_v6(rows, design, cost, cost15, degrade, budget_b, credits, a)
    out = dict(plan="PLAN-v6", design=dict(cells={f"{n}x{k}": w for (n, k), w in cells.items()}, window_min=wmin, warmup_min=wu),
               predictions=rows, cost=cost, cost_1_5x=cost15,
               degrade=dict(cells={f"{n}x{k}": w for (n, k), w in degrade["cells"].items()}, cost=degrade["cost"],
                            cost15=degrade["cost15"]),
               budget_plan_usage=budget_b, credits=credits,
               assumptions=dict(lambda_per_slot_hour=lam, completion=comp, b_review=brev, review_s=rs, alpha=a.alpha,
                                beta=a.beta, p=a.p),
               abort_rules=list(v6.ABORT_V6), operating_characteristics=v6.V6_OC)
    print(md)
    if a.md:
        Path(a.md).write_text(md + "\n")
    if a.json:
        Path(a.json).write_text(json.dumps(out, indent=2, default=str) + "\n")


# ---------------------------------------------------------------------- PLAN-v7
def to_markdown_v7(rows, design, costs, reviews, choice, a):
    import v5
    import v7
    cells = design["cells"]
    L = ["## Point predictions and design (pre-registration, PLAN-v7)\n"]
    L.append(f"Design (PLAN-v7 section 5, the {design['name']}-task design): cells N x K = " +
             ", ".join(f"{n}x{k}: {w}" for (n, k), w in sorted(cells.items())) +
             f"; {design['window_min']:g}-min windows ({design['warmup_min']:g} min warm-up, 10 min grace); order " +
             ", ".join(f"{n}K{k}" for n, k in v7.v6_order(cells)) + f". Before the sweep: T1b (one slot for "
             f"{v7.T1B_V7['one_slot_min']:g} min, then twelve for {v7.T1B_V7['twelve_slot_min']:g} min, K = {v7.T1B_V7['K']}, "
             f"one session per slot) for abort rules 1 and 2. Constants alpha = {a.alpha}, beta = {a.beta}, p = {a.p}; "
             f"over-dispersion CV {CV_OVERDISPERSION} per window.\n")
    L.append("### Budget (cloud tasks)\n")
    L.append(f"A task is one hand-out (a first attempt with its rework): about ${v7.USD_PER_TASK['central']:.2f} of cloud-session "
             f"credits (T0d $0.50, T0e $0.43; range ${v7.USD_PER_TASK['low']:.2f}-{v7.USD_PER_TASK['high']:.2f}). "
             f"${v7.BALANCE_USD:.0f} left, ${v7.FLOOR_USD:.0f} floor: ${v7.SPENDABLE_USD:.0f} to spend. Hand-outs per slot-hour "
             f"{a.tph:g} (K_hi) and {a.tph * v7.TASKS_PER_SLOT_HOUR_K1 / v7.TASKS_PER_SLOT_HOUR:.0f} (N_hi, K = 1).\n")
    L.append("| design | cells | T1b tasks | sweep tasks | total tasks | $ at 0.30 / 0.45 / 0.60 | Opus reviews |")
    L.append("|---|---|---|---|---|---|---|")
    for name, c in costs.items():
        L.append(f"| {name}{' (this)' if name == design['name'] else ''} | {', '.join(f'{n}x{k}: {w}' for (n, k), w in sorted(c['cells'].items()))} | "
                 f"{c['t1b_tasks']:.0f} | {c['sweep_tasks']:.0f} | {c['total_tasks']:.0f} | ${c['usd']['low']:.0f} / ${c['usd']['central']:.0f} / "
                 f"${c['usd']['high']:.0f} | {c['reviews']:.0f} |")
    if choice is not None:
        L.append(f"\nRule 2 at ${a.usd_per_task:.2f} per task, balance ${a.balance:.0f}"
                 + (f" less T1b's predicted ${a.spent_t1b:.0f}" if a.spent_t1b else "") + f" (floor ${v7.FLOOR_USD:.0f}): "
                 f"**{('run the ' + choice + '-task design') if choice else 'stop: report T1b'}**.")
    L.append("\n### Opus reviews per cell (the owner's Max plan, not credits)\n")
    L.append("| cell | windows | reviews per window | reviews |\n|---|---|---|---|")
    for (n, k), r in sorted(reviews.items()):
        L.append(f"| {n}x{k} | {r['windows']} | {r['per_window']:.0f} | {r['total']:.0f} |")
    L.append("\n### What the model predicts per cell (U = (1 - r) min(lambda X(N), cap))\n")
    L.append("| Rival | N | K | reviews demanded /h | reviewer capacity /h | reviewer util | binds | finished / window | 95% | "
             "uncapped | ceiling |")
    L.append("|---|---|---|---|---|---|---|---|---|---|---|")
    for x in rows:
        L.append(f"| {v5.RIVAL_LABEL_V5[x['rival']]} | {x['N']} | {x['K']} | {x['reviews_demand_per_hour']:.0f} | "
                 f"{x['reviewer_capacity_per_hour']:.0f} | {x['reviewer_util']:.2f} | {'yes' if x['binds'] else 'no'} | "
                 f"{x['finished']:.1f} | {x['finished_95'][0]}-{x['finished_95'][1]} | {x['uncapped']:.1f} | {x['ceiling']:.1f} |")
    L.append("\n### Abort rules (PLAN-v7 section 5.4)\n")
    for t in v7.ABORT_V7:
        L.append(f"- {t}")
    L.append("")
    L += v7.oc_statement_v7()
    return "\n".join(L)


def main_v7(a):
    import v7
    name = a.budget or "400"
    D = dict(v7.DESIGNS_V7[name])
    cells = ({tuple(int(x) for x in k.split("x")): int(v) for k, v in (c.split("=") for c in a.cells)} if a.cells
             else v7.cells_of(D["cells"]))
    wmin = a.window_min if a.window_min is not None else D["window_min"]
    wu = D["warmup_min"]
    lam = a.lambda_pilot if a.lambda_pilot is not None else 23.0
    comp = a.completion if a.completion is not None else 0.88
    brev = a.b_review if a.b_review is not None else 0.41
    rs = a.review_s if a.review_s is not None else 21.6
    a.tph = a.tasks_per_slot_hour or v7.TASKS_PER_SLOT_HOUR
    k1 = a.tph * v7.TASKS_PER_SLOT_HOUR_K1 / v7.TASKS_PER_SLOT_HOUR
    rows = v7.predictions_v7(cells, wmin, wu, lam, comp, brev, 0.11, rs, a.alpha, a.beta, a.p)
    costs = {}
    for nm, dd in v7.DESIGNS_V7.items():
        cc = cells if nm == name else v7.cells_of(dd["cells"])
        c = v7.design_tasks(cc, wmin if nm == name else dd["window_min"], tph=a.tph, tph_k1=k1)
        rv = v7.reviews_estimate(cc, wmin if nm == name else dd["window_min"], lam, brev, rs)
        costs[nm] = dict(c, cells=cc, reviews=sum(r["total"] for r in rv.values()) + v7.T1B_V7["reviews"])
    reviews = v7.reviews_estimate(cells, wmin, lam, brev, rs)
    choice = None
    if a.usd_per_task is not None:
        # --balance: the meter now (after T1b). Without it: the balance before T1b less T1b's predicted cost.
        spent = 0.0 if a.balance is not None else v7.T1B_V7["tasks"] * a.usd_per_task
        a.balance = a.balance if a.balance is not None else v7.BALANCE_USD
        a.spent_t1b = spent
        choice = v7.choose_design(a.usd_per_task, a.balance, spent_t1b=spent, tasks_per_slot_hour=a.tph) or ""
    design = dict(name=name, cells=cells, window_min=wmin, warmup_min=wu)
    md = to_markdown_v7(rows, design, costs, reviews, choice, a)
    out = dict(plan="PLAN-v7", design=dict(name=name, cells={f"{n}x{k}": w for (n, k), w in cells.items()}, window_min=wmin,
                                           warmup_min=wu, t1b=v7.T1B_V7),
               predictions=rows, costs={k: dict(v, cells={f"{n}x{kk}": w for (n, kk), w in v["cells"].items()}) for k, v in costs.items()},
               reviews={f"{n}x{k}": v for (n, k), v in reviews.items()}, rule2_choice=choice,
               assumptions=dict(lambda_per_slot_hour=lam, completion=comp, b_review=brev, review_s=rs, tasks_per_slot_hour=a.tph,
                                usd_per_task=v7.USD_PER_TASK, alpha=a.alpha, beta=a.beta, p=a.p),
               abort_rules=list(v7.ABORT_V7), operating_characteristics=v7.V7_OC)
    print(md)
    if a.md:
        Path(a.md).write_text(md + "\n")
    if a.json:
        Path(a.json).write_text(json.dumps(out, indent=2, default=str) + "\n")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--plan", choices=["v7", "v6", "v5", "v4"], default="v7",
                    help="v7 (default): PLAN-v7 predictions; v6 / v5 / v4: the superseded tables")
    ap.add_argument("--budget", choices=["300", "400", "500"], default=None, help="v7: which design (tasks in all; default 400)")
    ap.add_argument("--usd-per-task", type=float, default=None, help="v7: measured $ per task (T1b); prints rule 2's choice")
    ap.add_argument("--tasks-per-slot-hour", type=float, default=None, help="v7: hand-outs per slot-hour (default 25, simulated)")
    ap.add_argument("--cells", nargs="*", default=None, metavar="NxK=W", help="v6 / v7: windows per cell, e.g. 1x1=6 1x3=6 12x3=3 12x1=2")
    ap.add_argument("--session-h-per-day", type=float, default=None, help="v6: plan-usage allowance in session-hours per day (budget b)")
    ap.add_argument("--reps-v5", nargs="*", default=None, metavar="N=R", help="v5: windows per size, e.g. 1=8 12=3")
    ap.add_argument("--review-s", type=float, default=None, help="mean review seconds (v6 default 21, T1; v5 default 20)")
    ap.add_argument("--mq-s", type=float, default=None, help="v5: merge-queue seconds per change (default 2)")
    ap.add_argument("--pilot", help="pilot.json from derive.py --pilot")
    for k in ("lambda-pilot", "V", "b-review", "b-hidden", "b-other", "r0", "ci-time-min", "completion"):
        ap.add_argument(f"--{k}", type=float, default=None)
    ap.add_argument("--n-pilot", type=int, default=None)
    ap.add_argument("--window-min", type=float, default=None, help="window minutes (PLAN-v5: 90; PLAN-v4: 120)")
    ap.add_argument("--warmup-min", type=float, default=PLAN_V4["warmup_min"])
    ap.add_argument("--alpha", type=float, default=ALPHA)
    ap.add_argument("--beta", type=float, default=BETA)
    ap.add_argument("--p", type=float, default=None, help="collision p (v6 default 0.0075 from T1; v5 / v4 0.005)")
    ap.add_argument("--rival-rework", choices=["completion", "plan", "recovered"], default=PLAN_V4["rival_rework"])
    ap.add_argument("--sizes", nargs="+", type=int, default=None,
                    help="fleet sizes (PLAN-v5 and PLAN-v4: 1 12)")
    ap.add_argument("--reps", type=int, default=PLAN_V4["reps"], help="windows per size (PLAN-v4: 3)")
    ap.add_argument("--task-supply", type=int, default=None, help="tasks per window (TASKS.json length), for the supply check")
    ap.add_argument("--calibration", default=None,
                    help="calibration-review log (JSONL or CSV; README 'Calibration-review log'), for abort rule 4 only")
    ap.add_argument("--calibration-job", default=None, help="review-job version in the calibration log (default: the last row's)")
    ap.add_argument("--burn", type=float, default=None, help="$ per worker session-hour measured in T1/T2")
    ap.add_argument("--balance", type=float, default=None, help="credits left before the sweep, $ (abort rule 5; v7: rule 2)")
    ap.add_argument("--v3-gate", action="store_true",
                    help="SUPERSEDED: also print PLAN-v3's q-gate and budget rule (reference only; PLAN-v4 has no gate)")
    ap.add_argument("--pilot-spend", type=float, default=None, help="--v3-gate only: $ spent on T1 + T2")
    ap.add_argument("--md", default=None)
    ap.add_argument("--json", default=None)
    a = ap.parse_args()
    if a.plan == "v7":
        import v7
        a.p = v7.P_V7 if a.p is None else a.p
        return main_v7(a)
    if a.plan == "v6":
        import v6
        a.p = v6.P_V6 if a.p is None else a.p
        return main_v6(a)
    a.p = P_COLLISION if a.p is None else a.p
    a.review_s = 20.0 if a.review_s is None else a.review_s
    a.mq_s = 2.0 if a.mq_s is None else a.mq_s
    if a.plan == "v5":
        return main_v5(a)
    a.sizes = a.sizes or list(PLAN_V4["sizes"])
    a.window_min = a.window_min if a.window_min is not None else PLAN_V4["window_min"]
    pilot = json.loads(Path(a.pilot).read_text()) if a.pilot else {}
    over = {"lambda_pilot": a.lambda_pilot, "V": a.V, "b_review": a.b_review, "b_hidden": a.b_hidden,
            "b_other": a.b_other, "r0": a.r0, "ci_time_min": a.ci_time_min, "n_pilot": a.n_pilot,
            "completion": a.completion}
    for k, v in over.items():
        if v is not None:
            pilot[k] = v
    missing = [k for k in ("lambda_pilot", "V", "b_review", "b_hidden", "r0", "n_pilot") if pilot.get(k) is None]
    if missing:
        raise SystemExit(f"missing pilot inputs: {missing}")
    P = Params.from_pilot(pilot, window_min=a.window_min, warmup_min=a.warmup_min, alpha=a.alpha, beta=a.beta,
                          p=a.p, rival_rework=a.rival_rework)
    if sorted(a.sizes) != sorted(PLAN_V4["sizes"]) or a.reps != PLAN_V4["reps"] or a.window_min != PLAN_V4["window_min"]:
        print(f"note: not the PLAN-v4 design (sizes {list(PLAN_V4['sizes'])}, {PLAN_V4['reps']} x "
              f"{PLAN_V4['window_min']:g} min)", file=sys.stderr)
    pred = v4_predictions(P, sorted(a.sizes), a.reps)
    if a.task_supply:
        pred["supply"] = dict(n=a.task_supply, by_rival=supply_check(P, a.task_supply, sorted(a.sizes)))
    cal = load_calibration(a.calibration, a.calibration_job) if a.calibration else None
    ar4 = abort_rule_4(P, cal)
    credits = credit_rule(a.burn, a.balance) if a.burn is not None and a.balance is not None else None
    md = to_markdown_v4(P, pred, pilot, ar4, credits)
    out = dict(plan="PLAN-v4.1", params=P.as_dict(), design=dict(PLAN_V4), predictions=pred, abort_rule_4=ar4, calibration=cal,
               credits=credits, operating_characteristics=V4_OC,
               review_cv_ok=pilot.get("review_cv_ok"), review_time_cv=pilot.get("review_time_cv"))
    if a.v3_gate:
        g = gate(P)
        budget = None
        if a.burn is not None and a.pilot_spend is not None and g["N_low"] and g["N_high"]:
            budget = budget_rule(g["N_low"], g["N_high"], a.burn, a.pilot_spend)
        md += "\n\n" + to_markdown_v3(P, g, prediction_table(P, sorted(a.sizes)), pilot, budget)
        out["v3_gate_superseded"] = dict(gate=g, budget=budget)
    print(md)
    if a.md:
        Path(a.md).write_text(md + "\n")
    if a.json:
        Path(a.json).write_text(json.dumps(out, indent=2, default=str) + "\n")


if __name__ == "__main__":
    main()
