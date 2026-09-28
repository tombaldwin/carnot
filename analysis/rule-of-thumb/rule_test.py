"""Score simple fleet-sizing rules against the paper's model (equations 4-6).

Every percentage the paper gives for the rule of thumb and the sizing formula comes from this script.

A rule is judged against the smallest fleet that gets within 5% of the best finished output
("the right size"), not just against the best output, because output is nearly flat past the
peak and extra agents cost tokens and add unchecked work. For each simulated team we record:
  near   - the rule's fleet gets at least 90% of the best finished output
  lean   - it runs no more than the right size plus one agent per codebase (no paying for idle agents)
  good   - both
Teams are drawn at random over the ranges in the paper. Multi-project teams (2-3 codebases sharing
their reviewers) are searched exhaustively over every split of agents between codebases.

Run: python rule_test.py [--n 20000] [--seed 1]
"""
import argparse, itertools, json, math
import numpy as np

RHO = 0.75          # target reviewer load
NMAX = 16           # agents per codebase searched

def draw(rng, k):
    """One team: k codebases with their own drag, sharing reviewers."""
    return dict(
        a=rng.uniform(.02, .4, k), b=np.exp(rng.uniform(math.log(.001), math.log(.035), k)),
        p=rng.uniform(0, .05, k), r0=rng.uniform(.2, .5, k), lam=rng.uniform(4, 10, k),
        rev=int(rng.choice([1, 2, 3, 4, 5, 6])), auto=rng.uniform(0, .8),
        hrs=rng.uniform(3, 5), rate=rng.uniform(150, 300), loc=rng.uniform(100, 250), k=k)

def per_codebase(t):
    """raw output and rework for n = 0..NMAX agents in each codebase: arrays (k, NMAX+1)."""
    n = np.arange(NMAX + 1)[None, :]
    a, b, p, r0, lam = (t[x][:, None] for x in ("a", "b", "p", "r0", "lam"))
    X = np.where(n > 0, n / (1 + a * (n - 1) + b * n * (n - 1)), 0.0)
    r = 1 - (1 - r0) * (1 - p) ** np.maximum(n - 1, 0)
    return lam * X, r

def capacity(t):
    return t["rev"] * t["hrs"] * t["rate"] * RHO / (1 - t["auto"]) / t["loc"]   # changes/day

def finished(raw, r, V):
    """Finished output when the codebases share review capacity V (in proportion to what they submit)."""
    tot = raw.sum(-1)
    scale = np.where(tot > 0, np.minimum(1.0, V / np.maximum(tot, 1e-12)), 0.0)
    return ((1 - r) * raw).sum(-1) * scale

def landscape(t):
    """Best finished output for each total fleet size, over every split between codebases."""
    raw, r = per_codebase(t)
    V = capacity(t)
    k = t["k"]
    grids = np.array(list(itertools.product(range(NMAX + 1), repeat=k)))        # (M, k)
    rr = raw[np.arange(k), grids]; rw = r[np.arange(k), grids]
    U = finished(rr, rw, V)
    total = grids.sum(1)
    best_by_total = np.zeros(NMAX * k + 1)
    np.maximum.at(best_by_total, total, U)
    return raw, r, V, best_by_total

def spread(total, k, cap):
    alloc = [0] * k
    for _ in range(int(total)):
        i = min(range(k), key=lambda j: alloc[j])
        if alloc[i] >= cap: break
        alloc[i] += 1
    return alloc

def evaluate(t, raw, r, V, best_by_total, total, cap):
    k = t["k"]
    alloc = spread(max(1, total), k, cap)
    U = finished(raw[np.arange(k), alloc], r[np.arange(k), alloc], V)
    umax = best_by_total.max()
    right = int(np.argmax(best_by_total >= .95 * umax))
    n = sum(alloc)
    near = U >= .9 * umax
    lean = n <= right + k
    return near, lean, near and lean, n - right

def formula(t):
    """Equations 1-2 for one codebase (used only for k = 1)."""
    q = capacity(t) / t["lam"][0]
    a, b, p = t["a"][0], t["b"][0], t["p"][0]
    ceil = (1 - a) / (p + math.sqrt(b))
    den = 1 - a * q - b * q * q
    return round(min(q / den if den > 0 else ceil, ceil))

def rules():
    R = {}
    for m in [1, 1.5, 2]:
        for cap in [3, 4, 5, 6, 8]:
            R[f"{m:g} per reviewer / share still read by people, max {cap} per codebase"] = (lambda t, m=m: round(m * t["rev"] / (1 - t["auto"])), cap)
    for m in [1, 2, 3]:
        R[f"{m} per reviewer, max 6 per codebase"] = (lambda t, m=m: m * t["rev"], 6)
    for c in [3, 4, 5, 6, 8]:
        R[f"always {c} per codebase"] = (lambda t, c=c: c * t["k"], c)
    return R

def run(n, seed, ks):
    rng = np.random.default_rng(seed)
    R = rules()
    out = {}
    for k in ks:
        acc = {name: [] for name in R}
        if k == 1: acc["sizing formula (equations 1-2)"] = []
        for _ in range(n if k == 1 else n // 5):
            t = draw(rng, k)
            raw, r, V, best = landscape(t)
            for name, (f, cap) in R.items():
                acc[name].append(evaluate(t, raw, r, V, best, f(t), cap))
            if k == 1:
                acc["sizing formula (equations 1-2)"].append(evaluate(t, raw, r, V, best, formula(t), NMAX))
        out[k] = {name: dict(near=np.mean([x[0] for x in v]), lean=np.mean([x[1] for x in v]),
                             good=np.mean([x[2] for x in v]), median_extra=float(np.median([x[3] for x in v])),
                             teams=len(v)) for name, v in acc.items()}
    return out

if __name__ == "__main__":
    ap = argparse.ArgumentParser(); ap.add_argument("--n", type=int, default=20000); ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--json")
    args = ap.parse_args()
    res = run(args.n, args.seed, ks=[1, 2, 3])
    for k, rows in res.items():
        print(f"\n## {k} codebase{'s' if k > 1 else ''} sharing reviewers ({next(iter(rows.values()))['teams']} teams)")
        print(f"{'rule':70s} near  lean  good  median extra agents")
        for name, v in sorted(rows.items(), key=lambda x: -x[1]["good"]):
            print(f"{name:70s} {v['near']:.2f}  {v['lean']:.2f}  {v['good']:.2f}  {v['median_extra']:+.0f}")
    if args.json:
        json.dump({str(k): v for k, v in res.items()}, open(args.json, "w"), indent=1, default=float)
