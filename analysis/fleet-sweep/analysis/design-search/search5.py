#!/usr/bin/env python3
"""PLAN-v5 design search (simulation only; no network, no API, no cloud sessions).

    PY=/Users/tom/git/carnot/analysis/aidev/.venv/bin/python
    $PY search5.py --stage A      # screen every in-budget design, 9 truth cells, R = 200     -> v5_stageA.json
    $PY search5.py --stage B      # shortlist + pilot variants + window-CV scaling, R = 1000   -> v5_stageB.json
    $PY search5.py --stage OC     # the recommended and degrade designs, all cells, R = 2000  -> oc_v5.json
Common random numbers: the same seed block per truth across designs. See DESIGN-SEARCH-v5.md.
"""
from __future__ import annotations

import argparse
import itertools
import json
import math
import os
import time
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
from dataclasses import asdict
from pathlib import Path

import numpy as np

import dsim5
from dsim5 import D5, fits

HERE = Path(__file__).resolve().parent
SCALE_TRUTHS = ("linear", "mild", "amdahl", "usl", "carnot")
CELLS_A = SCALE_TRUTHS + ("usl/esc15", "usl/esc20", "carnot/p=0.01", "carnot/p=0.02")
TRUTH_IDX = {}
_POOL = None


def pool():
    global _POOL
    if _POOL is None:
        _POOL = ProcessPoolExecutor(max_workers=max(1, (os.cpu_count() or 2) - 1))
    return _POOL


def seed_of(tname, i, seed0=0):
    if tname not in TRUTH_IDX:
        TRUTH_IDX[tname] = sum(ord(c) * (k + 1) for k, c in enumerate(tname)) % 9973
    return 1 + seed0 + 100_000 * TRUTH_IDX[tname] + i


def run(designs, cells, R, seed0=0, **kw):
    """Returns {design: {cell: [result, ...]}}; kw goes into every cfg (e.g. cv_len=True)."""
    allc = []
    for d in designs:
        for c in cells:
            for i in range(R):
                allc.append((d, c, dict(design=asdict(d), truth=c, seed=seed_of(c, i, seed0), **kw)))
    res = list(pool().map(dsim5.study5, [x[2] for x in allc], chunksize=8))
    out = {}
    for (d, c, _), r in zip(allc, res):
        out.setdefault(d, {}).setdefault(c, []).append(r)
    return out


def _rate(rs, f):
    v = [bool(f(r)) for r in rs]
    return float(np.mean(v)) if v else math.nan


def _sig(x, a=0.05):
    return x is not None and x == x and x < a


def summarise(by_cell):
    m = {}
    for c, rs in by_cell.items():
        fam = c.split("/")[0]
        rs = [r for r in rs if r.get("status") == "SCORED"]
        m[f"n|{c}"] = len(rs)
        if "bend_p" in rs[0]:
            m[f"bend|{c}"] = _rate(rs, lambda r: _sig(r["bend_p"]))
            m[f"bend_estcv|{c}"] = _rate(rs, lambda r: _sig(r["bend_p_estcv"]))
            m[f"bend_welch|{c}"] = _rate(rs, lambda r: _sig(r["bend_p_welch"]))
            truth_bin = "linear" if fam == "linear" else "bending"
            m[f"bin|{c}"] = _rate(rs, lambda r: r["binary"] == truth_bin)
            if fam in ("linear", "amdahl", "usl", "carnot"):
                m[f"fam4|{c}"] = _rate(rs, lambda r: r["best"] == fam)
                f3 = {"usl": "usl-carnot", "carnot": "usl-carnot"}.get(fam, fam)
                m[f"fam3|{c}"] = _rate(rs, lambda r: r["best3"] == f3)
                m[f"picks|{c}"] = dict(Counter(r["best"] for r in rs))
            if "best_anchor" in rs[0]:
                m[f"bin_anchor|{c}"] = _rate(rs, lambda r: r["binary_anchor"] == truth_bin)
                if fam in ("linear", "amdahl", "usl", "carnot"):
                    m[f"fam4_anchor|{c}"] = _rate(rs, lambda r: r["best_anchor"] == fam)
            m[f"pa_ratio|{c}"] = float(np.median([r["pa_ratio"] for r in rs]))
            m[f"fin|{c}"] = {k: float(np.mean([r["finished"][k] for r in rs])) for k in rs[0]["finished"]}
            m[f"lam|{c}"] = {k: float(np.mean([r["lam"][k] for r in rs])) for k in rs[0]["lam"]}
            m[f"timeouts|{c}"] = {k: float(np.mean([r["timeouts"][k] for r in rs])) for k in rs[0]["timeouts"]}
        if "esc_p" in rs[0]:
            m[f"esc|{c}"] = _rate(rs, lambda r: _sig(r["esc_p"]))
            m[f"esc_hw|{c}"] = float(np.median([r["esc_hw"] for r in rs if r["esc_hw"] == r["esc_hw"]]))
            cl = [r["esc_cl_hw"] for r in rs if r.get("esc_cl_hw") is not None]
            m[f"esc_cl_hw|{c}"] = float(np.median(cl)) if cl else math.nan
            m[f"esc_n|{c}"] = float(np.median([r["esc_n"] for r in rs]))
            m[f"esc_by_size|{c}"] = {k: float(np.nanmean([r["esc_by_size"].get(k, math.nan) for r in rs]))
                                     for k in rs[0]["esc_by_size"]}
            m[f"esc_rate|{c}"] = float(np.nanmean([r["esc_rate"] for r in rs]))
        if "coll_p" in rs[0]:
            m[f"coll|{c}"] = _rate(rs, lambda r: _sig(r["coll_p"]))
            m[f"coll01|{c}"] = _rate(rs, lambda r: _sig(r["coll_p"], 0.01))
            m[f"coll_k|{c}"] = _rate(rs, lambda r: _sig(r["coll_pk"]))
            m[f"coll_m|{c}"] = _rate(rs, lambda r: _sig(r["coll_pm"]))
            m[f"coll_ev|{c}"] = float(np.median([r["coll_events"] for r in rs]))
            jm = [r["j_mean_max"] for r in rs if r.get("j_mean_max") is not None]
            m[f"j_max|{c}"] = float(np.median(jm)) if jm else math.nan
            if "p_ci" in rs[0]:
                ok = [r for r in rs if r.get("p_ci")]
                m[f"p_hat|{c}"] = float(np.median([r["p_hat"] for r in ok])) if ok else math.nan
                pt = {"p=0.005": 0.005, "p=0.01": 0.01, "p=0.02": 0.02, "p=0.05": 0.05}
                pv = next((v for k, v in pt.items() if k in c), 0.005 if fam == "carnot" else 0.0)
                m[f"p_cover|{c}"] = _rate(ok, lambda r: r["p_ci"][0] <= pv <= r["p_ci"][1])
        if "ru_max" in rs[0]:
            m[f"ru_max|{c}"] = float(np.median([r["ru_max"] for r in rs]))
            m[f"ru_max_p95|{c}"] = float(np.quantile([r["ru_max"] for r in rs], 0.95))
            m[f"ru_mean|{c}"] = float(np.median([r["ru_mean"] for r in rs]))
            m[f"mq_max|{c}"] = float(np.median([r["mq_max"] for r in rs if r["mq_max"] == r["mq_max"]]))
            m[f"rev_per_h|{c}"] = float(np.median([r["rev_per_h"] for r in rs]))
    return m


def headline(m):
    """Scores used to rank designs."""
    g = lambda k: m.get(k, math.nan)
    return dict(
        bend_amdahl=g("bend|amdahl"), bend_mild=g("bend|mild"), bend_fpr=g("bend|linear"),
        bin_min=min(g(f"bin|{t}") for t in ("linear", "amdahl", "usl", "carnot")),
        fam4_min=min(g(f"fam4|{t}") for t in ("linear", "amdahl", "usl")),
        fam3_min=min(g(f"fam3|{t}") for t in ("linear", "amdahl", "usl", "carnot")),
        esc20=g("esc|usl/esc20"), esc15=g("esc|usl/esc15"), esc_fpr=g("esc|usl"), esc_hw=g("esc_hw|usl"),
        coll01=g("coll|carnot/p=0.01"), coll02=g("coll|carnot/p=0.02"), coll005=g("coll|carnot"), coll_fpr=g("coll|usl"),
        ru_max_linear=g("ru_max|linear"))


def designs_A():
    ds = []
    for L in (60, 90, 120):
        for r12 in range(2, 9):
            for r1 in (2, 3, 4, 6, 8, 10, 12):
                ds.append(D5((1, 12), (r1, r12), L))
        for r8 in range(2, 11):
            for r1 in (2, 3, 4, 6, 8, 10, 12):
                ds.append(D5((1, 8), (r1, r8), L))
        for r12 in (2, 3, 4):
            for r2 in (2, 3, 4, 6):
                ds.append(D5((2, 12), (r2, r12), L))
            for mid in (4, 6):
                for rm in (1, 2, 3):
                    for r1 in (2, 3, 4, 6, 8):
                        ds.append(D5((1, mid, 12), (r1, rm, r12), L))
    ds = [d for d in ds if fits(d)]
    # keep, per (sizes, L, reps of the larger sizes), only the largest in-budget number of N-low windows and the
    # next one down (the cheap N = 1 windows are almost free; fewer would only waste budget)
    best = {}
    for d in ds:
        k = (d.sizes, d.L, d.reps[1:])
        best.setdefault(k, []).append(d)
    keep = []
    for k, v in best.items():
        v.sort(key=lambda d: d.reps[0], reverse=True)
        keep += v[:2]
    keep = [d for d in keep if d.windows() <= 16]
    return sorted(keep, key=lambda d: (d.sizes, d.L, d.reps))


def dump(path, obj):
    Path(path).write_text(json.dumps(obj, indent=1, default=lambda o: float(o) if isinstance(o, np.floating) else str(o)))


def row(d, m, **extra):
    return dict(design=asdict(d), label=d.label(), cost=d.cost(), cost15=d.cost(1.5 * dsim5.BURN), session_h=d.total_h(),
                sweep_h=d.sweep_h(), windows=d.windows(), wall_h=d.wall_h(), head=headline(m), metrics=m, **extra)


def stage_A(R):
    ds = designs_A()
    print("stage A designs", len(ds), flush=True)
    out = []
    t = time.time()
    for i in range(0, len(ds), 20):
        chunk = ds[i:i + 20]
        res = run(chunk, CELLS_A, R)
        out += [row(d, summarise(res[d])) for d in chunk]
        print(i + len(chunk), round(time.time() - t), flush=True)
        dump(HERE / "v5_stageA.json", out)
    return out


def load_design(r):
    dd = r["design"]
    return D5(tuple(dd["sizes"]), tuple(dd["reps"]), dd["L"], dd["pilot_w"], dd["anchor"])


CELLS_B = SCALE_TRUTHS + ("linear/cv=0.15", "amdahl/cv=0.15", "linear/cv=0.5", "usl/esc15", "usl/esc20", "linear/esc20",
                          "carnot/p=0.01", "carnot/p=0.02", "carnot/p=0.05", "linear/p=0.005", "linear/p=0.01",
                          "carnot/census", "carnot/census/man=0.5", "linear/slowrev", "usl/slowrev")


SHORTLIST = [  # from stage A (top of the ranking, both window lengths, one three-size and one N_max = 8 design)
    D5((1, 12), (8, 3), 120), D5((1, 12), (6, 3), 120), D5((1, 12), (12, 2), 120), D5((1, 12), (10, 4), 90),
    D5((1, 12), (8, 4), 90), D5((1, 12), (12, 3), 90), D5((1, 12), (10, 3), 90), D5((1, 12), (10, 6), 60),
    D5((1, 4, 12), (8, 3, 3), 90), D5((1, 8), (12, 4), 120),
    # degrade candidates that fit at 1.5x the assumed burn ($3.15 per session-hour)
    D5((1, 12), (4, 2), 120), D5((1, 12), (6, 2), 120), D5((1, 12), (10, 2), 90), D5((1, 12), (12, 2), 90),
    D5((1, 12), (8, 3), 60), D5((1, 12), (12, 3), 60)]


def stage_B(R, labels=None):
    ds = SHORTLIST
    out = []
    t = time.time()
    res = run(ds, CELLS_B, R, seed0=50_000)
    for d in ds:
        out.append(row(d, summarise(res[d]), kind="shortlist"))
    print("B main", round(time.time() - t), flush=True)
    # window CV growing as windows shorten (variance per unit time fixed): CV 0.3 x sqrt(120 / L)
    res = run(ds, SCALE_TRUTHS, R, seed0=60_000, cv_len=True)
    for r_, d in zip(out, ds):
        r_["cv_len"] = headline(summarise(res[d]))
    print("B cv_len", round(time.time() - t), flush=True)
    dump(HERE / "v5_stageB.json", out)
    return out


def stage_pilot(R, label):
    """The same sweep with a v4-style T2 pilot (2 or 4 x 60 min at N = 1) and pilot-anchored rival levels."""
    A = {r["label"]: r for r in json.loads((HERE / "v5_stageA.json").read_text())}
    base = load_design(A[label])
    ds = [base] + [D5(base.sizes, base.reps, base.L, pw, "pilot") for pw in (2, 4, 8)]
    res = run(ds, SCALE_TRUTHS, R, seed0=70_000, parts=("scale",))
    out = [row(d, summarise(res[d]), kind="pilot-variant", fits=fits(d)) for d in ds]
    dump(HERE / "v5_pilot.json", out)
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--stage", required=True, choices=["A", "B", "P"])
    ap.add_argument("--reps", type=int, default=None)
    ap.add_argument("--labels", nargs="*", default=None)
    a = ap.parse_args()
    t = time.time()
    if a.stage == "A":
        rows = stage_A(a.reps or 200)
    elif a.stage == "B":
        rows = stage_B(a.reps or 1000, a.labels)
    else:
        rows = stage_pilot(a.reps or 1000, a.labels[0])
    for r in rows[:40]:
        print(r["label"], round(r["cost"]), {k: (round(v, 2) if isinstance(v, float) else v) for k, v in r["head"].items()})
    print("time", round(time.time() - t))
