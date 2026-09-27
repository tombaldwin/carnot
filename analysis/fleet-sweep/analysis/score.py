#!/usr/bin/env python3
"""Primary analysis of the fleet sweep (PLAN-v3 sections 1, 2 and 7).

    python score.py --pilot pilot.json runs/<w1> runs/<w2> runs/<w3> runs/<w4> \
        --out-dir results/            # writes results/results.json and results/RESULTS-draft.md

Reads the four sweep windows (two sizes, any order) and the pre-registered pilot parameters, and
computes:

1. Rival scoring: negative-binomial (Poisson with fixed over-dispersion, CV 0.3) log-likelihood of each
   window's finished count under each rival's point prediction; likelihood ratios; the family with the
   highest likelihood. The pooled ratio finished(high) / finished(low) is descriptive only.
2. The section-1 outcomes and pre-registered surprises, coded PASS / FAIL (N/A when the condition
   they depend on is not met):
     S1 finished(high) >= 1.3 x finished(low) while the review queue is non-empty for most of the window
     S2 finished(high) <= 0.7 x finished(low) while the queue is non-empty
     S3 b rising with fleet size (one-sided Fisher exact p < 0.05 on bounces per review)
     S4 V outside +/-25% of the pilot (pooled per size)
     S5 escape rate flat or falling with queue depth while V rises (> 1.25 x)
   and O1 (V stable, incl. half-window drift), O2 (finished at N_high inside Carnot's 95% predictive
   interval), O3 (attempt rate rising), O4 (escaped defects rising with depth), O5 (collisions, descriptive).
3. V per window and half-window against the pilot's +/-25% band.
4. Attempts per hour and per agent-hour, low vs high.
5. Escaped defects: logistic on queue depth at review + time in window + window fixed effects, task as
   a stratum (conditional logistic). Fewer than 8 escaped-defect events -> descriptive only.
6. Collisions: logistic of "bounced at least once" on k and m with window fixed effects, censored first
   attempts excluded; plus the model's own form 1 - s_w (1-p)^k (1-p_m)^m for p-hat.
7. Descriptive: alpha, beta from carnot.py fit on (N, attempts per hour).
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
from common import (CV_OVERDISPERSION, RIVAL_LABEL, RIVALS, X_usl, clogit_fit, collision_fit,  # noqa: E402
                    logit_fit, lr_one_sided, nb_logpmf, rate_ci, rate_ratio_test, two_prop_one_sided,
                    window_dummies)
from derive import _clean, derive_dir  # noqa: E402
from predict import Params, predict_window  # noqa: E402
from scipy import stats  # noqa: E402

BAND = 0.25


def _exp(x):
    """exp that returns inf (not an exception) for a separated fit's huge coefficient."""
    try:
        return math.exp(x)
    except OverflowError:
        return math.inf
MIN_ESCAPES = 8
READINGS = ("plan", "recovered", "completion")
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
    def pool(SS):
        rev = sum(s["reviews"] for s in SS)
        bb = sum(s["bounces_total"] for s in SS)
        br = sum(s["bounces"]["review"] for s in SS)
        ar = sum(s["approvals_resolved"] for s in SS)
        bh = sum(s["bounces"]["escaped_defect"] + s["bounces"]["integration_failure"] for s in SS)
        return dict(reviews=rev, bounces=bb, b=bb / rev if rev else math.nan, b_review=br / rev if rev else math.nan,
                    b_hidden=bh / ar if ar else math.nan, approvals_resolved=ar)
    lo, hi = pool(S_lo), pool(S_hi)
    p_up = two_prop_one_sided(lo["bounces"], lo["reviews"], hi["bounces"], hi["reviews"])
    return dict(low=lo, high=hi, p_rising=p_up)


def attempt_checks(S_lo, S_hi, P: Params):
    def pool(SS):
        a = sum(s["attempts"] for s in SS)
        h = sum(s["hours"] for s in SS)
        wh = sum(s["worker_hours"] for s in SS)
        return dict(attempts=a, hours=h, worker_hours=wh, per_hour=a / h if h else math.nan,
                    per_agent_hour=a / wh if wh else math.nan)
    lo, hi = pool(S_lo), pool(S_hi)
    ratio, p_up, p_down = rate_ratio_test(lo["attempts"], lo["hours"], hi["attempts"], hi["hours"])
    ratio_pa, pa_up, pa_down = rate_ratio_test(lo["attempts"], lo["worker_hours"], hi["attempts"], hi["worker_hours"])
    nl, nh = S_lo[0]["n_workers"], S_hi[0]["n_workers"]
    usl_pa = (float(X_usl(nh, P.alpha, P.beta)) / nh) / (float(X_usl(nl, P.alpha, P.beta)) / nl)
    return dict(low=lo, high=hi, total_ratio=ratio, p_total_rising=p_up, p_total_falling=p_down,
                per_agent_ratio=ratio_pa, p_per_agent_rising=pa_up, p_per_agent_falling=pa_down,
                usl_predicted_per_agent_ratio=usl_pa)


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


def pooled_interval(mus, cv=CV_OVERDISPERSION, level=0.95):
    mu = sum(mus)
    var = mu + cv ** 2 * sum(m * m for m in mus)
    if var <= mu:
        d = stats.poisson(mu)
    else:
        k = mu * mu / (var - mu)
        d = stats.nbinom(k, k / (k + mu))
    a = (1 - level) / 2
    return int(d.ppf(a)), int(d.ppf(1 - a))


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
def score(derived, pilot, *, rival_rework="plan", escape_model="clogit_task", cv=CV_OVERDISPERSION,
          do_escape=True, do_collision=True, do_fit=True, window_min=None, warmup_min=None):
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
    R = dict(sizes=dict(N_low=nl, N_high=nh), pilot=pilot, params=P.as_dict(), cv=cv)
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
    R["outcomes"] = code_outcomes(R)
    return _clean(R)


def _o(id_, text, value, rule, code, note=""):
    return dict(id=id_, statement=text, value=value, rule=rule, code=code, note=note)


def code_outcomes(R):
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


def render_md(R):
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


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("runs", nargs="+", help="sweep window run directories")
    ap.add_argument("--pilot", required=True, help="pilot.json (the pre-registered one)")
    ap.add_argument("--out-dir", default=".")
    ap.add_argument("--rival-rework", choices=list(READINGS), default="plan")
    ap.add_argument("--escape-model", choices=ESCAPE_MODELS, default="clogit_task")
    a = ap.parse_args()
    pilot = json.loads(Path(a.pilot).read_text())
    derived = [derive_dir(r) for r in a.runs]
    bad = [d["summary"]["run_id"] for d in derived if d["summary"]["kind"] != "sweep"]
    if bad:
        print(f"warning: non-sweep runs scored: {bad}", file=sys.stderr)
    R = score(derived, pilot, rival_rework=a.rival_rework, escape_model=a.escape_model)
    out = Path(a.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / "results.json").write_text(json.dumps(R, indent=2, default=float) + "\n")
    md = render_md(R)
    (out / "RESULTS-draft.md").write_text(md)
    print(md)


if __name__ == "__main__":
    main()
