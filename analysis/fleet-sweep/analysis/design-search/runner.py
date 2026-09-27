"""Parallel evaluation of designs: common random numbers across designs (same seeds per truth)."""
from __future__ import annotations

import math
import os
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
from dataclasses import asdict

import numpy as np

import dsim

TRUTH_IDX = {t: i for i, t in enumerate(["carnot", "usl", "amdahl", "linear", "skim", "slow", "escape",
                                         "coll01", "coll05", "null", "skimN", "slowN"])}
FAM4 = ("carnot", "usl", "amdahl", "linear")
_POOL = None


def pool():
    global _POOL
    if _POOL is None:
        _POOL = ProcessPoolExecutor(max_workers=max(1, (os.cpu_count() or 2) - 1))
    return _POOL


def cfgs_for(design, truths, R, seed0=0, **kw):
    dd = asdict(design)
    return [dict(design=dd, truth=t, seed=1 + seed0 + 1_000_000 * TRUTH_IDX[t] + i, **kw)
            for t in truths for i in range(R)]


def run(designs, truths, R, seed0=0, extra=False, **kw):
    """Returns {design: {truth: [result, ...]}}."""
    allc = []
    for d in designs:
        allc += [(d, c) for c in cfgs_for(d, truths, R, seed0, extra=extra, **kw)]
    res = list(pool().map(dsim.study, [c for _, c in allc], chunksize=16))
    out = {}
    for (d, c), r in zip(allc, res):
        out.setdefault(d, {}).setdefault(c["truth"], []).append(r)
    return out


def _share(rs, f):
    return float(np.mean([bool(f(r)) for r in rs])) if rs else math.nan


def summarise(by_truth):
    m = {}
    for t, rs in by_truth.items():
        n = len(rs)
        scored = [r for r in rs if r.get("status") == "SCORED"]
        m[f"n_{t}"] = n
        m[f"scored_{t}"] = len(scored) / n
        if t in FAM4:
            m[f"rec_{t}"] = _share(rs, lambda r: r.get("correct"))
            if t == "carnot":
                m[f"bin_{t}"] = _share(rs, lambda r: r.get("best") == "carnot")
            else:
                m[f"bin_{t}"] = _share(rs, lambda r: r.get("status") == "SCORED" and "carnot" not in (r.get("best") or "carnot"))
            m[f"tie_{t}"] = _share(rs, lambda r: "|" in (r.get("best") or ""))
            m[f"picks_{t}"] = dict(Counter(r.get("best", r.get("status")) for r in rs))
        m[f"gateRUN_{t}"] = _share(rs, lambda r: r.get("gate") == "RUN")
        m[f"pilotV_{t}"] = list(np.quantile([r["pilot_V_ratio"] for r in rs if "pilot_V_ratio" in r], [0.1, 0.5, 0.9])) \
            if any("pilot_V_ratio" in r for r in rs) else None
        for code in ("O1n", "S4n", "S1r", "S2r"):
            m[f"{code}_{t}"] = _share(rs, lambda r, c=code: r.get(c) == "FAIL")
        m[f"Vratio_{t}"] = _share(rs, lambda r: r.get("Vratio_sig"))
        m[f"Vdur_{t}"] = _share(rs, lambda r: (r.get("Vdur_p") is not None and r["Vdur_p"] == r["Vdur_p"]
                                               and r["Vdur_p"] < 0.05))
        m[f"Vcal_{t}"] = _share(rs, lambda r: (r.get("Vcal_p") is not None and r["Vcal_p"] == r["Vcal_p"]
                                               and r["Vcal_p"] < 0.05))
        vh = [r["V_hi_lo"] for r in scored if r.get("V_hi_lo") is not None]
        m[f"VhiLo_{t}"] = float(np.median(vh)) if vh else math.nan
        nr = [sum(r["n_rev"]) for r in scored if "n_rev" in r]
        m[f"nrev_{t}"] = float(np.median(nr)) if nr else math.nan
        costs = [r["cost"] for r in scored if "cost" in r]
        m[f"cost_{t}"] = float(np.mean(costs)) if costs else math.nan
        if any("esc_events" in r for r in rs):
            for a in (0.05, 0.01):
                m[f"esc{a}_{t}"] = _share(rs, lambda r, a=a: r.get("esc_p") is not None and r["esc_p"] < a)
                m[f"coll{a}_{t}"] = _share(rs, lambda r, a=a: r.get("coll_p") is not None and r["coll_p"] < a)
            m[f"esc_ge8_{t}"] = _share(rs, lambda r: r.get("esc_events", 0) >= 8)
    if all(f"rec_{t}" in m for t in FAM4):
        m["rec_min"] = min(m[f"rec_{t}"] for t in FAM4)
        m["bin_min"] = min(m[f"bin_{t}"] for t in FAM4)
    if "O1n_skim" in m and "O1n_slow" in m:
        for code in ("O1n", "S4n", "Vratio", "Vdur", "Vcal"):
            m[f"{code}_pow"] = min(m[f"{code}_skim"], m[f"{code}_slow"])
    if "O1n_skimN" in m and "O1n_slowN" in m:
        for code in ("O1n", "S4n", "Vratio", "Vdur", "Vcal"):
            m[f"{code}_powN"] = min(m[f"{code}_skimN"], m[f"{code}_slowN"])
    return m
