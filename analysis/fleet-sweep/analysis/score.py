#!/usr/bin/env python3
"""Primary analysis of the fleet sweep. Default: PLAN-v5 (v5.py); `--plan v4` / `--plan v3`: the superseded codings.

    python score.py runs/<w1> ... runs/<w11> --out-dir results/ [--effort-log effort.jsonl] [--pilot pilot.json]
        # PLAN-v5: writes results/results.json and results/RESULTS-draft.md
    python score.py --plan v4 --pilot pilot.json runs/<w1> ... runs/<w6> --out-dir results/     # PLAN-v4.2

PLAN-v5 (review automated and fast; no review-capped rival), graded in advance (PLAN-v5 section 5, v5.GRADES_V5):
  SCALE [confirmatory, primary]      per-agent finished output falls with N: one-sided NB LR test (CV 0.3) of gamma < 0
                                     in finished ~ theta x slot-hours x N^gamma; also without supply-flagged windows
                                     (SCALE-nf).
  COLL [confirmatory, secondary]     collisions (rebase conflict or integration failure on a change's first merge-queue
                                     pass) rise with j, the merges of other changes since the change's base; one-sided LR.
  ESC-N, ESC, FAMILY, UTIL, COLL-m, COLL-k, LAMBDA, BOUNCE, EFFORT [descriptive]; see v5.py.
--pilot is optional in v5 (a pilot-anchored rival reading, descriptive); --effort-log reads the post-hoc max-effort
re-review (format: v5.load_effort_log).

PLAN-v4 (`--plan v4`), coded to PLAN-v4 section 1:

Reads the sweep windows (two sizes, any order; PLAN-v4: three at N = 1 and three at N = 12) and the
pre-registered pilot parameters (pilot.json, including the flag `review_cv_ok`). Every result carries a
grade fixed in advance (PLAN-v4 section 1 as reframed in section 7, v4.2: a measurement and calibration
study). Results are listed in this order:

Confirmatory, primary quantitative test
  O2   Finished at N_high: the total over the N_high windows lies in Carnot's 95% predictive interval,
       (1 - b_review)(1 - b_hidden(12))(1 - b_other) x V x hours with V and b from the pilot at N = 1.
       Also reported without the windows flagged for running out of tasks before minute 110 (O2-nf).
Confirmatory, manipulation check
  P1   The harness's fixed-capacity reviewer caps output: Carnot's review-capped prediction has a higher
       likelihood than the best uncapped rival (USL, Amdahl, linear). Negative-binomial likelihood (fixed CV
       0.3) of each window's finished count, `completion` reading; the likelihood ratio is reported. A tie
       (identical predictions) is not "higher". The reviewer is the binding limit by design (a fresh call
       per change that never sees the queue, calibrated to about half the N = 12 demand), so a FAIL
       indicates a harness or calibration fault, not support for an uncapped rival. Also reported without
       flagged windows (P1-nf).
Conditionally confirmatory (confirmatory only if pilot.json has review_cv_ok = true, else descriptive)
  Vdur    The reviewer's pace does not change with load: Welch t-test on log review durations, N_high vs
          N_low; FAIL iff two-sided p < 0.05, else PASS (PLAN-v4.1 section 6.1). review_cv_ok comes from the
          live T1 + T2 reviews only (derive.py --pilot).
Descriptive (reported whatever they show, with the simulated power from OPERATING-CHARACTERISTICS.md; the
ones with a rule are still coded PASS / FAIL)
  S3        b_review (review bounces / reviews) does not rise with N: FAIL if one-sided Fisher exact p < 0.05.
  O3        Attempts rise with N: fleet first-attempt rate high / low, exact conditional rate-ratio test,
            PASS at one-sided p < 0.05 (FAIL if it falls at p < 0.05, else INCONCLUSIVE). Attempts and hours
            stop at the task-supply exhaustion minute in flagged windows (derive.py).
  Vratio    V(high)/V(low) with its exact 95% interval, reported only: no equivalence claim is made (with
            about 40 and 75 reviews the interval is roughly 0.7-1.45, so it cannot show stability).
  S1r, S2r  observed finished ratio / Carnot's predicted ratio >= 1.3 / <= 0.7 with the queue non-empty
            (S2r is not a criterion: it fires about half the time under the model's own truth).
  RANK      four-way ranking with likelihood ratios and the simulated confusion matrix.
  ESC       escaped defects vs reviewer queue depth, `logit_cluster_task`; < 8 events -> not modelled.
  COLL      collisions: bounced at least once vs k and m (censored excluded), p-hat in the model form.
  BOUNCE    bounce causes other than review, by size (rebase conflicts, visible fails, escaped defects,
            integration failures).
  alpha, beta from carnot.py fit (three points; not identifiable).

`--plan v3` reproduces the superseded PLAN-v3 codings (S1-S5, O1-O5, V band 0.75-1.25) for the
design-search scripts; it is not the pre-registered analysis.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import math
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import (CONDITIONAL, CONFIRMATORY, CV_OVERDISPERSION, DESCRIPTIVE, PLAN_V4, RIVAL_LABEL,  # noqa: E402
                    RIVALS, ROLE_MANIPULATION, ROLE_PRIMARY, UNCAPPED_TRUTH_FOOTNOTE, V4_OC, X_usl, clogit_fit, collision_fit, logit_fit, lr_one_sided, nb_logpmf,
                    pooled_interval, rate_ci, rate_ratio_test, two_prop_one_sided, window_dummies)
from derive import _clean, derive_dir  # noqa: E402
from predict import Params, predict_window  # noqa: E402
from scipy import stats  # noqa: E402

BAND = 0.25                      # PLAN-v3 V band (superseded; --plan v3 and the design search only)
UNCAPPED = ("usl", "amdahl", "linear")


def _exp(x):
    """exp that returns inf (not an exception) for a separated fit's huge coefficient."""
    try:
        return math.exp(x)
    except OverflowError:
        return math.inf
MIN_ESCAPES = 8
READINGS = ("completion", "plan", "recovered")
ESCAPE_MODELS = ("clogit_task", "clogit_task_first", "logit_cluster_task")


# ---------------------------------------------------------------------- pieces
def rival_scores(S, P: Params, cv=CV_OVERDISPERSION):
    rows = []
    ll = {r: 0.0 for r in RIVALS}
    for s in S:
        pr = predict_window(P, s["n_workers"], hours=s["hours"])
        row = dict(run_id=s["run_id"], N=s["n_workers"], finished=s["finished"])
        for r in RIVALS:
            mu = pr[r]["finished"]
            li = float(nb_logpmf(s["finished"], mu, cv))
            ll[r] += li
            row[f"pred_{r}"] = mu
            row[f"ll_{r}"] = li
        rows.append(row)
    mx = max(ll.values())
    best_set = [r for r in RIVALS if ll[r] >= mx - 1e-9]
    others = [ll[r] for r in RIVALS if r not in best_set]
    # Ties happen when two rivals predict the same counts (Carnot = USL whenever review never binds);
    # the data cannot separate them, so the tie is reported rather than broken by list order.
    return dict(windows=rows, ll=ll, best="|".join(best_set), tie=len(best_set) > 1,
                log_lr_vs_carnot={r: ll[r] - ll["carnot"] for r in RIVALS},
                lr_best_vs_next=math.exp(mx - max(others)) if others else 1.0)


def _pool_V(S):
    n = sum(s["reviews"] for s in S)
    bh = sum(s["busy_hours"] for s in S)
    return n, bh, (n / bh if bh > 0 else math.nan), list(rate_ci(n, bh))


def _pool_half(S, i):
    n = sum(s["V_half"][i]["n"] for s in S)
    bh = sum(s["V_half"][i]["busy_h"] for s in S)
    return n, bh, (n / bh if bh > 0 else math.nan)


def ratio_ci(n1, t1, n2, t2, level=0.95):
    """Exact interval for (n2/t2)/(n1/t1) from the conditional binomial (Clopper-Pearson)."""
    n = n1 + n2
    if n == 0 or t1 <= 0 or t2 <= 0:
        return [math.nan, math.nan]
    a = (1 - level) / 2
    lo = stats.beta.ppf(a, n2, n1 + 1) if n2 > 0 else 0.0
    hi = stats.beta.ppf(1 - a, n2 + 1, n1) if n1 > 0 else 1.0
    conv = lambda p: math.inf if p >= 1 else p / (1 - p) * (t1 / t2)
    return [conv(lo), conv(hi)]


def _material_and_significant(ratio, ci):
    """Noise-aware band rule: outside +/-25% AND the 95% interval excludes 1."""
    if ratio != ratio:
        return False
    return (not _in_band(ratio)) and (ci[0] > 1 or ci[1] < 1)


def _in_band(x):
    return (1 - BAND) <= x <= (1 + BAND) if x == x else False


def v_checks(S_lo, S_hi, V_pilot, pilot_reviews=None, pilot_busy=None):
    out = {}
    for lab, SS in (("low", S_lo), ("high", S_hi)):
        n, bh, V, ci = _pool_V(SS)
        n1, b1, V1 = _pool_half(SS, 0)
        n2, b2, V2 = _pool_half(SS, 1)
        rp = V / V_pilot
        rci = ratio_ci(pilot_reviews, pilot_busy, n, bh) if pilot_reviews else [math.nan, math.nan]
        dr = V2 / V1 if V1 and V1 == V1 else math.nan
        dci = ratio_ci(n1, b1, n2, b2)
        out[lab] = dict(reviews=n, busy_hours=bh, V=V, V_ci=ci, ratio_to_pilot=rp, ratio_to_pilot_ci=rci,
                        in_band=_in_band(rp), off_band_significant=_material_and_significant(rp, rci),
                        half1=V1, half2=V2, drift_ratio=dr, drift_ci=dci,
                        drift_in_band=_in_band(dr), drift_off_band_significant=_material_and_significant(dr, dci))
    out["high_over_low"] = out["high"]["V"] / out["low"]["V"]
    out["high_over_low_ci"] = ratio_ci(out["low"]["reviews"], out["low"]["busy_hours"],
                                       out["high"]["reviews"], out["high"]["busy_hours"])
    out["high_low_in_band"] = _in_band(out["high_over_low"])
    out["high_low_off_band_significant"] = _material_and_significant(out["high_over_low"], out["high_over_low_ci"])
    per = []
    for s in S_lo + S_hi:
        row = dict(run_id=s["run_id"], N=s["n_workers"], V=s["V"], V_ci=s["V_ci"], ratio=s["V"] / V_pilot,
                   in_band=_in_band(s["V"] / V_pilot), halves=[])
        for h in s["V_half"]:
            row["halves"].append(dict(V=h["V"], n=h["n"], ratio=h["V"] / V_pilot if h["V"] == h["V"] else math.nan,
                                      in_band=_in_band(h["V"] / V_pilot) if h["V"] == h["V"] else False))
        per.append(row)
    out["per_window"] = per
    return out


def b_checks(S_lo, S_hi):
    """S3 (PLAN-v4): b_review = review bounces / reviews, one-sided Fisher exact for a rise. The other causes
    are merge-queue outcomes of approved changes and are reported per approval with a merge-queue result."""
    def pool(SS):
        rev = sum(s["reviews"] for s in SS)
        bb = sum(s["bounces_total"] for s in SS)
        br = sum(s["bounces"]["review"] for s in SS)
        ar = sum(s["approvals_resolved"] for s in SS)
        bh = sum(s["bounces"]["escaped_defect"] + s["bounces"]["integration_failure"] for s in SS)
        causes = {c: sum(s["bounces"][c] for s in SS) for c in S_lo[0]["bounces"]}
        bo = causes.get("rebase_conflict", 0) + causes.get("visible_fail", 0)
        return dict(reviews=rev, bounces=bb, bounces_review=br, b=bb / rev if rev else math.nan,
                    b_review=br / rev if rev else math.nan, b_hidden=bh / ar if ar else math.nan,
                    b_other=bo / ar if ar else math.nan, approvals_resolved=ar, causes=causes,
                    per_approval={c: causes[c] / ar if ar else math.nan for c in causes if c != "review"})
    lo, hi = pool(S_lo), pool(S_hi)
    p_up = two_prop_one_sided(lo["bounces"], lo["reviews"], hi["bounces"], hi["reviews"])
    p_rev = two_prop_one_sided(lo["bounces_review"], lo["reviews"], hi["bounces_review"], hi["reviews"])
    p_cause = {c: two_prop_one_sided(lo["causes"][c], lo["approvals_resolved"], hi["causes"][c], hi["approvals_resolved"])
               for c in lo["causes"] if c != "review"}
    return dict(low=lo, high=hi, p_rising=p_up, p_review_rising=p_rev, p_cause_rising_per_approval=p_cause)


def attempt_checks(S_lo, S_hi, P: Params):
    def pool(SS):
        # attempts, attempt_hours, worker_hours stop at the task-supply exhaustion minute in flagged windows
        a = sum(s["attempts"] for s in SS)
        h = sum(s.get("attempt_hours", s["hours"]) for s in SS)
        wh = sum(s["worker_hours"] for s in SS)
        return dict(attempts=a, hours=h, worker_hours=wh, per_hour=a / h if h else math.nan,
                    per_agent_hour=a / wh if wh else math.nan,
                    supply_truncated=[s["run_id"] for s in SS if s.get("supply_truncated")],
                    attempts_full=sum(s.get("attempts_full", s["attempts"]) for s in SS))
    lo, hi = pool(S_lo), pool(S_hi)
    ratio, p_up, p_down = rate_ratio_test(lo["attempts"], lo["hours"], hi["attempts"], hi["hours"])
    ratio_pa, pa_up, pa_down = rate_ratio_test(lo["attempts"], lo["worker_hours"], hi["attempts"], hi["worker_hours"])
    nl, nh = S_lo[0]["n_workers"], S_hi[0]["n_workers"]
    usl_pa = (float(X_usl(nh, P.alpha, P.beta)) / nh) / (float(X_usl(nl, P.alpha, P.beta)) / nl)
    return dict(low=lo, high=hi, total_ratio=ratio, p_total_rising=p_up, p_total_falling=p_down,
                per_agent_ratio=ratio_pa, p_per_agent_rising=pa_up, p_per_agent_falling=pa_down,
                usl_predicted_per_agent_ratio=usl_pa)


def capped_vs_uncapped(rv):
    """P1: log LR of Carnot (review-capped) against the best uncapped rival. Positive = Carnot higher."""
    ll = rv["ll"]
    best_u = max(UNCAPPED, key=lambda r: ll[r])
    log_lr = ll["carnot"] - ll[best_u]
    return dict(ll_carnot=ll["carnot"], best_uncapped=best_u, ll_best_uncapped=ll[best_u], log_lr=log_lr,
                lr=_exp(log_lr), carnot_higher=bool(log_lr > 1e-9), tie=bool(abs(log_lr) <= 1e-9))


def _welch_log(a, b):
    a = np.log(np.asarray([x for x in a if x and x > 0], float))
    b = np.log(np.asarray([x for x in b if x and x > 0], float))
    if len(a) < 3 or len(b) < 3:
        return dict(p=math.nan, n_low=len(a), n_high=len(b), ratio_geo=math.nan)
    t = stats.ttest_ind(b, a, equal_var=False)
    return dict(p=float(t.pvalue), t=float(t.statistic), n_low=len(a), n_high=len(b),
                ratio_geo=float(math.exp(b.mean() - a.mean())))


def v_constancy(S_lo, S_hi):
    """PLAN-v4.1 section 6.1: the Welch test on log review durations (high vs low) is the reviewer-pace test,
    FAIL iff two-sided p < 0.05. V(high)/V(low) and its exact 95% interval are descriptive (no band, no
    equivalence claim)."""
    n1, b1, V1, ci1 = _pool_V(S_lo)
    n2, b2, V2, ci2 = _pool_V(S_hi)
    ratio = V2 / V1 if V1 and V1 == V1 else math.nan
    ci = ratio_ci(n1, b1, n2, b2)
    d_lo = [x for s in S_lo for x in s.get("review_durations_s", [])]
    d_hi = [x for s in S_hi for x in s.get("review_durations_s", [])]
    w = _welch_log(d_lo, d_hi)
    from derive import _cv
    return dict(low=dict(reviews=n1, busy_hours=b1, V=V1, V_ci=ci1, review_time_cv=_cv(d_lo)),
                high=dict(reviews=n2, busy_hours=b2, V=V2, V_ci=ci2, review_time_cv=_cv(d_hi)),
                ratio=ratio, ratio_ci=ci, vdur=w,
                vdur_code="N/A" if w["p"] != w["p"] else ("FAIL" if w["p"] < 0.05 else "PASS"),
                open_review_clipped=[s["run_id"] for s in S_lo + S_hi if s.get("review_open_at_end")])


def escape_analysis(approvals, model="clogit_task"):
    rows = [a for a in approvals if a["depth"] is not None]
    n_ev = int(sum(a["escaped"] for a in rows))
    out = dict(n=len(rows), events=n_ev, min_events=MIN_ESCAPES, model=model,
               descriptive_only=n_ev < MIN_ESCAPES)
    bands = [(0, 0), (1, 2), (3, 5), (6, 10 ** 6)]
    out["by_depth"] = []
    for lo, hi in bands:
        sub = [a for a in rows if lo <= a["depth"] <= hi]
        out["by_depth"].append(dict(depth=f"{lo}+" if hi > 10 ** 5 else (f"{lo}" if lo == hi else f"{lo}-{hi}"),
                                    approvals=len(sub), escaped=int(sum(a["escaped"] for a in sub)),
                                    rate=(sum(a["escaped"] for a in sub) / len(sub)) if sub else math.nan))
    if n_ev == 0 or n_ev == len(rows):
        out.update(coef_depth=math.nan, p_one_sided=math.nan, p_two_sided=math.nan, fit_ok=False)
        return out
    if model == "clogit_task_first":
        seen = set()
        rr = []
        for a in sorted(rows, key=lambda a: (a["run_id"], a["task"], a["t_review_min"])):
            key = (a["run_id"], a["task"])
            if key not in seen:
                seen.add(key)
                rr.append(a)
        rows = rr
    y = np.array([float(a["escaped"]) for a in rows])
    depth = np.array([a["depth"] for a in rows], float)
    tmin = np.array([a["t_review_min"] for a in rows], float) / 10.0
    wins = [a["run_id"] for a in rows]
    fe = window_dummies(wins)
    if model.startswith("clogit"):
        strata = np.array([a["task"] for a in rows])
        cols = {"depth": depth, "time_10min": tmin, **fe}
        names = list(cols)
        Xf = np.column_stack([cols[k] for k in names])
        full = clogit_fit(Xf, y, strata, names)
        if not full["ok"] or "depth" in full["dropped"]:
            out.update(coef_depth=math.nan, p_one_sided=math.nan, p_two_sided=math.nan, fit_ok=False,
                       informative_strata=full["informative_strata"], dropped=full["dropped"])
            return out
        red_names = [k for k in names if k != "depth"]
        red = clogit_fit(np.column_stack([cols[k] for k in red_names]), y, strata, red_names)
        red_ll = red["ll"] if red["ok"] else _clogit_null_ll(y, strata)
        cd = float(full["coef"][0])
        p2, p1 = lr_one_sided(full["ll"], red_ll, cd)
        out.update(coef_depth=cd, se_depth=float(full["se"][0]), or_per_depth=_exp(cd),
                   coef_time=float(full["coef"][1]) if not math.isnan(full["coef"][1]) else math.nan,
                   p_one_sided=p1, p_two_sided=p2, fit_ok=True, informative_strata=full["informative_strata"],
                   informative_rows=full["informative_rows"], events_informative=full["events_informative"],
                   dropped=full["dropped"], separated=full.get("separated", False), n_used=len(rows))
        return out
    # unconditional logistic, window FE, cluster-robust by task
    cols = {"depth": depth, "time_10min": tmin, **fe}
    from common import _design
    names, Xf = _design(cols, len(y))
    full = logit_fit(Xf, y, names, cluster=[a["task"] for a in rows])
    rn, Xr = _design({k: v for k, v in cols.items() if k != "depth"}, len(y))
    red = logit_fit(Xr, y, rn)
    cd = float(full["coef"][1])
    p2, p1 = lr_one_sided(full["ll"], red["ll"], cd)
    se_cl = float(full["se_cluster"][1])
    z = cd / se_cl if se_cl > 0 else math.nan
    out.update(coef_depth=cd, se_depth=float(full["se"][1]), se_depth_cluster=se_cl, or_per_depth=_exp(cd),
               coef_time=float(full["coef"][2]), p_one_sided=p1, p_two_sided=p2,
               p_one_sided_wald_cluster=float(stats.norm.sf(z)) if z == z else math.nan,
               fit_ok=True, separated=full["separated"], n_used=len(rows))
    return out


def _clogit_null_ll(y, strata):
    ll = 0.0
    for s in set(strata.tolist()):
        idx = strata == s
        n, d = int(idx.sum()), int(y[idx].sum())
        if 0 < d < n:
            ll -= math.log(math.comb(n, d))
    return ll


def collision_analysis(prs, with_model_form=True):
    res = [p for p in prs if p["resolved"]]
    out = dict(n_resolved=len(res), n_all=len(prs), n_censored_excluded=len(prs) - len(res),
               events=int(sum(p["bounced"] for p in res)))
    if len(res) < 5 or out["events"] in (0, len(res)):
        out.update(fit_ok=False)
        return out
    y = np.array([float(p["bounced"]) for p in res])
    k = np.array([p["k"] for p in res], float)
    m = np.array([p["m"] for p in res], float)
    fe = window_dummies([p["run_id"] for p in res])
    from common import _design
    names, X = _design({"k": k, "m": m, **fe}, len(y))
    fit = logit_fit(X, y, names)
    rn, Xr = _design({"m": m, **fe}, len(y))
    red_k = logit_fit(Xr, y, rn)
    rn2, Xr2 = _design({"k": k, **fe}, len(y))
    red_m = logit_fit(Xr2, y, rn2)
    ck, cm = float(fit["coef"][1]), float(fit["coef"][2])
    pk2, pk1 = lr_one_sided(fit["ll"], red_k["ll"], ck)
    pm2, pm1 = lr_one_sided(fit["ll"], red_m["ll"], cm)
    out.update(fit_ok=True, coef_k=ck, se_k=float(fit["se"][1]), or_k=_exp(ck), p_k_two_sided=pk2, p_k_one_sided=pk1,
               coef_m=cm, se_m=float(fit["se"][2]), or_m=_exp(cm), p_m_two_sided=pm2, p_m_one_sided=pm1,
               mean_k=float(k.mean()), mean_m=float(m.mean()))
    if with_model_form:
        mf = collision_fit(y, k, m, [p["run_id"] for p in res])
        out.update(p_hat=mf["p_hat"], p_ci=mf["p_ci"], p_m_hat=mf["p_m_hat"])
    return out


def naive_not_finished_slope(prs):
    """The accounting review v2 (C) warned about: outcome = 'not finished by the end', censored included."""
    y = np.array([float(not p["finished"]) for p in prs])
    if len(y) < 5 or y.sum() in (0, len(y)):
        return dict(fit_ok=False)
    from common import _design
    names, X = _design({"k": np.array([p["k"] for p in prs], float),
                        **window_dummies([p["run_id"] for p in prs])}, len(y))
    fit = logit_fit(X, y, names)
    rn, Xr = _design(window_dummies([p["run_id"] for p in prs]), len(y))
    red = logit_fit(Xr, y, rn)
    p2, p1 = lr_one_sided(fit["ll"], red["ll"], float(fit["coef"][1]))
    return dict(fit_ok=True, coef_k=float(fit["coef"][1]), p_k_one_sided=p1, p_k_two_sided=p2)


def alpha_beta_descriptive(pilot, S_lo, S_hi):
    try:
        path = Path(__file__).resolve().parents[3] / "plugins/carnot/skills/carnot/scripts/carnot.py"
        spec = importlib.util.spec_from_file_location("carnot_script", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
    except Exception as e:  # pragma: no cover - descriptive only
        return dict(error=f"carnot.py not importable: {e}")
    rows = [(float(pilot["n_pilot"]), pilot["lambda_pilot"] * pilot["n_pilot"])]
    for SS in (S_lo, S_hi):
        a = sum(s["attempts"] for s in SS)
        h = sum(s["hours"] for s in SS)
        rows.append((float(SS[0]["n_workers"]), a / h))
    res = mod.fit_usl(rows)
    res["rows_N_attempts_per_hour"] = rows
    res["note"] = "descriptive only (PLAN-v3 section 7); three sizes cannot identify alpha and beta."
    return res


# ---------------------------------------------------------------------- main scoring
def score(derived, pilot, *, rival_rework=PLAN_V4["rival_rework"], escape_model=PLAN_V4["escape_model"],
          cv=CV_OVERDISPERSION, do_escape=True, do_collision=True, do_fit=True, window_min=None, warmup_min=None,
          plan="v4"):
    """plan='v4' (default): PLAN-v4 codings. plan='v3': the superseded PLAN-v3 codings (design search only)."""
    S = [d["summary"] for d in derived]
    sizes = sorted({s["n_workers"] for s in S})
    if len(sizes) != 2:
        raise ValueError(f"expected two fleet sizes, got {sizes}")
    nl, nh = sizes
    S_lo = [s for s in S if s["n_workers"] == nl]
    S_hi = [s for s in S if s["n_workers"] == nh]
    wmin = window_min or S[0]["window_min"]
    umin = warmup_min if warmup_min is not None else S[0]["warmup_min"]
    P = Params.from_pilot(pilot, window_min=wmin, warmup_min=umin, rival_rework=rival_rework)
    R = dict(plan=plan, sizes=dict(N_low=nl, N_high=nh), pilot=pilot, params=P.as_dict(), cv=cv)
    R["windows"] = S
    R["rivals"] = rival_scores(S, P, cv)
    R["rivals_by_reading"] = {}
    for reading in READINGS:
        if reading == "completion" and pilot.get("completion") is None:
            continue
        Pr = Params.from_pilot(pilot, window_min=wmin, warmup_min=umin, rival_rework=reading)
        R["rivals_by_reading"][reading] = rival_scores(S, Pr, cv)
    f_lo = sum(s["finished"] for s in S_lo) / len(S_lo)
    f_hi = sum(s["finished"] for s in S_hi) / len(S_hi)
    ratio = f_hi / f_lo if f_lo > 0 else math.inf
    qshare_hi = sum(s["queue_nonempty_share"] for s in S_hi) / len(S_hi)
    R["pooled"] = dict(finished_low_mean=f_lo, finished_high_mean=f_hi, ratio=ratio,
                       queue_nonempty_share_high=qshare_hi,
                       queue_nonempty_share_low=sum(s["queue_nonempty_share"] for s in S_lo) / len(S_lo))
    R["V"] = v_checks(S_lo, S_hi, pilot["V"], pilot.get("reviews"), pilot.get("busy_hours"))
    pr_lo = sum(predict_window(P, s["n_workers"], hours=s["hours"])["carnot"]["finished"] for s in S_lo) / len(S_lo)
    pr_hi = sum(predict_window(P, s["n_workers"], hours=s["hours"])["carnot"]["finished"] for s in S_hi) / len(S_hi)
    R["pooled"]["carnot_predicted_ratio"] = pr_hi / pr_lo if pr_lo > 0 else math.inf
    R["pooled"]["ratio_over_predicted"] = ratio / R["pooled"]["carnot_predicted_ratio"]
    R["b"] = b_checks(S_lo, S_hi)
    R["attempts"] = attempt_checks(S_lo, S_hi, P)
    apprs = [a for d in derived for a in d["approvals"]]
    prs = [p for d in derived for p in d["prs"]]
    if do_escape:
        R["escape"] = escape_analysis(apprs, escape_model)
        R["escape_sensitivity"] = {m: escape_analysis(apprs, m) for m in ESCAPE_MODELS if m != escape_model}
    if do_collision:
        R["collision"] = collision_analysis(prs)
        R["collision_naive_not_finished"] = naive_not_finished_slope(prs)
    if do_fit:
        R["alpha_beta_descriptive"] = alpha_beta_descriptive(pilot, S_lo, S_hi)

    # predicted finished at high (pooled) for O2
    mus_hi = [predict_window(P, s["n_workers"], hours=s["hours"])["carnot"]["finished"] for s in S_hi]
    lo95, hi95 = pooled_interval(mus_hi, cv)
    obs_hi = sum(s["finished"] for s in S_hi)
    R["O2_detail"] = dict(observed_high_total=obs_hi, predicted_high_total=sum(mus_hi), interval95=[lo95, hi95])
    if plan == "v3":
        R["outcomes"] = code_outcomes_v3(R)
        return _clean(R)
    R["primary"] = capped_vs_uncapped(R["rivals"])
    R["primary_by_reading"] = {k: capped_vs_uncapped(v) for k, v in R["rivals_by_reading"].items()}
    R["supply_flagged"] = [s["run_id"] for s in S if s.get("supply_flagged")]
    R["without_flagged"] = without_flagged(S, P, cv)
    R["V4"] = v_constancy(S_lo, S_hi)
    rco = pilot.get("review_cv_ok")
    R["review_cv_ok"] = bool(rco) if isinstance(rco, bool) else False
    R["review_cv_ok_recorded"] = isinstance(rco, bool)
    R["supply"] = [dict(run_id=s["run_id"], N=s["n_workers"], task_supply=s.get("task_supply"),
                        t_exhausted_min=s.get("t_exhausted_min"), attempts=s["attempts"],
                        attempts_full=s.get("attempts_full"), flagged=bool(s.get("supply_flagged")))
                   for s in S if s.get("supply_truncated")]
    R["supply_unknown"] = [s["run_id"] for s in S if s.get("task_supply") is None]
    R["operating_characteristics"] = V4_OC
    R["outcomes"] = code_outcomes_v4(R)
    return _clean(R)


def without_flagged(S, P: Params, cv=CV_OVERDISPERSION):
    """PLAN-v4.1 section 6.6: P1 and O2 recomputed on the windows that did not run out of tasks before minute
    110 (derive.py `supply_flagged`). None when no window is flagged (identical to the main result); N/A parts
    when a size has no unflagged window left."""
    flagged = [s["run_id"] for s in S if s.get("supply_flagged")]
    if not flagged:
        return None
    keep = [s for s in S if not s.get("supply_flagged")]
    nh = max(s["n_workers"] for s in S)
    out = dict(flagged=flagged, windows_kept=[s["run_id"] for s in keep], P1=None, O2=None)
    if len({s["n_workers"] for s in keep}) == 2:
        out["P1"] = capped_vs_uncapped(rival_scores(keep, P, cv))
    hi = [s for s in keep if s["n_workers"] == nh]
    if hi:
        mus = [predict_window(P, s["n_workers"], hours=s["hours"])["carnot"]["finished"] for s in hi]
        lo95, hi95 = pooled_interval(mus, cv)
        obs = sum(s["finished"] for s in hi)
        out["O2"] = dict(observed_high_total=obs, predicted_high_total=sum(mus), interval95=[lo95, hi95],
                         windows=len(hi), inside=bool(lo95 <= obs <= hi95))
    return out


def _o(id_, text, value, rule, code, note=""):
    return dict(id=id_, statement=text, value=value, rule=rule, code=code, note=note)


def _g(id_, grade, text, value, rule, code, note="", counts_as=None, role=None):
    return dict(id=id_, grade=grade, counts_as=counts_as or grade, role=role, statement=text, value=value, rule=rule,
                code=code, note=note)


# PLAN-v4 section 7.1 (v4.2): the order results are listed in results.json and RESULTS-draft.md.
V4_ORDER = ("O2", "O2-nf", "P1", "P1-nf", "Vdur", "S3", "O3", "Vratio", "S1r", "S2r", "RANK", "ESC", "COLL", "BOUNCE")


def code_outcomes_v4(R):
    """PLAN-v4 section 1 as reframed in section 7 (v4.2), graded in advance; listed in V4_ORDER."""
    O = []
    oc = V4_OC
    nl, nh = R["sizes"]["N_low"], R["sizes"]["N_high"]
    pr = R["primary"]
    sup_hi = [x for x in R.get("supply", []) if x["N"] == nh]
    note = (f"best uncapped rival: {RIVAL_LABEL[pr['best_uncapped']]}; log LR (Carnot - best uncapped) = {pr['log_lr']:+.2f}, "
            f"LR = {pr['lr']:.3g}" + (" (tie: identical predictions, counted as not higher)" if pr["tie"] else "") +
            f". Simulated: correct {oc['primary_correct']['carnot']:.2f} under Carnot truth, "
            f"{oc['primary_correct']['usl']:.2f} / {oc['primary_correct']['amdahl']:.2f} / {oc['primary_correct']['linear']:.2f} "
            f"under USL / Amdahl / linear.")
    if sup_hi:
        note += (" Supply warning: " + ", ".join(f"{x['run_id']} ran out of tasks at min {x['t_exhausted_min']:.0f}" for x in sup_hi)
                 + "; finished counts in those windows may be supply-limited.")
    wf = R.get("without_flagged")
    if wf:
        note += (f" Windows flagged for running out of tasks before minute 110: {', '.join(wf['flagged'])}; "
                 "see P1-nf for the result without them.")
    note += (" Manipulation check: the harness's fixed-capacity reviewer is the binding limit by design, so a FAIL "
             "indicates a harness or calibration fault, not support for an uncapped rival. " + UNCAPPED_TRUTH_FOOTNOTE)
    O.append(_g("P1", CONFIRMATORY, "Manipulation check: the harness's fixed-capacity reviewer caps output (Carnot's "
                "review-capped prediction has a higher likelihood than the best uncapped rival: USL, Amdahl, linear)",
                dict(log_lr=pr["log_lr"], lr=pr["lr"], best_uncapped=pr["best_uncapped"]),
                f"PASS if log L(Carnot) > max log L(USL, Amdahl, linear), `{R['params']['rival_rework']}` reading, NB CV "
                f"{R['cv']}", "PASS" if pr["carnot_higher"] else "FAIL", note, role=ROLE_MANIPULATION))
    d2 = R["O2_detail"]
    o2 = d2["interval95"][0] <= d2["observed_high_total"] <= d2["interval95"][1]
    O.append(_g("O2", CONFIRMATORY, f"Primary quantitative test: finished at N = {nh} lies inside Carnot's 95% predictive "
                "interval, (1 - b) x V x hours with V and b from the pilot", d2,
                f"PASS if the N = {nh} windows' total finished count is in the pooled 95% predictive interval "
                "(Poisson-gamma, CV 0.3 per window)", "PASS" if o2 else "FAIL",
                f"observed {d2['observed_high_total']}, predicted {d2['predicted_high_total']:.1f} "
                f"({d2['interval95'][0]}-{d2['interval95'][1]}); simulated false-alarm rate under Carnot truth "
                f"{oc['O2_false_alarm']['service_cv_1']:.2f} at review-time CV 1, {oc['O2_false_alarm']['service_cv_0_5']:.2f} at 0.5",
                role=ROLE_PRIMARY))
    # PLAN-v4.1 section 6.6: P1 and O2 without the windows that ran out of tasks before minute 110
    if not wf:
        c1, n1_, c2, n2_ = "N/A", "no window flagged: identical to P1", "N/A", "no window flagged: identical to O2"
        v1 = v2 = None
    else:
        p1n = wf["P1"]
        if p1n is None:
            c1, n1_, v1 = "N/A", "no unflagged window left at one of the sizes", None
        else:
            c1 = "PASS" if p1n["carnot_higher"] else "FAIL"
            v1 = dict(log_lr=p1n["log_lr"], lr=p1n["lr"], best_uncapped=p1n["best_uncapped"])
            n1_ = f"log LR {p1n['log_lr']:+.2f} vs {RIVAL_LABEL[p1n['best_uncapped']]} on {len(wf['windows_kept'])} windows"
        o2n = wf["O2"]
        if o2n is None:
            c2, n2_, v2 = "N/A", f"no unflagged N = {nh} window left", None
        else:
            c2, v2 = ("PASS" if o2n["inside"] else "FAIL"), o2n
            n2_ = (f"observed {o2n['observed_high_total']} in {o2n['windows']} window(s), predicted "
                   f"{o2n['predicted_high_total']:.1f} ({o2n['interval95'][0]}-{o2n['interval95'][1]})")
    O.append(_g("P1-nf", CONFIRMATORY, "P1 without the windows that ran out of tasks before minute 110 (reported beside P1)",
                v1, "as P1, on the unflagged windows only", c1, n1_, role=ROLE_MANIPULATION))
    O.append(_g("O2-nf", CONFIRMATORY, f"O2 without the N = {nh} windows that ran out of tasks before minute 110 "
                "(reported beside O2)", v2, "as O2, on the unflagged windows only", c2, n2_, role=ROLE_PRIMARY))
    b = R["b"]
    pr_ = b["p_review_rising"]
    O.append(_g("S3", DESCRIPTIVE, "The review-bounce share b_review does not rise with fleet size",
                dict(low=b["low"]["b_review"], high=b["high"]["b_review"], p_one_sided=pr_),
                "FAIL if b_review(high) > b_review(low) at one-sided Fisher exact p < 0.05 (review bounces / reviews, "
                "pooled per size); other bounce causes reported separately (BOUNCE)",
                "N/A" if pr_ != pr_ else ("FAIL" if pr_ < 0.05 else "PASS"),
                f"b_review {b['low']['bounces_review']}/{b['low']['reviews']} = {b['low']['b_review']:.2f} at N = {nl}, "
                f"{b['high']['bounces_review']}/{b['high']['reviews']} = {b['high']['b_review']:.2f} at N = {nh}; "
                f"one-sided p = {pr_:.3f}"))
    A = R["attempts"]
    if A["p_total_rising"] < 0.05:
        c3 = "PASS"
    elif A["p_total_falling"] < 0.05:
        c3 = "FAIL"
    else:
        c3 = "INCONCLUSIVE"
    trunc = A["low"]["supply_truncated"] + A["high"]["supply_truncated"]
    O.append(_g("O3", DESCRIPTIVE, "Attempts rise with fleet size (fleet first-attempt rate)",
                dict(total_ratio=A["total_ratio"], p_one_sided=A["p_total_rising"], per_agent_ratio=A["per_agent_ratio"],
                     usl_per_agent_ratio=A["usl_predicted_per_agent_ratio"]),
                "PASS if the high/low first-attempt rate ratio > 1 at one-sided exact p < 0.05; FAIL if < 1 at p < 0.05; "
                "else INCONCLUSIVE", c3,
                f"ratio {A['total_ratio']:.2f}, p = {A['p_total_rising']:.3g}" +
                (f"; attempts and hours cut at task-supply exhaustion in {', '.join(trunc)}" if trunc else "")))
    V = R["V4"]
    ok = R["review_cv_ok"]
    counts = CONFIRMATORY if ok else DESCRIPTIVE
    why = ("review_cv_ok = true in pilot.json: confirmatory" if ok else
           ("review_cv_ok = false in pilot.json: descriptive" if R["review_cv_ok_recorded"]
            else "review_cv_ok not recorded in pilot.json: descriptive"))
    w = V["vdur"]
    vp = oc["Vdur_power"]
    O.append(_g("Vdur", CONDITIONAL, "The reviewer's pace does not change with load (Welch t-test on log review durations, "
                f"N = {nh} vs N = {nl})",
                dict(p=w["p"], geo_mean_ratio=w["ratio_geo"], n_low=w["n_low"], n_high=w["n_high"]),
                "FAIL iff two-sided p < 0.05, else PASS; no equivalence claim", V["vdur_code"],
                f"geometric-mean duration ratio {_f(w['ratio_geo'])}, p = {_f(w['p'], '{:.3f}')}, n = {w['n_low']} / {w['n_high']}; "
                f"simulated power vs a +/-25% reviewer {vp['service_cv_0_5']:.2f} at review-time CV 0.5, "
                f"{vp['service_cv_1']:.2f} at CV 1; {why}", counts_as=counts))
    O.append(_g("Vratio", DESCRIPTIVE, f"V(N = {nh}) / V(N = {nl}) with its exact 95% interval (no equivalence claim)",
                dict(ratio=V["ratio"], ci=V["ratio_ci"]), "reported, not coded", "REPORTED",
                f"V {_f(V['low']['V'])} -> {_f(V['high']['V'])}, ratio {_f(V['ratio'])} ({_f(V['ratio_ci'][0])}-"
                f"{_f(V['ratio_ci'][1])})"))
    pooled = R["pooled"]
    sat = pooled["queue_nonempty_share_high"] >= 0.5
    rp = pooled["ratio_over_predicted"]
    nr = (f"observed ratio {pooled['ratio']:.2f} vs Carnot-predicted {pooled['carnot_predicted_ratio']:.2f}; queue non-empty "
          f"{pooled['queue_nonempty_share_high']:.0%} of the N = {nh} windows")
    O.append(_g("S1r", DESCRIPTIVE, "Surprise: finished ratio >= 1.3 x Carnot's predicted ratio with the queue non-empty",
                rp, "FAIL if (observed / predicted ratio) >= 1.3 and queue non-empty >= 50% of the N_high windows",
                "N/A" if not sat else ("FAIL" if rp >= 1.3 else "PASS"),
                nr + f"; false-alarm rate under Carnot {oc['S1r_false_alarm']:.2f}"))
    O.append(_g("S2r", DESCRIPTIVE, "Surprise: finished ratio <= 0.7 x Carnot's predicted ratio (not a criterion)",
                rp, "FAIL if (observed / predicted ratio) <= 0.7 and queue non-empty >= 50% of the N_high windows",
                "N/A" if not sat else ("FAIL" if rp <= 0.7 else "PASS"),
                nr + f"; fires {oc['S2r_false_alarm']:.2f} of the time under Carnot's own truth, so it is not a criterion"))
    rv = R["rivals"]
    order = sorted(RIVALS, key=lambda r: -rv["ll"][r])
    O.append(_g("RANK", DESCRIPTIVE, "Four-way ranking of the rivals (USL vs Amdahl is not claimed)",
                dict(order=order, ll=rv["ll"]), "reported with the simulated confusion matrix", "REPORTED",
                "highest likelihood: " + _label(rv["best"]) + "; order " + " > ".join(order)))
    esc = R.get("escape")
    if esc is not None:
        if esc["descriptive_only"]:
            ce, ne = "N/A", f"{esc['events']} escaped defects < {MIN_ESCAPES}: not modelled"
        elif not esc.get("fit_ok"):
            ce, ne = "N/A", "model not estimable"
        else:
            ce = "REPORTED"
            ne = f"OR per waiting change {esc['or_per_depth']:.2f}, one-sided p = {esc['p_one_sided']:.3f}"
        O.append(_g("ESC", DESCRIPTIVE, "Escaped defects vs reviewer queue depth", esc.get("coef_depth"),
                    f"`{esc['model']}`, window FE, task-clustered; modelled only with >= {MIN_ESCAPES} events", ce,
                    ne + f"; simulated power {oc['escape_power']['a05']:.2f} (a null result is not evidence)"))
    col = R.get("collision")
    if col is not None:
        if col.get("fit_ok"):
            nc = (f"k OR {col['or_k']:.3f} (one-sided p {col['p_k_one_sided']:.3f}); p-hat {col.get('p_hat', math.nan):.4f}")
            cc = "REPORTED"
        else:
            nc, cc = "not estimable", "N/A"
        O.append(_g("COLL", DESCRIPTIVE, "Collisions: bounced at least once vs changes in flight (k) and file overlap (m)",
                    None, "logistic with window FE, censored first attempts excluded; p-hat in the model form", cc,
                    nc + f"; simulated k-slope power {oc['collision_power']['p01_a05']:.2f} at p = 0.01, "
                    f"{oc['collision_power']['p05_a05']:.2f} at p = 0.05"))
    O.append(_g("BOUNCE", DESCRIPTIVE, "Bounce causes other than review (rebase conflict, visible fail, escaped defect, "
                "integration failure) per approval with a merge-queue result", b["p_cause_rising_per_approval"],
                "reported by size; not a criterion", "REPORTED",
                "; ".join(f"{c}: {b['low']['causes'][c]} -> {b['high']['causes'][c]}" for c in b["low"]["causes"] if c != "review")))
    rank = {k: i for i, k in enumerate(V4_ORDER)}
    return sorted(O, key=lambda o: rank.get(o["id"], len(rank)))


def code_outcomes_v3(R):
    """SUPERSEDED PLAN-v3 codings (score(plan='v3'), used by the design-search scripts only)."""
    O = []
    V = R["V"]
    pooled = R["pooled"]
    sat = pooled["queue_nonempty_share_high"] >= 0.5
    ratio = pooled["ratio"]
    # Surprises (PASS = the surprise did not happen)
    if not sat:
        c1 = c2 = "N/A"
        note = f"review queue non-empty only {pooled['queue_nonempty_share_high']:.0%} of the N_high windows"
    else:
        c1 = "FAIL" if ratio >= 1.3 else "PASS"
        c2 = "FAIL" if ratio <= 0.7 else "PASS"
        note = f"queue non-empty {pooled['queue_nonempty_share_high']:.0%} of the N_high windows"
    O.append(_o("S1", "finished(high) >= 1.3 x finished(low) while the queue is non-empty (reviewer scales with load)",
                ratio, "FAIL if ratio >= 1.3 and queue non-empty >= 50% of the N_high windows (post warm-up)", c1, note))
    O.append(_o("S2", "finished(high) <= 0.7 x finished(low) while the queue is non-empty (re-review churn / slowing)",
                ratio, "FAIL if ratio <= 0.7 and queue non-empty >= 50% of the N_high windows", c2, note))
    rp = pooled["ratio_over_predicted"]
    c1r = "N/A" if not sat else ("FAIL" if rp >= 1.3 else "PASS")
    c2r = "N/A" if not sat else ("FAIL" if rp <= 0.7 else "PASS")
    note_r = f"observed ratio {ratio:.2f} vs Carnot-predicted {pooled['carnot_predicted_ratio']:.2f}; " + note
    O.append(_o("S1r", "S1 relative to the prediction: observed ratio >= 1.3 x Carnot's predicted ratio, queue non-empty",
                rp, "FAIL if (observed ratio / predicted ratio) >= 1.3 and queue non-empty >= 50% of the N_high windows",
                c1r, note_r))
    O.append(_o("S2r", "S2 relative to the prediction: observed ratio <= 0.7 x Carnot's predicted ratio, queue non-empty",
                rp, "FAIL if (observed ratio / predicted ratio) <= 0.7 and queue non-empty >= 50% of the N_high windows",
                c2r, note_r))
    b = R["b"]
    O.append(_o("S3", "b rises with fleet size", [b["low"]["b"], b["high"]["b"], b["p_rising"]],
                "FAIL if b(high) > b(low) with one-sided Fisher exact p < 0.05 (bounces per review, pooled per size)",
                "FAIL" if (b["p_rising"] == b["p_rising"] and b["p_rising"] < 0.05) else "PASS"))
    s4 = V["low"]["in_band"] and V["high"]["in_band"]
    O.append(_o("S4", "V outside +/-25% of the pilot", [V["low"]["ratio_to_pilot"], V["high"]["ratio_to_pilot"]],
                "FAIL if pooled V at N_low or at N_high is outside [0.75, 1.25] x pilot V", "PASS" if s4 else "FAIL"))
    s4n = not (V["low"]["off_band_significant"] or V["high"]["off_band_significant"])
    O.append(_o("S4n", "V outside +/-25% of the pilot, noise-aware",
                dict(low=[V["low"]["ratio_to_pilot"], V["low"]["ratio_to_pilot_ci"]],
                     high=[V["high"]["ratio_to_pilot"], V["high"]["ratio_to_pilot_ci"]]),
                "FAIL if, at either size, pooled V / pilot V is outside [0.75, 1.25] AND its exact 95% interval excludes 1",
                "PASS" if s4n else "FAIL"))
    esc = R.get("escape")
    v_rises = V["high_over_low"] > 1 + BAND
    if esc is None:
        c5, n5 = "N/A", "escape analysis not run"
    elif not v_rises:
        c5, n5 = "PASS", f"V(high)/V(low) = {V['high_over_low']:.2f}, not rising beyond the band"
    elif esc["descriptive_only"] or not esc.get("fit_ok"):
        c5, n5 = "N/A", f"V rises ({V['high_over_low']:.2f}) but only {esc['events']} escaped defects (< {MIN_ESCAPES})"
    else:
        c5 = "FAIL" if not (esc["p_one_sided"] < 0.05) else "PASS"
        n5 = f"V(high)/V(low) = {V['high_over_low']:.2f}; depth slope one-sided p = {esc['p_one_sided']:.3f}"
    O.append(_o("S5", "escape rate flat or falling with queue depth while V rises",
                None if esc is None else esc.get("coef_depth"),
                "FAIL if V(high)/V(low) > 1.25 and the depth coefficient is not positive at one-sided p < 0.05 "
                "(needs >= 8 escaped defects)", c5, n5))
    # Predictions
    o1 = V["low"]["in_band"] and V["high"]["in_band"] and V["high_low_in_band"] and V["low"]["drift_in_band"] \
        and V["high"]["drift_in_band"]
    O.append(_o("O1", "V is a property of the reviewer: V(low), V(high) within +/-25% of the pilot and of each other; "
                "second-half / first-half within +/-25% at each size",
                dict(low=V["low"]["ratio_to_pilot"], high=V["high"]["ratio_to_pilot"], high_over_low=V["high_over_low"],
                     drift_low=V["low"]["drift_ratio"], drift_high=V["high"]["drift_ratio"]),
                "PASS if all five ratios are in [0.75, 1.25] (pooled per size)", "PASS" if o1 else "FAIL",
                "per-window and per-half-window ratios are reported, not coded (n per cell is small)"))
    o1n = not (V["low"]["off_band_significant"] or V["high"]["off_band_significant"] or V["high_low_off_band_significant"]
               or V["low"]["drift_off_band_significant"] or V["high"]["drift_off_band_significant"])
    O.append(_o("O1n", "O1, noise-aware", None,
                "FAIL if any of the five ratios is outside [0.75, 1.25] AND its exact 95% interval excludes 1",
                "PASS" if o1n else "FAIL"))
    d2 = R["O2_detail"]
    o2 = d2["interval95"][0] <= d2["observed_high_total"] <= d2["interval95"][1]
    O.append(_o("O2", "finished at N_high ~ (1 - b_hidden)(1 - b_review) V hours (Carnot point prediction from the pilot)",
                d2, "PASS if the N_high windows' total finished count lies in its 95% predictive interval "
                "(Poisson-gamma, CV 0.3 per window)", "PASS" if o2 else "FAIL"))
    A = R["attempts"]
    if A["p_total_rising"] < 0.05:
        c3 = "PASS"
    elif A["p_total_falling"] < 0.05:
        c3 = "FAIL"
    else:
        c3 = "INCONCLUSIVE"
    O.append(_o("O3", "attempts keep rising with fleet size (fleet attempt rate, first attempts per hour)",
                dict(total_ratio=A["total_ratio"], per_agent_ratio=A["per_agent_ratio"],
                     usl_per_agent_ratio=A["usl_predicted_per_agent_ratio"]),
                "PASS if high/low attempt rate > 1 at one-sided exact p < 0.05; FAIL if < 1 at p < 0.05",
                c3, "a per-agent-hour fall is expected under USL (drag); its size vs USL is reported"))
    if esc is None:
        c4, n4 = "N/A", ""
    elif esc["descriptive_only"]:
        c4, n4 = "N/A", f"{esc['events']} escaped defects < {MIN_ESCAPES}: descriptive only"
    elif not esc.get("fit_ok"):
        c4, n4 = "N/A", "model not estimable (no informative strata / no variation)"
    else:
        c4 = "PASS" if esc["p_one_sided"] < 0.05 else "FAIL"
        n4 = f"OR per extra waiting change = {esc['or_per_depth']:.2f}, one-sided p = {esc['p_one_sided']:.3f}"
    O.append(_o("O4", "escaped defects rise with queue depth", None if esc is None else esc.get("coef_depth"),
                f"PASS if depth coefficient > 0 at one-sided p < 0.05 ({esc['model'] if esc else ''}); "
                f"descriptive only if < {MIN_ESCAPES} events", c4, n4))
    col = R.get("collision")
    if col and col.get("fit_ok"):
        O.append(_o("O5", "collisions: bounce vs k and m (study 1 replication)",
                    dict(p_hat=col.get("p_hat"), p_ci=col.get("p_ci"), or_k=col["or_k"], p_k=col["p_k_two_sided"],
                         or_m=col["or_m"], p_m=col["p_m_two_sided"]),
                    "descriptive: p-hat and its interval against study 1 (p ~ 0-0.004; prediction uses 0.005)",
                    "DESCRIPTIVE"))
    return O


# ---------------------------------------------------------------------- report
def _f(x, fmt="{:.2f}"):
    if x is None or (isinstance(x, float) and math.isnan(x)):
        return "-"
    return fmt.format(x)


def _label(best):
    return " = ".join(RIVAL_LABEL[r] for r in best.split("|")) + (" (tie: identical predictions)" if "|" in best else "")


def render_md_v3(R):
    L = ["# Fleet sweep (study 2): results draft", "",
         "Generated by `analysis/score.py`. Everything below follows the pre-registered analysis (PLAN-v3 sections 1, 2, 7);"
         " numbers are filled in by the script, the prose around them is to be written.", ""]
    nl, nh = R["sizes"]["N_low"], R["sizes"]["N_high"]
    L.append(f"Sizes: N_low = {nl}, N_high = {nh}. Windows: {len(R['windows'])}. Over-dispersion CV fixed at {R['cv']}.")
    L.append("")
    L.append("## Pre-registered outcomes\n")
    L.append("| ID | Statement | Code | Detail |\n|---|---|---|---|")
    for o in R["outcomes"]:
        L.append(f"| {o['id']} | {o['statement']} | **{o['code']}** | {o['rule']}. {o['note']} |")
    L.append("")
    L.append("## Rival scoring (primary)\n")
    rv = R["rivals"]
    L.append("| Window | N | Finished | " + " | ".join(RIVAL_LABEL[r] for r in RIVALS) + " |")
    L.append("|---|---|---|" + "---|" * len(RIVALS))
    for w in rv["windows"]:
        L.append(f"| {w['run_id']} | {w['N']} | {w['finished']} | " +
                 " | ".join(f"{w['pred_' + r]:.1f} ({w['ll_' + r]:.2f})" for r in RIVALS) + " |")
    L.append("| **log-likelihood** | | | " + " | ".join(f"**{rv['ll'][r]:.2f}**" for r in RIVALS) + " |")
    L.append("| log LR vs Carnot | | | " + " | ".join(f"{rv['log_lr_vs_carnot'][r]:+.2f}" for r in RIVALS) + " |")
    L.append("")
    L.append(f"Cells: predicted finished (log-likelihood). Highest likelihood: **{_label(rv['best'])}**, "
             f"likelihood ratio {rv['lr_best_vs_next']:.1f} over the next best.")
    L.append(f"Rework reading used: `{R['params']['rival_rework']}`. All readings:")
    for reading, alt in R["rivals_by_reading"].items():
        L.append(f"- `{reading}`: best = {_label(alt['best'])}; log LR vs Carnot " +
                 ", ".join(f"{r} {alt['log_lr_vs_carnot'][r]:+.2f}" for r in RIVALS) + ".")
    p = R["pooled"]
    L.append(f"\nDescriptive: pooled finished(high)/finished(low) = {_f(p['ratio'])} (Carnot predicted {_f(p['carnot_predicted_ratio'])}) "
             f"({p['finished_high_mean']:.1f} vs {p['finished_low_mean']:.1f} per window). Review queue non-empty "
             f"{p['queue_nonempty_share_high']:.0%} of the N_high windows, {p['queue_nonempty_share_low']:.0%} of the N_low windows.\n")
    L.append("## V against the pilot's +/-25% band\n")
    V = R["V"]
    L.append(f"Pilot V = {R['pilot']['V']:.2f} reviews per busy hour.\n")
    L.append("| Window | N | V | 95% CI | V / pilot | in band | half 1 (n) | half 2 (n) |\n|---|---|---|---|---|---|---|---|")
    for w in V["per_window"]:
        h1, h2 = w["halves"]
        L.append(f"| {w['run_id']} | {w['N']} | {_f(w['V'])} | {_f(w['V_ci'][0], '{:.1f}')}-{_f(w['V_ci'][1], '{:.1f}')} | "
                 f"{_f(w['ratio'])} | {'yes' if w['in_band'] else 'no'} | {_f(h1['V'])} ({h1['n']}) | {_f(h2['V'])} ({h2['n']}) |")
    for lab in ("low", "high"):
        v = V[lab]
        L.append(f"| pooled {lab} | {nl if lab == 'low' else nh} | {_f(v['V'])} | {_f(v['V_ci'][0], '{:.1f}')}-{_f(v['V_ci'][1], '{:.1f}')} | "
                 f"{_f(v['ratio_to_pilot'])} | {'yes' if v['in_band'] else 'no'} | {_f(v['half1'])} | {_f(v['half2'])} |")
    L.append(f"\nV(high)/V(low) = {_f(V['high_over_low'])}; drift (half 2 / half 1): low {_f(V['low']['drift_ratio'])}, "
             f"high {_f(V['high']['drift_ratio'])}.\n")
    L.append("## Bounce rates\n")
    b = R["b"]
    L.append("| Size | reviews | b | b_review | b_hidden |\n|---|---|---|---|---|")
    for lab in ("low", "high"):
        L.append(f"| {lab} | {b[lab]['reviews']} | {_f(b[lab]['b'])} | {_f(b[lab]['b_review'])} | {_f(b[lab]['b_hidden'])} |")
    L.append(f"\nOne-sided p for b rising: {_f(b['p_rising'], '{:.3f}')}.\n")
    L.append("## Attempts\n")
    A = R["attempts"]
    L.append(f"First attempts per hour: low {_f(A['low']['per_hour'])}, high {_f(A['high']['per_hour'])} "
             f"(ratio {_f(A['total_ratio'])}, one-sided p rising {_f(A['p_total_rising'], '{:.3f}')}). Per agent-hour: "
             f"low {_f(A['low']['per_agent_hour'])}, high {_f(A['high']['per_agent_hour'])} (ratio {_f(A['per_agent_ratio'])}; "
             f"USL with alpha, beta fixed predicts {_f(A['usl_predicted_per_agent_ratio'])}).\n")
    if "escape" in R:
        e = R["escape"]
        L.append("## Escaped defects vs queue depth\n")
        L.append(f"{e['events']} escaped defects in {e['n']} approvals with a merge-queue result "
                 f"({'descriptive only: fewer than 8 events' if e['descriptive_only'] else 'model reported'}). Model: `{e['model']}`.\n")
        L.append("| depth at review | approvals | escaped | rate |\n|---|---|---|---|")
        for r in e["by_depth"]:
            L.append(f"| {r['depth']} | {r['approvals']} | {r['escaped']} | {_f(r['rate'])} |")
        if e.get("fit_ok"):
            L.append(f"\nDepth coefficient {_f(e['coef_depth'], '{:+.3f}')} (OR {_f(e.get('or_per_depth'))} per waiting change), "
                     f"LR one-sided p = {_f(e['p_one_sided'], '{:.3f}')}.")
            if "informative_strata" in e:
                L.append(f"Informative task strata: {e['informative_strata']}; dropped columns: {e.get('dropped')}.")
        for m, es in R.get("escape_sensitivity", {}).items():
            L.append(f"\nSensitivity `{m}`: coef {_f(es.get('coef_depth'), '{:+.3f}')}, one-sided p {_f(es.get('p_one_sided'), '{:.3f}')}.")
        L.append("")
    if "collision" in R:
        c = R["collision"]
        L.append("## Collisions: bounced at least once vs k and m\n")
        if c.get("fit_ok"):
            L.append(f"{c['n_resolved']} resolved first attempts ({c['n_censored_excluded']} censored excluded), "
                     f"{c['events']} bounced. Logistic with window FE: k OR {_f(c['or_k'], '{:.3f}')} (p {_f(c['p_k_two_sided'], '{:.3f}')}), "
                     f"m OR {_f(c['or_m'], '{:.3f}')} (p {_f(c['p_m_two_sided'], '{:.3f}')}). Model form: p-hat = {_f(c.get('p_hat'), '{:.4f}')} "
                     f"(95% profile interval {_f(c['p_ci'][0], '{:.4f}')}-{_f(c['p_ci'][1], '{:.4f}')}), p_m-hat = {_f(c.get('p_m_hat'), '{:.4f}')}.")
        else:
            L.append("Not estimable (too few resolved first attempts or no variation).")
        nv = R.get("collision_naive_not_finished", {})
        if nv.get("fit_ok"):
            L.append(f"\nCheck (review v2, C): the rejected accounting, 'not finished' with censored included, gives a k coefficient of "
                     f"{nv['coef_k']:+.3f} (one-sided p {nv['p_k_one_sided']:.3f}); it is not used.")
        L.append("")
    if "alpha_beta_descriptive" in R:
        ab = R["alpha_beta_descriptive"]
        L.append("## Descriptive: carnot.py fit\n")
        L.append(f"`{json.dumps(ab)}`\n")
    L.append("## Per-window derived quantities\n")
    L.append("| Window | N | attempts | lambda | reviews | V | b_review | b_hidden | b | finished | censored | escaped | integration | queue>0 |")
    L.append("|---|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    for s in R["windows"]:
        L.append(f"| {s['run_id']} | {s['n_workers']} | {s['attempts']} | {_f(s['lam'])} | {s['reviews']} | {_f(s['V'])} | "
                 f"{_f(s['b_review'])} | {_f(s['b_hidden'])} | {_f(s['b'])} | {s['finished']} | {s['censored']} | {s['escaped']} | "
                 f"{s['integration_failures']} | {_f(s['queue_nonempty_share'])} |")
    return "\n".join(L) + "\n"


GRADE_TAG = {CONFIRMATORY: "[confirmatory]", CONDITIONAL: "[conditional]", DESCRIPTIVE: "[descriptive]"}


def _grade_txt(o):
    g = o["grade"]
    if g == CONDITIONAL:
        return f"conditional -> {o['counts_as']}"
    if o.get("role"):
        return f"{g} ({o['role']})"
    return g


def render_md(R):
    if R.get("plan") == "v3":
        return render_md_v3(R)
    return render_md_v4(R)


def render_md_v4(R):
    nl, nh = R["sizes"]["N_low"], R["sizes"]["N_high"]
    OC = R["operating_characteristics"]
    L = ["# Fleet sweep (study 2): results draft", "",
         "Generated by `analysis/score.py` from the pre-registered analysis (PLAN-v4 section 1 as reframed in section 7, v4.2). Numbers are filled in by the "
         "script; the prose around them is to be written. Every result is labelled **[confirmatory]**, **[conditional]** "
         "(confirmatory only if the pilot's review-time CV <= 0.5, recorded as `review_cv_ok` before the first sweep window; "
         "otherwise descriptive) or **[descriptive]** (reported whatever it shows; its simulated power is given so a null "
         "result is not read as evidence).", "",
         "Framing (PLAN-v4 section 7, v4.2): a measurement and calibration study. **O2 is the primary quantitative "
         "confirmatory test** of the rule of thumb finished = (1 - b) x V x hours at N = 12, with V and b from the pilot. "
         "**P1 is a confirmatory manipulation check**: the harness's only review stage is a fixed-capacity serial reviewer "
         "(a fresh call per change that never sees the queue), calibrated to about half the N = 12 demand, so it is the "
         "binding limit by design; a P1 FAIL indicates a harness or calibration fault, not support for an uncapped rival. "
         "Vdur is conditionally confirmatory; everything else is descriptive. Wording for the paper: \"in a fleet whose only "
         "review stage is a fixed-capacity serial reviewer, finished output followed (1 - b) x V x hours to within ...\", "
         "not \"review was shown to be the binding limit\".", ""]
    L.append(f"Sizes: N_low = {nl}, N_high = {nh}. Windows: {len(R['windows'])} "
             f"({sum(1 for s in R['windows'] if s['n_workers'] == nl)} at N = {nl}, "
             f"{sum(1 for s in R['windows'] if s['n_workers'] == nh)} at N = {nh}). Over-dispersion CV fixed at {R['cv']}. "
             f"Rework reading `{R['params']['rival_rework']}`. review_cv_ok = **{R['review_cv_ok']}**"
             f"{'' if R['review_cv_ok_recorded'] else ' (not recorded in pilot.json, so treated as false)'}; pilot review-time "
             f"CV = {_f(R['pilot'].get('review_time_cv'))}.")
    if R["supply"]:
        L.append("")
        L.append("**Task supply ran out** in " + "; ".join(
            f"{x['run_id']} (N = {x['N']}): all {x['task_supply']} tasks claimed by minute {x['t_exhausted_min']:.1f}, "
            f"attempts counted to that minute {x['attempts']} (whole window {x['attempts_full']})"
            f"{'; FLAGGED (before minute 110)' if x.get('flagged') else ''}" for x in R["supply"]) +
            ". Lambda and the attempt-based results (O3, attempts per hour) use only the time before exhaustion.")
    if R["supply_unknown"]:
        L.append(f"\nTask supply unknown (no `task_supply` note or reset.json) for: {', '.join(R['supply_unknown'])}; "
                 "exhaustion could not be checked there.")
    L.append("")
    L.append("## Summary of pre-registered results\n")
    L.append("| ID | Grade | Statement | Code | Detail |\n|---|---|---|---|---|")
    for o in R["outcomes"]:
        L.append(f"| {o['id']} | {_grade_txt(o)} | {o['statement']} | **{o['code']}** | {o['rule']}. {o['note']} |")
    L.append("")

    # O2
    d2 = R["O2_detail"]
    L.append(f"## O2 [confirmatory, primary]: finished at N = {nh} against (1 - b) x V x hours\n")
    L.append(f"Observed total over the N = {nh} windows: {d2['observed_high_total']}. Carnot predicted {d2['predicted_high_total']:.1f}, "
             f"95% predictive interval {d2['interval95'][0]}-{d2['interval95'][1]}.\n")
    wf = R.get("without_flagged")
    if wf:
        o2n = wf["O2"]
        L.append("Without the windows flagged for running out of tasks before minute 110 (" + ", ".join(wf["flagged"]) + "): " +
                 ("no N = %d window left.\n" % nh if o2n is None else
                  f"observed {o2n['observed_high_total']} in {o2n['windows']} window(s), predicted {o2n['predicted_high_total']:.1f}, "
                  f"95% interval {o2n['interval95'][0]}-{o2n['interval95'][1]} -> {'inside' if o2n['inside'] else 'outside'}.\n"))

    # P1
    L.append("## P1 [confirmatory, manipulation check]: does the fixed-capacity reviewer cap output?\n")
    rv = R["rivals"]
    L.append("| Window | N | Finished | " + " | ".join(RIVAL_LABEL[r] for r in RIVALS) + " |")
    L.append("|---|---|---|" + "---|" * len(RIVALS))
    for w in rv["windows"]:
        L.append(f"| {w['run_id']} | {w['N']} | {w['finished']} | " +
                 " | ".join(f"{w['pred_' + r]:.1f} ({w['ll_' + r]:.2f})" for r in RIVALS) + " |")
    L.append("| **log-likelihood** | | | " + " | ".join(f"**{rv['ll'][r]:.2f}**" for r in RIVALS) + " |")
    L.append("")
    pr = R["primary"]
    L.append(f"Cells: predicted finished (log-likelihood). Carnot (review-capped) log L = {pr['ll_carnot']:.2f}; best uncapped "
             f"rival {RIVAL_LABEL[pr['best_uncapped']]}, log L = {pr['ll_best_uncapped']:.2f}. **Likelihood ratio Carnot / best "
             f"uncapped = {pr['lr']:.3g}** (log {pr['log_lr']:+.2f}): {'Carnot higher' if pr['carnot_higher'] else 'Carnot not higher'}.")
    L.append(f"Simulated operating characteristics: correct {OC['primary_correct']['carnot']:.2f} under Carnot truth, "
             f"{OC['primary_correct']['usl']:.2f} / {OC['primary_correct']['amdahl']:.2f} / {OC['primary_correct']['linear']:.2f} "
             f"under USL / Amdahl / linear truth; {OC['primary_correct']['carnot_lambda_30pct_low']:.2f} / "
             f"{OC['primary_correct']['usl_lambda_30pct_low']:.2f} (Carnot / USL truth) if agents are 30% slower than assumed. "
             f"Note: {UNCAPPED_TRUTH_FOOTNOTE} A PASS confirms the harness behaved as designed; a FAIL indicates a harness or "
             "calibration fault (for example the calibrated V exceeding the N = 12 demand), not support for an uncapped rival.\n")
    wf = R.get("without_flagged")
    if wf:
        p1n = wf["P1"]
        L.append("Without the windows flagged for running out of tasks before minute 110 (" + ", ".join(wf["flagged"]) + "): " +
                 ("no unflagged window left at one size.\n" if p1n is None else
                  f"log LR {p1n['log_lr']:+.2f} vs {RIVAL_LABEL[p1n['best_uncapped']]} -> "
                  f"{'Carnot higher' if p1n['carnot_higher'] else 'Carnot not higher'}.\n"))
    L.append("Sensitivity to the rework reading [descriptive]: " + "; ".join(
        f"`{k}`: log LR {v['log_lr']:+.2f} vs {v['best_uncapped']}" for k, v in R["primary_by_reading"].items()) + ".\n")

    # V constancy
    V4 = R["V4"]
    o_v = next(o for o in R["outcomes"] if o["id"] == "Vdur")
    L.append(f"## Reviewer pace, Vdur [conditional -> {o_v['counts_as']}]\n")
    L.append(f"{o_v['note']}.\n")
    L.append("| Window | N | reviews | V | 95% CI | review-time CV | half 1 V (n) | half 2 V (n) |\n|---|---|---|---|---|---|---|---|")
    for s in R["windows"]:
        h1, h2 = s["V_half"]
        L.append(f"| {s['run_id']} | {s['n_workers']} | {s['reviews']} | {_f(s['V'])} | {_f(s['V_ci'][0], '{:.1f}')}-"
                 f"{_f(s['V_ci'][1], '{:.1f}')} | {_f(s.get('review_time_cv'))} | {_f(h1['V'])} ({h1['n']}) | {_f(h2['V'])} ({h2['n']}) |")
    for lab, n in (("low", nl), ("high", nh)):
        v = V4[lab]
        L.append(f"| pooled | {n} | {v['reviews']} | {_f(v['V'])} | {_f(v['V_ci'][0], '{:.1f}')}-{_f(v['V_ci'][1], '{:.1f}')} | "
                 f"{_f(v['review_time_cv'])} | | |")
    w = V4["vdur"]
    L.append(f"\n- **Vdur [conditional -> {o_v['counts_as']}]:** Welch t-test on log review durations, N = {nh} vs N = {nl}: "
             f"geometric-mean ratio {_f(w['ratio_geo'])}, p = {_f(w['p'], '{:.3f}')} (n = {w['n_low']} / {w['n_high']}) -> "
             f"**{V4['vdur_code']}** (FAIL iff p < 0.05). Simulated power against a +/-25% reviewer "
             f"{OC['Vdur_power']['service_cv_0_5']:.2f} at review-time CV 0.5, {OC['Vdur_power']['service_cv_1']:.2f} at CV 1; "
             f"false-positive rate {OC['Vdur_fpr']['service_cv_0_5']:.2f} / {OC['Vdur_fpr']['service_cv_1']:.2f}.")
    L.append(f"- **Vratio [descriptive]:** V({nh})/V({nl}) = {_f(V4['ratio'])}, exact 95% interval {_f(V4['ratio_ci'][0])}-"
             f"{_f(V4['ratio_ci'][1])}. Reported only; no equivalence claim is made.")
    if V4["open_review_clipped"]:
        L.append(f"- A review was still running at the end of grace in {', '.join(V4['open_review_clipped'])}; it is not counted "
                 "and neither is its busy time (derive.py grace-end correction).")
    L.append("\nHalf-window V is reported, not coded [descriptive].\n")

    # S3 + bounce causes
    b = R["b"]
    L.append("## S3 [descriptive]: does the review-bounce share rise with N?\n")
    L.append("| Size | reviews | review bounces | b_review |\n|---|---|---|---|")
    for lab, n in (("low", nl), ("high", nh)):
        L.append(f"| N = {n} | {b[lab]['reviews']} | {b[lab]['bounces_review']} | {_f(b[lab]['b_review'])} |")
    L.append(f"\nOne-sided Fisher exact p for b_review rising: {_f(b['p_review_rising'], '{:.3f}')}.\n")
    L.append("### Other bounce causes [descriptive]\n")
    L.append("Per approval with a merge-queue result. Rebase conflicts are built into the task set and grow with the number of "
             "merges since a branch point, so they are expected to rise with N for reasons unrelated to the reviewer.\n")
    L.append("| Cause | N = %d count | per approval | N = %d count | per approval | one-sided p rising |" % (nl, nh))
    L.append("|---|---|---|---|---|---|")
    for c in b["low"]["causes"]:
        if c == "review":
            continue
        L.append(f"| {c} | {b['low']['causes'][c]} | {_f(b['low']['per_approval'][c])} | {b['high']['causes'][c]} | "
                 f"{_f(b['high']['per_approval'][c])} | {_f(b['p_cause_rising_per_approval'][c], '{:.3f}')} |")
    L.append(f"\nApprovals with a merge-queue result: {b['low']['approvals_resolved']} / {b['high']['approvals_resolved']}. "
             f"All bounces per review (b, the PLAN-v3 S3 measure): {_f(b['low']['b'])} / {_f(b['high']['b'])}, one-sided p "
             f"{_f(b['p_rising'], '{:.3f}')}.\n")

    # O3
    A = R["attempts"]
    L.append("## O3 [descriptive]: do attempts rise with N?\n")
    L.append(f"First attempts per hour: N = {nl} {_f(A['low']['per_hour'])} ({A['low']['attempts']} in {A['low']['hours']:.2f} h), "
             f"N = {nh} {_f(A['high']['per_hour'])} ({A['high']['attempts']} in {A['high']['hours']:.2f} h); ratio "
             f"{_f(A['total_ratio'])}, one-sided exact p {_f(A['p_total_rising'], '{:.3g}')}.")
    if A["low"]["supply_truncated"] or A["high"]["supply_truncated"]:
        L.append(f"Hours end at task-supply exhaustion in {', '.join(A['low']['supply_truncated'] + A['high']['supply_truncated'])} "
                 f"(whole-window attempts {A['low']['attempts_full']} / {A['high']['attempts_full']}, not used).")
    L.append(f"\nPer agent-hour [descriptive]: {_f(A['low']['per_agent_hour'])} -> {_f(A['high']['per_agent_hour'])} "
             f"(ratio {_f(A['per_agent_ratio'])}; USL with alpha, beta fixed predicts {_f(A['usl_predicted_per_agent_ratio'])}).\n")

    # S1r / S2r
    p = R["pooled"]
    L.append("## S1r / S2r [descriptive]\n")
    L.append(f"Pooled finished(N = {nh}) / finished(N = {nl}) = {_f(p['ratio'])} ({p['finished_high_mean']:.1f} vs "
             f"{p['finished_low_mean']:.1f} per window); Carnot predicted {_f(p['carnot_predicted_ratio'])}; observed / predicted = "
             f"{_f(p['ratio_over_predicted'])}. Review queue non-empty {p['queue_nonempty_share_high']:.0%} of the N = {nh} windows, "
             f"{p['queue_nonempty_share_low']:.0%} of the N = {nl} windows. False-alarm rates under Carnot truth: S1r "
             f"{OC['S1r_false_alarm']:.2f}, S2r {OC['S2r_false_alarm']:.2f} (S2r is therefore not a criterion).\n")

    # Ranking
    L.append("## Four-way ranking [descriptive]\n")
    order = sorted(RIVALS, key=lambda r: -rv["ll"][r])
    L.append("Order by likelihood: " + " > ".join(f"{RIVAL_LABEL[r]} ({rv['ll'][r]:.2f})" for r in order) +
             f". Highest: **{_label(rv['best'])}**, LR {rv['lr_best_vs_next']:.3g} over the next. USL vs Amdahl discrimination "
             "is not claimed.\n")
    L.append("Simulated confusion matrix at the design point (rows: truth; columns: family with the highest likelihood):\n")
    L.append("| truth | Carnot | USL | Amdahl | linear | tie |\n|---|---|---|---|---|---|")
    for t in RIVALS:
        L.append(f"| {t} | " + " | ".join(f"{OC['confusion'][t][c]:.2f}" for c in (*RIVALS, "tie")) + " |")
    L.append("\nAll rework readings [descriptive]:")
    for reading, alt in R["rivals_by_reading"].items():
        L.append(f"- `{reading}`: best = {_label(alt['best'])}; log LR vs Carnot " +
                 ", ".join(f"{r} {alt['log_lr_vs_carnot'][r]:+.2f}" for r in RIVALS) + ".")
    L.append("")

    if "escape" in R:
        e = R["escape"]
        L.append("## Escaped defects vs queue depth [descriptive]\n")
        L.append(f"{e['events']} escaped defects in {e['n']} approvals with a merge-queue result "
                 f"({'not modelled: fewer than 8 events' if e['descriptive_only'] else 'model reported'}). Model: `{e['model']}`. "
                 f"Simulated power {OC['escape_power']['a05']:.2f} at alpha 0.05, so a null result is not evidence.\n")
        L.append("| depth at review | approvals | escaped | rate |\n|---|---|---|---|")
        for r in e["by_depth"]:
            L.append(f"| {r['depth']} | {r['approvals']} | {r['escaped']} | {_f(r['rate'])} |")
        if e.get("fit_ok") and not e["descriptive_only"]:
            L.append(f"\nDepth coefficient {_f(e['coef_depth'], '{:+.3f}')} (OR {_f(e.get('or_per_depth'))} per waiting change), "
                     f"LR one-sided p = {_f(e['p_one_sided'], '{:.3f}')}.")
        for m, es in R.get("escape_sensitivity", {}).items():
            L.append(f"\nSensitivity `{m}`: coef {_f(es.get('coef_depth'), '{:+.3f}')}, one-sided p {_f(es.get('p_one_sided'), '{:.3f}')}.")
        L.append("")
    if "collision" in R:
        c = R["collision"]
        L.append("## Collisions: bounced at least once vs k and m [descriptive]\n")
        if c.get("fit_ok"):
            L.append(f"{c['n_resolved']} resolved first attempts ({c['n_censored_excluded']} censored excluded), "
                     f"{c['events']} bounced. Logistic with window FE: k OR {_f(c['or_k'], '{:.3f}')} (p {_f(c['p_k_two_sided'], '{:.3f}')}), "
                     f"m OR {_f(c['or_m'], '{:.3f}')} (p {_f(c['p_m_two_sided'], '{:.3f}')}). Model form: p-hat = {_f(c.get('p_hat'), '{:.4f}')} "
                     f"(95% profile interval {_f(c['p_ci'][0], '{:.4f}')}-{_f(c['p_ci'][1], '{:.4f}')}), p_m-hat = {_f(c.get('p_m_hat'), '{:.4f}')}.")
        else:
            L.append("Not estimable (too few resolved first attempts or no variation).")
        L.append(f"Simulated k-slope power {OC['collision_power']['p01_a05']:.2f} at p = 0.01 and {OC['collision_power']['p05_a05']:.2f} "
                 "at p = 0.05.")
        nv = R.get("collision_naive_not_finished", {})
        if nv.get("fit_ok"):
            L.append(f"\nCheck (review v2, C): the rejected accounting, 'not finished' with censored included, gives a k coefficient of "
                     f"{nv['coef_k']:+.3f} (one-sided p {nv['p_k_one_sided']:.3f}); it is not used.")
        L.append("")
    if "alpha_beta_descriptive" in R:
        ab = R["alpha_beta_descriptive"]
        L.append("## carnot.py fit of alpha, beta [descriptive]\n")
        L.append(f"`{json.dumps(ab)}`\n")
    L.append("## Per-window derived quantities [descriptive]\n")
    L.append("| Window | N | attempts (full) | lambda | supply | reviews | V | b_review | b_hidden | b | finished | censored | "
             "escaped | integration | queue>0 |")
    L.append("|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    for s in R["windows"]:
        sup = (f"out at {s['t_exhausted_min']:.0f} min" if s.get("supply_truncated") else
               ("?" if s.get("task_supply") is None else f"{s.get('tasks_claimed')}/{s['task_supply']}"))
        L.append(f"| {s['run_id']} | {s['n_workers']} | {s['attempts']} ({s.get('attempts_full', s['attempts'])}) | {_f(s['lam'])} | "
                 f"{sup} | {s['reviews']} | {_f(s['V'])} | {_f(s['b_review'])} | {_f(s['b_hidden'])} | {_f(s['b'])} | {s['finished']} | "
                 f"{s['censored']} | {s['escaped']} | {s['integration_failures']} | {_f(s['queue_nonempty_share'])} |")
    return "\n".join(L) + "\n"


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("runs", nargs="+", help="sweep window run directories")
    ap.add_argument("--pilot", default=None, help="pilot.json (required for --plan v4 / v3; optional, descriptive in v5)")
    ap.add_argument("--effort-log", default=None, help="v5: post-hoc effort re-review JSONL (v5.load_effort_log)")
    ap.add_argument("--out-dir", default=".")
    ap.add_argument("--rival-rework", choices=list(READINGS), default=PLAN_V4["rival_rework"])
    ap.add_argument("--escape-model", choices=ESCAPE_MODELS, default=PLAN_V4["escape_model"])
    ap.add_argument("--plan", choices=["v5", "v4", "v3"], default="v5",
                    help="v5 (default): PLAN-v5; v4: the superseded PLAN-v4.2 codings; v3: PLAN-v3 (design search only)")
    a = ap.parse_args()
    derived = [derive_dir(r) for r in a.runs]
    bad = [d["summary"]["run_id"] for d in derived if d["summary"]["kind"] != "sweep"]
    if bad:
        print(f"warning: non-sweep runs scored: {bad}", file=sys.stderr)
    if a.plan == "v5":
        import v5
        pilot = json.loads(Path(a.pilot).read_text()) if a.pilot else None
        effort = v5.load_effort_log(a.effort_log) if a.effort_log else None
        R = _clean(v5.score_v5(derived, pilot=pilot, effort_rows=effort))
        md = v5.render_md_v5(R)
    else:
        if not a.pilot:
            raise SystemExit(f"--plan {a.plan} needs --pilot pilot.json")
        pilot = json.loads(Path(a.pilot).read_text())
        R = score(derived, pilot, rival_rework=a.rival_rework, escape_model=a.escape_model, plan=a.plan)
        md = render_md(R)
    out = Path(a.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / "results.json").write_text(json.dumps(R, indent=2, default=float) + "\n")
    (out / "RESULTS-draft.md").write_text(md)
    print(md)


if __name__ == "__main__":
    main()
