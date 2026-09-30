#!/usr/bin/env python3
"""Operating characteristics of the PLAN-v7 designs (PLAN-v7 section 5) and of abort rule 1 in slot mode (T1b).

    PY=/Users/tom/git/carnot/analysis/aidev/.venv/bin/python
    $PY oc_v7.py --part t1b [--reps 1000]     # rule 1 on simulated slot-mode T1b logs: follow-up / launch / mixed
    $PY oc_v7.py --part oc  [--reps 1000]     # the 300 / 400 / 500-task designs, every window freshly simulated,
                                              # one task pool per study reused by every window (the real reset)

Simulation only: synth (V7_TRUTH) -> derive -> v6 / v7 codings, through dsim7. Writes oc_v7_t1b.json / oc_v7.json;
OPERATING-CHARACTERISTICS-v7.md and v7.V7_OC are written from them by hand.
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
import dsim7  # noqa: E402
from dsim7 import D7  # noqa: E402
import v7  # noqa: E402

T1B_TRUTHS = ("measured", "measured/thr=1.25", "measured/thr=1.5", "measured/thr=2", "measured/thrlaunch=1.5",
              "measured/thrlaunch=2", "measured/thrcode=1.5", "amdahl", "usl",
              "measured/upsd=0.25", "measured/upsd=0.25/thr=1.5", "measured/upsd=0.25/thr=2", "amdahl/upsd=0.25",
              "measured/upsd=0.25/thrlaunch=2", "measured/stall=0.03", "measured/stall=0.1",
              "measured/upsd=0.25/thrcode=1.5")
T1B_SHAPES = ((30, 10), (30, 15))       # PLAN-v7 after review: 30 + 15 (was 30 + 10; 15 + 10, 20 + 10, 45 + 10 in the
                                         # first search, oc_v7_t1b.json of 2026-09-30 morning)


def t1b_one(args):
    tname, L1, L2, K, seed = args
    run, ev = dsim7.t1b_sim(tname, L1, L2, K, seed)
    r = v7.throttle_v7(run, ev, split_min=L1)
    rev = sum(1 for e in ev if e["type"] == "review_end")
    out = dict(tasks=dsim7.handouts(ev), reviews=rev, dec=r["decision"], flag=r["coding_flag"], launch_flag=r["launch_flag"],
               nfA=r["n_followup"][0], nfB=r["n_followup"][1], nlA=r["n_launch"][0], nlB=r["n_launch"][1])
    for m in ("followup", "launch", "mixed"):
        x = r["startup_by"][m]
        d, _ = v7.v6.throttle_decision(x, r["coding"])
        out[f"{m}_dec"] = d
        out[f"{m}_ratio"] = x["ratio"]
        out[f"{m}_ci"] = x["ci"]
    return out


def part_t1b(reps, K=5):
    jobs = [(t, L1, L2, K, 500_000 + 1000 * i + L1 * 7 + L2) for t in T1B_TRUTHS for (L1, L2) in T1B_SHAPES for i in range(reps)]
    with ProcessPoolExecutor(max_workers=max(1, (os.cpu_count() or 2) - 1)) as ex:
        res = list(ex.map(t1b_one, jobs, chunksize=16))
    by = {}
    for (t, L1, L2, _, _), r in zip(jobs, res):
        by.setdefault(f"{t}|{L1}+{L2}", []).append(r)
    summ = {}
    for k, rs in by.items():
        s = dict(n=len(rs), tasks_median=float(np.median([r["tasks"] for r in rs])),
                 tasks_p90=float(np.percentile([r["tasks"] for r in rs], 90)),
                 reviews_median=float(np.median([r["reviews"] for r in rs])),
                 coding_flag=float(np.mean([r["flag"] for r in rs])), launch_flag=float(np.mean([r["launch_flag"] for r in rs])),
                 nfA=float(np.median([r["nfA"] for r in rs])), nfB=float(np.median([r["nfB"] for r in rs])),
                 nlA=float(np.median([r["nlA"] for r in rs])), nlB=float(np.median([r["nlB"] for r in rs])))
        for m in ("followup", "launch", "mixed"):
            s[f"{m}_stop"] = float(np.mean([r[f"{m}_dec"] == "STOP" for r in rs]))
            s[f"{m}_clear"] = float(np.mean([r[f"{m}_dec"] == "CLEAR" for r in rs]))
            s[f"{m}_ratio_median"] = float(np.nanmedian([r[f"{m}_ratio"] for r in rs]))
            hw = [0.5 * (math.log(r[f"{m}_ci"][1]) - math.log(r[f"{m}_ci"][0])) for r in rs
                  if r[f"{m}_ci"][0] == r[f"{m}_ci"][0] and r[f"{m}_ci"][0] > 0]
            s[f"{m}_ci_factor_median"] = float(math.exp(np.median(hw))) if hw else math.nan
        summ[k] = s
    return dict(reps=reps, K=K, summary=summ)


# ------------------------------------------------------------------------------------------------ design OCs
OC_TRUTHS = ("linear", "mild", "amdahl", "usl", "carnot", "measured", "linear/fastrev",
             "linear/cv=0.15", "linear/cv=0.5", "amdahl/cv=0.5", "linear/lam20", "linear/lam28", "amdahl/lam20",
             "linear/slowrev", "linear/cont=0.1", "linear/cont=0.2", "amdahl/cont=0.2", "measured/esc2x",
             "usl/p=0.0035", "usl/p=0.015", "measured/p=0.0035", "measured/p=0.015", "linear/thrcode=1.3",
             "linear/stall=0.03", "linear/tasksd=0.3", "amdahl/tasksd=0.3", "linear/tps=8", "linear/posfx=0.75",
             "amdahl/posfx=0.75", "linear/upsd=0.25", "linear/stall=0.1", "amdahl/stall=0.1", "linear/tps=8/mps=0")


CORE_TRUTHS = ("linear", "mild", "amdahl", "usl", "carnot", "measured", "linear/fastrev", "linear/cv=0.5", "linear/cont=0.1",
               "usl/p=0.0035", "usl/p=0.015", "measured/p=0.0035", "measured/esc2x", "linear/lam20", "linear/stall=0.1")


def design_of(dd):
    c = v7.cells_of(dd["cells"])
    return D7(tuple((n, k, w) for (n, k), w in sorted(c.items())), int(dd["window_min"]), int(dd["warmup_min"]))


def part_oc(reps, designs, truths=OC_TRUTHS, shared=True, fixed_order=False):
    jobs = []
    for name, d in designs.items():
        for t in truths:
            for i in range(reps):
                seed = 1 + 700_000 + 10_000 * (sum(ord(c) * (j + 3) for j, c in enumerate(t.replace("/tasksd=0.3", ""))) % 997) + i
                jobs.append((name, t, dict(design=d, truth=t, seed=seed, shared_pool=shared, fixed_order=fixed_order,
                                           parts=("scale", "cap", "coll", "esc", "family", "util", "scale_sens", "p_hat"))))
    with ProcessPoolExecutor(max_workers=max(1, (os.cpu_count() or 2) - 1)) as ex:
        res = list(ex.map(dsim7.study_fresh, [j[2] for j in jobs], chunksize=4))
    out = {}
    for (name, t, _), r in zip(jobs, res):
        out.setdefault(name, {}).setdefault(t, []).append(r)
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
                    esc_hw=mean(rs, "esc_hw"), esc_n=mean(rs, "esc_n"), coll_events=mean(rs, "coll_events"), coll_n=mean(rs, "coll_n"),
                    flag_rev=float(np.mean([bool(r.get("flag_rev")) for r in rs])),
                    flag_supply=float(np.mean([bool(r.get("flag_supply")) for r in rs])),
                    tasks=mean(rs, "tasks"), tasks_p90=pct(rs, "tasks", 90), tasks_p10=pct(rs, "tasks", 10),
                    reviews=mean(rs, "reviews_total"), sessions=mean(rs, "sessions"),
                    best={b: float(np.mean([r.get("best") == b for r in rs])) for b in ("linear", "amdahl", "usl", "carnot")},
                    binary={b: float(np.mean([r.get("binary") == b for r in rs])) for b in ("linear", "bending")},
                    p_ci_cover=None)
        tp = next((float(x[2:]) for x in t.split("/")[1:] if x.startswith("p=")), {"carnot": 0.0075, "measured": 0.0075}.get(t.split("/")[0]))
        if tp:
            cov = [r["p_ci"][0] <= tp <= r["p_ci"][1] for r in rs if r.get("p_ci")]
            m[t]["p_ci_cover"] = float(np.mean(cov)) if cov else None
        for key in rs[0]:
            if key.split("_")[0] in ("util", "utilmax", "qne", "fin", "lam", "rev", "busyh", "exh", "supflag"):
                m[t][key] = mean(rs, key)
                if key.startswith("utilmax") or key.startswith("fin"):
                    m[t][key + "_p95"] = pct(rs, key, 95)
    return m


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--part", choices=["t1b", "oc", "reuse"], required=True)
    ap.add_argument("--reps", type=int, default=1000)
    a = ap.parse_args()
    t = time.time()
    if a.part == "t1b":
        out = part_t1b(a.reps)
        out["seconds"] = time.time() - t
        (HERE / "oc_v7_t1b.json").write_text(json.dumps(out, indent=1) + "\n")
        for k, v in out["summary"].items():
            print(f"{k:<42} tasks {v['tasks_median']:.0f} (p90 {v['tasks_p90']:.0f}) nfA {v['nfA']:.0f} nfB {v['nfB']:.0f} nlA {v['nlA']:.0f} | "
                  + " | ".join(f"{m[:4]} stop {v[m + '_stop']:.3f} clear {v[m + '_clear']:.3f} x/{v[m + '_ci_factor_median']:.2f}"
                               for m in ("followup", "launch", "mixed")) + f" | codeflag {v['coding_flag']:.2f}")
        return
    designs = {k: design_of(v) for k, v in v7.DESIGNS_V7.items()}
    if a.part == "reuse":   # task reuse across windows: one shared pool vs a fresh pool per window, with task-level times
        tr = ("linear/tasksd=0.3", "amdahl/tasksd=0.3", "linear/tasksd=0.5")
        res = {"shared": part_oc(a.reps, {"400": designs["400"]}, tr, True)["400"],
               "fresh": part_oc(a.reps, {"400": designs["400"]}, tr, False)["400"],
               "fixed-order": part_oc(a.reps, {"400": designs["400"]}, tr, True, True)["400"]}
        out = {k: summarise(v) for k, v in res.items()}
        (HERE / "oc_v7_reuse.json").write_text(json.dumps(dict(reps=a.reps, designs=out, seconds=time.time() - t), indent=1, default=float) + "\n")
        for k, m in out.items():
            for tn, v in m.items():
                print(f"{k:<12} {tn:<20} scale {v['scale']:.3f} pa {v['pa_ratio']:.3f} cap {v['cap']:.3f}")
        return
    res = part_oc(a.reps, {"400": designs["400"]})
    res.update(part_oc(a.reps, {k: v for k, v in designs.items() if k != "400"}, CORE_TRUTHS))
    out = dict(meta=dict(reps=a.reps), designs={})
    for name, by in res.items():
        out["designs"][name] = dict(label=designs[name].label(), truths=summarise(by))
    out["meta"]["seconds"] = time.time() - t
    (HERE / "oc_v7.json").write_text(json.dumps(out, indent=1, default=float) + "\n")
    for name, dd in out["designs"].items():
        print(name, dd["label"])
        for tn, v in dd["truths"].items():
            print(f"  {tn:<22} scale {v['scale']:.3f} cap {v['cap']:.3f} coll {v['coll']:.3f} esc {v['esc']:.3f} pa {v['pa_ratio']:.2f} "
                  f"capr {v['cap_ratio']:.2f} tasks {v['tasks']:.0f} (p90 {v['tasks_p90']:.0f}) reviews {v['reviews']:.0f}")


if __name__ == "__main__":
    main()
