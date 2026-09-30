#!/usr/bin/env python3
"""Calibration check of the PLAN-v7 simulator against the command-launcher trials T0d and T0e (numbers only).

    PY=/Users/tom/git/carnot/analysis/aidev/.venv/bin/python
    $PY calib_t0de.py [--reps 1000] [--out calib_t0de.json]

T0d (2026-09-29): one slot, 45 min, one reviewer, one session per task (every task a launch): the simulator with
tasks_per_session = 1. T0e: one slot, one session per slot (tasks_per_session was 8); the session stalled during its
6th task (context compaction), so only the stretch before the stall (8.5 min) is compared: first submissions and the
follow-up start-up. The same measures are taken from simulated logs (synth.V7_TRUTH, linear workers with T1's collision
rate, window CV 0.3) and from t0de_params_public.json (t0de_params.py on the private logs).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))
from synth import T0, make_task_pool, make_truth_v7, simulate  # noqa: E402
import v7  # noqa: E402


def measures(run, ev, pre_min=None):
    t0 = v7.v6.parse_t(run["window_start"])
    legs = v7.task_legs_v7(run, ev)
    sub1 = [v7.v6.parse_t(e["t"]) - t0 for e in ev if e["type"] == "submit" and e["attempt_no"] == 1]
    rv = [e for e in ev if e["type"] == "review_end"]
    rw, last = [], {}
    for e in ev:
        t = v7.v6.parse_t(e["t"]) - t0
        if e["type"] == "session_message" and e.get("kind") == "rework":
            last[e["task"]] = t
        elif e["type"] == "submit" and e["attempt_no"] > 1 and e["task"] in last:
            rw.append(t - last.pop(e["task"]))
    med = lambda xs: float(np.median(xs)) if len(xs) else np.nan
    out = dict(first_submissions=len([s for s in sub1 if s <= 45 * 60]),
               handed_out=sum(1 for e in ev if e["type"] == "session_launch" or (e["type"] == "session_message" and e.get("kind") == "task")),
               reviews=len(rv), request_changes_share=(sum(e["verdict"] == "request_changes" for e in rv) / len(rv)) if rv else np.nan,
               review_s_median=med([e["duration_s"] for e in rv]), merges=sum(e["type"] == "merge" for e in ev),
               startup_launch_median=med([x["startup_s"] for x in legs if not x["followup"] and x["startup_s"]]),
               startup_followup_median=med([x["startup_s"] for x in legs if x["followup"] and x["startup_s"]]),
               coding_median=med([x["coding_s"] for x in legs if x["coding_s"]]), rework_median=med(rw))
    if pre_min:
        out["first_submissions_pre"] = len([s for s in sub1 if s <= pre_min * 60])
    return out


def observed():
    d = json.loads((HERE / "t0de_params_public.json").read_text())
    a, b = d["runs"]["T0d"], d["runs"]["T0e"]
    return dict(
        T0d=dict(first_submissions=a["first_submissions"], handed_out=a["handed_out"], reviews=a["reviews"],
                 request_changes_share=a["request_changes"] / a["reviews"], review_s_median=a["review_s"]["median"],
                 merges=a["merges"], startup_launch_median=a["startup_launch_s"]["median"], coding_median=a["coding_s"]["median"],
                 rework_median=a["rework_s"]["median"]),
        T0e=dict(first_submissions_pre=round(b["lam_before_stall"] * b["stall_after_min"] / 60),
                 startup_followup_median=b["startup_followup_s"]["median"], coding_median=b["coding_s"]["median"]),
        pre_min=b["stall_after_min"])


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--reps", type=int, default=1000)
    ap.add_argument("--family", default="measured")
    ap.add_argument("--out", default=str(HERE / "calib_t0de.json"))
    a = ap.parse_args()
    obs = observed()
    sims = dict(T0d=[], T0e=[])
    for s in range(a.reps):
        for name, tps in (("T0d", 1), ("T0e", 8)):
            truth = make_truth_v7(a.family, n_reviewers=1, tasks_per_session=tps)
            run, ev = simulate(truth, 1, seed=90_000 + s * 2 + (name == "T0e"), window_min=45, warmup_min=0, grace_min=10,
                               task_pool=make_task_pool(truth, s * 31 + 5), run_id=f"cal{name}-{s}", kind="trial", t0=T0)
            sims[name].append(measures(run, ev, obs["pre_min"] if name == "T0e" else None))
    out = dict(reps=a.reps, family=a.family, measures={})
    print(f"{'run':<5} {'measure':<26} {'obs':>8} {'sim median':>11} {'sim 5-95%':>16} {'obs pct':>8}")
    for name in ("T0d", "T0e"):
        for k, o in obs[name].items():
            v = np.array([r[k] for r in sims[name]], float)
            v = v[~np.isnan(v)]
            pct = float(np.mean(v < o) + 0.5 * np.mean(v == o))
            out["measures"][f"{name}|{k}"] = dict(observed=o, sim_median=float(np.median(v)), sim_p5=float(np.percentile(v, 5)),
                                                  sim_p95=float(np.percentile(v, 95)), observed_percentile=pct)
            print(f"{name:<5} {k:<26} {o:>8.2f} {np.median(v):>11.2f} {np.percentile(v, 5):>7.2f}-{np.percentile(v, 95):<8.2f} {pct:>8.2f}")
    lam = np.array([r["first_submissions"] / 0.75 for r in sims["T0d"]])
    out["lambda_T0d_sim"] = dict(median=float(np.median(lam)), p5=float(np.percentile(lam, 5)), p95=float(np.percentile(lam, 95)))
    Path(a.out).write_text(json.dumps(out, indent=1) + "\n")


if __name__ == "__main__":
    main()
