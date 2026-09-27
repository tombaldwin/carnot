"""Shared pieces of the fleet-sweep analysis: model curves, time parsing, the
over-dispersed count likelihood, and small regression routines (numpy/scipy only).

Nothing here reads files or knows about the harness; derive/predict/score/synth
import from it so every script uses one definition of X(N), one likelihood and
one logistic fitter.
"""
from __future__ import annotations

import datetime as dt
import math

import numpy as np
from scipy import optimize, special, stats

# Model constants fixed by PLAN-v3 section 2 (study 1 for p), unchanged in PLAN-v4.
ALPHA = 0.1
BETA = 0.01
P_COLLISION = 0.005
CV_OVERDISPERSION = 0.3  # fixed, not estimated (PLAN-v3 section 2)

RIVALS = ("carnot", "usl", "amdahl", "linear")
RIVAL_LABEL = {
    "carnot": "Carnot, review-capped",
    "usl": "USL, no review limit",
    "amdahl": "Amdahl, alpha only, no review limit",
    "linear": "Linear",
}
BOUNCE_CAUSES = ("review", "rebase_conflict", "visible_fail", "escaped_defect", "integration_failure")

# ----------------------------------------------------------------------------- PLAN-v4 design point
# PLAN-v4 section 2: fixed sizes, no pilot gate, three 120-min windows per size in ABBAAB order.
PLAN_V4 = dict(sizes=(1, 12), reps=3, order="ABBAAB", window_min=120.0, warmup_min=10.0, grace_min=10.0,
               rival_rework="completion", escape_model="logit_cluster_task", lambda_target=8.0, q=2.0,
               review_cv_max=0.5, min_escape_events=8, alpha_test=0.05,
               sweep_session_hours=78.0, balance_floor_usd=50.0)

# Result grades (PLAN-v4 section 1). Every result in RESULTS-draft.md carries one of these.
CONFIRMATORY = "confirmatory"
CONDITIONAL = "conditional"
DESCRIPTIVE = "descriptive"

# Simulated operating characteristics at the design point, PLAN-v4.1 section 6.8: recomputed by
# design-search/oc_v41.py with the final setup (220-task supply, the grace-end V fix, a live-only pilot of T1
# plus eight 60-min T2 windows with no calibration pooling, Haiku assumptions as in DESIGN-SEARCH, the v4.1
# codings) and tabulated in ../OPERATING-CHARACTERISTICS.md. These, not the DESIGN-SEARCH figures, go into the
# pre-registration; the analysis only prints them next to the results (copied from oc_v41.json by hand).
V4_OC = dict(
    source="OPERATING-CHARACTERISTICS.md, design-search/oc_v41.py, 2000 simulated studies per cell",
    primary_correct=dict(carnot=0.97, usl=0.80,  # carnot: 0.965 (1930 of 2000), rounded
                         amdahl=1.00, linear=1.00, carnot_lambda_30pct_low=0.76,
                         usl_lambda_30pct_low=0.66, burn_1_5x=0.95, usl_burn_1_5x=0.75),
    # rows: truth; columns: family with the highest likelihood (base scenario, review-time CV 1)
    confusion=dict(carnot=dict(carnot=0.965, usl=0.023, amdahl=0.001, linear=0.0, tie=0.012),
                   usl=dict(carnot=0.198, usl=0.570, amdahl=0.221, linear=0.008, tie=0.005),
                   amdahl=dict(carnot=0.002, usl=0.158, amdahl=0.675, linear=0.166, tie=0.0),
                   linear=dict(carnot=0.0, usl=0.001, amdahl=0.069, linear=0.931, tie=0.0)),
    O2_false_alarm=dict(service_cv_1=0.10, service_cv_0_5=0.04),
    S3_false_alarm=0.06, O3_power=1.00,
    S1r_false_alarm=0.05, S2r_false_alarm=0.53,
    Vdur_power=dict(service_cv_1=0.18, service_cv_0_5=0.73), Vdur_fpr=dict(service_cv_1=0.04, service_cv_0_5=0.05),
    review_cv_ok_prob=dict(cv_0_3=1.00, cv_0_5=0.53, cv_0_7=0.00),
    escape_power=dict(a05=0.12, a01=0.04), escape_null=0.06,
    collision_power=dict(p01_a05=0.08, p05_a05=0.36, p05_a01=0.15), collision_null=0.05,
    loads=dict(N1=0.92, N12=3.23),
)


# ----------------------------------------------------------------------------- model curves
def X_usl(n, alpha=ALPHA, beta=BETA):
    """Gunther's USL in single-agent units (model.md)."""
    n = np.asarray(n, dtype=float)
    return n / (1 + alpha * (n - 1) + beta * n * (n - 1))


def X_amdahl(n, alpha=ALPHA):
    n = np.asarray(n, dtype=float)
    return n / (1 + alpha * (n - 1))


def X_linear(n, **_):
    return np.asarray(n, dtype=float)


def X_for(rival, n, alpha=ALPHA, beta=BETA):
    if rival in ("carnot", "usl"):
        return X_usl(n, alpha, beta)
    if rival == "amdahl":
        return X_amdahl(n, alpha)
    if rival == "linear":
        return X_linear(n)
    raise ValueError(rival)


# ----------------------------------------------------------------------------- time
def parse_t(s: str) -> float:
    """SCHEMA.md timestamp (UTC ISO 8601 with ms, trailing Z) -> POSIX seconds."""
    return dt.datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()


def iso(sec: float) -> str:
    t = dt.datetime.fromtimestamp(sec, dt.timezone.utc)
    return t.strftime("%Y-%m-%dT%H:%M:%S.") + f"{t.microsecond // 1000:03d}Z"


# ----------------------------------------------------------------------------- counts
def nb_size(cv=CV_OVERDISPERSION):
    """Negative binomial = Poisson(mu * G), G ~ Gamma(mean 1, CV cv): size k = 1/cv^2."""
    return math.inf if cv <= 0 else 1.0 / cv ** 2


def nb_logpmf(y, mu, cv=CV_OVERDISPERSION):
    """Log-likelihood of count y under mean mu with fixed common-mode CV (Var = mu + cv^2 mu^2)."""
    y = np.asarray(y, dtype=float)
    mu = np.maximum(np.asarray(mu, dtype=float), 1e-9)
    if cv <= 0:
        return stats.poisson.logpmf(y, mu)
    k = nb_size(cv)
    return (special.gammaln(y + k) - special.gammaln(k) - special.gammaln(y + 1)
            + k * np.log(k / (k + mu)) + y * np.log(mu / (k + mu)))


def nb_interval(mu, cv=CV_OVERDISPERSION, level=0.95):
    mu = max(float(mu), 1e-9)
    if cv <= 0:
        d = stats.poisson(mu)
    else:
        k = nb_size(cv)
        d = stats.nbinom(k, k / (k + mu))
    a = (1 - level) / 2
    return int(d.ppf(a)), int(d.ppf(1 - a))


def pooled_interval(mus, cv=CV_OVERDISPERSION, level=0.95):
    """Predictive interval for the total count over several windows with means mus, each window
    Poisson-gamma with common-mode CV cv (moment-matched negative binomial for the sum)."""
    mu = sum(mus)
    var = mu + cv ** 2 * sum(m * m for m in mus)
    if var <= mu:
        d = stats.poisson(max(mu, 1e-9))
    else:
        k = mu * mu / (var - mu)
        d = stats.nbinom(k, k / (k + mu))
    a = (1 - level) / 2
    return int(d.ppf(a)), int(d.ppf(1 - a))


def rate_ci(n, t, level=0.95):
    """Exact (gamma) interval for a Poisson rate n/t."""
    if t <= 0:
        return (math.nan, math.nan)
    a = (1 - level) / 2
    lo = 0.0 if n == 0 else stats.chi2.ppf(a, 2 * n) / 2 / t
    hi = stats.chi2.ppf(1 - a, 2 * n + 2) / 2 / t
    return (lo, hi)


def rate_ratio_test(n1, t1, n2, t2):
    """Conditional binomial test of rate2/rate1 (exact). Returns ratio, one-sided p(ratio>1), p(ratio<1)."""
    n = n1 + n2
    if n == 0 or t1 <= 0 or t2 <= 0:
        return math.nan, math.nan, math.nan
    p0 = t2 / (t1 + t2)
    ratio = (n2 / t2) / (n1 / t1) if n1 > 0 else math.inf
    p_up = stats.binom.sf(n2 - 1, n, p0)
    p_down = stats.binom.cdf(n2, n, p0)
    return ratio, float(p_up), float(p_down)


def two_prop_one_sided(k1, n1, k2, n2):
    """One-sided p for p2 > p1 (Fisher exact, conditional)."""
    if n1 == 0 or n2 == 0:
        return math.nan
    table = [[k2, n2 - k2], [k1, n1 - k1]]
    return float(stats.fisher_exact(table, alternative="greater").pvalue)


# ----------------------------------------------------------------------------- logistic
def _design(cols: dict, n: int, intercept=True):
    names, mats = [], []
    if intercept:
        names.append("const")
        mats.append(np.ones(n))
    for k, v in cols.items():
        names.append(k)
        mats.append(np.asarray(v, dtype=float))
    return names, np.column_stack(mats) if mats else np.zeros((n, 0))


def window_dummies(windows, prefix="w"):
    """Fixed effects for all windows but the first (reference)."""
    levels = sorted(set(windows))
    return {f"{prefix}[{lv}]": (np.asarray(windows) == lv).astype(float) for lv in levels[1:]}


def logit_fit(X, y, names, cluster=None, ridge=1e-6, maxit=100):
    """Unconditional logistic regression by Newton-Raphson with a tiny ridge (keeps separated
    fits finite). Returns coefficients, model and cluster-robust SEs, log-likelihood."""
    X = np.asarray(X, float)
    y = np.asarray(y, float)
    n, p = X.shape
    beta = np.zeros(p)
    R = ridge * np.eye(p)
    if p and names[0] == "const":
        R[0, 0] = 0
    converged = False
    for _ in range(maxit):
        eta = np.clip(X @ beta, -30, 30)
        mu = special.expit(eta)
        W = mu * (1 - mu)
        g = X.T @ (y - mu) - R @ beta
        H = X.T @ (X * W[:, None]) + R
        try:
            step = np.linalg.solve(H, g)
        except np.linalg.LinAlgError:
            step = np.linalg.lstsq(H, g, rcond=None)[0]
        beta = beta + step
        if np.max(np.abs(step)) < 1e-8:
            converged = True
            break
    eta = np.clip(X @ beta, -30, 30)
    mu = special.expit(eta)
    ll = float(np.sum(y * np.log(np.maximum(mu, 1e-300)) + (1 - y) * np.log(np.maximum(1 - mu, 1e-300))))
    H = X.T @ (X * (mu * (1 - mu))[:, None]) + R
    try:
        Hinv = np.linalg.inv(H)
    except np.linalg.LinAlgError:
        Hinv = np.linalg.pinv(H)
    se = np.sqrt(np.maximum(np.diag(Hinv), 0))
    se_cl = None
    if cluster is not None:
        scores = X * (y - mu)[:, None]
        cl = np.asarray(cluster)
        levels = np.unique(cl)
        meat = np.zeros((p, p))
        for lv in levels:
            s = scores[cl == lv].sum(axis=0)
            meat += np.outer(s, s)
        G = len(levels)
        adj = G / (G - 1) if G > 1 else 1.0
        V = Hinv @ meat @ Hinv * adj
        se_cl = np.sqrt(np.maximum(np.diag(V), 0))
    separated = bool(np.any(np.abs(beta) > 15))
    return {"names": list(names), "coef": beta, "se": se, "se_cluster": se_cl, "ll": ll,
            "converged": converged, "separated": separated, "n": n, "events": int(y.sum())}


def _clogit_ll(beta, strata_X, strata_y):
    ll = 0.0
    for Xs, ys in zip(strata_X, strata_y):
        eta = Xs @ beta
        d = int(ys.sum())
        num = float(eta[ys == 1].sum())
        # log of the elementary symmetric polynomial e_d(exp(eta)) by DP, fully in log space (a linear-scale DP
        # underflows to e_d = 0 when one row dominates a stratum with d >= 2 events; found by oc_v41.py)
        le = np.full(d + 1, -np.inf)
        le[0] = 0.0
        for ei in eta:
            le[1:] = np.logaddexp(le[1:], ei + le[:-1])
        ll += num - float(le[d])
    return ll


def clogit_fit(X, y, strata, names):
    """Conditional (fixed-strata) logistic regression. Only strata with 0 < events < size inform
    the fit. Columns with no within-stratum variation are dropped and reported."""
    X = np.asarray(X, float)
    y = np.asarray(y, float)
    strata = np.asarray(strata)
    sx, sy = [], []
    for s in np.unique(strata):
        idx = strata == s
        d = y[idx].sum()
        if 0 < d < idx.sum():
            sx.append(X[idx])
            sy.append(y[idx])
    out = {"names": list(names), "informative_strata": len(sx),
           "informative_rows": int(sum(len(v) for v in sy)), "events_informative": int(sum(v.sum() for v in sy))}
    if not sx:
        out.update(coef=None, ll=None, ok=False, dropped=list(names))
        return out
    allx = np.vstack([xx - xx.mean(axis=0) for xx in sx])
    keep = [j for j in range(X.shape[1]) if np.ptp(allx[:, j]) > 1e-12]
    dropped = [names[j] for j in range(X.shape[1]) if j not in keep]
    sxk = [xx[:, keep] for xx in sx]
    p = len(keep)
    if p == 0:
        out.update(coef=None, ll=None, ok=False, dropped=dropped)
        return out
    f = lambda b: -_clogit_ll(b, sxk, sy)
    res = optimize.minimize(f, np.zeros(p), method="L-BFGS-B", bounds=[(-20, 20)] * p)
    b = res.x
    # numerical Hessian for SEs
    h = 1e-4
    H = np.zeros((p, p))
    for i in range(p):
        for j in range(i, p):
            ei = np.zeros(p); ej = np.zeros(p)
            ei[i] = h; ej[j] = h
            H[i, j] = H[j, i] = (f(b + ei + ej) - f(b + ei - ej) - f(b - ei + ej) + f(b - ei - ej)) / (4 * h * h)
    try:
        se = np.sqrt(np.maximum(np.diag(np.linalg.inv(H)), 0))
    except np.linalg.LinAlgError:
        se = np.full(p, np.nan)
    coef = np.full(X.shape[1], np.nan)
    sef = np.full(X.shape[1], np.nan)
    coef[keep] = b
    sef[keep] = se
    out.update(coef=coef, se=sef, ll=-res.fun, ok=True, dropped=dropped,
               separated=bool(np.any(np.abs(b) > 15)), keep=keep)
    return out


def lr_one_sided(ll_full, ll_reduced, coef):
    """Likelihood-ratio test of one coefficient; one-sided p for coef > 0."""
    if ll_full is None or ll_reduced is None or coef is None or not np.isfinite(coef):
        return math.nan, math.nan
    lr = max(0.0, 2 * (ll_full - ll_reduced))
    p2 = float(stats.chi2.sf(lr, 1))
    p1 = p2 / 2 if coef > 0 else 1 - p2 / 2
    return p2, p1


# ----------------------------------------------------------------------------- collision model form
def collision_fit(y, k, m, windows, fit_pm=True):
    """The model's own form, per PR: P(bounced at least once) = 1 - s_w (1-p)^k (1-p_m)^m, where
    s_w is a per-window 'no bounce for other reasons' share. Returns p-hat with a profile-likelihood
    95% interval (p in [0, 0.5])."""
    y = np.asarray(y, float)
    k = np.asarray(k, float)
    m = np.asarray(m, float)
    w = np.asarray(windows)
    levels = sorted(set(w.tolist()))
    widx = np.array([levels.index(v) for v in w])
    nw = len(levels)

    def nll(theta, p_fixed=None):
        s = special.expit(theta[:nw])
        if p_fixed is None:
            p = theta[nw]
            pm = theta[nw + 1] if fit_pm else 0.0
        else:
            p = p_fixed
            pm = theta[nw] if fit_pm else 0.0
        surv = s[widx] * (1 - p) ** k * (1 - pm) ** m
        surv = np.clip(surv, 1e-12, 1 - 1e-12)
        return -float(np.sum(y * np.log(1 - surv) + (1 - y) * np.log(surv)))

    x0 = [0.5] * nw
    bounds = [(-10, 10)] * nw + [(0.0, 0.5)] + ([(0.0, 0.5)] if fit_pm else [])
    best = None
    for p0 in (0.0, 0.01, 0.05):
        r = optimize.minimize(nll, np.array(x0 + [p0] + ([0.0] if fit_pm else [])), method="L-BFGS-B",
                              bounds=bounds)
        if best is None or r.fun < best.fun:
            best = r
    p_hat = float(best.x[nw])
    pm_hat = float(best.x[nw + 1]) if fit_pm else 0.0
    nll_min = best.fun

    def prof(pv):
        r = optimize.minimize(lambda th: nll(th, p_fixed=pv), np.array(list(best.x[:nw]) + ([pm_hat] if fit_pm else [])),
                              method="L-BFGS-B", bounds=[(-10, 10)] * nw + ([(0.0, 0.5)] if fit_pm else []))
        return r.fun - nll_min - stats.chi2.ppf(0.95, 1) / 2

    lo = 0.0
    if p_hat > 1e-6 and prof(0.0) > 0:
        lo = optimize.brentq(prof, 0.0, p_hat, xtol=1e-5)
    hi = 0.5
    if prof(0.5) > 0:
        hi = optimize.brentq(prof, p_hat, 0.5, xtol=1e-5)
    return {"p_hat": p_hat, "p_ci": [lo, hi], "p_m_hat": pm_hat, "n": int(len(y)), "events": int(y.sum())}
