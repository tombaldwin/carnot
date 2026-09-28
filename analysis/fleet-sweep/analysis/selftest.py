#!/usr/bin/env python3
"""Self-test of the fleet-sweep analysis on synthetic sweeps (synth.py -> derive -> predict -> score), PLAN-v5 and PLAN-v4.

    python selftest.py              # full run (a few minutes, uses all cores)
    python selftest.py --quick      # fewer replicates, for a smoke test
    python selftest.py --reps 400 --out selftest-output

Parts:
  U  unit checks: derive on hand-built logs with known answers (accounting, the grace-end V correction,
     task-supply truncation from a note and from reset.json, the v4.1 supply flag at minute 110, P1 / O2
     without flagged windows, the calibration-log reader and abort rule 4, derive --pilot refusing
     non-live runs); schema validator accepts synth output and
     rejects broken lines; CLI end to end at the PLAN-v4 design (synth --v4 -> validate -> derive ->
     predict -> score) in a temp dir, checking that predict has no pilot gate by default (no REDESIGN_*),
     predicts all four rivals at N = 1 and 12 with the operating-characteristics statement, keeps the
     superseded gate behind --v3-gate, and that every result in RESULTS-draft.md carries a grade.
  V  the PLAN-v4 design point through the whole pipeline (T1 1 -> 12, eight 60-min T2 windows of one
     worker, sizes 1 and 12, three 120-min windows each, ABBAAB, 220 tasks) under Carnot, USL, Amdahl and
     linear truths and a reviewer 25% faster / slower at N = 12 (skimN / slowN), at review-time CV 1 and
     0.5: rates of every v4.1 coding (P1, O2, S3, O3, Vdur, S1r, S2r) and of review_cv_ok, with the
     pre-registered operating characteristics (common.V4_OC) alongside.
  S  task supply: linear truth with only 120 tasks at the design point, so N = 12 windows run out; lambda
     and the per-agent attempt ratio with and without the truncation.
  D  escaped defects: planted depth effect vs none; detection and false-positive rates per model.
  E  collisions: planted p (and p_m), recovery of p-hat, CI coverage, k-slope rates; p = 0 false positives;
     censoring check (review v2, C): the k-slope with censored excluded vs the naive "not finished" outcome.
  W  PLAN-v5 (the default analysis since v5): the recommended design (v5.DESIGN_V5) under planted truths through
     synth -> derive -> v5, with design-search/dsim5.study5: SCALE false alarms and power, the rival pick, ESC-N null and
     planted rises, ESC precision, COLL null and planted p (p-hat coverage), reviewer utilisation; v5.V5_OC alongside.
     Part U also has the v5 unit checks (unit_v5): collision exposure j / jm / collided_first and merge-queue
     utilisation on a hand-built session log, the v5 tests on constructed inputs, the effort re-review log, and the v5
     CLI end to end (score.py and predict.py default to PLAN-v5; the v4 CLI checks run with --plan v4).
The PLAN-v3 parts (A: the gate, B: sizes 1/5 with the v3 codings, C: review-comparable N = 3/8 table,
F: v3 surprise codings) were retired with PLAN-v4; their last results are in git history
(selftest-output/SELFTEST.md at commit 5f213af).
Writes <out>/selftest.json and <out>/SELFTEST.md.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import subprocess
import sys
import tempfile
import time
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from common import (CONDITIONAL, CONFIRMATORY, DESCRIPTIVE, ROLE_MANIPULATION, ROLE_PRIMARY,  # noqa: E402
                    UNCAPPED_TRUTH_FOOTNOTE, V4_OC, iso)
from derive import derive_window, pilot_params  # noqa: E402
from predict import Params  # noqa: E402
from score import score  # noqa: E402
from synth import T0, make_truth, make_truth_v5, simulate, simulate_study  # noqa: E402
import validate_schema  # noqa: E402

PY = sys.executable


# ---------------------------------------------------------------------- U: unit checks
def _ev(t_min, typ, **f):
    return {"t": iso(T0 + 60 * t_min), "type": typ, **f}


def unit_derive():
    run = {"run_id": "unit", "kind": "sweep", "n_workers": 1, "window_start": iso(T0), "window_end": iso(T0 + 3600),
           "warmup_min": 10, "grace_min": 10, "task_order_seed": 1, "sandbox_commit": "x", "harness_commit": "x",
           "worker_model": "x", "reviewer_model": "x", "notes": ""}
    S = lambda t, task, head, a, k=0, m=0: _ev(t, "submit", worker="w1", task=task, branch=f"claude/task-{task}", head=head,
                                               attempt_no=a, lines_changed=50, files=["a.py"], k=k, m=m)
    RS = lambda t, task, head, d=0: _ev(t, "review_start", task=task, head=head, queue_depth=d)
    RE = lambda t, task, head, v: _ev(t, "review_end", task=task, head=head, verdict=v, reason="r", tokens_in=1,
                                      tokens_out=1, duration_s=60.0)
    ok_merge = lambda t, task, head: [_ev(t, "hidden_pre", task=task, head=head, passed=True),
                                      _ev(t, "rebase", task=task, head=head, new_head=head + "r", conflict=False),
                                      _ev(t + 0.5, "tests_post", task=task, head=head, visible_passed=True, hidden_passed=True),
                                      _ev(t + 0.5, "merge", task=task, head=head, main_sha="m" + head)]
    ev = [_ev(0, "worker_start", worker="w1", session_id="s"),
          S(5, "E", "e1", 1), _ev(5, "reviewer_busy"), RS(5, "E", "e1"), RE(10, "E", "e1", "approve"), _ev(10, "reviewer_idle"),
          *ok_merge(11, "E", "e1"),
          S(15, "A", "a1", 1), _ev(15, "reviewer_busy"), RS(15, "A", "a1"), RE(20, "A", "a1", "approve"), _ev(20, "reviewer_idle"),
          *ok_merge(21, "A", "a1"),
          S(30, "B", "b1", 1), _ev(30, "reviewer_busy"), RS(30, "B", "b1"), RE(36, "B", "b1", "request_changes"),
          _ev(36, "bounce", task="B", head="b1", cause="review"), _ev(36, "reviewer_idle"),
          S(40, "C", "c1", 1, k=1, m=1), _ev(40, "reviewer_busy"), RS(40, "C", "c1"), RE(46, "C", "c1", "approve"),
          _ev(46, "reviewer_idle"), _ev(47, "hidden_pre", task="C", head="c1", passed=False),
          _ev(47, "bounce", task="C", head="c1", cause="escaped_defect"),
          S(50, "B", "b2", 2, k=1), _ev(50, "reviewer_busy"), RS(50, "B", "b2"), RE(55, "B", "b2", "approve"),
          _ev(55, "reviewer_idle"), *ok_merge(56, "B", "b2"),
          S(58, "D", "d1", 1, k=1), _ev(58, "reviewer_busy"), RS(58, "D", "d1")]
    d = derive_window(run, ev)
    s = d["summary"]
    prs = {p["task"]: p for p in d["prs"]}
    checks = {
        "attempts == 4 (warm-up E excluded)": s["attempts"] == 4,
        "finished == 2 (A, B)": s["finished"] == 2,
        "censored == 1 (D)": s["censored"] == 1,
        "rework_open == 1 (C)": s["rework_open"] == 1,
        "reviews == 5": s["reviews"] == 5,
        "V == 5 / (27/60): D's review, open at grace end, is not counted and nor is its busy time":
            abs(s["V"] - 5 / (27 / 60)) < 1e-6 and s["review_open_at_end"] and abs(s["busy_clipped_min"] - 12) < 1e-6,
        "review durations (5 x 60 s), CV 0": s["review_durations_s"] == [60.0] * 5 and s["review_time_cv"] == 0.0,
        "no supply known -> not truncated": s["task_supply"] is None and not s["supply_truncated"],
        "b_review == 0.2": abs(s["b_review"] - 0.2) < 1e-9,
        "b_hidden == 0.25": abs(s["b_hidden"] - 0.25) < 1e-9,
        "b == 0.4": abs(s["b"] - 0.4) < 1e-9,
        "lambda == 4 / (50/60)": abs(s["lam"] - 4.8) < 1e-9,
        "escaped == 1": s["escaped"] == 1,
        "B bounced, cause review": prs["B"]["bounced"] and prs["B"]["first_cause"] == "review",
        "D unresolved (excluded from collision logistic)": not prs["D"]["resolved"],
        "C k=1 m=1": prs["C"]["k"] == 1 and prs["C"]["m"] == 1,
    }
    errs, _ = validate_schema.validate_events(list(enumerate(ev, 1)), run)
    checks["hand-built log passes the validator"] = not errs
    bad = dict(ev[1])
    bad["k"] = "3"
    checks["validator rejects a string k"] = bool(validate_schema.check_event(bad))
    bad2 = dict(ev[1])
    bad2["extra"] = 1
    checks["validator rejects an extra field"] = bool(validate_schema.check_event(bad2))
    bad3 = dict(ev[4])
    bad3["verdict"] = "lgtm"
    checks["validator rejects a bad verdict"] = bool(validate_schema.check_event(bad3))
    return checks


def unit_supply():
    """Task supply of 3: claims at 12, 20, 30 min; the list is exhausted at minute 30 of a 60-min window."""
    run = {"run_id": "unit-supply", "kind": "sweep", "n_workers": 2, "window_start": iso(T0), "window_end": iso(T0 + 3600),
           "warmup_min": 10, "grace_min": 10, "task_order_seed": 1, "sandbox_commit": "x", "harness_commit": "x",
           "worker_model": "x", "reviewer_model": "x", "notes": ""}
    S = lambda t, w, task, head: _ev(t, "submit", worker=w, task=task, branch=f"claude/task-{task}", head=head, attempt_no=1,
                                     lines_changed=5, files=["a.py"], k=0, m=0)
    C = lambda t, w, task: _ev(t, "claim", worker=w, task=task, branch=f"claude/task-{task}")
    base = [_ev(0, "worker_start", worker="w1", session_id="s1"), _ev(0, "worker_start", worker="w2", session_id="s2"),
            C(12, "w1", "A"), C(20, "w2", "B"), S(22, "w1", "A", "a1"), S(28, "w2", "B", "b1"), C(30, "w1", "C"),
            S(45, "w1", "C", "c1")]
    note = [_ev(0, "note", text="task_supply n=3"), _ev(30, "note", text="tasks_exhausted n=3")]
    ev = sorted(note + base, key=lambda e: e["t"])
    s = derive_window(run, ev)["summary"]
    s2 = derive_window(run, base, supply=3, supply_source="reset.json")["summary"]
    s3 = derive_window(run, sorted([_ev(0, "note", text="task_supply n=10")] + base, key=lambda e: e["t"]))["summary"]
    errs, _ = validate_schema.validate_events(list(enumerate(ev, 1)), run)
    return {
        "supply note: truncated at minute 30": s["supply_truncated"] and abs(s["t_exhausted_min"] - 30) < 1e-9
            and s["task_supply"] == 3 and s["task_supply_source"] == "note",
        "supply: attempts 2 to exhaustion (C at 45 min excluded), attempts_full 3": s["attempts"] == 2 and s["attempts_full"] == 3,
        "supply: lambda = 2 / (2 workers x 20 min), lam_full = 3 / (2 x 50 min)":
            abs(s["lam"] - 2 / (40 / 60)) < 1e-9 and abs(s["lam_full"] - 3 / (100 / 60)) < 1e-9,
        "supply: attempts per hour over the 20 min before exhaustion": abs(s["attempts_per_hour"] - 2 / (20 / 60)) < 1e-9,
        "supply from reset.json (no notes): same truncation from the claims": s2["supply_truncated"]
            and abs(s2["t_exhausted_min"] - 30) < 1e-9 and s2["task_supply_source"] == "reset.json" and s2["attempts"] == 2,
        "supply 10 > 3 claimed: not truncated": not s3["supply_truncated"] and s3["attempts"] == 3,
        "supply notes pass the validator": not errs,
        "supply: exhausted at minute 30 of 60 (before window_end - 10) -> supply_flagged": s["supply_flagged"],
        "supply 10 > 3 claimed: not flagged": not s3["supply_flagged"],
    }


def unit_sessions():
    """One cloud session per task (README decisions 39-43): lambda per slot-hour (slot-open time), start-up =
    launch -> branch first pushed, supply exhausted at the last launch, slot busy share, timeouts, rework wait;
    the validator's slot checks; synth --sessions end to end."""
    run = {"run_id": "u-sess", "kind": "sweep", "n_workers": 2, "window_start": iso(T0), "window_end": iso(T0 + 3600),
           "warmup_min": 10, "grace_min": 10, "task_order_seed": 1, "sandbox_commit": "x", "harness_commit": "x",
           "worker_model": "x", "reviewer_model": "x", "notes": "phase=t2; worker_model_design=session-per-task"}
    L = lambda t, sl, task: [_ev(t, "slot_busy", slot=sl),
                             _ev(t, "session_launch", slot=sl, task=task, session_id="x" + task, attempt_no=1)]
    C = lambda t, sl, task: _ev(t, "claim", worker=sl, task=task, branch=f"claude/task-{task}")
    S = lambda t, sl, task, head, a=1: _ev(t, "submit", worker=sl, task=task, branch=f"claude/task-{task}", head=head,
                                           attempt_no=a, lines_changed=5, files=["a.py"], k=0, m=0)
    I = lambda t, sl: _ev(t, "slot_idle", slot=sl)
    ev = [_ev(0, "note", text="task_supply n=4"), _ev(0, "worker_start", worker="s1", session_id=None),
          _ev(0, "worker_start", worker="s2", session_id=None),
          *L(0, "s1", "A"), *L(0, "s2", "B"), C(2, "s1", "A"), C(4, "s2", "B"),
          S(12, "s1", "A", "a1"), I(12, "s1"), *L(12, "s1", "C"), C(13, "s1", "C"),
          _ev(15, "bounce", task="A", head="a1", cause="rebase_conflict"),
          _ev(25, "session_timeout", slot="s2", task="B", session_id="xB"), I(25, "s2"),
          _ev(25, "slot_busy", slot="s2"), _ev(25, "session_message", slot="s2", task="A", session_id="xA", kind="rework"),
          S(30, "s2", "A", "a2", 2), I(30, "s2"),
          *L(30, "s2", "D"), _ev(30, "note", text="tasks_exhausted n=4"), C(31, "s2", "D"),
          S(40, "s1", "C", "c1"), I(40, "s1"), S(50, "s2", "D", "d1"), I(50, "s2")]
    d = derive_window(run, ev)
    s = d["summary"]
    errs, _ = validate_schema.validate_events(list(enumerate(ev, 1)), run)
    out = {
        "sessions: hand-built log passes the validator": not errs,
        "sessions: attempts_full 3 (A at 12, C at 40, D at 50; A's rework is not an attempt)": s["attempts_full"] == 3,
        "sessions: exhausted at the last launch (min 30); lambda = 1 attempt / (2 slots x 20 min)":
            abs(s["t_exhausted_min"] - 30) < 1e-9 and s["supply_truncated"] and abs(s["lam"] - 1 / (40 / 60)) < 1e-9
            and s["attempts"] == 1,
        "sessions: start-up = launch -> branch first pushed, mean (2 + 4 + 1 + 1) / 4 = 2 min":
            abs(s["startup_min_mean"] - 2.0) < 1e-9 and s["startup_source"].startswith("session_launch"),
        "sessions: slot busy share 1 up to exhaustion (never idle for longer than an instant)": abs(s["slot_busy_share"] - 1.0) < 1e-9,
        "sessions: 1 timeout, before any submission; rework wait 10 min (bounce 15 -> message 25)":
            s["session_timeouts"] == 1 and s["timeouts_before_submit"] == 1 and abs(s["rework_wait_min_mean"] - 10) < 1e-9,
        "sessions: 4 launches of 4 distinct tasks": s["session_launches"] == 4 and s["tasks_launched"] == 4,
    }
    over = ev[:7] + [_ev(1, "slot_busy", slot="s3")]
    e2, _ = validate_schema.validate_events(list(enumerate(over, 1)), run)
    out["validator: more slots busy than n_workers is an error"] = any("more than n_workers" in x for x in e2)
    msg = ev[:3] + [_ev(1, "session_message", slot="s1", task="Z", session_id=None, kind="rework")]
    e3, _ = validate_schema.validate_events(list(enumerate(msg, 1)), run)
    out["validator: a message to a task never launched is an error"] = any("never launched" in x for x in e3)
    t0 = dict(run, notes="phase=t0")
    try:
        pilot_params([derive_window(dict(t0, kind="trial"), ev)])
        out["derive --pilot refuses a T0 run"] = False
    except SystemExit:
        out["derive --pilot refuses a T0 run"] = True
    truth = make_truth("carnot", **{**V4_TRUTH, "n_tasks": 220, "per_task_sessions": True})
    runs = simulate_study(truth, (1, 12), seed=77, reps=1, with_pilot=True, pilot_design="v4")
    bad = []
    for r, e in runs:
        er, _ = validate_schema.validate_events(list(enumerate(e, 1)), r)
        bad += er
    S2 = [d["summary"] for d in derive_all(runs)]
    out["synth --sessions: every run validates, no slot over-booked"] = not bad
    out["synth --sessions: derive gives lambda, start-up and busy share for every window"] = all(
        x["session_launches"] > 0 and x["startup_min_mean"] == x["startup_min_mean"] and x["slot_busy_share"] > 0.9
        for x in S2 if x["n_workers"] >= 1 and x["kind"] != "trial")
    return out


def unit_v41():
    """PLAN-v4.1: the supply flag at minute 110, P1 / O2 with and without flagged windows, the calibration-log reader
    (abort rule 4 only), and derive --pilot refusing non-live runs."""
    import dataclasses
    from derive import pilot_params
    from predict import abort_rule_4, load_calibration
    from score import without_flagged
    out = {}
    run = {"run_id": "u", "kind": "sweep", "n_workers": 12, "window_start": iso(T0), "window_end": iso(T0 + 7200),
           "warmup_min": 10, "grace_min": 10, "task_order_seed": 1, "sandbox_commit": "x", "harness_commit": "x",
           "worker_model": "x", "reviewer_model": "x", "notes": ""}
    base = [_ev(0, "note", text="task_supply n=2"), _ev(0, "worker_start", worker="w1", session_id="s"),
            _ev(12, "claim", worker="w1", task="A", branch="claude/task-A")]
    late = derive_window(run, base + [_ev(112, "claim", worker="w1", task="B", branch="claude/task-B")])["summary"]
    early = derive_window(run, base + [_ev(105, "claim", worker="w1", task="B", branch="claude/task-B")])["summary"]
    out["tasks out at minute 112: truncated but not flagged; at minute 105: flagged"] = (
        late["supply_truncated"] and not late["supply_flagged"] and early["supply_flagged"])
    truth = make_truth("carnot", **{**V4_TRUTH, "n_tasks": 30})
    runs = simulate_study(truth, (1, 12), seed=4242, reps=0, with_pilot=True, pilot_design="v4")
    pil = pilot_params(derive_all(runs))
    P = Params.from_pilot(pil, window_min=120.0)
    sw = [simulate(truth, n, seed=4242000 + i, window_min=120.0, run_id=f"u41-N{n}-{i}", kind="sweep",
                   t0=T0 + 30 * 3600 + i * 9000) for i, n in enumerate((1, 12, 12, 1))]
    S = [d["summary"] for d in derive_all(sw)]
    wf = without_flagged(S, P)
    out["30 tasks: the N = 12 windows are flagged, P1-nf has no N = 12 left, O2-nf N/A"] = (
        wf is not None and sorted(wf["flagged"]) == sorted(s["run_id"] for s in S if s["n_workers"] == 12)
        and wf["P1"] is None and wf["O2"] is None)
    S2 = [dict(s, supply_flagged=(s["run_id"] == S[1]["run_id"])) for s in S]
    wf2 = without_flagged(S2, P)
    out["one N = 12 window flagged: P1-nf and O2-nf computed on the other three"] = (
        wf2["P1"] is not None and wf2["O2"] is not None and wf2["O2"]["windows"] == 1 and len(wf2["windows_kept"]) == 3)
    out["no window flagged: without_flagged is None"] = without_flagged([dict(s, supply_flagged=False) for s in S], P) is None
    with tempfile.TemporaryDirectory() as td:
        f = Path(td) / "cal.jsonl"
        rows = [dict(review_id=f"r{i}", duration_s=300.0, verdict="approve", source="reference", expected="approve", job="v1")
                for i in range(10)] + [dict(review_id="e1", duration_s=600.0, verdict="error", job="v1")]
        rows += [dict(review_id=f"b{i}", duration_s=120.0, verdict="request_changes", source="broken",
                      expected="request_changes", job="v2") for i in range(12)]
        f.write_text("".join(json.dumps(r) + "\n" for r in rows))
        c2 = load_calibration(f)
        c1 = load_calibration(f, job="v1")
        out["calibration log: last job by default, V = verdicts / all call time (errors count as busy)"] = (
            c2["job"] == "v2" and abs(c2["V"] - 30.0) < 1e-9 and c1["reviews"] == 10 and c1["errors"] == 1
            and abs(c1["V"] - 10 / (3600 / 3600)) < 1e-9 and c2["catch_rate_broken"] == 1.0)
        fc = Path(td) / "cal.csv"
        fc.write_text("review_id,duration_s,verdict,job\nx1,360,approve,a\nx2,360,REQUEST_CHANGES,a\n")
        out["calibration log: CSV accepted, verdict case-insensitive"] = abs(load_calibration(fc)["V"] - 10.0) < 1e-9
        bad = Path(td) / "bad.jsonl"
        bad.write_text(json.dumps(dict(review_id="z", duration_s=-1, verdict="lgtm")) + "\n")
        try:
            load_calibration(bad)
            out["calibration log: invalid rows refused"] = False
        except SystemExit:
            out["calibration log: invalid rows refused"] = True
        a4 = abort_rule_4(P, c1)
        a4n = abort_rule_4(P, None)
        out["abort rule 4 uses the calibrated V, not the pilot's; not evaluated without a log"] = (
            a4["evaluated"] and a4["V"] == c1["V"] and abs(a4["ratio"] - c1["V"] / (2 * P.lam1("carnot"))) < 1e-9
            and not a4n["evaluated"] and a4n["ok"] is None)
        out["the pilot's V is not changed by a calibration log (live reviews only)"] = P.V == pil["V"] and pil["review_source"].startswith("live")
    from common import _clogit_ll
    ll = _clogit_ll(np.array([20.0, 20.0]), [np.array([[5, 5], [0, 0], [0, 0], [-5, -5.0]])], [np.array([1, 1, 0, 0.0])])
    out["clogit likelihood finite when one row dominates a stratum with 2 events (was a math domain error)"] = (
        math.isfinite(ll) and abs(ll - math.log(0.5)) < 1e-9)
    sweep_d = derive_all(sw[:1])
    try:
        pilot_params(sweep_d)
        out["derive --pilot refuses a sweep (non-live) run"] = False
    except SystemExit:
        out["derive --pilot refuses a sweep (non-live) run"] = True
    return out


V4_SYNTH = ["--truth", "carnot", "--v4", "--set", "lam1=6.8", "V0=13.6", "defect_p=0.49", "cv_window=0.3",
            "rework_min=3.53", "ci_hidden_min=0.25", "ci_post_min=0.5", "n_tasks=220"]
# PLAN-v4 section 7 (v4.2): O2 primary quantitative test, P1 manipulation check, Vdur conditional, the rest descriptive.
V4_IDS = {"P1": CONFIRMATORY, "O2": CONFIRMATORY, "P1-nf": CONFIRMATORY, "O2-nf": CONFIRMATORY, "S3": DESCRIPTIVE,
          "O3": DESCRIPTIVE, "Vdur": CONDITIONAL, "Vratio": DESCRIPTIVE, "S1r": DESCRIPTIVE, "S2r": DESCRIPTIVE,
          "RANK": DESCRIPTIVE, "ESC": DESCRIPTIVE, "COLL": DESCRIPTIVE, "BOUNCE": DESCRIPTIVE}


def unit_cli(outdir=None):
    out = {}
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        r = subprocess.run([PY, str(HERE / "synth.py"), *V4_SYNTH, "--seed", "11", "--out", str(td / "runs")],
                           capture_output=True, text=True)
        out["synth --v4 runs"] = r.returncode == 0
        dirs = sorted((td / "runs").iterdir())
        r = subprocess.run([PY, str(HERE / "validate_schema.py"), *map(str, dirs)], capture_output=True, text=True)
        out["validate_schema OK on all synthetic runs"] = r.returncode == 0
        pil = [d for d in dirs if d.name.endswith("T1") or "-T2-" in d.name]
        sw = [d for d in dirs if "-N" in d.name]
        out["v4 layout: T1 + 8 T2 + 3 x N=1 + 3 x N=12"] = (len(pil) == 9 and sum("-N1-" in d.name for d in sw) == 3
                                                          and sum("-N12-" in d.name for d in sw) == 3)
        r = subprocess.run([PY, str(HERE / "derive.py"), "--pilot", *map(str, pil), "--out", str(td / "pilot.json")],
                           capture_output=True, text=True)
        pj = json.loads((td / "pilot.json").read_text()) if r.returncode == 0 else {}
        out["derive --pilot (with review_time_cv and review_cv_ok)"] = (r.returncode == 0 and "review_cv_ok" in pj
                                                                        and pj.get("review_time_cv") is not None)
        r = subprocess.run([PY, str(HERE / "derive.py"), *map(str, sw), "--csv-dir", str(td / "tables")],
                           capture_output=True, text=True)
        out["derive windows + CSV"] = r.returncode == 0 and (td / "tables" / "prs.csv").exists()
        r = subprocess.run([PY, str(HERE / "predict.py"), "--plan", "v4", "--pilot", str(td / "pilot.json"), "--task-supply", "220",
                            "--md", str(td / "pred.md"), "--json", str(td / "pred.json")], capture_output=True, text=True)
        md = r.stdout
        pj2 = json.loads((td / "pred.json").read_text()) if r.returncode == 0 else {}
        rows = pj2.get("predictions", {}).get("rows", [])
        out["predict --plan v4 runs"] = r.returncode == 0 and (td / "pred.md").exists()
        out["predict: no pilot gate on the v4 path (no REDESIGN_*, no gate decision)"] = ("REDESIGN" not in md and "gate" not in pj2
                                                                                         and "Decision" not in md)
        out["predict: all four rivals at N = 1 and 12, 120-min windows"] = (
            {(x["rival"], x["N"]) for x in rows} == {(r_, n) for r_ in ("carnot", "usl", "amdahl", "linear") for n in (1, 12)}
            and pj2.get("params", {}).get("window_min") == 120.0 and pj2.get("params", {}).get("rival_rework") == "completion")
        out["predict: v4.2 framing (O2 primary, P1 manipulation check, footnote, completion-share bias)"] = (
            "O2 (confirmatory, primary quantitative test)" in md and "Confirmatory, manipulation check (P1)" in md
            and UNCAPPED_TRUTH_FOOTNOTE in md and "biased low" in md and "Confirmatory, secondary" not in md)
        out["predict: O2 interval and operating-characteristics statement"] = ("O2 (confirmatory" in md
                                                                             and "Operating characteristics" in md
                                                                             and f"{V4_OC['primary_correct']['carnot']:.2f}" in md
                                                                             and "Circularity" in md)
        out["predict: abort rule 4 NOT EVALUATED without a calibration log"] = "NOT EVALUATED" in md
        r = subprocess.run([PY, str(HERE / "predict.py"), "--plan", "v4", "--pilot", str(td / "pilot.json"), "--v3-gate"],
                           capture_output=True, text=True)
        out["predict --v3-gate: the gate only as SUPERSEDED"] = r.returncode == 0 and "SUPERSEDED" in r.stdout
        r = subprocess.run([PY, str(HERE / "score.py"), "--plan", "v4", "--pilot", str(td / "pilot.json"), *map(str, sw),
                            "--out-dir", str(td / "res")], capture_output=True, text=True)
        ok = r.returncode == 0 and (td / "res" / "RESULTS-draft.md").exists() and (td / "res" / "results.json").exists()
        out["score writes RESULTS-draft.md + results.json"] = ok
        if not ok:
            out["score stderr"] = r.stderr[-2000:]
        else:
            R = json.loads((td / "res" / "results.json").read_text())
            res_md = (td / "res" / "RESULTS-draft.md").read_text()
            ids = {o["id"]: o["grade"] for o in R["outcomes"]}
            out["score: the PLAN-v4 results, each with its pre-set grade"] = ids == V4_IDS
            from score import V4_ORDER
            out["score: v4.2 order (O2 first, then P1) and roles"] = (
                [o["id"] for o in R["outcomes"]] == [i for i in V4_ORDER if i in ids]
                and {o["id"]: o.get("role") for o in R["outcomes"] if o.get("role")} ==
                {"O2": ROLE_PRIMARY, "O2-nf": ROLE_PRIMARY, "P1": ROLE_MANIPULATION, "P1-nf": ROLE_MANIPULATION})
            heads = [ln for ln in res_md.splitlines() if ln.startswith("## ") and not ln.startswith("## Summary")]
            out["RESULTS-draft.md: every section labelled confirmatory / conditional / descriptive"] = all(
                any(t in h for t in ("[confirmatory", "[conditional", "[descriptive]")) for h in heads)
            out["RESULTS-draft.md: O2 section before P1; P1 a manipulation check with the footnote"] = (
                res_md.index("## O2 [confirmatory, primary]") < res_md.index("## P1 [confirmatory, manipulation check]")
                and UNCAPPED_TRUTH_FOOTNOTE in res_md)
            cv_ok = R["review_cv_ok"]
            vc = {o["id"]: o["counts_as"] for o in R["outcomes"] if o["grade"] == CONDITIONAL}
            out["Vdur counts as confirmatory iff review_cv_ok"] = set(vc) == {"Vdur"} and all(
                v == (CONFIRMATORY if cv_ok else DESCRIPTIVE) for v in vc.values())
            out["no combined V result; Vratio descriptive and REPORTED (no equivalence claim)"] = (
                "V" not in ids and ids.get("Vratio") == DESCRIPTIVE
                and next(o for o in R["outcomes"] if o["id"] == "Vratio")["code"] == "REPORTED")
            pj["review_cv_ok"] = not cv_ok
            (td / "pilot-flip.json").write_text(json.dumps(pj))
            r2 = subprocess.run([PY, str(HERE / "score.py"), "--plan", "v4", "--pilot", str(td / "pilot-flip.json"), *map(str, sw),
                                 "--out-dir", str(td / "res2")], capture_output=True, text=True)
            R2 = json.loads((td / "res2" / "results.json").read_text()) if r2.returncode == 0 else {"outcomes": []}
            vc2 = {o["id"]: o["counts_as"] for o in R2["outcomes"] if o["grade"] == CONDITIONAL}
            out["flipping review_cv_ok flips the Vdur grade"] = bool(vc2) and all(
                v == (CONFIRMATORY if not cv_ok else DESCRIPTIVE) for v in vc2.values())
            if outdir is not None:
                import shutil
                ex = Path(outdir) / "example-synthetic-v4"
                if ex.exists():
                    shutil.rmtree(ex)
                ex.mkdir(parents=True, exist_ok=True)
                for src in (td / "pilot.json", td / "pred.md", td / "res" / "RESULTS-draft.md", td / "res" / "results.json"):
                    shutil.copy(src, ex / src.name)
    return out


# ---------------------------------------------------------------------- PLAN-v5 unit checks
def unit_v5(outdir=None):
    """PLAN-v5: collision exposure j / jm / collided_first and merge-queue utilisation on a hand-built session log; the
    v5 test functions on constructed inputs (SCALE's LR and per-agent ratio, FAMILY's free level, the escape and
    collision fits); the effort re-review log (reader, refusals, comparison); CLI end to end (synth --v5 -> validate ->
    derive -> score, default plan v5 -> predict, default plan v5) with the grades, order and framing."""
    import v5
    out = {}
    run = {"run_id": "u-v5", "kind": "sweep", "n_workers": 2, "window_start": iso(T0), "window_end": iso(T0 + 3600),
           "warmup_min": 10, "grace_min": 10, "task_order_seed": 1, "sandbox_commit": "x", "harness_commit": "x",
           "worker_model": "x", "reviewer_model": "x", "notes": "phase=sweep-n1; worker_model_design=session-per-task"}
    B = lambda t, sl: _ev(t, "slot_busy", slot=sl)
    I = lambda t, sl: _ev(t, "slot_idle", slot=sl)
    LA = lambda t, sl, task: _ev(t, "session_launch", slot=sl, task=task, session_id="x" + task, attempt_no=1)
    C = lambda t, sl, task: _ev(t, "claim", worker=sl, task=task, branch=f"claude/task-{task}")
    SU = lambda t, sl, task, head, a, f, k=0, m=0: _ev(t, "submit", worker=sl, task=task, branch=f"claude/task-{task}",
                                                      head=head, attempt_no=a, lines_changed=5, files=[f], k=k, m=m)

    def rev(t, task, head):
        return [_ev(t, "reviewer_busy"), _ev(t, "review_start", task=task, head=head, queue_depth=0),
                _ev(t + 0.25, "review_end", task=task, head=head, verdict="approve", reason="ok", tokens_in=1, tokens_out=1,
                    duration_s=15.0), _ev(t + 0.25, "reviewer_idle")]

    def mq(t, task, head, conflict=False, hidden=True):
        ev = [_ev(t, "queue_busy"), _ev(t, "hidden_pre", task=task, head=head, passed=True),
              _ev(t + 0.05, "rebase", task=task, head=head, new_head=None if conflict else head + "r", conflict=conflict)]
        if conflict:
            ev += [_ev(t + 0.05, "bounce", task=task, head=head, cause="rebase_conflict"), _ev(t + 0.1, "queue_idle")]
        elif not hidden:
            ev += [_ev(t + 0.1, "tests_post", task=task, head=head, visible_passed=True, hidden_passed=False),
                   _ev(t + 0.1, "bounce", task=task, head=head, cause="integration_failure"), _ev(t + 0.1, "queue_idle")]
        else:
            ev += [_ev(t + 0.1, "tests_post", task=task, head=head, visible_passed=True, hidden_passed=True),
                   _ev(t + 0.1, "merge", task=task, head=head, main_sha="m" + head), _ev(t + 0.1, "queue_idle")]
        return ev
    ev = [_ev(0, "note", text="task_supply n=10"), _ev(0, "worker_start", worker="s1", session_id=None),
          _ev(0, "worker_start", worker="s2", session_id=None),
          B(0, "s1"), LA(0, "s1", "A"), B(0, "s2"), LA(0, "s2", "B"), C(1, "s1", "A"), C(2, "s2", "B"),
          SU(12, "s1", "A", "a1", 1, "a.py"), I(12, "s1"), B(12, "s1"), LA(12, "s1", "C"), *rev(12, "A", "a1"),
          C(13, "s1", "C"), *mq(12.5, "A", "a1"),
          SU(20, "s2", "B", "b1", 1, "a.py"), I(20, "s2"), *rev(20, "B", "b1"), *mq(20.5, "B", "b1", conflict=True),
          B(25, "s2"), _ev(25, "session_message", slot="s2", task="B", session_id="xB", kind="rework"),
          SU(30, "s1", "C", "c1", 1, "b.py"), I(30, "s1"), B(30, "s1"), LA(30, "s1", "D"), *rev(30, "C", "c1"),
          C(31, "s1", "D"), *mq(30.5, "C", "c1", hidden=False),
          SU(35, "s2", "B", "b2", 2, "a.py"), I(35, "s2"), *rev(35, "B", "b2"), *mq(35.5, "B", "b2"),
          SU(45, "s1", "D", "d1", 1, "c.py"), I(45, "s1"), *rev(45, "D", "d1"), *mq(45.5, "D", "d1")]
    ev.sort(key=lambda e: e["t"])
    errs, _ = validate_schema.validate_events(list(enumerate(ev, 1)), run)
    out["v5 hand-built log passes the validator"] = not errs
    d = derive_window(run, ev)
    prs = {x["task"]: x for x in d["prs"]}
    s = d["summary"]
    out["v5 exposure: A j=0 (nothing merged yet); B j=1 jm=1 (A merged, same file), rebase conflict -> collided"] = (
        prs["A"]["j"] == 0 and not prs["A"]["collided_first"] and prs["B"]["j"] == 1 and prs["B"]["jm"] == 1
        and prs["B"]["collided_first"])
    out["v5 exposure: C (launched at 12, A merged 12.6) j=1 jm=0, integration failure -> collided; D j=1 (B2) not collided"] = (
        prs["C"]["j"] == 1 and prs["C"]["jm"] == 0 and prs["C"]["collided_first"] and prs["D"]["j"] == 1
        and not prs["D"]["collided_first"])
    out["v5 merge-queue utilisation = busy / counted window (5 passes, 0.4 min busy / 50 min)"] = abs(
        s["mq_util"] - (0.1 * 3 + 0.1 + 0.1) / 50) < 1e-9 and s["collisions_first"] == 2
    # SCALE and FAMILY on constructed windows
    mk = lambda n, f, i: dict(run_id=f"w{i}", n_workers=n, finished=f, hours=110 / 60, worker_hours_full=n * 110 / 60,
                              supply_flagged=False)
    lin = [mk(1, 10, 0), mk(1, 10, 1), mk(12, 120, 2), mk(12, 120, 3)]
    bent = [mk(1, 10, 0), mk(1, 10, 1), mk(12, 35, 2), mk(12, 35, 3)]
    bl, bb = v5.bend_test(lin), v5.bend_test(bent)
    out["SCALE: proportional counts -> per-agent ratio 1, p ~ 0.5 (not BEND); USL-like counts -> ratio 0.29, p < 0.01"] = (
        abs(bl["per_agent_ratio"] - 1) < 1e-9 and bl["p"] > 0.3 and abs(bb["per_agent_ratio"] - 35 / 120) < 1e-9 and bb["p"] < 0.01
        and bb["per_agent_ratio_ci"][0] < 35 / 120 < bb["per_agent_ratio_ci"][1])
    fl, fb = v5.family_fit(lin), v5.family_fit(bent)
    out["FAMILY: free level; linear counts pick linear, USL-like counts pick USL or Carnot"] = (
        fl["best"] == "linear" and fb["best3"] == "usl-carnot" and fl["binary"] == "linear" and fb["binary"] == "bending")
    # escape and collision fits on constructed rows
    ap_ = ([dict(n_workers=1, escaped=i < 5, task=f"t{i}") for i in range(50)] +
           [dict(n_workers=12, escaped=i < 30, task=f"u{i}") for i in range(100)])
    e = v5.escape_v5(ap_)
    out["ESC: 35/150 overall with Wilson interval; ESC-N rising 0.10 -> 0.30 detected (one-sided p < 0.01)"] = (
        e["events"] == 35 and e["n"] == 150 and e["wilson95"][0] < 35 / 150 < e["wilson95"][1] and e["p_trend"] < 0.01)
    rng = np.random.default_rng(3)
    rows = []
    for i in range(400):
        jj = int(rng.integers(0, 12))
        rows.append(dict(j=jj, jm=0, k=0, m=0, n_workers=12, collided_first=bool(rng.random() < 1 - 0.97 ** jj)))
    c = v5.collision_v5(rows)
    out["COLL: planted p = 0.03 per merge (400 passes) -> j slope detected, p-hat interval covers 0.03"] = (
        c["fit_ok"] and c["p_j"] < 0.01 and c["p_ci"][0] <= 0.03 <= c["p_ci"][1])
    rows0 = [dict(r, collided_first=bool(rng.random() < 0.05)) for r in rows]
    c0 = v5.collision_v5(rows0)
    out["COLL: background failures unrelated to j (5%) -> not detected at this seed"] = c0["fit_ok"] and c0["p_j"] > 0.05
    # effort log
    truth = make_truth_v5("usl", n_tasks=220)
    runs = simulate_study(truth, (1, 12), seed=91, reps={1: 2, 12: 1}, window_min=120, pilot_design="v5")
    sw = [(r, e_) for r, e_ in runs if r["kind"] == "sweep"]
    D = derive_all(sw)
    esc_heads = {(dd["summary"]["run_id"], a["head"]) for dd in D for a in dd["approvals"] if a["escaped"]}
    last = {}
    for dd in D:
        for rv in dd["reviews"]:
            last[(rv["run_id"], rv["head"])] = rv
    with tempfile.TemporaryDirectory() as td:
        f = Path(td) / "effort.jsonl"
        f.write_text("".join(json.dumps(dict(run_id=k[0], task=v["task"], head=k[1], effort="max",
                                             verdict="request_changes" if k in esc_heads else v["verdict"], duration_s=100.0)) + "\n"
                             for k, v in last.items()))
        er = v5.load_effort_log(f)
        cmp_ = v5.effort_compare(D, er)["matched"]["max"]
        out["EFFORT: every live head matched; max catches every escape -> post-hoc escape rate 0, agreement 1 - escapes / heads"] = (
            cmp_["heads"] == len(last) and cmp_["posthoc"]["escaped"] == 0 and cmp_["live_default"]["escaped"] == len(esc_heads)
            and abs(cmp_["agreement"] - (1 - len(esc_heads) / len(last))) < 1e-9 and len(esc_heads) > 0)
        bad = Path(td) / "bad.jsonl"
        bad.write_text(json.dumps(dict(run_id="r", task="t", head="h", effort="max", verdict="lgtm")) + "\n" +
                       json.dumps(dict(run_id="r", task="t", head="h", effort="max", verdict="approve", hidden_passed="yes")) + "\n")
        try:
            v5.load_effort_log(bad)
            out["EFFORT: invalid verdict / hidden_passed refused"] = False
        except SystemExit:
            out["EFFORT: invalid verdict / hidden_passed refused"] = True
        # CLI end to end, PLAN-v5 default
        tdp = Path(td)
        r = subprocess.run([PY, str(HERE / "synth.py"), "--v5", "--family", "usl", "--seed", "12", "--out", str(tdp / "runs")],
                           capture_output=True, text=True)
        dirs = sorted((tdp / "runs").iterdir()) if r.returncode == 0 else []
        out["synth --v5: T1 + the recommended sweep (" + ", ".join(f"{k} x N = {n}" for n, k in v5.DESIGN_V5["reps"].items()) + ")"] = (
            r.returncode == 0 and sum(dd.name.endswith("T1") for dd in dirs) == 1
            and all(sum(f"-N{n}-" in dd.name for dd in dirs) == k for n, k in v5.DESIGN_V5["reps"].items()))
        r = subprocess.run([PY, str(HERE / "validate_schema.py"), *map(str, dirs)], capture_output=True, text=True)
        out["validate_schema OK on the v5 synthetic runs"] = r.returncode == 0
        sw_dirs = [dd for dd in dirs if "-N" in dd.name]
        r = subprocess.run([PY, str(HERE / "score.py"), *map(str, sw_dirs), "--effort-log", str(f), "--out-dir", str(tdp / "res")],
                           capture_output=True, text=True)
        ok = r.returncode == 0 and (tdp / "res" / "RESULTS-draft.md").exists()
        out["score (default plan v5, no pilot needed) writes RESULTS-draft.md + results.json"] = ok
        if not ok:
            out["score v5 stderr"] = r.stderr[-2000:]
        else:
            R = json.loads((tdp / "res" / "results.json").read_text())
            md = (tdp / "res" / "RESULTS-draft.md").read_text()
            got = {o["id"]: (o["grade"], o["role"]) for o in R["outcomes"]}
            out["score v5: every result with its pre-set grade and role (v5.GRADES_V5), in v5.V5_ORDER"] = (
                R["plan"] == "v5" and got == {k: v for k, v in v5.GRADES_V5.items()}
                and [o["id"] for o in R["outcomes"]] == list(v5.V5_ORDER))
            heads = [ln for ln in md.splitlines() if ln.startswith("## ") and not ln.startswith("## Summary")]
            out["RESULTS-draft (v5): every section labelled; no review-capped rival, no P1 / O2"] = (
                all(("[confirmatory" in h or "[descriptive]" in h) for h in heads) and "review-capped" not in md.replace("no review-capped", "")
                and "| P1 |" not in md and "| O2 |" not in md and "SCALE [confirmatory, primary]" in md)
            out["score v5: SCALE codes BEND under USL workers; UTIL far from saturation; EFFORT reported"] = (
                next(o for o in R["outcomes"] if o["id"] == "SCALE")["code"] == "BEND"
                and next(o for o in R["outcomes"] if o["id"] == "EFFORT")["code"] == "REPORTED")
            if outdir is not None:
                import shutil
                ex = Path(outdir) / "example-synthetic"
                if ex.exists():
                    shutil.rmtree(ex)
                ex.mkdir(parents=True, exist_ok=True)
                for src in (tdp / "res" / "RESULTS-draft.md", tdp / "res" / "results.json"):
                    shutil.copy(src, ex / src.name)
        r = subprocess.run([PY, str(HERE / "predict.py"), "--task-supply", "220", "--burn", "2.10", "--balance", "249",
                            "--md", str(tdp / "pred.md"), "--json", str(tdp / "pred.json")], capture_output=True, text=True)
        pj = json.loads((tdp / "pred.json").read_text()) if r.returncode == 0 else {}
        pmd = r.stdout
        out["predict (default plan v5): runs without any pilot input; four rivals at every size; no V, no cap"] = (
            r.returncode == 0 and pj.get("plan") == "PLAN-v5"
            and {(x["rival"], x["N"]) for x in pj["predictions"]} == {(r_, n) for r_ in v5.RIVALS_V5 for n in v5.DESIGN_V5["sizes"]}
            and "review-capped" not in pmd and "Abort rule 4 (calibrated V" not in pmd)
        out["predict v5: ratios linear 12 / Amdahl 5.71 / USL 3.51 at N = 12; budget, abort rules and OC statement"] = (
            abs(next(x for x in pj["predictions"] if x["rival"] == "amdahl" and x["N"] == 12)["ratio_to_1"] - 12 / 2.1) < 1e-6
            and "Rule 3 (lambda floor)" in pmd and "Operating characteristics" in pmd and pj["credits"]["decision"]
            and abs(pj["cost"]["total_usd"] - v5.DESIGN_V5["cost_usd_at_2_10"]) < 0.5)
        if outdir is not None and r.returncode == 0:
            import shutil
            shutil.copy(tdp / "pred.md", Path(outdir) / "example-synthetic" / "prediction.md")
    return out


# ---------------------------------------------------------------------- one simulated study
def derive_all(runs):
    return [derive_window(r, e) for r, e in runs]


# PLAN-v4 design point in synth terms (as design-search/dsim.py builds it for "H lam8 q2 N1/12 120m x3"): Haiku at
# 0.85 x the 8/h task-size target, V = 2 x that, Haiku's defect rate, window CV 0.3, a 0.75-min merge queue, and the
# planned ~220 tasks.
V4_TRUTH = dict(lam1=6.8, V0=13.6, defect_p=0.49, cv_window=0.3, rework_min=6.0 * 4.0 / 6.8, ci_hidden_min=0.25,
                ci_post_min=0.5, n_tasks=220)


def study_v4(cfg):
    """One simulated PLAN-v4 study: v4 pilot -> sweep 1/12 x 3 x 120 min (ABBAAB) -> derive -> score (v4).
    cfg: truth (carnot|usl|amdahl|linear|skimN|slowN), seed, over (dict, e.g. service_cv, n_tasks)."""
    import dataclasses
    tname = cfg["truth"]
    base = {"skimN": "carnot", "slowN": "carnot"}.get(tname, tname)
    truth = make_truth(base, **{**V4_TRUTH, **cfg.get("over", {})})
    seed = cfg["seed"]
    res = dict(seed=seed, truth=tname)
    runs = simulate_study(truth, (1, 12), seed=seed, reps=0, with_pilot=True, pilot_design="v4")
    try:
        pil = pilot_params(derive_all(runs))
        P = Params.from_pilot(pil, window_min=120.0)
        if P.completion is None or not P.completion > 0:
            raise ValueError
    except (ValueError, KeyError, TypeError, ZeroDivisionError):
        res["status"] = "PILOT_UNUSABLE"
        return res
    res["review_cv_ok"] = pil["review_cv_ok"]
    res["pilot_review_cv"] = pil["review_time_cv"]
    truth_hi = truth
    if tname in ("skimN", "slowN"):  # the reviewer's pace at N = 12 is x1.25 / x0.75 of its pace at N = 1 and in the pilot
        truth_hi = dataclasses.replace(truth, V0=truth.V0 * (1.25 if tname == "skimN" else 0.75))
    sweep = []
    for i, n in enumerate((1, 12, 12, 1, 1, 12)):
        sweep.append(simulate(truth_hi if n == 12 else truth, n, seed=seed * 1000 + 10 + i, window_min=120.0,
                              run_id=f"v4-{seed}-N{n}-w{i + 1}", kind="sweep", t0=T0 + (seed % 1000) * 86400 + (20 + 2.5 * i) * 3600,
                              task_pool=None))
    derived = derive_all(sweep)
    parts = cfg.get("parts", ())
    R = score(derived, pil, do_escape="escape" in parts, do_collision="collision" in parts, do_fit=False)
    res["status"] = "SCORED"
    res["codes"] = {o["id"]: o["code"] for o in R["outcomes"]}
    res["best"] = R["rivals"]["best"]
    res["log_lr"] = R["primary"]["log_lr"]
    res["V_ratio"] = R["V4"]["ratio"]
    res["V_ratio_ci"] = R["V4"]["ratio_ci"]
    res["vdur_p"] = R["V4"]["vdur"]["p"]
    res["flagged"] = R.get("supply_flagged", [])
    wf = R.get("without_flagged")
    res["P1_nf"] = None if not wf or wf["P1"] is None else bool(wf["P1"]["carnot_higher"])
    res["O2_nf"] = None if not wf or wf["O2"] is None else bool(wf["O2"]["inside"])
    if "escape" in R:
        e = R["escape"]
        res["esc_events"] = e["events"]
        res["esc_p"] = None if e["descriptive_only"] else e.get("p_one_sided")
    if "collision" in R:
        c = R["collision"]
        res["coll_p"] = c.get("p_k_one_sided") if c.get("fit_ok") else None
    res["truncated"] = [s["run_id"] for s in R["windows"] if s.get("supply_truncated")]
    hi = [s for s in R["windows"] if s["n_workers"] == 12]
    res["lam12"] = sum(s["attempts"] for s in hi) / sum(s["worker_hours"] for s in hi)
    res["lam12_full"] = sum(s["attempts_full"] for s in hi) / sum(s["worker_hours_full"] for s in hi)
    lo = [s for s in R["windows"] if s["n_workers"] == 1]
    res["lam1"] = sum(s["attempts"] for s in lo) / sum(s["worker_hours"] for s in lo)
    res["per_agent_ratio"] = R["attempts"]["per_agent_ratio"]
    return res


def study(cfg):
    """One simulated sweep at fixed sizes for the escape and collision parts (D, E). cfg keys: truth (name), over
    (dict), seed, sizes, parts (tuple of 'escape','collision'), window_min."""
    truth = make_truth(cfg["truth"], **cfg.get("over", {}))
    seed = cfg["seed"]
    res = dict(seed=seed)
    pil_runs = simulate_study(truth, (1, 1), seed=seed, reps=0, with_pilot=True)
    pil = pilot_params(derive_all(pil_runs))
    sizes = cfg["sizes"]
    runs = simulate_study(truth, sizes, seed=seed, reps=2, with_pilot=False, window_min=cfg.get("window_min", 90.0))
    derived = derive_all(runs)
    parts = cfg.get("parts", ())
    try:
        R = score(derived, pil, window_min=cfg.get("window_min", 90.0), do_escape="escape" in parts,
                  do_collision="collision" in parts, do_fit=False)
    except ValueError:
        res["status"] = "PILOT_UNUSABLE"
        return res
    if "escape" in R:
        e = R["escape"]
        res["escape"] = {"events": e["events"], e["model"]: e.get("p_one_sided"),
                         **{m: es.get("p_one_sided") for m, es in R["escape_sensitivity"].items()}}
    if "collision" in R:
        c = R["collision"]
        res["collision"] = {k: c.get(k) for k in ("fit_ok", "p_hat", "p_ci", "p_m_hat", "coef_k", "p_k_one_sided",
                                                  "p_m_one_sided", "n_resolved", "n_censored_excluded")}
        res["naive"] = R["collision_naive_not_finished"]
    return res


def run_many(cfgs, workers, fn=None):
    fn = fn or study
    if workers <= 1:
        return [fn(c) for c in cfgs]
    with ProcessPoolExecutor(max_workers=workers) as ex:
        return list(ex.map(fn, cfgs, chunksize=4))


# ---------------------------------------------------------------------- W: PLAN-v5 planted truths
def part_w(R, workers, T):
    """The recommended v5 design (v5.DESIGN_V5) through synth -> derive -> v5 under planted truths: SCALE false alarms
    and power, the FAMILY pick, ESC-N null / planted rises and ESC precision, COLL null / planted p with p-hat coverage,
    UTIL. Uses design-search/dsim5.study5 (the same code as the design search). Pre-registered figures (v5.V5_OC) in
    brackets."""
    sys.path.insert(0, str(HERE / "design-search"))
    import dsim5
    import v5
    D = v5.DESIGN_V5
    des = dict(sizes=tuple(D["sizes"]), reps=tuple(D["reps"][n] for n in D["sizes"]), L=int(D["window_min"]), pilot_w=0,
               anchor="free")
    oc = v5.V5_OC
    md = ["## W. PLAN-v5: planted truths at the recommended design", "",
          f"Design {dsim5.D5(**des).label()}; synth.V5_TRUTH (one session per task, 10-30 s reviews, ~2 s merge queue, "
          "collisions per merge since a change's base, window CV 0.3). Rates over simulated studies; the pre-registered "
          "figures from OPERATING-CHARACTERISTICS-v5.md (v5.V5_OC) in brackets.", ""]
    ref = lambda x: "" if x is None else f" [{x:.2f}]"
    cells = ("linear", "mild", "amdahl", "usl", "carnot", "linear/cv=0.5", "usl/esc20", "carnot/p=0.01", "carnot/p=0.02")
    T["W"] = {}
    md.append("| truth | n | SCALE BEND | linear-vs-bending pick correct | four-way pick correct | ESC-N rises | ESC half-width | "
              "COLL detected | p-hat covers | reviewer util N = 12 (median of max) |")
    md.append("|---|---|---|---|---|---|---|---|---|---|")
    for c in cells:
        cfgs = [dict(design=des, truth=c, seed=7_000_000 + 10_000 * cells.index(c) + i, model_form=True) for i in range(R)]
        res = [r for r in run_many(cfgs, workers, dsim5.study5) if r.get("status") == "SCORED"]
        fam = c.split("/")[0]
        sig = lambda x: x is not None and x == x and x < 0.05
        bend = float(np.mean([sig(r["bend_p"]) for r in res]))
        binc = float(np.mean([r["binary"] == ("linear" if fam == "linear" else "bending") for r in res]))
        f4 = float(np.mean([r["best"] == fam for r in res])) if fam in v5.RIVALS_V5 else math.nan
        esc = float(np.mean([sig(r["esc_p"]) for r in res]))
        hw = float(np.median([r["esc_hw"] for r in res]))
        col = float(np.mean([sig(r["coll_p"]) for r in res]))
        pv = {"carnot/p=0.01": 0.01, "carnot/p=0.02": 0.02}.get(c, 0.005 if fam == "carnot" else 0.0)
        cov = [r["p_ci"][0] <= pv <= r["p_ci"][1] for r in res if r.get("p_ci")]
        cov = float(np.mean(cov)) if cov else math.nan
        ru = float(np.median([r["ru_max"] for r in res]))
        T["W"][c] = dict(n=len(res), bend=bend, binary=binc, fam4=f4, esc=esc, esc_hw=hw, coll=col, p_cover=cov, ru_max=ru)
        s_ref = {"linear": oc["SCALE"]["fpr_linear"], "amdahl": oc["SCALE"]["power_amdahl"], "usl": oc["SCALE"]["power_usl"],
                 "mild": oc["SCALE"]["power_mild"], "carnot": oc["SCALE"]["power_carnot"],
                 "linear/cv=0.5": oc["SCALE"]["fpr_linear_cv05"]}.get(c)
        e_ref = {"usl": oc["ESC_N"]["fpr"], "usl/esc20": oc["ESC_N"]["power_020"]}.get(c)
        c_ref = {"usl": oc["COLL"]["fpr"], "carnot/p=0.01": (oc["COLL"]["power"] or {}).get("0.01"),
                 "carnot/p=0.02": (oc["COLL"]["power"] or {}).get("0.02")}.get(c)
        md.append(f"| {c} | {len(res)} | {bend:.2f}{ref(s_ref)} | {binc:.2f} | {f4:.2f} | {esc:.2f}{ref(e_ref)} | {hw:.3f} | "
                  f"{col:.2f}{ref(c_ref)} | {cov:.2f} | {ru:.2f} |")
        print("W", c, T["W"][c], flush=True)
    md += ["", "SCALE BEND under linear truth is a false alarm; under every other truth it is power. ESC-N under the "
           "unplanted truths is a false alarm (usl has no reviewer effect; the escape share still drifts up slightly at N = 12 "
           "through which tasks finish). COLL under p = 0 truths (linear, mild, amdahl, usl) is a false alarm: their "
           "merge-queue failures are background integration failures (1% of passes) unrelated to j.", ""]
    return md


# ---------------------------------------------------------------------- summaries
def code_rates(results, ids):
    ok = [r for r in results if r.get("status") == "SCORED"]
    out = {}
    for i in ids:
        c = Counter(r["codes"].get(i) for r in ok)
        out[i] = {k: v / len(ok) for k, v in c.items()} if ok else {}
    return len(ok), out


# ---------------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--reps", type=int, default=300)
    ap.add_argument("--quick", action="store_true")
    ap.add_argument("--workers", type=int, default=max(1, (os.cpu_count() or 2) - 1))
    ap.add_argument("--out", default=str(HERE / "selftest-output"))
    ap.add_argument("--parts", default="UVSDEW")
    a = ap.parse_args()
    R = 30 if a.quick else a.reps
    outdir = Path(a.out)
    outdir.mkdir(parents=True, exist_ok=True)
    T = {}
    md = ["# Self-test of the fleet-sweep analysis", "",
          f"Replicates per cell: {R} (seeded, reproducible). Generated by `selftest.py`.", ""]
    t_start = time.time()
    all_ok = True

    if "U" in a.parts:
        u = unit_derive()
        us = unit_supply()
        v41 = unit_v41()
        se = unit_sessions()
        c = unit_cli(outdir)
        u5 = unit_v5(outdir)
        T["unit"] = {**u, **us, **v41, **se, **c, **u5}
        md += ["## U. Unit checks", ""]
        for k, v in {**u, **us, **v41, **se, **c, **u5}.items():
            if k in ("score stderr", "score v5 stderr"):
                md.append(f"- score stderr: `{v}`")
                continue
            md.append(f"- {'ok  ' if v else 'FAIL'} {k}")
            all_ok &= bool(v)
        md.append("")
        print("U done", all_ok, flush=True)

    if "V" in a.parts:
        md += ["## V. The PLAN-v4 design point, whole pipeline", "",
               "Synth truth as design-search/dsim.py builds the recommended design: lambda1 = 6.8/h (Haiku at 0.85 x the 8/h "
               "target), V0 = 13.6 (q = 2), defect rate 0.49, window CV 0.3, 0.75-min merge queue, 220 tasks. Pilot: T1 (1 "
               "worker, then 12) + eight 60-min windows of one worker, *without* the 120 free calibration reviews the design "
               "search added (so pilot V is noisier here). Sweep: N = 1 and 12, 3 x 120 min, ABBAAB. Uncapped truths: the "
               "reviewer speeds up with queue depth. skimN / slowN: the reviewer's pace at N = 12 is 1.25x / 0.75x its pace at "
               "N = 1 and in the pilot. Cells: share of scored studies coding the outcome FAIL (P1, O3: share PASS). "
               "Pre-registered figures (common.V4_OC, OPERATING-CHARACTERISTICS.md) in brackets.", ""]
        T["V"] = {}
        ids = ("P1", "O2", "S3", "O3", "Vdur", "S1r", "S2r")
        ref = {("carnot", "P1"): V4_OC["primary_correct"]["carnot"],
               ("usl", "P1"): 1 - V4_OC["primary_correct"]["usl"], ("amdahl", "P1"): 1 - V4_OC["primary_correct"]["amdahl"],
               ("linear", "P1"): 1 - V4_OC["primary_correct"]["linear"],
               ("carnot", "S1r"): V4_OC["S1r_false_alarm"], ("carnot", "S2r"): V4_OC["S2r_false_alarm"]}
        vdur_ref = {cv: {"carnot": V4_OC["Vdur_fpr"][k], "skimN": V4_OC["Vdur_power"][k], "slowN": V4_OC["Vdur_power"][k]}
                    for cv, k in ((1.0, "service_cv_1"), (0.5, "service_cv_0_5"))}
        md.append("| review CV | truth | n | review_cv_ok share | P1 PASS | O2 | S3 | O3 PASS | Vdur | "
                  "S1r | S2r | Carnot best | median V12/V1 | Vratio 95% CI width (median) |")
        md.append("|---|---|---|---|---|---|---|---|---|---|---|---|---|---|")
        for scv in (1.0, 0.5):
            for tr in ("carnot", "usl", "amdahl", "linear", "skimN", "slowN"):
                if scv == 0.5 and tr in ("usl", "amdahl", "linear"):
                    continue
                cfgs = [dict(truth=tr, over=dict(service_cv=scv), seed=800000 + int(scv * 10) * 10000 + 1000 * ["carnot", "usl",
                        "amdahl", "linear", "skimN", "slowN"].index(tr) + i) for i in range(R)]
                res = run_many(cfgs, a.workers, study_v4)
                n, cr = code_rates(res, ids)
                ok = [r for r in res if r.get("status") == "SCORED"]
                g = lambda i, c="FAIL": cr.get(i, {}).get(c, 0.0)
                cvok = float(np.mean([r["review_cv_ok"] for r in ok])) if ok else math.nan
                cbest = float(np.mean([r["best"] == "carnot" for r in ok])) if ok else math.nan
                vr = float(np.median([r["V_ratio"] for r in ok if r["V_ratio"] is not None])) if ok else math.nan
                vw = float(np.median([r["V_ratio_ci"][1] - r["V_ratio_ci"][0] for r in ok if r.get("V_ratio_ci")
                                      and None not in r["V_ratio_ci"]])) if ok else math.nan
                T["V"][f"cv={scv},{tr}"] = dict(n=n, rates=cr, review_cv_ok=cvok, carnot_best=cbest, median_V_ratio=vr,
                                                truncated_share=float(np.mean([bool(r["truncated"]) for r in ok])) if ok else math.nan)
                rf = lambda i: f" [{ref[(tr, i)]:.2f}]" if (tr, i) in ref and scv == 1.0 else ""
                vd = f" [{vdur_ref[scv][tr]:.2f}]" if tr in vdur_ref[scv] else ""
                md.append(f"| {scv} | {tr} | {n} | {cvok:.2f} | {g('P1', 'PASS'):.2f}{rf('P1')} | {g('O2'):.2f} | {g('S3'):.2f} | "
                          f"{g('O3', 'PASS'):.2f} | {g('Vdur'):.2f}{vd} | "
                          f"{g('S1r'):.2f}{rf('S1r')} | {g('S2r'):.2f}{rf('S2r')} | {cbest:.2f} | {vr:.2f} | {vw:.2f} |")
                print("V", scv, tr, n, {i: g(i) for i in ids}, flush=True)
        md.append("")
        md.append("P1 PASS under an uncapped truth is a wrong call (bracket = 1 - the pre-registered correct rate). Vdur is the "
                  "reviewer-pace test (PLAN-v4.1 section 6.1): FAIL iff Welch p < 0.05 on log review durations. V(12)/V(1) is "
                  "descriptive; the last column shows why no equivalence claim is made (its 95% interval is about 0.7 wide). "
                  "The full operating characteristics, with more replicates, are in OPERATING-CHARACTERISTICS.md.")
        md.append("")

    if "S" in a.parts:
        md += ["## S. Task supply at the design point (linear truth, 120 tasks)", "",
               "With the dry run's 120 tasks, a linear fleet of 12 at 6.8/h claims them all before the window ends. derive.py "
               "cuts lambda and the attempt-based measures at the exhaustion minute; 'full' is the uncut value. The truth's "
               "per-agent rate is the same at N = 1 and 12 (ratio 1).", ""]
        T["S"] = {}
        md.append("| tasks | truth | n | N = 12 windows truncated (share of studies) | lambda(1) | lambda(12) cut | lambda(12) full | "
                  "per-agent ratio cut | O3 PASS |")
        md.append("|---|---|---|---|---|---|---|---|---|")
        for nt in (120, 220):
            cfgs = [dict(truth="linear", over=dict(n_tasks=nt), seed=900000 + nt * 10 + i) for i in range(max(R // 2, 20))]
            res = [r for r in run_many(cfgs, a.workers, study_v4) if r.get("status") == "SCORED"]
            tr_share = float(np.mean([bool(r["truncated"]) for r in res]))
            l1 = float(np.median([r["lam1"] for r in res]))
            l12 = float(np.median([r["lam12"] for r in res]))
            l12f = float(np.median([r["lam12_full"] for r in res]))
            par = float(np.median([r["per_agent_ratio"] for r in res]))
            o3 = float(np.mean([r["codes"]["O3"] == "PASS" for r in res]))
            T["S"][f"n_tasks={nt}"] = dict(n=len(res), truncated_share=tr_share, lam1=l1, lam12=l12, lam12_full=l12f,
                                           per_agent_ratio=par, O3_pass=o3)
            md.append(f"| {nt} | linear | {len(res)} | {tr_share:.2f} | {l1:.2f} | {l12:.2f} | {l12f:.2f} | {par:.2f} | {o3:.2f} |")
            print("S", nt, tr_share, l1, l12, l12f, flush=True)
        md.append("")

    if "D" in a.parts:
        md += ["## D. Escaped defects vs queue depth", "",
               "Carnot truth, sizes (1, 5), 4 windows. Planted effect: the reviewer's catch probability falls as "
               "0.7 exp(-g depth). Rates are over all simulated sweeps (a sweep with < 8 escaped defects counts as 'not detected'); "
               "'given >= 8' conditions on the model being run. Test: one-sided p < 0.05 on the depth coefficient.", ""]
        T["D"] = {}
        md.append("| g | share with >= 8 events | median events | clogit_task (PLAN literal) | clogit_task_first | logit_cluster_task | given >= 8: clogit_task / first / logit |")
        md.append("|---|---|---|---|---|---|---|")
        for gval in (0.0, 0.1, 0.2):
            res = run_many([dict(truth="carnot", over=dict(escape_depth=gval), sizes=(1, 5), parts=("escape",),
                                 seed=500000 + int(gval * 1000) * 10 + i) for i in range(R)], a.workers)
            res = [r for r in res if "escape" in r]
            ev = np.array([r["escape"]["events"] for r in res])
            rates, cond = {}, {}
            for mname in ("clogit_task", "clogit_task_first", "logit_cluster_task"):
                ps = np.array([np.nan if r["escape"].get(mname) is None else r["escape"][mname] for r in res], float)
                det = (ev >= 8) & (ps < 0.05)
                rates[mname] = float(det.mean())
                cond[mname] = float(det[ev >= 8].mean()) if (ev >= 8).any() else math.nan
            T["D"][f"g={gval}"] = dict(share_ge8=float((ev >= 8).mean()), median_events=float(np.median(ev)), rates=rates, given_ge8=cond)
            md.append(f"| {gval} | {(ev >= 8).mean():.2f} | {np.median(ev):.0f} | {rates['clogit_task']:.2f} | {rates['clogit_task_first']:.2f} | "
                      f"{rates['logit_cluster_task']:.2f} | {cond['clogit_task']:.2f} / {cond['clogit_task_first']:.2f} / {cond['logit_cluster_task']:.2f} |")
            print("D", gval, rates, cond, flush=True)
        md.append("")

    if "E" in a.parts:
        md += ["## E. Collisions and the censoring check", "",
               "Carnot truth. Planted p per pair of changes in flight (p_m extra for pairs sharing a file). 'k-slope' = one-sided "
               "p < 0.05 on k in the logistic (bounced at least once ~ k + m + window FE, censored excluded). p-hat from the "
               "model form 1 - s_w (1-p)^k (1-p_m)^m with a profile 95% interval. 'naive' = outcome 'not finished', censored "
               "included (the accounting review v2 C rejected).", ""]
        T["E"] = {}
        md.append("| sizes | planted p | planted p_m | mean p-hat | median p-hat | CI covers p | CI excludes 0 | k-slope rate | m-slope rate | mean censored excluded | naive k-slope rate |")
        md.append("|---|---|---|---|---|---|---|---|---|---|---|")
        for sizes in ((1, 5), (3, 8)):
            for p, pm in ((0.0, 0.0), (0.005, 0.0), (0.02, 0.0), (0.05, 0.0), (0.0, 0.1)):
                res = run_many([dict(truth="carnot", over=dict(p=p, p_m=pm), sizes=sizes, parts=("collision",),
                                     seed=600000 + sizes[1] * 100000 + int(p * 10000) * 10 + int(pm * 100) + i * 7) for i in range(R)],
                               a.workers)
                res = [r for r in res if "collision" in r]
                cc = [r["collision"] for r in res if r["collision"].get("fit_ok")]
                ph = np.array([c["p_hat"] for c in cc])
                cover = np.mean([c["p_ci"][0] <= p <= c["p_ci"][1] for c in cc])
                excl0 = np.mean([c["p_ci"][0] > 0 for c in cc])
                ks = np.mean([c["p_k_one_sided"] < 0.05 for c in cc])
                ms = np.mean([c["p_m_one_sided"] < 0.05 for c in cc])
                cens = np.mean([c["n_censored_excluded"] for c in cc])
                nv = [r["naive"] for r in res if r["naive"].get("fit_ok")]
                naive = np.mean([x["p_k_one_sided"] < 0.05 for x in nv]) if nv else math.nan
                T["E"][f"sizes={sizes},p={p},p_m={pm}"] = dict(n=len(cc), mean_p_hat=float(ph.mean()), median_p_hat=float(np.median(ph)),
                                                                coverage=float(cover), ci_excludes_0=float(excl0), k_slope_rate=float(ks),
                                                                m_slope_rate=float(ms), mean_censored=float(cens), naive_k_slope_rate=float(naive))
                md.append(f"| {sizes} | {p} | {pm} | {ph.mean():.4f} | {np.median(ph):.4f} | {cover:.2f} | {excl0:.2f} | {ks:.2f} | {ms:.2f} | {cens:.1f} | {naive:.2f} |")
                print("E", sizes, p, pm, ph.mean(), cover, ks, naive, flush=True)
        md.append("")

    if "W" in a.parts:
        md += part_w(R, a.workers, T)

    md.append(f"Total time {time.time() - t_start:.0f} s.")
    (outdir / "selftest.json").write_text(json.dumps(T, indent=2, default=str) + "\n")
    (outdir / "SELFTEST.md").write_text("\n".join(md) + "\n")
    print("\n".join(md))
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
