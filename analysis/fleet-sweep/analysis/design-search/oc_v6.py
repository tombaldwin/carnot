#!/usr/bin/env python3
"""Operating characteristics of the PLAN-v6 design (PLAN-v6 section 5) and of the revised abort rule 1 (T1b).

    PY=/Users/tom/git/carnot/analysis/aidev/.venv/bin/python
    $PY oc_v6.py --part t1b [--reps 1000]     # rule 1 decisions on simulated T1b logs, one-slot phase 30-120 min
    $PY oc_v6.py --part oc  [--reps 1000]     # the recommended and degrade designs, every window freshly simulated

Simulation only: synth (V6_TRUTH) -> derive -> v6 codings, through dsim6. Writes oc_v6_t1b.json / oc_v6.json;
OPERATING-CHARACTERISTICS-v6.md and v6.V6_OC are written from them by hand.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))
import dsim6  # noqa: E402
from dsim6 import D6, build_truth6  # noqa: E402
from synth import T0, make_task_pool, simulate  # noqa: E402
import v6  # noqa: E402

T1B_TRUTHS = ("measured", "measured/thr=1.25", "measured/thr=1.5", "measured/thr=2", "measured/thrcode=1.5",
              "usl", "amdahl", "measured/cv=0.5")
T1B_L1 = (30, 60, 90, 120)


def t1b_one(args):
    tname, L1, seed = args
    truth = build_truth6(tname, 3)
    sched = [(0.0, L1 + 30.0)] + [(float(L1), L1 + 30.0)] * 11
    run, ev = simulate(truth, 12, seed=seed, window_min=L1 + 30, warmup_min=0, grace_min=10, schedule=sched,
                       task_pool=make_task_pool(truth, seed * 31 + 5), run_id=f"t1b{seed}", kind="trial", t0=T0)
    r = v6.throttle_v6(run, ev, split_min=L1)
    return dict(dec=r["decision"], flag=r["coding_flag"], up=r["startup"]["ratio"], up_ci=r["startup"]["ci"],
                code=r["coding"]["ratio"], n_a=r["startup"]["n_a"], n_b=r["startup"]["n_b"])


def part_t1b(reps):
    jobs = [(t, L1, 500_000 + 1000 * i + L1) for t in T1B_TRUTHS for L1 in T1B_L1 for i in range(reps)]
    with ProcessPoolExecutor(max_workers=max(1, (os.cpu_count() or 2) - 1)) as ex:
        res = list(ex.map(t1b_one, jobs, chunksize=16))
    out = {}
    for (t, L1, _), r in zip(jobs, res):
        out.setdefault(t, {}).setdefault(str(L1), []).append(r)
    summ = {}
    for t, byL in out.items():
        for L1, rs in byL.items():
            hw = [0.5 * (math.log(r["up_ci"][1]) - math.log(r["up_ci"][0])) for r in rs if r["up_ci"][0] == r["up_ci"][0]]
            summ[f"{t}|{L1}"] = dict(stop=float(np.mean([r["dec"] == "STOP" for r in rs])),
                                    clear=float(np.mean([r["dec"] == "CLEAR" for r in rs])),
                                    inconclusive=float(np.mean([r["dec"] == "INCONCLUSIVE" for r in rs])),
                                    coding_flag=float(np.mean([r["flag"] for r in rs])),
                                    startup_ratio_median=float(np.median([r["up"] for r in rs])),
                                    coding_ratio_median=float(np.nanmedian([r["code"] for r in rs])),
                                    halfwidth_log_median=float(np.median(hw)) if hw else math.nan,
                                    ci_factor_median=float(math.exp(np.median(hw))) if hw else math.nan,
                                    n_a_median=float(np.median([r["n_a"] for r in rs])),
                                    n_b_median=float(np.median([r["n_b"] for r in rs])))
    return dict(reps=reps, summary=summ)


# ------------------------------------------------------------------------------------------------ design OCs
OC_TRUTHS = ("linear", "mild", "amdahl", "usl", "carnot", "measured", "linear/fastrev",
             "linear/cv=0.15", "linear/cv=0.5", "amdahl/cv=0.15", "amdahl/cv=0.5", "linear/lam70", "amdahl/lam70",
             "linear/slowrev", "linear/cont=0.2", "amdahl/cont=0.2", "measured/esc2x",
             "usl/p=0.0035", "usl/p=0.0075", "usl/p=0.015", "measured/p=0.0035", "measured/p=0.015",
             "linear/thrcode=1.3")


def design_of(dd):
    c = v6.cells_of(dd["cells"])
    return D6(tuple((n, k, w) for (n, k), w in sorted(c.items())), int(dd.get("window_min", v6.DESIGN_V6["window_min"])),
              int(dd.get("warmup_min", v6.DESIGN_V6["warmup_min"])))


def part_oc(reps, designs=None):
    full = design_of(v6.DESIGN_V6)
    deg = design_of(dict(v6.DESIGN_V6["degrade"], window_min=v6.DESIGN_V6["window_min"], warmup_min=v6.DESIGN_V6["warmup_min"]))
    ds = designs or [full, deg]
    jobs = []
    for d in ds:
        for t in OC_TRUTHS:
            for i in range(reps):
                seed = 1 + 900_000 + 10_000 * (sum(ord(c) * (j + 3) for j, c in enumerate(t)) % 997) + i   # same seeds per truth in every design
                jobs.append((d, t, dict(design=d, truth=t, seed=seed,
                                        parts=("scale", "cap", "coll", "esc", "family", "util", "scale_sens", "p_hat"))))
    with ProcessPoolExecutor(max_workers=max(1, (os.cpu_count() or 2) - 1)) as ex:
        res = list(ex.map(dsim6.study_fresh, [j[2] for j in jobs], chunksize=4))
    out = {}
    for (d, t, _), r in zip(jobs, res):
        out.setdefault(d.label(), {}).setdefault(t, []).append(r)
    return out


def summarise(by):
    rate = lambda rs, k: float(np.mean([(r.get(k) is not None and r.get(k) == r.get(k) and r.get(k) < 0.05) for r in rs]))
    mean = lambda rs, k: float(np.nanmean([r.get(k) for r in rs if r.get(k) is not None])) if any(r.get(k) is not None for r in rs) else math.nan
    pct = lambda rs, k, q: float(np.nanpercentile([r.get(k) for r in rs if r.get(k) is not None], q)) if any(r.get(k) is not None for r in rs) else math.nan
    m = {}
    for t, rs in by.items():
        m[t] = dict(n=len(rs), scale=rate(rs, "bend_p"), scale_estcv=rate(rs, "bend_p_estcv"), scale_welch=rate(rs, "bend_p_welch"),
                    cap=rate(rs, "cap_p"), coll=rate(rs, "coll_p"), esc=rate(rs, "esc_p"),
                    k1_two_sided=float(np.mean([(r.get("k1_p2") is not None and r["k1_p2"] < 0.05) for r in rs])),
                    pa_ratio=mean(rs, "pa_ratio"), cap_ratio=mean(rs, "cap_ratio"), esc_rate=mean(rs, "esc_rate"),
                    esc_hw=mean(rs, "esc_hw"), esc_n=mean(rs, "esc_n"), coll_events=mean(rs, "coll_events"),
                    flag_rev=float(np.mean([bool(r.get("flag_rev")) for r in rs])),
                    flag_supply=float(np.mean([bool(r.get("flag_supply")) for r in rs])),
                    best={b: float(np.mean([r.get("best") == b for r in rs])) for b in ("linear", "amdahl", "usl", "carnot")},
                    binary={b: float(np.mean([r.get("binary") == b for r in rs])) for b in ("linear", "bending")},
                    p_ci_cover=None)
        tp = next((float(x[2:]) for x in t.split("/")[1:] if x.startswith("p=")), {"carnot": 0.0075, "measured": 0.0075}.get(t.split("/")[0]))
        if tp:
            cov = [r["p_ci"][0] <= tp <= r["p_ci"][1] for r in rs if r.get("p_ci")]
            m[t]["p_ci_cover"] = float(np.mean(cov)) if cov else None
            m[t]["p_hat_median"] = float(np.nanmedian([r["p_hat"] for r in rs if r.get("p_hat") is not None])) if any(r.get("p_hat") is not None for r in rs) else None
        for key in rs[0]:
            if key.split("_")[0] in ("util", "utilmax", "qne", "fin", "lam", "rev", "busyh", "exh", "supflag"):
                m[t][key] = mean(rs, key)
                if key.startswith("utilmax") or key.startswith("fin"):
                    m[t][key + "_p95"] = pct(rs, key, 95)
                    m[t][key + "_p5"] = pct(rs, key, 5)
    return m


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--part", choices=["t1b", "oc"], required=True)
    ap.add_argument("--reps", type=int, default=1000)
    a = ap.parse_args()
    t = time.time()
    if a.part == "t1b":
        out = part_t1b(a.reps)
        out["seconds"] = time.time() - t
        (HERE / "oc_v6_t1b.json").write_text(json.dumps(out, indent=1) + "\n")
        for k, v in out["summary"].items():
            print(f"{k:<28} stop {v['stop']:.3f} clear {v['clear']:.3f} inconcl {v['inconclusive']:.3f} codeflag {v['coding_flag']:.3f} "
                  f"CI x/{v['ci_factor_median']:.3f} nA {v['n_a_median']:.0f} nB {v['n_b_median']:.0f}")
        return
    res = part_oc(a.reps)
    out = dict(meta=dict(reps=a.reps, seconds=None), designs={})
    for lab, by in res.items():
        out["designs"][lab] = summarise(by)
    out["meta"]["seconds"] = time.time() - t
    (HERE / "oc_v6.json").write_text(json.dumps(out, indent=1, default=float) + "\n")
    for lab, m in out["designs"].items():
        print(lab)
        for tn, v in m.items():
            print(f"  {tn:<22} scale {v['scale']:.3f} cap {v['cap']:.3f} coll {v['coll']:.3f} esc {v['esc']:.3f} pa {v['pa_ratio']:.2f} capr {v['cap_ratio']:.2f}")


if __name__ == "__main__":
    main()
