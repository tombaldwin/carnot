#!/usr/bin/env python3
"""Staged design search for study 2 (simulation only; no network, no API).

    PY=/Users/tom/git/carnot/analysis/aidev/.venv/bin/python
    $PY design_search.py --stage A      # sweep-shape screen, fixed good pilot, 4 truths, R = 100
    $PY design_search.py --stage B      # pilot levers on the top 30 of A, R = 100
    $PY design_search.py --stage C      # top designs + references, all truths, R = 800; robustness
Writes stageA.json, stageB.json, stageC.json next to this file. See DESIGN-SEARCH.md.
"""
from __future__ import annotations

import argparse
import itertools
import json
import math
import time
from dataclasses import asdict, replace
from pathlib import Path

import dsim
import runner
from dsim import Design, design_cost

HERE = Path(__file__).resolve().parent
SIZES = [(2, 6), (2, 8), (3, 9), (1, 6), (1, 8), (1, 10), (1, 12), (1, 3, 8), (1, 4, 10), (1, 4, 12)]
GOOD_PILOT = dict(n_p=1, L_p=60, pw=4, vcal=120, t1_reviewed=True)
PILOTS = [dict(n_p=n, L_p=lp, pw=pw, vcal=vc, t1_reviewed=True)
          for n in (1, 2) for (lp, pw) in ((60, 1), (120, 1), (60, 4), (60, 8)) for vc in (0, 120)] + \
         [dict(n_p=4, L_p=60, pw=1, vcal=0, t1_reviewed=True), dict(n_p=2, L_p=60, pw=1, vcal=0, t1_reviewed=False)]


def key(m):
    return (round(m["rec_min"], 3), round(m["bin_min"], 3))


def dump(path, rows):
    Path(path).write_text(json.dumps(rows, indent=1, default=lambda o: o if not isinstance(o, float) else float(o)))


B_FACTOR = {"sonnet": 1.64, "haiku": 1.84}   # 1/(1-b), b = bounces per review measured in synth (see DESIGN-SEARCH.md)


def load_ratio(d):
    """Design-point review demand / V at N_low and N_high (Carnot X): lambda X(N) / (1-b) / V."""
    from common import X_usl
    if d.gated:
        return None, None
    f = B_FACTOR[d.worker] / d.q
    return f * float(X_usl(min(d.sizes))), f * float(X_usl(max(d.sizes)))


def regime(d):
    lo, hi = load_ratio(d)
    if lo is None:
        return "gate"
    if lo <= 0.7 and hi >= 1.5:
        return "non-circular"
    if lo < 1.0 and hi >= 1.5:
        return "slack at low (near knee)"
    return "circular (saturated at low)"


def row(d, m, extra=None):
    c = design_cost(d)
    lo, hi = load_ratio(d)
    return dict(design=asdict(d), label=d.label(), nominal_cost=c["total"], agent_h=c["agent_h"],
                load_low=lo, load_high=hi, regime=regime(d),
                metrics={k: v for k, v in m.items()}, **(extra or {}))


def stage_A(R):
    ds = []
    for w, lam, q, sz, L, reps in itertools.product(("sonnet", "haiku"), (4, 6, 8), (1.5, 2, 2.5, 3), SIZES,
                                                   (60, 90, 120), (1, 2, 3, 4)):
        d = Design(worker=w, lam=lam, q=q, sizes=sz, L=L, reps=reps, **GOOD_PILOT)
        if design_cost(d)["total"] <= dsim.BUDGET:
            ds.append(d)
    print("stage A designs", len(ds), flush=True)
    out = []
    for i in range(0, len(ds), 200):
        chunk = ds[i:i + 200]
        res = runner.run(chunk, runner.FAM4, R)
        out += [row(d, runner.summarise(res[d])) for d in chunk]
        print(i + len(chunk), flush=True)
    out.sort(key=lambda r: key(r["metrics"]), reverse=True)
    dump(HERE / "stageA.json", out)
    return out


def stage_B(R, top=30):
    A = json.loads((HERE / "stageA.json").read_text())
    picked = []
    for q in (1.5, 2, 2.5, 3):
        sub = [r for r in A if r["design"]["q"] == q]
        picked += sorted(sub, key=lambda r: key(r["metrics"]), reverse=True)[:5]
        picked += sorted(sub, key=lambda r: (r["metrics"]["bin_min"], r["metrics"]["rec_min"]), reverse=True)[:4]
    base = []
    for r in picked:
        d = Design(**{**r["design"], "sizes": tuple(r["design"]["sizes"])})
        if d not in base:
            base.append(d)
    ds = []
    for b in base:
        for p in PILOTS:
            d = replace(b, **p)
            if d not in ds:
                ds.append(d)
    print("stage B designs", len(ds), flush=True)
    out = []
    for i in range(0, len(ds), 200):
        chunk = ds[i:i + 200]
        res = runner.run(chunk, runner.FAM4, R, seed0=50_000)
        out += [row(d, runner.summarise(res[d])) for d in chunk]
        print(i + len(chunk), flush=True)
    out.sort(key=lambda r: key(r["metrics"]), reverse=True)
    dump(HERE / "stageB.json", out)
    return out


ALL = ["carnot", "usl", "amdahl", "linear", "skim", "slow", "escape", "coll01", "coll05", "null"]

REFERENCES = {
    # PLAN-v3 as written: Sonnet, lambda 4, natural reviewer at the self-test's V0 = 12.7 (q = 3.2), pilot gate,
    # 2 x 90 min, T2 = 2 workers x 60 min, T1 reviewed, no calibration.
    "PLAN-v3 (gate)": Design(worker="sonnet", lam=4, q=3.2, sizes=("gate",), L=90, reps=2, n_p=2, L_p=60, pw=1, vcal=0),
    "PLAN-v3 sizes fixed (1, 5)": Design(worker="sonnet", lam=4, q=3.2, sizes=(1, 5), L=90, reps=2, n_p=2, L_p=60, pw=1,
                                         vcal=0),
    "Gate + better pilot": Design(worker="sonnet", lam=8, q=3.2, sizes=("gate",), L=60, reps=3, n_p=1, L_p=60, pw=4,
                                  vcal=120),
    "Gate + better pilot, lambda 4": Design(worker="sonnet", lam=4, q=3.2, sizes=("gate",), L=90, reps=2, n_p=1, L_p=60,
                                            pw=4, vcal=120),
    "Fixed (2, 8), plan pilot": Design(worker="sonnet", lam=4, q=3.2, sizes=(2, 8), L=90, reps=2, n_p=2, L_p=60, pw=1,
                                       vcal=0),
    "Fixed (3, 9), plan pilot": Design(worker="sonnet", lam=4, q=3.2, sizes=(3, 9), L=90, reps=1, n_p=2, L_p=60, pw=1,
                                       vcal=0),
}


def stage_C(R, top=12):
    B = json.loads((HERE / "stageB.json").read_text())
    ds = []
    for q in (1.5, 2, 2.5, 3):
        sub = [r for r in B if r["design"]["q"] == q]
        pick = sorted(sub, key=lambda r: key(r["metrics"]), reverse=True)[:3] + \
            sorted(sub, key=lambda r: (r["metrics"]["bin_min"], r["metrics"]["rec_min"]), reverse=True)[:3]
        for r in pick:
            d = Design(**{**r["design"], "sizes": tuple(r["design"]["sizes"])})
            if d not in ds:
                ds.append(d)
    # make sure both worker models are represented among the confirmed designs
    for w in ("sonnet", "haiku"):
        if not any(d.worker == w for d in ds):
            for r in B:
                if r["design"]["worker"] == w:
                    ds.append(Design(**{**r["design"], "sizes": tuple(r["design"]["sizes"])}))
                    break
    refs = list(REFERENCES.items())
    out = []
    t = time.time()
    res = runner.run(ds + [d for _, d in refs], ALL, R, seed0=100_000, extra=True)
    for d in ds:
        out.append(row(d, runner.summarise(res[d]), dict(kind="search")))
    for name, d in refs:
        out.append(row(d, runner.summarise(res[d]), dict(kind="reference", name=name)))
    print("C main", time.time() - t, flush=True)
    # service-time CV 0.5 (more regular reviews): V tests
    res = runner.run(ds, ["carnot", "skim", "slow"], R, seed0=200_000, service_cv=0.5)
    for r_, d in zip(out, ds):
        m = runner.summarise(res[d])
        r_["svc05"] = {k: m[k] for k in m if k.split("_")[0] in ("O1n", "S4n", "Vratio", "Vdur", "Vcal")}
    print("C svc", time.time() - t, flush=True)
    dump(HERE / "stageC.json", out)
    return out


def robustness(R, n=5):
    C = json.loads((HERE / "stageC.json").read_text())
    ds = [Design(**{**r["design"], "sizes": tuple(r["design"]["sizes"])}) for r in C if r.get("kind") == "search"][:n]
    scen = {"burn x1.5": dict(burn_mult=1.5), "lambda x0.7": dict(lam_scale=0.7),
            "slow merge queue (2.6 min)": dict(ci_slow=True), "anchor on low arm (analysis variant)": dict(anchor_low=True),
            "calibration shift sd 0.2": dict(vcal_shift_sd=0.2), "no window over-dispersion (cv 0)": dict(cv_window=0.0),
            "haiku lambda x0.7": dict(haiku_lam=0.7), "haiku lambda x1.0": dict(haiku_lam=1.0)}
    out = []
    for name, kw in scen.items():
        sub = ds
        if name.startswith("haiku"):
            sub = [d for d in ds if d.worker == "haiku"]
        if name.startswith("anchor"):
            sub = [d for d in ds if not d.gated and min(d.sizes) == d.n_p]
        if not sub:
            continue
        res = runner.run(sub, ["carnot", "usl", "amdahl", "linear", "skim", "slow"], R, seed0=300_000, **kw)
        for d in sub:
            out.append(dict(scenario=name, label=d.label(), design=asdict(d), metrics=runner.summarise(res[d])))
        print("robust", name, flush=True)
    # rework-lost truths (bounced changes abandoned) for the top design
    res = runner.run(ds[:2], ["usl", "amdahl", "linear"], R, seed0=400_000, truth_over=dict(rework_returns=False))
    for d in ds[:2]:
        out.append(dict(scenario="uncapped truths with rework lost", label=d.label(), design=asdict(d),
                        metrics=runner.summarise(res[d])))
    dump(HERE / "robustness.json", out)
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--stage", required=True, choices=["A", "B", "C", "R"])
    ap.add_argument("--reps", type=int, default=None)
    a = ap.parse_args()
    t = time.time()
    if a.stage == "A":
        rows = stage_A(a.reps or 100)
    elif a.stage == "B":
        rows = stage_B(a.reps or 100)
    elif a.stage == "C":
        rows = stage_C(a.reps or 800)
    else:
        rows = robustness(a.reps or 800)
    for r in rows[:15]:
        m = r["metrics"]
        print(r.get("scenario", ""), r["label"], round(r.get("nominal_cost", 0)), [round(m[f"rec_{t}"], 2) for t in runner.FAM4],
              round(m["rec_min"], 2), round(m["bin_min"], 2))
    print("time", round(time.time() - t))
