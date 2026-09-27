#!/usr/bin/env python3
"""Operating characteristics of the final PLAN-v4.1 design (PLAN-v4 section 6.8).

    python oc_v41.py [--reps 2000] [--reps-extra 1000] [--workers 9] [--out oc_v41.json]

Simulation only (synth -> derive -> predict -> score); no network, API or harness. One simulated study is
selftest.py's PLAN-v4 pipeline, generalised to the degrade rule's sweep:

  * pilot: T1 (1 worker for 30 min, then 12 for the last 15 min; its PRs reviewed) plus eight separate 60-min
    T2 windows of one worker; derive.pilot_params on those live runs only (no calibration reviews pooled);
  * sweep: N = 1 and 12, three 120-min windows each in ABBAAB order (10 min warm-up, 10 min grace), or the
    degrade rule's sweep at 1.5x burn (design-search/dsim.fit_budget: 2 x 120 min, ABBA);
  * 220 tasks per window; derive.py's grace-end V fix and task-supply truncation / flag;
  * score.py's v4.1 codings (P1, P1-nf, O2, O2-nf, S3, O3, Vdur conditional, Vratio descriptive, S1r/S2r, ESC,
    COLL).

Assumptions as in DESIGN-SEARCH.md for the recommended design: Haiku at 0.85 x the 8/h task-size target
(lambda1 = 6.8), V0 = 2 x 6.8 = 13.6, defect rate 0.49, window-to-window CV 0.3 on worker speed, 0.75-min
serial merge queue, review-time CV 1 (exponential) unless stated. Uncapped truths: the reviewer speeds up with
queue depth (synth reviewer_load = 2). skimN / slowN: the reviewer's pace at N = 12 is 1.25x / 0.75x its
pace at N = 1 and in the pilot. lambda -30%: true lambda1 = 0.7 x 6.8 with V0 unchanged (rework time scaled
as in dsim). Writes oc_v41.json; OPERATING-CHARACTERISTICS.md is written from it by hand.
"""
from __future__ import annotations

import argparse
import dataclasses
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
from derive import derive_window, pilot_params  # noqa: E402
from predict import Params  # noqa: E402
from score import score  # noqa: E402
from synth import T0, make_truth, simulate, simulate_study  # noqa: E402
from selftest import V4_TRUTH  # noqa: E402
from dsim import COST, fit_budget  # noqa: E402

LAM = V4_TRUTH["lam1"]


def sweep_order(reps):
    order = []
    for r in range(reps):
        order += [1, 12] if r % 2 == 0 else [12, 1]
    return order


def study(cfg):
    """cfg: truth, seed, over (synth overrides), reps (3), window_min (120), parts (escape/collision)."""
    tname = cfg["truth"]
    base = {"skimN": "carnot", "slowN": "carnot"}.get(tname, tname)
    truth = make_truth(base, **{**V4_TRUTH, **cfg.get("over", {})})
    seed = cfg["seed"]
    res = dict(seed=seed, truth=tname)
    runs = simulate_study(truth, (1, 12), seed=seed, reps=0, with_pilot=True, pilot_design="v4")
    der = [derive_window(r, e) for r, e in runs]
    try:
        pil = pilot_params(der)
        P = Params.from_pilot(pil, window_min=cfg.get("window_min", 120.0))
        if P.completion is None or not P.completion > 0:
            raise ValueError
    except (ValueError, KeyError, TypeError, ZeroDivisionError):
        res["status"] = "PILOT_UNUSABLE"
        return res
    res["review_cv_ok"] = bool(pil["review_cv_ok"])
    res["pilot_review_cv"] = pil["review_time_cv"]
    res["pilot_reviews"] = pil["reviews"]
    if cfg.get("pilot_only"):
        res["status"] = "PILOT_ONLY"
        return res
    truth_hi = truth
    if tname in ("skimN", "slowN"):
        truth_hi = dataclasses.replace(truth, V0=truth.V0 * (1.25 if tname == "skimN" else 0.75))
    L = cfg.get("window_min", 120.0)
    sweep = []
    for i, n in enumerate(sweep_order(cfg.get("reps", 3))):
        sweep.append(simulate(truth_hi if n == 12 else truth, n, seed=seed * 1000 + 10 + i, window_min=L,
                              run_id=f"oc-{seed}-N{n}-w{i + 1}", kind="sweep",
                              t0=T0 + (seed % 1000) * 86400 + (20 + 2.5 * i) * 3600, task_pool=None))
    derived = [derive_window(r, e) for r, e in sweep]
    parts = cfg.get("parts", ())
    R = score(derived, pil, do_escape="escape" in parts, do_collision="collision" in parts, do_fit=False)
    res["status"] = "SCORED"
    res["codes"] = {o["id"]: o["code"] for o in R["outcomes"]}
    res["best"] = R["rivals"]["best"]
    res["log_lr"] = R["primary"]["log_lr"]
    res["V_ratio"] = R["V4"]["ratio"]
    res["V_ratio_ci"] = R["V4"]["ratio_ci"]
    res["n_rev"] = (R["V4"]["vdur"]["n_low"], R["V4"]["vdur"]["n_high"])
    res["flagged"] = R.get("supply_flagged", [])
    res["truncated"] = [s["run_id"] for s in R["windows"] if s.get("supply_truncated")]
    wf = R.get("without_flagged")
    res["P1_nf"] = None if not wf or wf["P1"] is None else bool(wf["P1"]["carnot_higher"])
    res["O2_nf"] = None if not wf or wf["O2"] is None else bool(wf["O2"]["inside"])
    if "escape" in R:
        e = R["escape"]
        res["esc_events"] = e["events"]
        res["esc_p"] = None if e["descriptive_only"] or not e.get("fit_ok") else e.get("p_one_sided")
    if "collision" in R:
        c = R["collision"]
        res["coll_p"] = c.get("p_k_one_sided") if c.get("fit_ok") else None
    hi = [s for s in R["windows"] if s["n_workers"] == 12]
    res["last_claim_share"] = [s["tasks_claimed"] / s["task_supply"] for s in hi if s.get("task_supply")]
    return res


def run(cfgs, workers):
    with ProcessPoolExecutor(max_workers=workers) as ex:
        return list(ex.map(study, cfgs, chunksize=8))


def rate(xs):
    xs = [bool(x) for x in xs]
    n = len(xs)
    if not n:
        return dict(p=None, n=0, hw=None)
    p = sum(xs) / n
    return dict(p=p, n=n, hw=1.96 * math.sqrt(p * (1 - p) / n))


def summarise(res):
    ok = [r for r in res if r.get("status") == "SCORED"]
    out = dict(n_total=len(res), n_scored=len(ok), pilot_unusable=sum(r.get("status") == "PILOT_UNUSABLE" for r in res))
    if not ok:
        return out
    code = lambda i, c: rate([r["codes"].get(i) == c for r in ok])
    out.update(
        P1_pass=code("P1", "PASS"), O2_fail=code("O2", "FAIL"), S3_fail=code("S3", "FAIL"), O3_pass=code("O3", "PASS"),
        Vdur_fail=code("Vdur", "FAIL"), S1r_fail=code("S1r", "FAIL"), S2r_fail=code("S2r", "FAIL"),
        review_cv_ok=rate([r["review_cv_ok"] for r in ok]),
        best={f: sum(r["best"] == f for r in ok) / len(ok) for f in ("carnot", "usl", "amdahl", "linear")},
        tie=sum("|" in r["best"] for r in ok) / len(ok),
        any_flagged=rate([bool(r["flagged"]) for r in ok]), any_truncated=rate([bool(r["truncated"]) for r in ok]),
        P1_nf_pass=rate([r["P1_nf"] for r in ok if r["P1_nf"] is not None]),
        O2_nf_fail=rate([not r["O2_nf"] for r in ok if r["O2_nf"] is not None]),
        median_V_ratio=float(np.median([r["V_ratio"] for r in ok if r["V_ratio"] is not None])),
        median_V_ratio_ci=[float(np.median([r["V_ratio_ci"][k] for r in ok if r["V_ratio_ci"][k] is not None])) for k in (0, 1)],
        median_reviews=[float(np.median([r["n_rev"][k] for r in ok])) for k in (0, 1)],
        median_pilot_reviews=float(np.median([r["pilot_reviews"] for r in ok])),
        median_claim_share_N12=float(np.median([x for r in ok for x in r["last_claim_share"]])) if any(r["last_claim_share"] for r in ok) else None,
    )
    if any("esc_events" in r for r in ok):
        ev = np.array([r["esc_events"] for r in ok])
        ps = np.array([np.nan if r.get("esc_p") is None else r["esc_p"] for r in ok], float)
        out["escape"] = dict(share_ge8=float((ev >= 8).mean()), median_events=float(np.median(ev)),
                             det_a05=rate(((ev >= 8) & (ps < 0.05)).tolist()), det_a01=rate(((ev >= 8) & (ps < 0.01)).tolist()))
    if any("coll_p" in r for r in ok):
        ps = np.array([np.nan if r.get("coll_p") is None else r["coll_p"] for r in ok], float)
        out["collision"] = dict(fit_share=float(np.isfinite(ps).mean()), det_a05=rate((ps < 0.05).tolist()),
                                det_a01=rate((ps < 0.01).tolist()))
    return out


def pilot_only_summary(res):
    ok = [r for r in res if r.get("status") == "PILOT_ONLY"]
    return dict(n=len(ok), review_cv_ok=rate([r["review_cv_ok"] for r in ok]),
                median_pilot_cv=float(np.median([r["pilot_review_cv"] for r in ok])),
                median_pilot_reviews=float(np.median([r["pilot_reviews"] for r in ok])))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--reps", type=int, default=2000)
    ap.add_argument("--reps-extra", type=int, default=1000, help="escape / collision cells")
    ap.add_argument("--workers", type=int, default=max(1, (os.cpu_count() or 2) - 1))
    ap.add_argument("--out", default=str(HERE / "oc_v41.json"))
    ap.add_argument("--only", default=None, help="comma-separated cell names (debug)")
    a = ap.parse_args()
    R, RX = a.reps, a.reps_extra
    lam70 = dict(lam1=0.7 * LAM, rework_min=6.0 * 4.0 / (0.7 * LAM))
    burn = fit_budget((1, 12), 3, 120, COST["haiku"] * 1.5, COST["haiku"] * 1.5 * 11.25)
    cells = {}
    truths = ("carnot", "usl", "amdahl", "linear")
    # common random numbers: the same seed block per truth across scenarios
    for ti, t in enumerate(truths):
        cells[f"base/cv1/{t}"] = [dict(truth=t, seed=1_000_000 + ti * 100_000 + i) for i in range(R)]
        cells[f"lam70/cv1/{t}"] = [dict(truth=t, seed=1_000_000 + ti * 100_000 + i, over=lam70) for i in range(R)]
        cells[f"burn15/cv1/{t}"] = [dict(truth=t, seed=1_000_000 + ti * 100_000 + i, reps=burn[0], window_min=float(burn[1]))
                                    for i in range(R)]
    for ci, cv in enumerate((0.5, 1.0)):
        for tj, t in enumerate(("carnot", "skimN", "slowN")):
            if cv == 1.0 and t == "carnot":
                continue
            cells[f"base/cv{cv:g}/{t}"] = [dict(truth=t, seed=2_000_000 + ci * 300_000 + tj * 100_000 + i,
                                                over=dict(service_cv=cv)) for i in range(R)]
    for ci, cv in enumerate((0.3, 0.5, 0.7, 1.0)):
        cells[f"pilot/cv{cv:g}"] = [dict(truth="carnot", seed=3_000_000 + ci * 100_000 + i, over=dict(service_cv=cv),
                                         pilot_only=True) for i in range(R)]
    for gi, g in enumerate((0.0, 0.2)):
        cells[f"escape/g{g:g}"] = [dict(truth="carnot", seed=4_000_000 + gi * 100_000 + i, over=dict(escape_depth=g),
                                        parts=("escape",)) for i in range(RX)]
    for pi, p in enumerate((0.0, 0.01, 0.05)):
        cells[f"collision/p{p:g}"] = [dict(truth="carnot", seed=5_000_000 + pi * 100_000 + i, over=dict(p=p),
                                           parts=("collision",)) for i in range(RX)]
    if a.only:
        keep = set(a.only.split(","))
        cells = {k: v for k, v in cells.items() if k in keep}
    out = dict(meta=dict(reps=R, reps_extra=RX, design_truth=V4_TRUTH, burn_1_5x_sweep=dict(reps=burn[0], window_min=burn[1]),
                         lam70=lam70, started=time.strftime("%Y-%m-%d %H:%M:%S")), cells={})
    t0 = time.time()
    for name, cfgs in cells.items():
        res = run(cfgs, a.workers)
        out["cells"][name] = pilot_only_summary(res) if name.startswith("pilot/") else summarise(res)
        print(f"{time.time() - t0:7.0f}s {name}: " + json.dumps(out["cells"][name])[:300], flush=True)
        Path(a.out).write_text(json.dumps(out, indent=1) + "\n")
    out["meta"]["seconds"] = time.time() - t0
    Path(a.out).write_text(json.dumps(out, indent=1) + "\n")


if __name__ == "__main__":
    main()
