#!/usr/bin/env python3
"""Simulator parameters for PLAN-v7 from the command-launcher trials T0d and T0e (2026-09-29), numbers only.

    PY=/Users/tom/git/carnot/analysis/aidev/.venv/bin/python
    $PY t0de_params.py <runs>/T0d-2026-09-29 <runs>/T0e-2026-09-29 --public design-search/t0de_params_public.json \
        [--meter T0d=247,239 --meter T0e=239,236]

Reads run.json + events.jsonl only and writes **aggregates only** (counts, medians, quantiles; no task id, task text,
review reason, error text or note body) to the public file, which synth.V7_TRUTH and the v7 design search read.

T0d: one slot, 45 min, `claude --cloud` with the #81776 workaround, **one session per task** (every task a launch).
T0e: one slot, 45 min, **one session per slot** (`session_per = "slot"`, tasks_per_session = 8 then): the first task
launched, later ones as follow-up messages (`session_message kind=task`); the session auto-compacted during its 6th
task and stalled until the 25-min timeout, so the per-task legs are taken from the first 6 tasks and lambda is given
both over the whole window and over the stretch before the stall.

Per task on a slot:
  hand-out delay   slot freed (slot_idle) -> session_launch (the launch command returns the session id) or
                   session_message kind=task (the follow-up command returns); rework: slot freed -> kind=rework
  start-up         hand-out -> claim (branch first pushed): a launch includes provisioning and the clone, a
                   follow-up does not
  coding           claim -> first READY
  rework           session_message kind=rework -> the task's next READY
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

import numpy as np


def _t(s):
    from datetime import datetime
    return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()


def _stats(xs):
    xs = [float(x) for x in xs if x is not None and x == x]
    if not xs:
        return dict(n=0)
    lg = [math.log(x) for x in xs if x > 0]
    return dict(n=len(xs), mean=float(np.mean(xs)), median=float(np.median(xs)), min=float(min(xs)), max=float(max(xs)),
                log_sd=float(np.std(lg, ddof=1)) if len(lg) > 1 else 0.0,
                geo_mean=float(math.exp(np.mean(lg))) if lg else math.nan)


def legs(run_dir: Path):
    run = json.loads((run_dir / "run.json").read_text())
    ev = [json.loads(x) for x in (run_dir / "events.jsonl").read_text().splitlines() if x.strip()]
    t0 = _t(run["window_start"])
    we = _t(run["window_end"]) - t0
    rows, idle_at = {}, None
    ho_launch, ho_follow, ho_rework, up_launch, up_follow, coding, rework = [], [], [], [], [], [], []
    for e in ev:
        t = _t(e["t"]) - t0
        ty = e["type"]
        if ty == "slot_idle":
            idle_at = t
        elif ty == "session_launch":
            rows.setdefault(e["task"], {}).update(h=t, kind="launch")
            if idle_at is not None:
                ho_launch.append(t - idle_at)
        elif ty == "session_message" and e.get("kind") == "task":
            rows.setdefault(e["task"], {}).update(h=t, kind="follow")
            if idle_at is not None:
                ho_follow.append(t - idle_at)
        elif ty == "session_message" and e.get("kind") == "rework":
            rows.setdefault(e["task"], {}).setdefault("rw", []).append(t)
            if idle_at is not None:
                ho_rework.append(t - idle_at)
        elif ty == "claim":
            rows.setdefault(e["task"], {}).setdefault("c", t)
        elif ty == "submit":
            r = rows.setdefault(e["task"], {})
            if e["attempt_no"] == 1:
                r.setdefault("s", t)
            else:
                r.setdefault("rs", []).append(t)
    for r in rows.values():
        if "h" in r and "c" in r:
            (up_launch if r["kind"] == "launch" else up_follow).append(r["c"] - r["h"])
        if "c" in r and "s" in r:
            coding.append(r["s"] - r["c"])
        for m, s in zip(r.get("rw", []), r.get("rs", [])):
            if s > m:
                rework.append(s - m)
    subs1 = sorted(r["s"] for r in rows.values() if "s" in r and r["s"] <= we)
    rv = [e for e in ev if e["type"] == "review_end"]
    timeouts = [_t(e["t"]) - t0 for e in ev if e["type"] == "session_timeout"]
    t_stall = None
    if timeouts:   # the stretch before the task that timed out was handed out
        t_stall = timeouts[0] - 25 * 60
    return dict(
        window_min=we / 60, handed_out=len([r for r in rows.values() if "h" in r]),
        launches=sum(1 for r in rows.values() if r.get("kind") == "launch"),
        followups=sum(1 for r in rows.values() if r.get("kind") == "follow"),
        first_submissions=len(subs1), lam_window=len(subs1) / (we / 3600),
        lam_before_stall=(sum(1 for s in subs1 if s <= t_stall) / (t_stall / 3600)) if t_stall else None,
        stall_after_min=t_stall / 60 if t_stall else None, timeouts=len(timeouts),
        handout_delay_launch_s=_stats(ho_launch), handout_delay_followup_s=_stats(ho_follow),
        handout_delay_rework_s=_stats(ho_rework),
        startup_launch_s=_stats(up_launch), startup_followup_s=_stats(up_follow), coding_s=_stats(coding),
        rework_s=_stats(rework),
        reviews=len(rv), request_changes=sum(e["verdict"] == "request_changes" for e in rv),
        review_s=_stats([e["duration_s"] for e in rv]), review_durations=[float(e["duration_s"]) for e in rv],
        merges=sum(e["type"] == "merge" for e in ev),
        rework_legs=len(rework)), dict(coding=coding, rework=rework, up_launch=up_launch, up_follow=up_follow)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("runs", nargs="+")
    ap.add_argument("--public", required=True)
    ap.add_argument("--meter", nargs="*", default=["T0d=247,239", "T0e=239,236"], metavar="NAME=M0,M1")
    ap.add_argument("--t1", default=str(Path(__file__).resolve().parent / "design-search" / "t1_params_public.json"))
    a = ap.parse_args()
    out, pooled = {}, dict(coding=[], rework=[], up_launch=[], up_follow=[], review=[])
    for r in a.runs:
        name = Path(r).name.split("-")[0]
        s, raw = legs(Path(r))
        pooled["review"] += s.pop("review_durations")
        for k in ("coding", "rework", "up_launch", "up_follow"):
            pooled[k] += raw[k]
        out[name] = s
    meters = {}
    for m in a.meter:
        k, v = m.split("=")
        m0, m1 = (float(x) for x in v.split(","))
        n = out.get(k, {}).get("handed_out")
        meters[k] = dict(before=m0, after=m1, spent=m0 - m1, per_task=(m0 - m1) / n if n else None,
                         note="the meter shows whole dollars: +-$1 on each reading")
    t1 = json.loads(Path(a.t1).read_text())
    q = t1["review_pooled_quantiles_s"]
    # pooled review durations: the T1 + T0c quantile table (134 reviews) resampled at its 21 points, weighted by
    # count, plus the T0d / T0e durations; quantiles of the union
    t1_pts = [float(q[f"p{i}"]) for i in range(0, 101, 5)]
    t1_n = int(t1.get("review_pooled", {}).get("n", 134))
    synth_t1 = list(np.interp(np.linspace(0, 1, t1_n), np.linspace(0, 1, len(t1_pts)), t1_pts))
    allrev = synth_t1 + pooled["review"]
    pub = dict(
        source="t0de_params.py on the private T0d / T0e logs (numbers only)",
        runs=out, meter=meters,
        pooled=dict(coding_s=_stats(pooled["coding"]), rework_s=_stats(pooled["rework"]),
                    startup_launch_s=_stats(pooled["up_launch"]), startup_followup_s=_stats(pooled["up_follow"]),
                    review_s=_stats(pooled["review"]),
                    request_changes_share=(sum(v["request_changes"] for v in out.values()) /
                                           max(1, sum(v["reviews"] for v in out.values())))),
        review_pooled_all_quantiles_s={f"p{i}": float(np.quantile(allrev, i / 100)) for i in range(0, 101, 5)},
        review_pooled_all_n=len(allrev))
    Path(a.public).write_text(json.dumps(pub, indent=1) + "\n")
    print(json.dumps(pub, indent=1))


if __name__ == "__main__":
    main()
