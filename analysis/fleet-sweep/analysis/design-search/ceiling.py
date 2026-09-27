"""Upper bound on family recovery for any sweep: oracle parameters (no pilot error, no rework / censoring /
merge-queue bias), finished counts per window ~ NB(mu_truth, CV 0.3), scored with the pre-registered NB
likelihood (common.nb_logpmf, CV 0.3). If a sweep shape cannot reach the target here, no pilot can rescue it.

Also computes the same with a level-free likelihood (each rival's scale profiled out: shape only), to show
what anchoring noise costs versus what window over-dispersion alone costs.
"""
from __future__ import annotations

import itertools
import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common import X_for, nb_logpmf  # noqa: E402

RIV = ("carnot", "usl", "amdahl", "linear")


def means(sizes, lam, q, h, c=0.72, appr_share=0.55, b=0.45):
    """Per-window finished means for each rival. Carnot cap = approvals/h = V (1 - b_review)(1 - b_hidden) ~
    V * appr_share; demand = lam X / (1 - b). Rivals anchored at N = 1 (oracle, so anchoring point is moot)."""
    V = q * lam
    out = {}
    for r in RIV:
        xs = np.array([float(X_for(r, n)) for n in sizes])
        out[r] = c * lam * xs * h
    dem = lam * np.array([float(X_for("carnot", n)) for n in sizes]) / (1 - b)
    out["carnot"] = np.minimum(out["usl"], appr_share * np.minimum(dem, V) * h)
    return out


def recover(sizes, reps, L, lam, q, R=4000, cv=0.3, profile=False, rng=None):
    rng = rng or np.random.default_rng(1)
    h = (L - 10) / 60
    mu = means(sizes, lam, q, h)
    idx = np.repeat(np.arange(len(sizes)), reps)
    res = {}
    for t in RIV:
        m = mu[t][idx]
        g = rng.gamma(1 / cv ** 2, cv ** 2, size=(R, len(idx)))
        y = rng.poisson(m * g)
        ll = {}
        for r in RIV:
            pr = np.broadcast_to(mu[r][idx], y.shape)
            if profile:
                # best common scale per study (grid), shape only
                sc = np.exp(np.linspace(-1.0, 1.0, 21))
                best = np.full(R, -np.inf)
                for s in sc:
                    best = np.maximum(best, nb_logpmf(y, pr * s, cv).sum(1))
                ll[r] = best
            else:
                ll[r] = nb_logpmf(y, pr, cv).sum(1)
        L_ = np.vstack([ll[r] for r in RIV])
        mx = L_.max(0)
        win = (L_ >= mx - 1e-9)
        uniq = win.sum(0) == 1
        res[t] = float((win[RIV.index(t)] & uniq).mean())
    res["min"] = min(res[t] for t in RIV)
    return res


def main():
    rows = []
    rng = np.random.default_rng(7)
    size_sets = [(1, 4), (1, 6), (1, 8), (2, 8), (1, 10), (1, 12), (1, 3, 8), (1, 4, 10), (1, 2, 4, 8)]
    for sizes, reps, L, lam, q in itertools.product(size_sets, [1, 2, 3, 4], [60, 90, 120], [4, 6, 8],
                                                    [1.5, 2, 2.5, 3]):
        wh = reps * sum(sizes) * L / 60
        for worker, rate in (("sonnet", 4.2), ("haiku", 2.1)):
            cost = rate * (wh + 6.0)     # + a minimal pilot (~6 worker-hours)
            if cost > 200:
                continue
            for prof in (False, True):
                r = recover(sizes, reps, L, lam, q, R=1000, profile=prof, rng=rng)
                rows.append(dict(sizes=sizes, reps=reps, L=L, lam=lam, q=q, worker=worker, cost=round(cost),
                                 profile=prof, **{k: round(v, 3) for k, v in r.items()}))
    out = Path(__file__).resolve().parent / "ceiling.json"
    out.write_text(json.dumps(rows, indent=1))
    for prof in (False, True):
        sub = sorted([r for r in rows if r["profile"] == prof], key=lambda r: -r["min"])
        print("profile" if prof else "anchored (oracle level)")
        for r in sub[:15]:
            print(r)


if __name__ == "__main__":
    main()
