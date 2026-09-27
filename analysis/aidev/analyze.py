"""Test the Carnot rework term on AIDev, as pre-registered in PREREG.md.

    .venv/bin/python analyze.py            # writes results.json and prints a summary

Model (H1): P(merged) = exp(c_repo + theta * k), theta = log(1 - p), one intercept per repository.
The repository intercepts are profiled out exactly (a one-dimensional root per repository for any
given theta), so the only parameters optimised directly are the effects being tested.
"""
import json, math, re, sys
import numpy as np, pandas as pd
from scipy.optimize import minimize, minimize_scalar
from scipy.stats import chi2

NOISE_RE = re.compile(r"(^|/)(package-lock\.json|yarn\.lock|pnpm-lock\.yaml|Cargo\.lock|poetry\.lock|go\.sum|uv\.lock|"
                      r"Gemfile\.lock|CHANGELOG[^/]*|.*\.md|.*\.snap)$|^docs?/", re.I)
MIN_PRS = 20

# ------------------------------------------------------------------ data
def load():
    pr = pd.read_parquet("data/pull_request.parquet",
                         columns=["id", "agent", "user_id", "state", "created_at", "closed_at", "merged_at", "repo_id"])
    for c in ("created_at", "closed_at", "merged_at"):
        pr[c] = pd.to_datetime(pr[c], utc=True, errors="coerce")
    rv = pd.read_parquet("data/pr_reviews.parquet", columns=["pr_id", "state"])
    changes = set(rv.loc[rv.state == "CHANGES_REQUESTED", "pr_id"])
    files = pd.read_parquet("data/pr_files.parquet")
    files = files[~files.filename.fillna("").str.contains(NOISE_RE)]
    fsets = files.groupby("pr_id").filename.agg(lambda s: frozenset(s)).to_dict()
    pr["closed"] = pr.closed_at.notna()
    pr["unmerged"] = pr.closed & pr.merged_at.isna()
    pr["secondary"] = pr.unmerged | pr.id.isin(changes)
    return pr, fsets


def concurrency(pr, fsets):
    """k at creation, k from a different user, k from a different agent, k over the first 24h,
    and m (concurrent at creation and sharing a code file), per PR."""
    out = []
    far = pd.Timestamp.max.tz_localize("UTC")
    for repo, g in pr.groupby("repo_id", sort=False):
        g = g.sort_values("created_at")
        ids = g.id.to_numpy(); users = g.user_id.to_numpy(); agents = g.agent.to_numpy()
        cr = g.created_at.to_numpy(); cl = g.closed_at.fillna(far).to_numpy()
        n = len(g)
        for i in range(n):
            t = cr[i]
            conc = (cr < t) & (cl > t)                      # open when i was created
            conc[i] = False
            k = int(conc.sum())
            win = (cr < t + np.timedelta64(24, "h")) & (cl > t); win[i] = False
            idx = np.nonzero(conc)[0]
            fi = fsets.get(ids[i], frozenset())
            m = sum(1 for j in idx if fi and (fi & fsets.get(ids[j], frozenset())))
            pairs_with_files = sum(1 for j in idx if fi and fsets.get(ids[j]))
            out.append((ids[i], k, int((conc & (users != users[i])).sum()), int((conc & (agents != agents[i])).sum()),
                        int(win.sum()), m, pairs_with_files))
    c = pd.DataFrame(out, columns=["id", "k", "k_diff_user", "k_diff_agent", "k_24h", "m", "pairs_with_files"])
    return pr.merge(c, on="id")


# ------------------------------------------------------------------ models
def _profile_intercepts(off, y, grp, ngrp, link):
    """Per-repository intercept maximising the likelihood, for fixed offsets, by bisection."""
    if link == "log":            # P(y=1 = merged) = exp(c + off), needs c + off < 0
        hi = np.full(ngrp, np.inf); np.minimum.at(hi, grp, -off); hi = hi - 1e-9
        lo = hi - 40.0
        def score(c):
            eta = c[grp] + off; p = np.exp(eta)
            s = y - (1 - y) * p / (1 - p)
            return np.bincount(grp, s, ngrp)
    else:                        # identity: P(y=1 = unmerged) = r + off, needs 0 < r + off < 1
        lo = np.full(ngrp, -np.inf); np.maximum.at(lo, grp, -off); lo = lo + 1e-9
        hi = np.full(ngrp, np.inf); np.minimum.at(hi, grp, 1 - off); hi = hi - 1e-9
        def score(r):
            p = r[grp] + off
            s = y / p - (1 - y) / (1 - p)
            return np.bincount(grp, s, ngrp)
    for _ in range(80):          # score is decreasing in the intercept for both links
        mid = (lo + hi) / 2
        pos = score(mid) > 0
        lo = np.where(pos, mid, lo); hi = np.where(pos, hi, mid)
    return (lo + hi) / 2


def loglik(beta, X, y, grp, ngrp, link):
    off = X @ beta if X.shape[1] else np.zeros(len(y))
    c = _profile_intercepts(off, y, grp, ngrp, link)
    p = (np.exp(c[grp] + off) if link == "log" else c[grp] + off)
    p = np.clip(p, 1e-12, 1 - 1e-12)
    return float(np.sum(y * np.log(p) + (1 - y) * np.log(1 - p)))


def fit(X, y, grp, ngrp, link="log", x0=None):
    k = X.shape[1]
    if k == 0:
        return np.zeros(0), loglik(np.zeros(0), X, y, grp, ngrp, link)
    f = lambda b: -loglik(np.atleast_1d(b), X, y, grp, ngrp, link)
    if k == 1:
        r = minimize_scalar(f, bounds=(-2.0, 2.0) if link == "log" else (-0.5, 0.5), method="bounded",
                            options={"xatol": 1e-7})
        return np.array([r.x]), -r.fun
    r = minimize(f, x0 if x0 is not None else np.zeros(k), method="Nelder-Mead",
                 options={"xatol": 1e-7, "fatol": 1e-6, "maxiter": 4000})
    return r.x, -r.fun


def profile_ci(X, y, grp, ngrp, beta_hat, ll_hat, j=0, level=0.95):
    """Likelihood-ratio interval for coefficient j (others re-fitted at each point)."""
    cut = ll_hat - chi2.ppf(level, 1) / 2
    def ll_at(v):
        if X.shape[1] == 1:
            return loglik(np.array([v]), X, y, grp, ngrp, "log")
        others = [i for i in range(X.shape[1]) if i != j]
        b0 = np.delete(beta_hat, j)
        f = lambda b: -loglik(np.insert(b, j, v), X, y, grp, ngrp, "log")
        r = minimize(f, b0, method="Nelder-Mead", options={"xatol": 1e-6, "fatol": 1e-5})
        return -r.fun
    def edge(sign):
        step, v = 1e-6, beta_hat[j]   # was 0.002: floored every interval at about +/-0.001 (bug fixed 2026-09-27)
        while ll_at(v + sign * step) > cut and step < 4:
            step *= 2
        a, b = v + sign * step / 2, v + sign * step
        for _ in range(30):
            mid = (a + b) / 2
            if ll_at(mid) > cut: a = mid
            else: b = mid
        return (a + b) / 2
    return edge(-1), edge(+1)


def design(df, cols):
    return df[cols].to_numpy(dtype=float) if cols else np.zeros((len(df), 0))


def run_h1(df, kcol="k", extra=None, label=""):
    grp, ngrp = pd.factorize(df.repo_id)[0], df.repo_id.nunique()
    y = (~df.unmerged).to_numpy(dtype=float)
    cols = [kcol] + (extra or [])
    X = design(df, cols)
    b, ll = fit(X, y, grp, ngrp, "log")
    _, ll0 = fit(design(df, extra or []), y, grp, ngrp, "log")
    lr = 2 * (ll - ll0)
    lo, hi = profile_ci(X, y, grp, ngrp, b, ll, 0)
    th = b[0]
    return {"label": label, "n_prs": int(len(df)), "n_repos": int(ngrp), "theta": th, "theta_ci95": [lo, hi],
            "p_hat": 1 - math.exp(th), "p_ci95": [1 - math.exp(hi), 1 - math.exp(lo)],
            "lr_stat": lr, "lr_pvalue": float(chi2.sf(lr, 1)), "loglik": ll}


def main():
    pr, fsets = load()
    import os
    if os.path.exists("data/concurrency.parquet"):      # the slow step, cached; delete to recompute
        df = pd.read_parquet("data/concurrency.parquet")
    else:
        df = concurrency(pr, fsets)
        df.to_parquet("data/concurrency.parquet")
    df = df[df.closed]
    counts = df.groupby("repo_id").id.transform("size")
    df = df[counts >= MIN_PRS].copy()
    R = {"sample": {"closed_prs_in_repos_ge_20": int(len(df)), "repos": int(df.repo_id.nunique()),
                    "unmerged_rate": float(df.unmerged.mean()), "k_mean": float(df.k.mean()),
                    "k_median": float(df.k.median()), "share_k0": float((df.k == 0).mean())}}

    # H1
    R["H1"] = run_h1(df, "k", label="primary: closed unmerged vs k at creation")

    # H2: geometric (log link) vs linear (identity link), same per-repo intercepts
    grp, ngrp = pd.factorize(df.repo_id)[0], df.repo_id.nunique()
    y_unm = df.unmerged.to_numpy(dtype=float)
    blin, lllin = fit(design(df, ["k"]), y_unm, grp, ngrp, "identity")
    llgeo = R["H1"]["loglik"]
    R["H2"] = {"loglik_geometric": llgeo, "loglik_linear": lllin, "slope_linear": float(blin[0]),
               "aic_geometric_minus_linear": float(-2 * (llgeo - lllin))}  # same parameter count
    # binned shape: observed unmerged rate minus the repository's own baseline
    base = df.groupby("repo_id").unmerged.transform("mean")
    th = R["H1"]["theta"]
    bins = [0, 1, 2, 3, 5, 9, 17, 33, 10**9]
    lab = ["0", "1", "2", "3-4", "5-8", "9-16", "17-32", "33+"]
    df["kbin"] = pd.cut(df.k, bins=bins, right=False, labels=lab)
    shape = df.assign(resid=df.unmerged.astype(float) - base).groupby("kbin", observed=True).agg(
        n=("id", "size"), unmerged=("unmerged", "mean"), minus_repo_baseline=("resid", "mean"), k_mean=("k", "mean"))
    R["H2"]["shape"] = shape.reset_index().to_dict(orient="records")

    # H3: split k into m (file-sharing) and k - m
    d3 = df[df.id.isin(fsets.keys())].copy()
    d3["k_rest"] = d3.k - d3.m
    g3, n3 = pd.factorize(d3.repo_id)[0], d3.repo_id.nunique()
    y3 = (~d3.unmerged).to_numpy(dtype=float)
    X3 = design(d3, ["m", "k_rest"])
    b3, ll3 = fit(X3, y3, g3, n3, "log")
    ci_m = profile_ci(X3, y3, g3, n3, b3, ll3, 0)
    ci_r = profile_ci(X3, y3, g3, n3, b3, ll3, 1)
    R["H3"] = {"n_prs": int(len(d3)), "theta_m": b3[0], "theta_m_ci95": ci_m, "theta_rest": b3[1], "theta_rest_ci95": ci_r,
               "p_m": 1 - math.exp(b3[0]), "p_rest": 1 - math.exp(b3[1]), "share_with_m_ge1": float((d3.m > 0).mean())}

    # H4: file overlap among concurrent pairs where both have file data
    R["H4"] = {"pairs_with_files": int(df.pairs_with_files.sum()), "pairs_sharing_file": int(df.m.sum()),
               "file_overlap_rate": float(df.m.sum() / max(1, df.pairs_with_files.sum())),
               "p_hat": R["H1"]["p_hat"]}

    # secondary outcome
    ds = df.assign(unmerged=df.secondary)
    R["secondary"] = run_h1(ds, "k", label="secondary: unmerged or changes requested")

    # sensitivity analyses
    S = []
    S.append(run_h1(df, "k_diff_user", label="S1a: k from a different user only"))
    S.append(run_h1(df, "k_diff_agent", label="S1b: k from a different agent only"))
    agents = sorted(df.agent.dropna().unique())
    for a in agents[1:]:
        df["ag_" + a] = (df.agent == a).astype(float)
    S.append(run_h1(df, "k", extra=["ag_" + a for a in agents[1:]], label="S2: agent fixed effects"))
    S.append(run_h1(df, "k_24h", label="S3: k over the first 24 hours"))
    big = df.groupby("repo_id").id.transform("size") > 500
    S.append(run_h1(df[~big], "k", label="S4: repositories with at most 500 PRs"))
    R["sensitivity"] = S

    json.dump(R, open("results.json", "w"), indent=2, default=lambda o: o.item() if hasattr(o, "item") else str(o))
    print(json.dumps(R, indent=2, default=lambda o: o.item() if hasattr(o, "item") else str(o)))


if __name__ == "__main__":
    main()
