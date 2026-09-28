#!/usr/bin/env python3
"""PLAN-v5 analysis codings: what limits an agent fleet when review is automated (PLAN-v5 sections 1 and 5).

Imported by score.py (the default `--plan v5`), predict.py (the default path) and design-search/dsim5.py, so the
design search, the self-test and the real analysis run one implementation. Nothing here reads files except the
optional effort re-review log, and nothing uses the network.

Tests (grades fixed in advance by the v5 design search, DESIGN-SEARCH-v5.md; OPERATING-CHARACTERISTICS-v5.md):

  SCALE    Does finished work bend below proportional? Per-agent finished rate modelled as theta N^gamma; one-sided
           likelihood-ratio test of gamma < 0 (negative binomial, over-dispersion CV fixed at 0.3 per window, as in
           v4), each window's exposure = its slot-hours after warm-up. BEND if p < 0.05, else LINEAR-NOT-REJECTED.
           The per-agent ratio N_high : N_low with its profile 95% interval is reported beside it.
  FAMILY   Which rival fits best: linear, Amdahl (alpha), USL (alpha, beta), Carnot uncapped (USL x the collision
           rework factor (1 - p)^(N - 1)), each with its own free level fitted to all sweep windows (so the sweep's
           N = 1 windows anchor it and no pilot enters), NB likelihood CV 0.3. With p = 0.005 Carnot and USL predict
           within 6% of each other at N = 12, so the four-way pick is reported with the simulated confusion matrix and
           a three-way reading (linear / Amdahl / USL-or-Carnot).
  ESC      The escape rate: approved changes whose hidden tests fail on the approved head, over approvals with a
           hidden-test result, with a Wilson and a task-clustered 95% interval; by size.
  ESC-N    Escapes rising with N: logistic regression of escaped on (N - 1) / 11, one-sided likelihood-ratio p
           (>= 8 escapes or not modelled).
  COLL     Collisions: a change's first merge-queue pass ends in a rebase conflict or an integration failure,
           against j = merges of other changes since its base (its session's launch or its last rework message);
           one-sided likelihood-ratio test of the j slope (logistic, no window effects: at N = 1 j is 0-1, so the
           size contrast is part of the information), and p-hat in the model form 1 - s (1 - p)^j with a profile
           interval.
  COLL-m   H3: the same with jm (those of the j that shared a file with the change) added; one-sided p for jm.
  COLL-k   Study 1's H1 replicated: bounced in the merge queue by collision vs k (changes in flight at first submit)
           and m, as logged by the harness.
  UTIL     Reviewer and merge-queue utilisation per size (busy share of the counted window), review time, V.
  EFFORT   Post-hoc max-effort re-review of every submitted head (a JSONL, `load_effort_log`): verdict agreement with
           the live default-effort review and escapes by effort, descriptive.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
from scipy import optimize, stats

from common import (ALPHA, BETA, CONFIRMATORY, CV_OVERDISPERSION, DESCRIPTIVE, P_COLLISION, X_amdahl, X_linear, X_usl,
                    _design, collision_fit, logit_fit, lr_one_sided, nb_logpmf)

RIVALS_V5 = ("linear", "amdahl", "usl", "carnot")
RIVAL_LABEL_V5 = {"linear": "Linear", "amdahl": "Amdahl (alpha only)", "usl": "USL (alpha, beta)",
                  "carnot": "Carnot uncapped (USL x collision rework)"}
FAMILY3 = {"linear": "linear", "amdahl": "amdahl", "usl": "usl-carnot", "carnot": "usl-carnot"}
SECONDARY = "confirmatory, secondary"      # role label for confirmatory tests that are not the primary one
MIN_ESCAPES_V5 = 8
MIN_COLLISIONS_V5 = 3
ALPHA_TEST = 0.05

# ---------------------------------------------------------------------------------------------- the design
# PLAN-v5 section 5 (from the v5 design search). Session-hours: sum over windows of N x window / 60.
DESIGN_V5 = dict(
    sizes=(1, 12), reps={1: 12, 12: 3}, window_min=90.0, warmup_min=10.0, grace_min=10.0,
    order="1, 1, 12, 1, 1, 1, 1, 12, 1, 1, 1, 1, 12, 1, 1 (synth.v5_order: N = 12 windows spread evenly, never first or last)",
    sweep_session_hours=72.0, t0_usd=1.0, t1_session_hours=6.5,
    pilot="none: T1 checks throttling; every rival's level is fitted to the sweep's own windows (its N = 1 windows anchor it)",
    balance_floor_usd=50.0, budget_usd=250.0,
    degrade=dict(sizes=(1, 12), reps={1: 12, 12: 2}, window_min=90.0, sweep_session_hours=54.0,
                 note="the full design without its last N = 12 window"),
    burn_assumed=2.10, cv=CV_OVERDISPERSION, alpha=ALPHA, beta=BETA, p=P_COLLISION,
    lambda_floor=3.0, review_util_alarm=0.8, mq_time_alarm_s=30.0)
# $ at the assumed burn: T0 ($1) + burn x (T1 6.5 + sweep) session-hours
DESIGN_V5["cost_usd_at_2_10"] = DESIGN_V5["t0_usd"] + 2.10 * (DESIGN_V5["t1_session_hours"] + DESIGN_V5["sweep_session_hours"])

# Simulated operating characteristics (design-search/oc_v5.py, OPERATING-CHARACTERISTICS-v5.md). Filled from
# oc_v5.json by hand; printed by predict.py and score.py next to the results, never recomputed by them.
V5_OC = dict(
    source="OPERATING-CHARACTERISTICS-v5.md, design-search/oc_v5.py, 2000 simulated studies per cell",
    design="N = 1 x 12 windows, N = 12 x 3 windows, 90 min each; degrade: N = 12 x 2",
    SCALE=dict(fpr_linear=0.030, power_amdahl=0.91, power_usl=1.00, power_carnot=1.00, power_mild=0.26,
               fpr_linear_cv015=0.001, power_amdahl_cv015=0.97, fpr_linear_cv05=0.11, power_amdahl_cv05=0.81,
               fpr_linear_slow_reviews=0.033, burn15_power_amdahl=0.82, burn15_power_usl=1.00, burn15_fpr=0.028,
               lam70_power_amdahl=0.89, lam70_fpr=0.027),
    FAMILY=dict(confusion=dict(linear=dict(linear=0.97, amdahl=0.03, usl=0.0, carnot=0.0),
                               amdahl=dict(linear=0.08, amdahl=0.84, usl=0.08, carnot=0.005),
                               usl=dict(linear=0.0, amdahl=0.23, usl=0.43, carnot=0.35),
                               carnot=dict(linear=0.0, amdahl=0.22, usl=0.43, carnot=0.35)),
                three_way_correct=dict(linear=0.97, amdahl=0.84, usl=0.77, carnot=0.78),
                binary_correct=dict(linear=0.97, amdahl=0.92, usl=1.00, carnot=1.00)),
    ESC=dict(halfwidth_median=0.038, halfwidth_median_linear=0.026, approvals_median=242, approvals_median_linear=498),
    ESC_N=dict(fpr=0.06, fpr_linear=0.05, power_015=0.37, power_020=0.78, power_015_linear=0.42, power_020_linear=0.86),
    COLL=dict(fpr=0.02, fpr_linear=0.03, power={"0.005": 0.28, "0.01": 0.52, "0.02": 0.83, "0.05": 0.98},
              power_linear={"0.005": 0.83, "0.01": 0.98, "0.02": 1.00}, census=0.64, census_half=0.37, p_ci_coverage=0.95),
    UTIL=dict(review_util_N12=dict(linear=0.79, mild=0.62, amdahl=0.41, usl=0.26, linear_p95=1.00, linear_slow_reviews=1.00),
              mq_util_N12=dict(linear=0.054, usl=0.018)),
)


# ---------------------------------------------------------------------------------------------- curves
def shape(rival, n, alpha=ALPHA, beta=BETA, p=P_COLLISION):
    """Finished output per hour in single-agent units, g_R(N), with g_R(1) = 1 for every rival."""
    n = float(n)
    if rival == "linear":
        return float(X_linear(n))
    if rival == "amdahl":
        return float(X_amdahl(n, alpha))
    if rival == "usl":
        return float(X_usl(n, alpha, beta))
    if rival == "carnot":
        return float(X_usl(n, alpha, beta)) * (1 - p) ** (n - 1)
    raise ValueError(rival)


def exposure_hours(s):
    """Window hours after warm-up, net of slot down-time: slot-open hours / N (= hours when no slot was down)."""
    n = max(int(s["n_workers"]), 1)
    wh = s.get("worker_hours_full")
    if wh is not None and wh == wh and wh > 0:
        return float(wh) / n
    return float(s["hours"])


def _fit_level(y, a, cv):
    """ML level theta for counts y ~ NB(theta a, cv); returns (theta, loglik)."""
    y = np.asarray(y, float)
    a = np.asarray(a, float)
    t0 = max(y.sum(), 0.5) / max(a.sum(), 1e-9)
    f = lambda lt: -float(np.sum(nb_logpmf(y, math.exp(lt) * a, cv)))
    r = optimize.minimize_scalar(f, bounds=(math.log(t0) - 4, math.log(t0) + 4), method="bounded",
                                 options=dict(xatol=1e-7))
    return math.exp(r.x), -r.fun


# ---------------------------------------------------------------------------------------------- FAMILY
def family_fit(S, cv=CV_OVERDISPERSION, alpha=ALPHA, beta=BETA, p=P_COLLISION, anchor_theta=None):
    """Each rival's likelihood with its own free level (anchor_theta=None) or with the level fixed in advance
    (anchor_theta = finished per single-agent hour, e.g. pilot lambda x completion)."""
    y = np.array([s["finished"] for s in S], float)
    h = np.array([exposure_hours(s) for s in S], float)
    N = [s["n_workers"] for s in S]
    ll, theta, pred = {}, {}, {}
    for r in RIVALS_V5:
        a = h * np.array([shape(r, n, alpha, beta, p) for n in N])
        if anchor_theta is None:
            th, l = _fit_level(y, a, cv)
        else:
            th = float(anchor_theta)
            l = float(np.sum(nb_logpmf(y, th * a, cv)))
        ll[r], theta[r], pred[r] = l, th, (th * a).tolist()
    mx = max(ll.values())
    best = [r for r in RIVALS_V5 if ll[r] >= mx - 1e-9]
    lin = ll["linear"]
    bent = max(ll[r] for r in ("amdahl", "usl", "carnot"))
    f3 = {}
    for r in RIVALS_V5:
        f3[FAMILY3[r]] = max(f3.get(FAMILY3[r], -math.inf), ll[r])
    mx3 = max(f3.values())
    return dict(ll=ll, theta=theta, predicted=pred, best="|".join(best), tie=len(best) > 1,
                best3="|".join(k for k in f3 if f3[k] >= mx3 - 1e-9),
                binary=("linear" if lin > bent + 1e-9 else ("bending" if bent > lin + 1e-9 else "tie")),
                log_lr_linear_vs_best_bent=lin - bent, anchored=anchor_theta is not None,
                windows=[dict(run_id=s["run_id"], N=s["n_workers"], finished=s["finished"], hours=float(hh),
                              **{f"pred_{r}": pred[r][i] for r in RIVALS_V5}) for i, (s, hh) in enumerate(zip(S, h))])


# ---------------------------------------------------------------------------------------------- SCALE
def _nb_ll_gamma(y, h, N, lt, g, cv):
    mu = np.exp(lt) * h * N * np.power(N, g)
    return float(np.sum(nb_logpmf(y, mu, cv)))


def bend_test(S, cv=CV_OVERDISPERSION, method="lr_fixed"):
    """One-sided test that per-agent finished output falls with N: finished_w ~ NB(theta h_w N_w^(1 + gamma)).
    method: lr_fixed (CV fixed, pre-registered), lr_estcv (CV estimated jointly; sensitivity), welch (one-sided
    Welch t on log per-agent rates, largest vs smallest size; sensitivity)."""
    y = np.array([s["finished"] for s in S], float)
    h = np.array([exposure_hours(s) for s in S], float)
    N = np.array([s["n_workers"] for s in S], float)
    sizes = sorted(set(N.tolist()))
    out = dict(method=method, sizes=sizes)
    if len(sizes) < 2:
        return dict(out, p=math.nan, gamma=math.nan)
    lo, hi = sizes[0], sizes[-1]
    pa = {n: y[N == n].sum() / (h[N == n] * n).sum() for n in sizes}
    out["per_agent"] = {int(n): float(v) for n, v in pa.items()}
    out["per_agent_ratio"] = float(pa[hi] / pa[lo]) if pa[lo] > 0 else math.nan
    if method == "welch":
        a = np.log((y[N == lo] + 0.5) / (h[N == lo] * lo))
        b = np.log((y[N == hi] + 0.5) / (h[N == hi] * hi))
        if len(a) < 2 or len(b) < 2:
            return dict(out, p=math.nan)
        t = stats.ttest_ind(b, a, equal_var=False)
        p = float(t.pvalue / 2 if t.statistic < 0 else 1 - t.pvalue / 2)
        return dict(out, p=p, t=float(t.statistic))
    lt0 = math.log(max(y.sum(), 0.5) / (h * N).sum())
    if method == "lr_fixed":
        f1 = lambda th: -_nb_ll_gamma(y, h, N, th[0], th[1], cv)
        r1 = optimize.minimize(f1, np.array([lt0, 0.0]), method="L-BFGS-B", bounds=[(lt0 - 6, lt0 + 6), (-3, 1)])
        _, ll0 = _fit_level(y, h * N, cv)
        ll1 = -r1.fun
        g = float(r1.x[1])
        p2, p1 = lr_one_sided(ll1, ll0, -g)   # one-sided for gamma < 0
        out.update(p=p1, p_two_sided=p2, gamma=g, ll_bend=ll1, ll_linear=ll0)
        # profile 95% interval for gamma, reported as the per-agent ratio hi : lo
        crit = stats.chi2.ppf(0.95, 1) / 2

        def prof(gv):
            r = optimize.minimize_scalar(lambda lt: -_nb_ll_gamma(y, h, N, lt, gv, cv),
                                         bounds=(lt0 - 8, lt0 + 8), method="bounded")
            return (ll1 + r.fun) - crit
        lo_g, hi_g = -3.0, 1.0
        try:
            if prof(-3.0) > 0:
                lo_g = optimize.brentq(prof, -3.0, g)
            if prof(1.0) > 0:
                hi_g = optimize.brentq(prof, g, 1.0)
        except ValueError:
            pass
        f = math.log(hi / lo)
        out["per_agent_ratio_model"] = math.exp(g * f)
        out["per_agent_ratio_ci"] = [math.exp(lo_g * f), math.exp(hi_g * f)]
        return out
    if method == "lr_estcv":
        f1 = lambda th: -_nb_ll_gamma(y, h, N, th[0], th[1], th[2])
        f0 = lambda th: -_nb_ll_gamma(y, h, N, th[0], 0.0, th[1])
        r1 = optimize.minimize(f1, np.array([lt0, 0.0, 0.3]), method="L-BFGS-B",
                               bounds=[(lt0 - 6, lt0 + 6), (-3, 1), (0.0, 2.0)])
        r0 = optimize.minimize(f0, np.array([lt0, 0.3]), method="L-BFGS-B", bounds=[(lt0 - 6, lt0 + 6), (0.0, 2.0)])
        g = float(r1.x[1])
        p2, p1 = lr_one_sided(-r1.fun, -r0.fun, -g)
        return dict(out, p=p1, p_two_sided=p2, gamma=g, cv_hat=float(r1.x[2]))
    raise ValueError(method)


# ---------------------------------------------------------------------------------------------- ESC
def wilson(k, n, level=0.95):
    if n == 0:
        return [math.nan, math.nan]
    z = stats.norm.ppf(1 - (1 - level) / 2)
    ph = k / n
    d = 1 + z * z / n
    c = (ph + z * z / (2 * n)) / d
    hw = z * math.sqrt(ph * (1 - ph) / n + z * z / (4 * n * n)) / d
    return [max(0.0, c - hw), min(1.0, c + hw)]


def escape_v5(approvals, n_max=None):
    """approvals: derive.py approval rows (each approved head with a hidden_pre result)."""
    rows = [a for a in approvals if a.get("escaped") is not None]
    n = len(rows)
    ev = int(sum(a["escaped"] for a in rows))
    sizes = sorted({a["n_workers"] for a in rows})
    out = dict(n=n, events=ev, rate=ev / n if n else math.nan, wilson95=wilson(ev, n), min_events=MIN_ESCAPES_V5,
               by_size={int(s): dict(approvals=sum(1 for a in rows if a["n_workers"] == s),
                                     escaped=int(sum(a["escaped"] for a in rows if a["n_workers"] == s))) for s in sizes})
    for v in out["by_size"].values():
        v["rate"] = v["escaped"] / v["approvals"] if v["approvals"] else math.nan
        v["wilson95"] = wilson(v["escaped"], v["approvals"])
    out["halfwidth"] = (out["wilson95"][1] - out["wilson95"][0]) / 2 if n else math.nan
    if 0 < ev < n:
        y = np.array([float(a["escaped"]) for a in rows])
        f0 = logit_fit(np.ones((n, 1)), y, ["const"], cluster=[a["task"] for a in rows])
        b0 = float(f0["coef"][0])
        se = float(f0["se_cluster"][0]) if f0["se_cluster"] is not None else float(f0["se"][0])
        ex = lambda x: 1 / (1 + math.exp(-x))
        out["cluster95"] = [ex(b0 - 1.96 * se), ex(b0 + 1.96 * se)]
    else:
        out["cluster95"] = [math.nan, math.nan]
    nm = n_max or (max(sizes) if sizes else 1)
    out["trend_fit_ok"] = False
    out["p_trend"] = math.nan
    if len(sizes) >= 2 and ev >= MIN_ESCAPES_V5 and ev < n:
        y = np.array([float(a["escaped"]) for a in rows])
        x = np.array([(a["n_workers"] - 1) / max(nm - 1, 1) for a in rows], float)
        names, X = _design({"N_scaled": x}, n)
        full = logit_fit(X, y, names)
        red = logit_fit(np.ones((n, 1)), y, ["const"])
        c = float(full["coef"][1])
        p2, p1 = lr_one_sided(full["ll"], red["ll"], c)
        out.update(trend_fit_ok=True, p_trend=p1, p_trend_two_sided=p2, coef_N=c, or_1_to_Nmax=math.exp(min(c, 50)))
        if len(sizes) == 2:
            lo, hi = out["by_size"][sizes[0]], out["by_size"][sizes[-1]]
            out["p_fisher"] = float(stats.fisher_exact([[hi["escaped"], hi["approvals"] - hi["escaped"]],
                                                        [lo["escaped"], lo["approvals"] - lo["escaped"]]],
                                                       alternative="greater").pvalue)
    return out


# ---------------------------------------------------------------------------------------------- COLL
def collision_v5(prs, model_form=True):
    rows = [p for p in prs if p.get("collided_first") is not None and p.get("j") is not None]
    n = len(rows)
    ev = int(sum(p["collided_first"] for p in rows))
    out = dict(n=n, events=ev, min_events=MIN_COLLISIONS_V5, fit_ok=False, p_j=math.nan, p_jm=math.nan, p_k=math.nan,
               by_size={})
    for s in sorted({p["n_workers"] for p in rows}):
        sub = [p for p in rows if p["n_workers"] == s]
        out["by_size"][int(s)] = dict(n=len(sub), collisions=int(sum(p["collided_first"] for p in sub)),
                                      j_mean=float(np.mean([p["j"] for p in sub])))
    if n < 10 or ev < MIN_COLLISIONS_V5 or ev == n:
        return out
    y = np.array([float(p["collided_first"]) for p in rows])
    j = np.array([p["j"] for p in rows], float)
    jm = np.array([p["jm"] for p in rows], float)
    if np.ptp(j) == 0:
        return out
    names, X = _design({"j": j}, n)
    fit = logit_fit(X, y, names)
    red = logit_fit(np.ones((n, 1)), y, ["const"])
    cj = float(fit["coef"][1])
    p2, p1 = lr_one_sided(fit["ll"], red["ll"], cj)
    out.update(fit_ok=True, coef_j=cj, or_j=math.exp(min(cj, 50)), p_j=p1, p_j_two_sided=p2, j_mean=float(j.mean()))
    if np.ptp(jm) > 0:
        n2, X2 = _design({"j": j, "jm": jm}, n)
        f2 = logit_fit(X2, y, n2)
        cm = float(f2["coef"][2])
        _, pm1 = lr_one_sided(f2["ll"], fit["ll"], cm)
        out.update(coef_jm=cm, or_jm=math.exp(min(cm, 50)), p_jm=pm1)
    k = np.array([p.get("k", 0) or 0 for p in rows], float)
    m = np.array([p.get("m", 0) or 0 for p in rows], float)
    if np.ptp(k) > 0:
        nk, Xk = _design({"k": k, "m": m}, n)
        fk = logit_fit(Xk, y, nk)
        nr, Xr = _design({"m": m}, n)
        fr = logit_fit(Xr, y, nr)
        ck = float(fk["coef"][1])
        _, pk1 = lr_one_sided(fk["ll"], fr["ll"], ck)
        out.update(coef_k=ck, p_k=pk1, k_mean=float(k.mean()))
    if model_form:
        try:
            mf = collision_fit(y, j, jm, ["all"] * n, fit_pm=False)
            out.update(p_hat=mf["p_hat"], p_ci=mf["p_ci"])
        except Exception as e:  # pragma: no cover - descriptive
            out["p_hat_error"] = repr(e)[:200]
    return out


# ---------------------------------------------------------------------------------------------- UTIL
def utilisation_v5(S):
    out = {}
    for n in sorted({s["n_workers"] for s in S}):
        SS = [s for s in S if s["n_workers"] == n]
        ru = [s["reviewer_util"] for s in SS if s.get("reviewer_util") == s.get("reviewer_util")]
        qu = [s.get("mq_util") for s in SS if s.get("mq_util") is not None and s.get("mq_util") == s.get("mq_util")]
        nrev = sum(s["reviews"] for s in SS)
        bh = sum(s["busy_hours"] for s in SS)
        rs = [x for s in SS for x in s.get("review_durations_s", [])]
        out[int(n)] = dict(windows=len(SS), reviewer_util_mean=float(np.mean(ru)) if ru else math.nan,
                           reviewer_util_max=float(np.max(ru)) if ru else math.nan,
                           mq_util_mean=float(np.mean(qu)) if qu else math.nan, mq_util_max=float(np.max(qu)) if qu else math.nan,
                           V=nrev / bh if bh > 0 else math.nan, reviews=nrev,
                           review_s_mean=float(np.mean(rs)) if rs else math.nan,
                           review_s_p90=float(np.quantile(rs, 0.9)) if rs else math.nan,
                           reviews_per_hour=nrev / sum(s["hours"] for s in SS) if SS else math.nan,
                           ci_time_s_mean=float(np.nanmean([s["ci_time_min"] * 60 for s in SS])) if SS else math.nan)
    return out


# ---------------------------------------------------------------------------------------------- EFFORT (post hoc)
EFFORT_VERDICTS = ("approve", "request_changes")


def load_effort_log(path):
    """Post-hoc re-review log (PLAN-v5 question 5): one JSON object per line,
        {"run_id": "...", "task": "T042", "head": "<sha>", "effort": "max", "verdict": "approve|request_changes|error",
         "duration_s": 131.2, "hidden_passed": true|false|null}
    run_id, task, head, effort and verdict are required; hidden_passed is optional (the hidden tests run post hoc on the
    same head, for heads the live merge queue never tested). Rows are keyed by (run_id, head); a duplicate is an error."""
    rows, errs, seen = [], [], set()
    for i, ln in enumerate(Path(path).read_text().splitlines(), 1):
        if not ln.strip():
            continue
        try:
            r = json.loads(ln)
        except json.JSONDecodeError as e:
            errs.append(f"line {i}: not JSON ({e})")
            continue
        for k in ("run_id", "task", "head", "effort", "verdict"):
            if r.get(k) in (None, ""):
                errs.append(f"line {i}: missing {k}")
        v = str(r.get("verdict", "")).strip().lower()
        if v not in (*EFFORT_VERDICTS, "error"):
            errs.append(f"line {i}: verdict {v!r} not approve / request_changes / error")
        r["verdict"] = v
        hp = r.get("hidden_passed")
        if hp is not None and not isinstance(hp, bool):
            errs.append(f"line {i}: hidden_passed must be true, false or null")
        key = (r.get("run_id"), r.get("head"), r.get("effort"))
        if key in seen:
            errs.append(f"line {i}: duplicate (run_id, head, effort) {key}")
        seen.add(key)
        rows.append(r)
    if errs:
        raise SystemExit(f"{path}: effort log invalid:\n  " + "\n  ".join(errs[:20]))
    return rows


def effort_compare(derived, effort_rows):
    """Descriptive: live default-effort verdict vs post-hoc effort verdict on the same heads; escapes by effort."""
    live = {}
    for d in derived:
        rid = d["summary"]["run_id"]
        esc = {a["head"]: a["escaped"] for a in d["approvals"]}
        for rv in d.get("reviews", []):   # the last live verdict on each head (a retry after an error has none)
            live[(rid, rv["head"])] = dict(verdict=rv["verdict"], task=rv["task"], n_workers=d["summary"]["n_workers"],
                                           hidden_failed=esc.get(rv["head"]))
    by_effort = {}
    for r in effort_rows:
        if r["verdict"] == "error":
            continue
        by_effort.setdefault(str(r["effort"]), []).append(r)
    out = dict(matched={}, unmatched={}, efforts=sorted(by_effort))
    for eff, rows in by_effort.items():
        m = [(r, live[(r["run_id"], r["head"])]) for r in rows if (r["run_id"], r["head"]) in live]
        out["unmatched"][eff] = len(rows) - len(m)
        agree = sum(1 for r, l in m if r["verdict"] == l["verdict"])
        # truth for a head: live hidden_pre (approved heads) or the post-hoc hidden run
        def truth(r, l):
            if l["hidden_failed"] is not None:
                return bool(l["hidden_failed"])
            if r.get("hidden_passed") is not None:
                return not r["hidden_passed"]
            return None
        lab = [(r, l, truth(r, l)) for r, l in m]
        lab = [x for x in lab if x[2] is not None]
        def esc(verdict_of):
            appr = [x for x in lab if verdict_of(x) == "approve"]
            k = sum(1 for x in appr if x[2])
            return dict(approvals=len(appr), escaped=k, rate=k / len(appr) if appr else math.nan,
                        wilson95=wilson(k, len(appr)))
        bad = [x for x in lab if x[2]]
        good = [x for x in lab if not x[2]]
        out["matched"][eff] = dict(
            heads=len(m), agreement=agree / len(m) if m else math.nan, with_hidden_truth=len(lab),
            live_default=esc(lambda x: x[1]["verdict"]), posthoc=esc(lambda x: x[0]["verdict"]),
            catch_live=(sum(1 for x in bad if x[1]["verdict"] == "request_changes") / len(bad)) if bad else math.nan,
            catch_posthoc=(sum(1 for x in bad if x[0]["verdict"] == "request_changes") / len(bad)) if bad else math.nan,
            false_reject_live=(sum(1 for x in good if x[1]["verdict"] == "request_changes") / len(good)) if good else math.nan,
            false_reject_posthoc=(sum(1 for x in good if x[0]["verdict"] == "request_changes") / len(good)) if good else math.nan,
            duration_s_mean=float(np.mean([r["duration_s"] for r, _ in m if r.get("duration_s")])) if any(
                r.get("duration_s") for r, _ in m) else math.nan,
            note="heads rejected live have hidden-test truth only if the post-hoc run supplied hidden_passed")
    return out


# ---------------------------------------------------------------------------------------------- scoring
# Grades fixed in advance by the v5 design search (DESIGN-SEARCH-v5.md section 4; PLAN-v5 section 5).
V5_ORDER = ("SCALE", "SCALE-nf", "COLL", "ESC-N", "ESC", "FAMILY", "UTIL", "COLL-m", "COLL-k", "LAMBDA", "BOUNCE",
            "EFFORT")
GRADES_V5 = {"SCALE": (CONFIRMATORY, "primary"), "SCALE-nf": (CONFIRMATORY, "primary"),
             "COLL": (CONFIRMATORY, SECONDARY), "ESC-N": (DESCRIPTIVE, None), "ESC": (DESCRIPTIVE, None),
             "FAMILY": (DESCRIPTIVE, None), "UTIL": (DESCRIPTIVE, None), "COLL-m": (DESCRIPTIVE, None),
             "COLL-k": (DESCRIPTIVE, None), "LAMBDA": (DESCRIPTIVE, None), "BOUNCE": (DESCRIPTIVE, None),
             "EFFORT": (DESCRIPTIVE, None)}


def _f(x, fmt="{:.2f}"):
    try:
        return "n/a" if x is None or x != x else fmt.format(x)
    except (TypeError, ValueError):
        return str(x)


def _res(id_, statement, value, rule, code, note=""):
    g, role = GRADES_V5[id_]
    return dict(id=id_, grade=g, counts_as=g, role=role, statement=statement, value=value, rule=rule, code=code, note=note)


def _pool_bounces(S):
    causes = {}
    for s in S:
        for c, v in s["bounces"].items():
            causes[c] = causes.get(c, 0) + v
    ar = sum(s["approvals_resolved"] for s in S)
    rev = sum(s["reviews"] for s in S)
    return dict(causes=causes, approvals_resolved=ar, reviews=rev,
                b_review=causes.get("review", 0) / rev if rev else math.nan,
                per_approval={c: v / ar if ar else math.nan for c, v in causes.items() if c != "review"})


def score_v5(derived, *, cv=CV_OVERDISPERSION, pilot=None, effort_rows=None, alpha=ALPHA, beta=BETA, p=P_COLLISION):
    """PLAN-v5 codings on the sweep windows (any number of sizes >= 2). pilot (optional): a pilot.json whose
    lambda_pilot x completion gives a pilot-anchored rival reading, descriptive only."""
    S = [d["summary"] for d in derived]
    sizes = sorted({s["n_workers"] for s in S})
    if len(sizes) < 2:
        raise ValueError(f"expected at least two fleet sizes, got {sizes}")
    nlo, nhi = sizes[0], sizes[-1]
    R = dict(plan="v5", sizes=sizes, N_low=nlo, N_high=nhi, cv=cv, windows=S, constants=dict(alpha=alpha, beta=beta, p=p))
    R["scale"] = bend_test(S, cv, "lr_fixed")
    R["scale_sensitivity"] = {m: bend_test(S, cv, m) for m in ("lr_estcv", "welch")}
    flagged = [s["run_id"] for s in S if s.get("supply_flagged")]
    R["supply_flagged"] = flagged
    keep = [s for s in S if not s.get("supply_flagged")]
    R["scale_nf"] = bend_test(keep, cv, "lr_fixed") if flagged and len({s["n_workers"] for s in keep}) >= 2 else None
    R["family"] = family_fit(S, cv, alpha, beta, p)
    if pilot and pilot.get("lambda_pilot") and pilot.get("completion"):
        th = float(pilot["lambda_pilot"]) * float(pilot["completion"])
        R["family_pilot_anchored"] = family_fit(S, cv, alpha, beta, p, anchor_theta=th)
    apprs = [a for d in derived for a in d["approvals"]]
    prs = [x for d in derived for x in d["prs"]]
    R["escape"] = escape_v5(apprs, n_max=nhi)
    R["collision"] = collision_v5(prs)
    R["util"] = utilisation_v5(S)
    R["per_size"] = {}
    for n in sizes:
        SS = [s for s in S if s["n_workers"] == n]
        wh = sum(s["worker_hours"] for s in SS)
        R["per_size"][int(n)] = dict(
            windows=len(SS), finished=sum(s["finished"] for s in SS), attempts=sum(s["attempts"] for s in SS),
            lam=sum(s["attempts"] for s in SS) / wh if wh else math.nan,
            finished_per_slot_hour=sum(s["finished"] for s in SS) / sum(exposure_hours(s) * n for s in SS),
            completion=(sum(s["finished"] for s in SS) / sum(s["attempts_full"] for s in SS)) if sum(s["attempts_full"] for s in SS) else math.nan,
            timeouts=sum(s.get("session_timeouts", 0) for s in SS),
            timeouts_before_submit=sum(s.get("timeouts_before_submit", 0) for s in SS),
            startup_min=float(np.nanmean([s.get("startup_min_mean", math.nan) for s in SS])),
            rework_wait_min=float(np.nanmean([s.get("rework_wait_min_mean", math.nan) for s in SS])),
            censored=sum(s["censored"] for s in SS), rework_open=sum(s["rework_open"] for s in SS),
            bounces=_pool_bounces(SS))
    if effort_rows is not None:
        R["effort"] = effort_compare(derived, effort_rows)
    R["operating_characteristics"] = V5_OC
    R["outcomes"] = code_outcomes_v5(R)
    return R


def code_outcomes_v5(R):
    O = []
    oc = V5_OC
    nlo, nhi = R["N_low"], R["N_high"]
    sc = R["scale"]
    code = "N/A" if sc["p"] != sc["p"] else ("BEND" if sc["p"] < ALPHA_TEST else "LINEAR-NOT-REJECTED")
    ci = sc.get("per_agent_ratio_ci") or [math.nan, math.nan]
    s_oc = oc["SCALE"]
    O.append(_res("SCALE", f"Finished work bends below proportional: per-agent finished output at N = {nhi} is lower than "
                  f"at N = {nlo}", dict(p=sc["p"], per_agent_ratio=sc["per_agent_ratio"], ci95=ci, gamma=sc.get("gamma")),
                  "one-sided NB likelihood-ratio test of gamma < 0 in finished ~ theta x slot-hours x N^gamma (CV 0.3 fixed); "
                  "BEND iff p < 0.05",
                  code, f"per-agent ratio N = {nhi} : {nlo} = {_f(sc['per_agent_ratio'])} (95% {_f(ci[0])}-{_f(ci[1])}), "
                  f"p = {_f(sc['p'], '{:.3g}')}; simulated false-alarm rate under linear truth {_f(s_oc['fpr_linear'])}, power "
                  f"{_f(s_oc['power_amdahl'])} under Amdahl (alpha 0.1), {_f(s_oc['power_usl'])} under USL, "
                  f"{_f(s_oc['power_mild'])} against a mild bend (alpha 0.03)"))
    if R.get("scale_nf") is not None:
        s2 = R["scale_nf"]
        c2 = "BEND" if s2["p"] < ALPHA_TEST else "LINEAR-NOT-REJECTED"
        n2 = f"without {', '.join(R['supply_flagged'])}: p = {_f(s2['p'], '{:.3g}')}"
    else:
        s2, c2 = None, "N/A"
        n2 = "no window ran out of tasks more than 10 min before its end: identical to SCALE"
    O.append(_res("SCALE-nf", "SCALE without the windows that ran out of tasks more than 10 min before their end (reported beside SCALE)",
                  None if s2 is None else dict(p=s2["p"], per_agent_ratio=s2["per_agent_ratio"]), "as SCALE", c2, n2))
    c = R["collision"]
    c_oc = oc["COLL"]
    pw = c_oc.get("power") or {}
    if not c["fit_ok"]:
        cc = "NOT-DETECTED" if c["events"] < MIN_COLLISIONS_V5 else "N/A"
        nc = f"{c['events']} collisions in {c['n']} first merge-queue passes (fewer than {MIN_COLLISIONS_V5} or no variation in j): not modelled, coded as not detected"
    else:
        cc = "DETECTED" if c["p_j"] < ALPHA_TEST else "NOT-DETECTED"
        nc = (f"OR per merge since base {_f(c['or_j'], '{:.3f}')}, one-sided p = {_f(c['p_j'], '{:.3g}')}; p-hat "
              f"{_f(c.get('p_hat'), '{:.4f}')} (95% {_f((c.get('p_ci') or [None])[0], '{:.4f}')}-"
              f"{_f((c.get('p_ci') or [None, None])[1], '{:.4f}')}); {c['events']} collisions in {c['n']} passes")
    nc += (f"; simulated false-alarm rate {_f(c_oc.get('fpr'))}, power " +
           ", ".join(f"{_f(v)} at p = {k}" for k, v in pw.items()) + " with USL-like workers; " +
           ", ".join(f"{_f(v)} at p = {k}" for k, v in (c_oc.get("power_linear") or {}).items()) + " with linear workers")
    O.append(_res("COLL", "Collisions force rework: a change's first merge-queue pass fails (rebase conflict or integration "
                  "failure) more often the more other changes merged since its base", dict(p=c.get("p_j"), or_j=c.get("or_j"),
                  p_hat=c.get("p_hat"), p_ci=c.get("p_ci"), events=c["events"], n=c["n"]),
                  "logistic collided ~ j (merges of other changes since the change's launch or last rework message), "
                  "one-sided LR p < 0.05 -> DETECTED", cc, nc))
    e = R["escape"]
    e_oc = oc["ESC_N"]
    if not e["trend_fit_ok"]:
        ce, ne = "N/A", f"{e['events']} escapes (< {MIN_ESCAPES_V5}) or one size only: not modelled"
    else:
        ce = "RISES" if e["p_trend"] < ALPHA_TEST else "NOT-DETECTED"
        ne = f"OR N = 1 -> {nhi}: {_f(e['or_1_to_Nmax'])}, one-sided p = {_f(e['p_trend'], '{:.3g}')}"
    ne += "; by size: " + ", ".join(f"N = {k}: {v['escaped']}/{v['approvals']} = {_f(v['rate'])}" for k, v in e["by_size"].items())
    ne += (f"; simulated power {_f(e_oc['power_020'])} for 0.10 -> 0.20, {_f(e_oc['power_015'])} for 0.10 -> 0.15, "
           f"false-alarm rate {_f(e_oc['fpr'])}")
    O.append(_res("ESC-N", f"The escape rate rises with fleet size (N = {nlo} to {nhi})",
                  dict(p=e["p_trend"], by_size={k: v["rate"] for k, v in e["by_size"].items()}),
                  "logistic escaped ~ (N - 1) / (N_max - 1) over approvals with a hidden-test result, one-sided LR p < 0.05; "
                  f">= {MIN_ESCAPES_V5} escapes", ce, ne))
    O.append(_res("ESC", "The escape rate: approved changes that fail their hidden tests on the approved head (the "
                  "verifier's ceiling)", dict(rate=e["rate"], wilson95=e["wilson95"], cluster95=e["cluster95"], n=e["n"]),
                  "estimate with Wilson and task-clustered 95% intervals; integration failures are the collision channel (COLL)",
                  "REPORTED", f"{e['events']}/{e['n']} = {_f(e['rate'], '{:.3f}')} (Wilson {_f(e['wilson95'][0], '{:.3f}')}-"
                  f"{_f(e['wilson95'][1], '{:.3f}')}, clustered {_f(e['cluster95'][0], '{:.3f}')}-{_f(e['cluster95'][1], '{:.3f}')}); "
                  f"simulated median half-width {_f(oc['ESC']['halfwidth_median'], '{:.3f}')}"))
    f = R["family"]
    order = sorted(RIVALS_V5, key=lambda r: -f["ll"][r])
    O.append(_res("FAMILY", "Which rival fits best (linear, Amdahl, USL, Carnot uncapped), each with a free level",
                  dict(best=f["best"], best3=f["best3"], ll=f["ll"], binary=f["binary"]),
                  "highest NB likelihood; reported with the simulated confusion matrix (USL and Carnot differ by (1 - p)^(N-1), "
                  "about 6% at N = 12 with p = 0.005, so they are not separated)", "REPORTED",
                  "order " + " > ".join(f"{r} ({f['ll'][r]:.2f})" for r in order) + f"; three-way: {f['best3']}; linear vs bending: "
                  f"{f['binary']} (log LR linear - best bent {f['log_lr_linear_vs_best_bent']:+.2f})"))
    u = R["util"]
    uh = u[int(nhi)]
    flag = ("saturating" if uh["reviewer_util_max"] >= 0.8 else "approaching" if uh["reviewer_util_max"] >= 0.5 else "far from saturation")
    O.append(_res("UTIL", f"Where the limit moves: reviewer and merge-queue utilisation at N = {nhi}",
                  {str(k): dict(reviewer_util_mean=v["reviewer_util_mean"], reviewer_util_max=v["reviewer_util_max"],
                                mq_util_max=v["mq_util_max"], review_s_mean=v["review_s_mean"], V=v["V"]) for k, v in u.items()},
                  "reported; a reviewer busy >= 50% of an N_high window is 'approaching', >= 80% 'saturating' (then SCALE may "
                  "reflect review as well as the workers)", flag.upper().replace(" ", "-"),
                  "; ".join(f"N = {k}: reviewer {_f(v['reviewer_util_mean'])} (max {_f(v['reviewer_util_max'])}), merge queue "
                            f"{_f(v['mq_util_max'], '{:.3f}')}, review {_f(v['review_s_mean'], '{:.0f}')} s, V {_f(v['V'], '{:.0f}')}/h"
                            for k, v in u.items())))
    O.append(_res("COLL-m", "H3: collisions come from changes sharing a file (jm, given j)",
                  dict(p=c.get("p_jm"), or_jm=c.get("or_jm")), "logistic collided ~ j + jm, one-sided LR p for jm", "REPORTED",
                  f"OR {_f(c.get('or_jm'), '{:.3f}')}, p = {_f(c.get('p_jm'), '{:.3g}')}" if c["fit_ok"] else "not modelled"))
    O.append(_res("COLL-k", "Study 1's H1 under control: collisions vs k (changes in flight at first submit) and m",
                  dict(p=c.get("p_k")), "logistic collided ~ k + m, one-sided LR p for k", "REPORTED",
                  f"p = {_f(c.get('p_k'), '{:.3g}')}, mean k {_f(c.get('k_mean'))}" if c["fit_ok"] else "not modelled"))
    ps = R["per_size"]
    O.append(_res("LAMBDA", "Per-size lambda (first submissions per slot-hour), completion, timeouts, start-up, rework wait",
                  ps, "reported", "REPORTED",
                  "; ".join(f"N = {k}: lambda {_f(v['lam'])}, completion {_f(v['completion'])}, timeouts {v['timeouts']}, "
                            f"start-up {_f(v['startup_min'])} min" for k, v in ps.items())))
    O.append(_res("BOUNCE", "Bounce causes per approval with a merge-queue result, by size", {k: v["bounces"] for k, v in ps.items()},
                  "reported", "REPORTED",
                  "; ".join(f"N = {k}: " + ", ".join(f"{c_}: {n_}" for c_, n_ in v["bounces"]["causes"].items()) for k, v in ps.items())))
    ef = R.get("effort")
    if ef is None:
        O.append(_res("EFFORT", "Escapes by review effort (post-hoc max-effort re-review of every submitted head)", None,
                      "descriptive; needs --effort-log", "N/A", "no effort log given"))
    else:
        txt = []
        for eff, v in ef["matched"].items():
            txt.append(f"{eff}: {v['heads']} heads, agreement {_f(v['agreement'])}, escapes live {_f(v['live_default']['rate'], '{:.3f}')} "
                       f"vs {eff} {_f(v['posthoc']['rate'], '{:.3f}')} (on {v['with_hidden_truth']} heads with hidden-test truth)")
        O.append(_res("EFFORT", "Escapes by review effort (post-hoc max-effort re-review of every submitted head)",
                      ef["matched"], "descriptive", "REPORTED", "; ".join(txt) or "no matched heads"))
    rank = {k: i for i, k in enumerate(V5_ORDER)}
    return sorted(O, key=lambda o: rank.get(o["id"], len(rank)))


GRADE_TAG_V5 = {CONFIRMATORY: "confirmatory", DESCRIPTIVE: "descriptive"}


def _gtxt(o):
    return f"{o['grade']}" + (f", {o['role']}" if o.get("role") and o["role"] != SECONDARY else
                              (", secondary" if o.get("role") == SECONDARY else ""))


def render_md_v5(R):
    nlo, nhi = R["N_low"], R["N_high"]
    OC = R["operating_characteristics"]
    L = ["# Fleet sweep (study 2, PLAN-v5): results draft", "",
         "Generated by `analysis/score.py` (PLAN-v5; the default `--plan v5`). Numbers are filled in by the script; the prose "
         "around them is to be written. Every result is labelled **[confirmatory]** or **[descriptive]** as fixed before the "
         "sweep (PLAN-v5 section 5). Review is automated and fast (a fresh default-effort call per change, 10-30 s), so the "
         "study asks what limits the fleet when review does not: the workers' own scaling (SCALE, primary), collisions "
         "(COLL), and quality (ESC). There is no review-capped rival.", ""]
    L.append(f"Sizes: {', '.join(str(n) for n in R['sizes'])}. Windows: {len(R['windows'])} (" +
             ", ".join(f"{sum(1 for s in R['windows'] if s['n_workers'] == n)} at N = {n}" for n in R["sizes"]) +
             f"). Over-dispersion CV fixed at {R['cv']}. Constants: alpha = {R['constants']['alpha']}, beta = "
             f"{R['constants']['beta']}, p = {R['constants']['p']}.")
    if R["supply_flagged"]:
        L.append(f"\n**Task supply ran out more than 10 min before the window end** in {', '.join(R['supply_flagged'])}; see SCALE-nf.")
    L += ["", "## Summary of pre-registered results\n", "| ID | Grade | Statement | Code | Detail |", "|---|---|---|---|---|"]
    for o in R["outcomes"]:
        L.append(f"| {o['id']} | {_gtxt(o)} | {o['statement']} | **{o['code']}** | {o['rule']}. {o['note']} |")
    L.append("")
    sc = R["scale"]
    L.append(f"## SCALE [confirmatory, primary]: does finished work bend?\n")
    L.append("| Window | N | slot-hours | finished | per slot-hour |\n|---|---|---|---|---|")
    for s in R["windows"]:
        h = exposure_hours(s) * s["n_workers"]
        L.append(f"| {s['run_id']} | {s['n_workers']} | {h:.2f} | {s['finished']} | {s['finished'] / h if h else math.nan:.2f} |")
    ci = sc.get("per_agent_ratio_ci") or [math.nan, math.nan]
    L.append(f"\nPer-agent finished rate: " + ", ".join(f"N = {k}: {_f(v)}" for k, v in sc.get("per_agent", {}).items()) +
             f". Ratio N = {nhi} : {nlo} = {_f(sc['per_agent_ratio'])} (model {_f(sc.get('per_agent_ratio_model'))}, profile 95% "
             f"{_f(ci[0])}-{_f(ci[1])}); gamma = {_f(sc.get('gamma'), '{:+.3f}')}; one-sided LR p = {_f(sc['p'], '{:.3g}')}.")
    L.append("Sensitivity [descriptive]: " + "; ".join(f"`{m}` p = {_f(v['p'], '{:.3g}')}" + (f" (CV-hat {_f(v.get('cv_hat'))})" if 'cv_hat' in v else "")
                                                     for m, v in R["scale_sensitivity"].items()) + ".")
    so = OC["SCALE"]
    L.append(f"Simulated: false alarm {_f(so['fpr_linear'])} under linear truth (CV 0.3; {_f(so['fpr_linear_cv05'])} if the true "
             f"window CV is 0.5); power {_f(so['power_amdahl'])} / {_f(so['power_usl'])} / {_f(so['power_mild'])} under Amdahl / USL / "
             f"mild-bend truth.\n")
    L.append("Rival predictions per window (each rival's level fitted to all windows) [descriptive]:\n")
    f = R["family"]
    L.append("| Window | N | finished | " + " | ".join(RIVAL_LABEL_V5[r] for r in RIVALS_V5) + " |")
    L.append("|---|---|---|" + "---|" * len(RIVALS_V5))
    for w in f["windows"]:
        L.append(f"| {w['run_id']} | {w['N']} | {w['finished']} | " + " | ".join(f"{w['pred_' + r]:.1f}" for r in RIVALS_V5) + " |")
    L.append("| **log-likelihood** | | | " + " | ".join(f"**{f['ll'][r]:.2f}**" for r in RIVALS_V5) + " |")
    L.append(f"\nFAMILY [descriptive]: best {f['best']}, three-way {f['best3']}, linear vs bending {f['binary']}.")
    cm = OC["FAMILY"].get("confusion") or {}
    if cm:
        L.append("\nSimulated confusion matrix (rows: truth; columns: best-fitting rival):\n")
        L.append("| truth | " + " | ".join(RIVALS_V5) + " |\n|---|" + "---|" * len(RIVALS_V5))
        for t, row in cm.items():
            L.append(f"| {t} | " + " | ".join(_f(row.get(c, 0.0)) for c in RIVALS_V5) + " |")
    if "family_pilot_anchored" in R:
        fa = R["family_pilot_anchored"]
        L.append(f"\nPilot-anchored reading [descriptive]: best {fa['best']}, linear vs bending {fa['binary']}.")
    L.append("")
    c = R["collision"]
    L.append("## COLL [confirmatory, secondary]: collisions against merges since a change's base\n")
    L.append("| N | first merge-queue passes | collisions | mean j |\n|---|---|---|---|")
    for k, v in c["by_size"].items():
        L.append(f"| {k} | {v['n']} | {v['collisions']} | {_f(v['j_mean'])} |")
    oc_c = next(o for o in R["outcomes"] if o["id"] == "COLL")
    L.append(f"\n{oc_c['note']}.\n")
    L.append(f"COLL-m [descriptive] (H3, file-sharing merges jm): {next(o for o in R['outcomes'] if o['id'] == 'COLL-m')['note']}. "
             f"COLL-k [descriptive] (study 1's H1 on k in flight): {next(o for o in R['outcomes'] if o['id'] == 'COLL-k')['note']}.\n")
    e = R["escape"]
    L.append("## ESC and ESC-N [descriptive]: the escape rate, overall and by size\n")
    L.append("| N | approvals with a hidden-test result | escaped | rate | Wilson 95% |\n|---|---|---|---|---|")
    for k, v in e["by_size"].items():
        L.append(f"| {k} | {v['approvals']} | {v['escaped']} | {_f(v['rate'], '{:.3f}')} | {_f(v['wilson95'][0], '{:.3f}')}-{_f(v['wilson95'][1], '{:.3f}')} |")
    L.append(f"| all | {e['n']} | {e['events']} | {_f(e['rate'], '{:.3f}')} | {_f(e['wilson95'][0], '{:.3f}')}-{_f(e['wilson95'][1], '{:.3f}')} |")
    L.append(f"\n{next(o for o in R['outcomes'] if o['id'] == 'ESC-N')['note']}. Integration failures (fail only after rebase) "
             "are the collision channel and are counted in COLL, not here.\n")
    L.append("## UTIL [descriptive]: reviewer and merge queue\n")
    L.append("| N | reviewer util mean | max | merge-queue util max | review s mean | p90 | V /busy h | reviews /h | merge-queue s/change |")
    L.append("|---|---|---|---|---|---|---|---|---|")
    for k, v in R["util"].items():
        L.append(f"| {k} | {_f(v['reviewer_util_mean'])} | {_f(v['reviewer_util_max'])} | {_f(v['mq_util_max'], '{:.3f}')} | "
                 f"{_f(v['review_s_mean'], '{:.0f}')} | {_f(v['review_s_p90'], '{:.0f}')} | {_f(v['V'], '{:.0f}')} | "
                 f"{_f(v['reviews_per_hour'], '{:.1f}')} | {_f(v['ci_time_s_mean'], '{:.1f}')} |")
    L.append("")
    L.append("## LAMBDA and BOUNCE [descriptive]\n")
    L.append("| N | windows | lambda /slot-h | finished /slot-h | completion | timeouts (before submit) | start-up min | rework wait min | b_review |")
    L.append("|---|---|---|---|---|---|---|---|---|")
    for k, v in R["per_size"].items():
        L.append(f"| {k} | {v['windows']} | {_f(v['lam'])} | {_f(v['finished_per_slot_hour'])} | {_f(v['completion'])} | "
                 f"{v['timeouts']} ({v['timeouts_before_submit']}) | {_f(v['startup_min'])} | {_f(v['rework_wait_min'])} | "
                 f"{_f(v['bounces']['b_review'])} |")
    L.append("")
    for k, v in R["per_size"].items():
        L.append(f"- N = {k}: " + ", ".join(f"{c_} {n_}" for c_, n_ in v["bounces"]["causes"].items()) +
                 f" (approvals with a merge-queue result {v['bounces']['approvals_resolved']})")
    L.append("")
    L.append("## EFFORT [descriptive]: post-hoc re-review at max effort\n")
    ef = R.get("effort")
    if ef is None:
        L.append("No effort log given (`score.py --effort-log <jsonl>`; format in `v5.load_effort_log`).\n")
    else:
        L.append("| effort | heads matched | agreement with live | heads with hidden-test truth | escape rate live (default) | "
                 "escape rate post hoc | catch live | catch post hoc | false reject live | false reject post hoc |")
        L.append("|---|---|---|---|---|---|---|---|---|---|")
        for eff, v in ef["matched"].items():
            L.append(f"| {eff} | {v['heads']} | {_f(v['agreement'])} | {v['with_hidden_truth']} | {_f(v['live_default']['rate'], '{:.3f}')} "
                     f"({v['live_default']['escaped']}/{v['live_default']['approvals']}) | {_f(v['posthoc']['rate'], '{:.3f}')} "
                     f"({v['posthoc']['escaped']}/{v['posthoc']['approvals']}) | {_f(v['catch_live'])} | {_f(v['catch_posthoc'])} | "
                     f"{_f(v['false_reject_live'])} | {_f(v['false_reject_posthoc'])} |")
        if any(ef["unmatched"].values()):
            L.append(f"\nRows not matched to a live review: {ef['unmatched']}.")
        L.append("")
    L.append("## Per-window derived quantities [descriptive]\n")
    L.append("| Window | N | attempts | lambda | finished | censored | reviews | reviewer util | escaped | integration | rebase conflicts | timeouts |")
    L.append("|---|---|---|---|---|---|---|---|---|---|---|---|")
    for s in R["windows"]:
        L.append(f"| {s['run_id']} | {s['n_workers']} | {s['attempts']} | {_f(s['lam'])} | {s['finished']} | {s['censored']} | "
                 f"{s['reviews']} | {_f(s['reviewer_util'])} | {s['escaped']} | {s['integration_failures']} | "
                 f"{s['bounces'].get('rebase_conflict', 0)} | {s.get('session_timeouts', 0)} |")
    return "\n".join(L) + "\n"


# ---------------------------------------------------------------------------------------------- predictions
def design_cost(sizes, reps, window_min, burn, t0_usd=None, t1_h=None, pilot_h=0.0):
    t0 = DESIGN_V5["t0_usd"] * burn / DESIGN_V5["burn_assumed"] if t0_usd is None else t0_usd
    t1 = DESIGN_V5["t1_session_hours"] if t1_h is None else t1_h
    sweep_h = sum(n * reps.get(n, 0) for n in sizes) * window_min / 60.0
    return dict(sweep_session_hours=sweep_h, t1_session_hours=t1, pilot_session_hours=pilot_h, t0_usd=t0,
                total_usd=t0 + burn * (t1 + pilot_h + sweep_h), burn=burn)


def predictions_v5(sizes, reps, window_min=120.0, warmup_min=10.0, lam=6.0, completion=0.9, b_review=0.3,
                   review_s=20.0, mq_s=2.0, alpha=ALPHA, beta=BETA, p=P_COLLISION, cv=CV_OVERDISPERSION, supply=None):
    """Point predictions per rival: the ratio to N = 1 (no pilot needed; what SCALE and FAMILY test) and, for
    illustration, finished per window from lambda x completion (from T1 or the design assumption)."""
    from common import nb_interval, pooled_interval
    h = (window_min - warmup_min) / 60.0
    rows = []
    for r in RIVALS_V5:
        for n in sizes:
            g = shape(r, n, alpha, beta, p)
            mu = lam * completion * g * h
            att = lam * float({"linear": X_linear, "amdahl": lambda x: X_amdahl(x, alpha),
                               "usl": lambda x: X_usl(x, alpha, beta), "carnot": lambda x: X_usl(x, alpha, beta)}[r](n)) * h
            rev_h = att / h / max(1 - b_review, 1e-9)
            k = reps.get(n, 0)
            rows.append(dict(rival=r, N=n, ratio_to_1=g, per_agent_ratio=g / n, finished=mu, finished_95=list(nb_interval(mu, cv)),
                             windows=k, finished_total=mu * k,
                             finished_total_95=list(pooled_interval([mu] * k, cv)) if k else None,
                             attempts=att, reviews_per_hour=rev_h, reviewer_util=rev_h * review_s / 3600.0,
                             reviewer_util_slow=rev_h * 1.5 * review_s / 3600.0, mq_util=rev_h * (1 - b_review) * mq_s / 3600.0,
                             supply_exhausted_min=(60.0 * supply / (att / h) if supply and att > 0 and 60.0 * supply / (att / h) < window_min else None)))
    return rows


def oc_statement_v5():
    oc = V5_OC
    s = oc["SCALE"]
    c = oc["COLL"]
    e = oc["ESC_N"]
    L = [f"### Operating characteristics (simulated; {oc['source']})", ""]
    L.append(f"- **Confirmatory, primary (SCALE):** per-agent finished output falls from N = 1 to N = 12 (one-sided NB LR, CV 0.3). "
             f"False alarm under linear workers {_f(s['fpr_linear'])} (window CV 0.3), {_f(s['fpr_linear_cv015'])} at CV 0.15, "
             f"{_f(s['fpr_linear_cv05'])} at CV 0.5; power {_f(s['power_amdahl'])} under Amdahl (alpha 0.1), {_f(s['power_usl'])} under "
             f"USL, {_f(s['power_carnot'])} under Carnot uncapped, {_f(s['power_mild'])} against a mild bend (alpha 0.03, not a rival). "
             f"At 1.5x burn (degrade design): power {_f(s['burn15_power_amdahl'])}, false alarm {_f(s['burn15_fpr'])}; at lambda "
             f"-30%: {_f(s['lam70_power_amdahl'])} / {_f(s['lam70_fpr'])}.")
    L.append(f"- **Confirmatory, secondary (COLL):** collisions rise with merges since a change's base (one-sided LR on j). False "
             f"alarm {_f(c.get('fpr'))}; power " + ", ".join(f"{_f(v)} at p = {k}" for k, v in (c.get("power") or {}).items()) +
             " with USL-like workers; " + ", ".join(f"{_f(v)} at p = {k}" for k, v in (c.get("power_linear") or {}).items()) +
             " with linear workers (more merges). A null result rules out p of about 0.02 or more, not p = 0.005.")
    L.append(f"- **Descriptive:** ESC-N (escapes rising with N): power {_f(e['power_020'])} for 0.10 -> 0.20, {_f(e['power_015'])} "
             f"for 0.10 -> 0.15, false alarm {_f(e['fpr'])}; ESC (the escape rate, median 95% half-width "
             f"{_f(oc['ESC']['halfwidth_median'], '{:.3f}')}); FAMILY (four-way pick; USL and Carnot are not separable); UTIL; "
             "COLL-m; COLL-k; LAMBDA; BOUNCE; EFFORT.")
    return L
