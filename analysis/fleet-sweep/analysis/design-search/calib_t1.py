#!/usr/bin/env python3
"""Calibration check of the PLAN-v6 simulator against the live trial T1 (numbers only; no network).

    PY=/Users/tom/git/carnot/analysis/aidev/.venv/bin/python
    $PY calib_t1.py [--reps 500] [--out calib_t1.json]

T1 as run on 2026-09-28: one slot (s1) for 30 min, then twelve slots for 30 min, 10 min grace, one serial reviewer
(K = 1); slots s3 and s2 were lost at minutes 34.9 and 40.7 (CLI auto-update). The simulator runs exactly that
schedule (synth.V6_TRUTH, linear workers with T1's collision rate, window CV 0.3) and the same measures are taken
from the simulated and the observed logs: lambda per phase (phase B without s2 / s3), the reviewer's busy share in
phase B, the waiting-for-review depth every 5 minutes, reviews and submissions in phase B, merges. The observed
values come from t1_params_public.json (t1_params.py on the private log).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from synth import T0, make_task_pool, make_truth_v6, simulate  # noqa: E402
from common import parse_t  # noqa: E402

SPLIT = 30 * 60
WE = 60 * 60
LOST = {"s2": 40.7, "s3": 34.9}
SCHED = [(0.0, 60.0), (30.0, LOST["s2"]), (30.0, LOST["s3"])] + [(30.0, 60.0)] * 9


def measures(run, ev):
    t0 = parse_t(run["window_start"])
    rel = lambda e: parse_t(e["t"]) - t0
    excl = set(LOST)
    sub1 = [(rel(e), e["worker"]) for e in ev if e["type"] == "submit" and e["attempt_no"] == 1]
    lamA = sum(1 for t, s in sub1 if t < SPLIT and s == "s1") / 0.5
    lamB = sum(1 for t, s in sub1 if SPLIT <= t <= WE and s not in excl) / (10 * 0.5)
    iv, since = [], None
    for e in ev:
        if e["type"] == "reviewer_busy" and since is None:
            since = rel(e)
        elif e["type"] == "reviewer_idle" and since is not None:
            iv.append((since, rel(e)))
            since = None
    if since is not None:
        iv.append((since, WE + 600))
    ov = lambda a, b: sum(max(0.0, min(y, b) - max(x, a)) for x, y in iv)
    steps, seen = [], set()
    for e in ev:
        if e["type"] == "submit":
            steps.append((rel(e), 1))
        elif e["type"] == "review_start" and (e["task"], e["head"]) not in seen:
            seen.add((e["task"], e["head"]))
            steps.append((rel(e), -1))
    steps.sort()

    def depth_at(t):
        return sum(s for tt, s in steps if tt <= t)
    rv = [e for e in ev if e["type"] == "review_end"]
    return dict(lam_A=lamA, lam_B=lamB, busy_B=ov(SPLIT, WE) / (WE - SPLIT), busy_B_last20=ov(WE - 1200, WE) / 1200,
                **{f"depth_{m}": depth_at(m * 60) for m in (35, 40, 45, 50, 55, 60, 65, 70)},
                max_depth=max((d for d in np.cumsum([s for t, s in steps if t <= WE])), default=0),
                reviews_B=sum(1 for e in rv if SPLIT <= rel(e) <= WE),
                submits_B=sum(1 for e in ev if e["type"] == "submit" and SPLIT <= rel(e) <= WE),
                request_changes_share=(sum(e["verdict"] == "request_changes" for e in rv) / len(rv)) if rv else np.nan,
                merges=sum(1 for e in ev if e["type"] == "merge"),
                review_s_mean=float(np.mean([e["duration_s"] for e in rv])) if rv else np.nan)


def observed():
    d = json.loads((HERE / "t1_params_public.json").read_text())
    q = d["queue"]["depth_at_min"]
    return dict(lam_A=d["phase"]["A"]["lam"], lam_B=d["phase"]["B"]["lam"], busy_B=d["review"]["busy_share_B"],
                busy_B_last20=d["review"]["busy_share_B_last20"],
                **{f"depth_{m}": q[str(m)] for m in (35, 40, 45, 50, 55, 60, 65, 70)},
                max_depth=d["queue"]["max_depth_window"], reviews_B=d["demand_B"]["reviews_ended"],
                submits_B=d["demand_B"]["submits"], request_changes_share=d["review"]["request_changes_share"],
                merges=d["bounces"]["merges"], review_s_mean=d["review"]["duration_s"]["mean"])


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--reps", type=int, default=500)
    ap.add_argument("--family", default="measured")
    ap.add_argument("--out", default=str(HERE / "calib_t1.json"))
    a = ap.parse_args()
    obs = observed()
    rows = []
    for s in range(a.reps):
        truth = make_truth_v6(a.family, n_reviewers=1)
        pool = make_task_pool(truth, s * 31 + 5)
        run, ev = simulate(truth, 12, seed=70_000 + s, window_min=60, warmup_min=10, grace_min=10, schedule=SCHED,
                           task_pool=pool, run_id=f"calT1-{s}", kind="trial", t0=T0)
        rows.append(measures(run, ev))
    keys = list(obs)
    out = dict(reps=a.reps, family=a.family, measures={})
    print(f"{'measure':<22} {'T1':>8} {'sim median':>11} {'sim 5-95%':>16} {'obs pct':>8}")
    for k in keys:
        v = np.array([r[k] for r in rows], float)
        pct = float(np.mean(v < obs[k]) + 0.5 * np.mean(v == obs[k]))
        out["measures"][k] = dict(observed=obs[k], sim_median=float(np.nanmedian(v)), sim_p5=float(np.nanpercentile(v, 5)),
                                  sim_p95=float(np.nanpercentile(v, 95)), observed_percentile=pct)
        print(f"{k:<22} {obs[k]:>8.2f} {np.nanmedian(v):>11.2f} {np.nanpercentile(v, 5):>7.2f}-{np.nanpercentile(v, 95):<8.2f} {pct:>8.2f}")
    Path(a.out).write_text(json.dumps(out, indent=1) + "\n")


if __name__ == "__main__":
    main()
