"""EXPLORATORY, not pre-registered: written 2026-09-27 AFTER seeing results.json.

The pre-registered sample is dominated by repositories where an agent opens and merges its own PRs
in seconds (mochilang/mochi alone is 13,194 PRs, median life 36 s). Those are not review-gated fleets,
which is what the model describes. This re-runs H1 and H3 on repositories whose median PR stays
open at least an hour. Treat the result as a hypothesis for study 2, not as a test.
"""
import json, math, pandas as pd
import analyze as A

pr, fsets = A.load()
df = pd.read_parquet("data/concurrency.parquet")
df = df[df.closed].copy()
df["life_h"] = (df.closed_at - df.created_at).dt.total_seconds() / 3600
df = df[df.groupby("repo_id").id.transform("size") >= A.MIN_PRS]
gated = df.groupby("repo_id").life_h.transform("median") >= 1.0
d = df[gated].copy()
out = {"repos": int(d.repo_id.nunique()), "prs": int(len(d)), "unmerged_rate": float(d.unmerged.mean()),
       "k_median": float(d.k.median())}
out["H1"] = A.run_h1(d, "k", label="exploratory H1: review-gated repos (median PR life >= 1h)")
d3 = d[d.id.isin(fsets.keys())].copy(); d3["k_rest"] = d3.k - d3.m
g, n = pd.factorize(d3.repo_id)[0], d3.repo_id.nunique()
y = (~d3.unmerged).to_numpy(float); X = A.design(d3, ["m", "k_rest"])
b, ll = A.fit(X, y, g, n, "log")
out["H3"] = {"p_m": 1 - math.exp(b[0]), "theta_m_ci95": A.profile_ci(X, y, g, n, b, ll, 0),
             "p_rest": 1 - math.exp(b[1]), "theta_rest_ci95": A.profile_ci(X, y, g, n, b, ll, 1)}
json.dump(out, open("exploratory.json", "w"), indent=2, default=float)
print(json.dumps(out, indent=2, default=float))
