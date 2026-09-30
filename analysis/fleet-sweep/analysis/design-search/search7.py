#!/usr/bin/env python3
"""PLAN-v7 design search under a budget counted in cloud tasks (simulation only; no network, API or cloud sessions).

    PY=/Users/tom/git/carnot/analysis/aidev/.venv/bin/python
    $PY search7.py --stage A     # window banks for 6 truths x L in {15,20,25,30} x (N, K); every design with
                                 # T1b + expected tasks <= 560 scored for SCALE and CAP, R = 150 per truth -> v7_stageA.json
    $PY search7.py --stage B     # shortlist per budget (300 / 400 / 500 tasks): all codings, more truths, R = 600
                                 #                                                                 -> v7_stageB.json

Studies are assembled from per-cell window banks (dsim7.make_banks), so every design is scored on the same simulated
windows (common random numbers). A design's cost is T1b (65 tasks, oc_v7.py --part t1b) plus the mean hand-outs of
its windows under "measured" workers (linear, T1's collision rate: the most expensive plausible truth). See
DESIGN-SEARCH-v7.md.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import pickle
import random
import sys
import time
import zlib
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))
import dsim7  # noqa: E402
from dsim7 import D7  # noqa: E402

SCRATCH = Path(os.environ.get("V7_SCRATCH", "/tmp/v7-banks"))
TRUTHS_A = ("linear", "mild", "amdahl", "usl", "measured", "linear/fastrev")
LS = (15, 20, 25, 30)
T1B_TASKS = 65
MAX_TASKS = 560
HI = {12: ((1, 3, 4, 5), LS), 8: ((1, 3, 4), (20, 30)), 6: ((1, 3), (20, 30))}
KHI = {12: (4, 5), 8: (3, 4), 6: (3,)}

_BANKS = None


def _init(path):
    global _BANKS
    with open(path, "rb") as f:
        _BANKS = pickle.load(f)


def _study(args):
    design, tname, rep, parts = args
    rng = random.Random(zlib.crc32(f"{design.label()}|{tname}|{rep}".encode()))
    wins = []
    for n, k, w in design.cells:
        if w:
            wins += rng.sample(_BANKS[(tname, n, min(k, 3) if n == 1 else k, design.L)], w)
    r = dsim7.score_study(wins, parts)
    r["truth"], r["rep"] = tname, rep
    return r


def run_studies(designs, truths, R, bank_path, parts):
    jobs = [(d, t, i, parts) for d in designs for t in truths for i in range(R)]
    with ProcessPoolExecutor(max_workers=max(1, (os.cpu_count() or 2) - 1), initializer=_init, initargs=(str(bank_path),)) as ex:
        res = list(ex.map(_study, jobs, chunksize=64))
    out = {}
    for (d, t, _, _), r in zip(jobs, res):
        out.setdefault(d, {}).setdefault(t, []).append(r)
    return out


def rate(rs, key, a=0.05):
    v = [r.get(key) for r in rs]
    v = [x for x in v if x is not None]
    return float(np.mean([(x == x and x < a) for x in v])) if v else math.nan


def mean(rs, key):
    v = [r.get(key) for r in rs if r.get(key) is not None and r.get(key) == r.get(key)]
    return float(np.mean(v)) if v else math.nan


def cell_tasks(banks, truth="measured"):
    return {(n, k, L): float(np.mean([w["S"]["tasks"] for w in b])) for (t, n, k, L), b in banks.items() if t == truth}


def expected_tasks(d, ct):
    return T1B_TASKS + sum(w * ct[(n, min(k, 3) if n == 1 else k, d.L)] for n, k, w in d.cells if w)


def designs_A(ct):
    out = []
    for nhi, (_, Ls) in HI.items():
        for L in Ls:
            for khi in KHI[nhi]:
                for n_hi in range(1, 6):
                    for n_k1 in range(0, 3):
                        for n1 in (4, 6, 8, 10, 12, 16, 20):
                            cells = ((1, 1, n1 // 2), (1, khi, n1 - n1 // 2), (nhi, khi, n_hi), (nhi, 1, n_k1))
                            d = D7(tuple(c for c in cells if c[2]), L)
                            if expected_tasks(d, ct) <= MAX_TASKS:
                                out.append(d)
    return out


def summarise(res, ct, extra=False):
    rows = []
    for d, by in res.items():
        m = dict(design=d.label(), L=d.L, cells=[list(c) for c in d.cells], windows=d.windows(), slot_hours=d.slot_hours(),
                 tasks=expected_tasks(d, ct), tasks_study_mean=mean(by["measured"], "tasks") + T1B_TASKS,
                 tasks_study_p90=float(np.percentile([r["tasks"] for r in by["measured"]], 90)) + T1B_TASKS,
                 reviews=mean(by["measured"], "reviews_total"))
        nhi = max(n for n, k, w in d.cells)
        khi = max(k for n, k, w in d.cells if n == nhi)
        m["N_hi"], m["K_hi"] = nhi, khi
        m["n_hi"] = sum(w for n, k, w in d.cells if n == nhi and k == khi)
        m["n_k1"] = sum(w for n, k, w in d.cells if n == nhi and k == 1)
        m["n1"] = sum(w for n, k, w in d.cells if n == 1)
        for tn, rs in by.items():
            m[f"scale|{tn}"] = rate(rs, "bend_p")
            m[f"cap|{tn}"] = rate(rs, "cap_p")
            m[f"pa|{tn}"] = mean(rs, "pa_ratio")
            m[f"capratio|{tn}"] = mean(rs, "cap_ratio")
            m[f"flagrev|{tn}"] = float(np.mean([bool(r.get("flag_rev")) for r in rs]))
            m[f"util_hi|{tn}"] = mean(rs, f"util_{nhi}x{khi}")
            if extra:
                for k in ("coll", "esc"):
                    m[f"{k}|{tn}"] = rate(rs, f"{k}_p")
                m[f"scale_welch|{tn}"] = rate(rs, "bend_p_welch")
                m[f"scale_estcv|{tn}"] = rate(rs, "bend_p_estcv")
                m[f"k1|{tn}"] = rate([dict(p=r.get("k1_p2")) for r in rs], "p")
                m[f"binary_ok|{tn}"] = float(np.mean([r.get("binary") == ("linear" if tn.split("/")[0] in ("linear", "measured") else "bending") for r in rs]))
                m[f"esc_hw|{tn}"] = mean(rs, "esc_hw")
                m[f"coll_events|{tn}"] = mean(rs, "coll_events")
        rows.append(m)
    return rows


def stage_A(R):
    SCRATCH.mkdir(parents=True, exist_ok=True)
    bank_path = SCRATCH / "banks_A.pkl"
    t = time.time()
    if not bank_path.exists():
        cells = []
        for tn in TRUTHS_A:
            for L in LS:
                cells += [(tn, 1, 1, L), (tn, 1, 3, L)]
            for nhi, (ks, Ls) in HI.items():
                cells += [(tn, nhi, k, L) for k in ks for L in Ls]
        banks = dsim7.make_banks(cells, {1: 2000, 6: 500, 8: 500, 12: 500}, W=3, lite=True)
        bank_path.write_bytes(pickle.dumps(banks))
        print(f"banks: {len(banks)} cells in {time.time() - t:.0f} s", flush=True)
    banks = pickle.loads(bank_path.read_bytes())
    ct = cell_tasks(banks)
    cell_info = {f"{t}|{n}|{k}|{L}": dict(tasks=float(np.mean([w['S']['tasks'] for w in b])),
                                          finished=float(np.mean([w['S']['finished'] for w in b])),
                                          util=float(np.mean([w['S']['reviewer_util'] for w in b])),
                                          reviews=float(np.mean([w['S']['reviews'] for w in b])),
                                          lam=float(np.nanmean([w['S']['lam'] for w in b])))
                 for (t, n, k, L), b in banks.items()}
    ds = designs_A(ct)
    print(f"{len(ds)} designs", flush=True)
    del banks
    res = run_studies(ds, TRUTHS_A, R, bank_path, ("scale", "cap", "util"))
    rows = summarise(res, ct)
    (HERE / "v7_stageA.json").write_text(json.dumps(dict(R=R, seconds=time.time() - t, t1b_tasks=T1B_TASKS, cells=cell_info,
                                                         rows=rows), indent=0) + "\n")
    print(f"stage A done in {time.time() - t:.0f} s", flush=True)


# ------------------------------------------------------------------------------------------------ stage B
TRUTHS_B = ("linear", "mild", "amdahl", "usl", "carnot", "measured", "linear/fastrev", "linear/cv=0.15", "linear/cv=0.5",
            "amdahl/cv=0.5", "linear/cont=0.1", "usl/p=0.0035", "usl/p=0.015", "measured/esc2x", "measured/p=0.0035")


def shortlist_B():
    """Around the stage-A picks per budget (DESIGN-SEARCH-v7.md section 3): N_hi = 12 with K_hi = 5 (or 4), 15- or
    20-min windows, 0-2 CAP windows (N_hi, K = 1), the N = 1 count. (L, K_hi, N_hi windows, K = 1 windows, N = 1 windows)."""
    specs = (
        # about 300 tasks
        (15, 5, 2, 0, 8), (15, 5, 2, 0, 10), (15, 5, 2, 0, 12), (15, 4, 2, 0, 10), (15, 5, 1, 1, 8),
        (20, 5, 1, 0, 12), (20, 5, 2, 0, 4), (20, 5, 2, 0, 6),
        # about 400 tasks
        (15, 5, 3, 0, 8), (15, 5, 3, 0, 12), (15, 4, 3, 0, 12), (15, 5, 2, 1, 10), (15, 5, 2, 1, 12),
        (20, 5, 2, 0, 12), (20, 5, 2, 0, 16), (20, 4, 2, 0, 12), (20, 5, 1, 1, 12), (20, 5, 2, 1, 4),
        # about 500 tasks
        (15, 5, 4, 0, 12), (15, 5, 3, 1, 12), (15, 5, 3, 1, 16), (15, 5, 2, 2, 12),
        (20, 5, 3, 0, 12), (20, 5, 2, 1, 8), (20, 5, 2, 1, 12), (20, 5, 3, 1, 4), (20, 4, 2, 1, 10))
    return [D7(tuple(c for c in ((1, 1, n1 // 2), (1, khi, n1 - n1 // 2), (12, khi, n_hi), (12, 1, n_k1)) if c[2]), L_)
            for (L_, khi, n_hi, n_k1, n1) in specs]


def stage_B(R):
    SCRATCH.mkdir(parents=True, exist_ok=True)
    bank_path = SCRATCH / "banks_B.pkl"
    t = time.time()
    ds = shortlist_B()
    if not bank_path.exists():
        need = sorted({(n, min(k, 3) if n == 1 else k, d.L) for d in ds for n, k, w in d.cells})
        cells = [(tn, n, k, L) for tn in TRUTHS_B for (n, k, L) in need]
        banks = dsim7.make_banks(cells, {1: 1500, 12: 500}, W=3, lite=False)
        bank_path.write_bytes(pickle.dumps(banks))
        print(f"banks: {len(banks)} cells in {time.time() - t:.0f} s", flush=True)
    banks = pickle.loads(bank_path.read_bytes())
    ct = cell_tasks(banks)
    del banks
    res = run_studies(ds, TRUTHS_B, R, bank_path, ("scale", "cap", "coll", "esc", "family", "util", "scale_sens"))
    rows = summarise(res, ct, extra=True)
    (HERE / "v7_stageB.json").write_text(json.dumps(dict(R=R, seconds=time.time() - t, t1b_tasks=T1B_TASKS, rows=rows), indent=0) + "\n")
    print(f"stage B done in {time.time() - t:.0f} s", flush=True)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--stage", choices=["A", "B"], required=True)
    ap.add_argument("--reps", type=int, default=None)
    a = ap.parse_args()
    if a.stage == "A":
        stage_A(a.reps or 150)
    else:
        stage_B(a.reps or 600)


if __name__ == "__main__":
    main()
