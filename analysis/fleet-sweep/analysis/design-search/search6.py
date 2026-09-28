#!/usr/bin/env python3
"""PLAN-v6 design search (simulation only; no network, no API, no cloud sessions).

    PY=/Users/tom/git/carnot/analysis/aidev/.venv/bin/python
    $PY search6.py --stage A     # window banks for 6 truths x L in {30,45,60,75} x (N, K); every design within budget (a)
                                 # scored for SCALE and CAP, R = 200 per truth                     -> v6_stageA.json
    $PY search6.py --stage B     # shortlist: SCALE / CAP / COLL / ESC-N under more truths, R = 1000 -> v6_stageB.json

Studies are assembled from per-cell window banks (dsim6.make_banks), so every design is scored on the same simulated
windows (common random numbers). See DESIGN-SEARCH-v6.md.
"""
from __future__ import annotations

import argparse
import itertools
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
import dsim6  # noqa: E402
from dsim6 import D6  # noqa: E402

SCRATCH = Path(os.environ.get("V6_SCRATCH", "/tmp/v6-banks"))
TRUTHS_A = ("linear", "mild", "amdahl", "usl", "measured", "linear/fastrev")
LS = (30, 45, 60, 75)
SWEEP_ROOM_H = dsim6.ROOM / dsim6.BURN - dsim6.T1B_H     # 87.3 sweep session-hours at $2.10

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
            b = _BANKS[(tname, n, k, design.L)]
            wins += rng.sample(b, w)
    r = dsim6.score_study(wins, parts)
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


def designs_A():
    out = []
    for L in LS:
        for khi in (2, 3):
            for nhi in range(1, 6):
                for nk1 in range(0, 6):
                    for n1 in range(4, 25, 2):
                        if nk1:
                            cells = ((1, 1, n1 // 2), (1, 3, n1 - n1 // 2), (12, khi, nhi), (12, 1, nk1))
                        else:
                            cells = ((1, 3, n1), (12, khi, nhi))   # at N = 1 the reviewer count is immaterial
                        d = D6(cells, L)
                        if d.sweep_h() <= SWEEP_ROOM_H + 1e-9:
                            out.append(d)
    # N_high = 8 alternatives (K = 3 and K = 1), 45 and 60 min
    for L in (45, 60):
        for nhi in range(2, 6):
            for nk1 in range(0, 5):
                for n1 in range(6, 25, 2):
                    cells = ((1, 1, n1 // 2), (1, 3, n1 - n1 // 2), (8, 3, nhi), (8, 1, nk1)) if nk1 else ((1, 3, n1), (8, 3, nhi))
                    d = D6(cells, L)
                    if d.sweep_h() <= SWEEP_ROOM_H + 1e-9:
                        out.append(d)
    # keep, per (L, sizes, K_hi, N_hi windows, K = 1 windows), the two largest N = 1 counts that fit
    best = {}
    for d in out:
        key = (d.L, tuple((n, k) for n, k, w in d.cells if n != 1), tuple(w for n, k, w in d.cells if n != 1))
        best.setdefault(key, []).append(d)
    keep = []
    for key, ds in best.items():
        ds.sort(key=lambda d: -d.windows())
        keep += ds[:2]
        small = [d for d in ds if sum(w for n, k, w in d.cells if n == 1) == 12]
        keep += [d for d in small if d not in keep]
    return keep


def summarise_A(res):
    rows = []
    for d, by in res.items():
        m = dict(design=d.label(), L=d.L, cells=[list(c) for c in d.cells], sweep_h=d.sweep_h(), cost=d.cost(),
                 fits_up_to=d.fits_up_to(), wall_h=d.wall_h(), windows=d.windows())
        m["scale_fpr"] = rate(by["linear"], "bend_p")
        for t in ("amdahl", "usl", "mild"):
            m[f"scale_{t}"] = rate(by[t], "bend_p")
        m["scale_fpr_measured"] = rate(by["measured"], "bend_p")
        if any("cap_p" in r for r in by["linear"]):
            m["cap_fpr"] = rate(by["linear/fastrev"], "cap_p")
            for t in ("linear", "measured", "amdahl", "mild", "usl"):
                m[f"cap_{t}"] = rate(by[t], "cap_p")
            m["cap_ratio_linear"] = mean(by["linear"], "cap_ratio")
        hi = max(n for n, k, w in d.cells)
        khi = max(k for n, k, w in d.cells if n == hi)
        m["util_hi_linear"] = mean(by["linear"], f"util_{hi}x{khi}")
        m["utilmax_hi_linear"] = mean(by["linear"], f"utilmax_{hi}x{khi}")
        m["supflag_hi_linear"] = mean(by["linear"], f"supflag_{hi}x{khi}")
        m["flag_rev_linear"] = float(np.mean([bool(r.get("flag_rev")) for r in by["linear"]]))
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
                cells += [(tn, 1, 1, L), (tn, 1, 3, L), (tn, 12, 1, L), (tn, 12, 2, L), (tn, 12, 3, L)]
            for L in (45, 60):
                cells += [(tn, 8, 1, L), (tn, 8, 3, L)]
        banks = dsim6.make_banks(cells, {1: 1200, 8: 400, 12: 500}, W=5, lite=True)
        bank_path.write_bytes(pickle.dumps(banks))
        print(f"banks: {len(banks)} cells in {time.time() - t:.0f} s", flush=True)
    ds = designs_A()
    print(f"{len(ds)} designs", flush=True)
    res = run_studies(ds, TRUTHS_A, R, bank_path, ("scale", "cap", "util"))
    rows = summarise_A(res)
    (HERE / "v6_stageA.json").write_text(json.dumps(dict(R=R, seconds=time.time() - t, rows=rows), indent=0) + "\n")
    print(f"stage A done in {time.time() - t:.0f} s", flush=True)


# ------------------------------------------------------------------------------------------------ stage B
TRUTHS_B = ("linear", "mild", "amdahl", "usl", "carnot", "measured", "linear/fastrev", "linear/cv=0.15", "linear/cv=0.5",
            "amdahl/cv=0.5", "linear/lam70", "amdahl/lam70", "linear/slowrev", "linear/cont=0.2", "usl/p=0.0035",
            "usl/p=0.015", "measured/esc2x", "measured/p=0.0035", "measured/p=0.015")


def shortlist_B():
    """Around the stage-A pick (45-min windows): the N = 12 split between K = 3 and K = 1, the N = 1 count, the degrade
    candidates, and the one-factor designs (K = 3 only: v5-like; K = 1 only: review-bound SCALE)."""
    L = []
    for (n3, n1k, n1) in ((3, 4, 12), (3, 3, 12), (4, 3, 12), (4, 4, 12), (3, 4, 6), (2, 3, 10), (2, 2, 10), (3, 2, 12)):
        L.append(D6(((1, 1, n1 // 2), (1, 3, n1 - n1 // 2), (12, 3, n3), (12, 1, n1k)), 45))
    L.append(D6(((1, 3, 12), (12, 3, 3)), 45))
    L.append(D6(((1, 1, 12), (12, 1, 3)), 45))
    return L


def stage_B(R):
    SCRATCH.mkdir(parents=True, exist_ok=True)
    bank_path = SCRATCH / "banks_B.pkl"
    t = time.time()
    if not bank_path.exists():
        cells = [(tn, n, k, 45) for tn in TRUTHS_B for n, k in ((1, 1), (1, 3), (12, 1), (12, 3))]
        banks = dsim6.make_banks(cells, {1: 1500, 12: 600}, W=5, lite=False)
        bank_path.write_bytes(pickle.dumps(banks))
        print(f"banks: {len(banks)} cells in {time.time() - t:.0f} s", flush=True)
    ds = shortlist_B()
    res = run_studies(ds, TRUTHS_B, R, bank_path, ("scale", "cap", "coll", "esc", "family", "util", "scale_sens"))
    out = []
    for d, by in res.items():
        m = dict(design=d.label(), L=d.L, cells=[list(c) for c in d.cells], sweep_h=d.sweep_h(), cost=d.cost(),
                 cost15=d.cost(1.5 * dsim6.BURN), fits_up_to=d.fits_up_to(), wall_h=d.wall_h())
        for tn, rs in by.items():
            m[f"bend|{tn}"] = rate(rs, "bend_p")
            m[f"bend_estcv|{tn}"] = rate(rs, "bend_p_estcv")
            m[f"bend_welch|{tn}"] = rate(rs, "bend_p_welch")
            m[f"cap|{tn}"] = rate(rs, "cap_p")
            m[f"coll|{tn}"] = rate(rs, "coll_p")
            m[f"esc|{tn}"] = rate(rs, "esc_p")
            m[f"esc_hw|{tn}"] = mean(rs, "esc_hw")
            m[f"esc_rate|{tn}"] = mean(rs, "esc_rate")
            m[f"pa|{tn}"] = mean(rs, "pa_ratio")
            m[f"capratio|{tn}"] = mean(rs, "cap_ratio")
            m[f"k1|{tn}"] = rate([dict(p=r.get("k1_p2")) for r in rs], "p")
            m[f"binary_ok|{tn}"] = float(np.mean([r.get("binary") == ("linear" if tn.split("/")[0] in ("linear", "measured") else "bending") for r in rs]))
            m[f"best|{tn}"] = {b: float(np.mean([r.get("best") == b for r in rs])) for b in ("linear", "amdahl", "usl", "carnot")}
            for key in rs[0]:
                if key.split("_")[0] in ("util", "utilmax", "qne", "fin", "lam", "rev", "busyh", "exh", "supflag"):
                    m[f"{key}|{tn}"] = mean(rs, key)
        out.append(m)
    (HERE / "v6_stageB.json").write_text(json.dumps(dict(R=R, seconds=time.time() - t, rows=out), indent=0) + "\n")
    print(f"stage B done in {time.time() - t:.0f} s", flush=True)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--stage", choices=["A", "B"], required=True)
    ap.add_argument("--reps", type=int, default=None)
    a = ap.parse_args()
    if a.stage == "A":
        stage_A(a.reps or 200)
    else:
        stage_B(a.reps or 1000)


if __name__ == "__main__":
    main()
