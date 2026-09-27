#!/usr/bin/env python3
"""Derive per-window quantities from runs/<id>/run.json + events.jsonl (PLAN-v4; accounting of PLAN-v3 section 6).

    python derive.py runs/<id> [runs/<id> ...] --out derived.json [--csv-dir tables/]
    python derive.py --pilot runs/<T1> runs/<T2> ... --out pilot.json

Per window: attempts, reviews, V (reviews per reviewer-busy hour, all reviews), V per half-window,
review durations and their CV, b_review, b_hidden, b (by cause), lambda (first attempts per
worker-hour after warm-up), finished, censored, escaped defects, integration failures, the task-supply
check, and a per-PR table (first attempt: k, m, bounced at least once, first cause, queue depth at its
first review, time in window).

Definitions used (PLAN-v3 section 6, carried over by PLAN-v4; the choices where the text leaves room are
listed in README.md):

* Times are relative to window_start. The counting window for attempts and finished work is
  [window_start + warm-up, window_end]; the analysis sees events up to window_end + grace.
* Attempt: a task's first `submit` (attempt_no = 1). Counted if it falls in the counting window.
* Finished: a counted attempt whose task has a `merge` by window_end + grace and whose merged head
  passed hidden tests in `tests_post`.
* Status at the end for everything not finished: `rework_open` if the task's last submit/bounce is
  a bounce (sent back, not yet re-submitted), else `censored` (a head waiting for or in review or in
  the merge queue). Censored changes are neither finished nor rework.
* Review: a `review_end` by window_end + grace. Its queue depth is the queue_depth of the first
  `review_start` for that head (the hand-over; a retry after `review_error` does not reset it).
* V = reviews / reviewer-busy hours, busy time from reviewer_busy/reviewer_idle (clipped to
  [window_start, window_end + grace]); falls back to summed review durations if those events are absent.
  **Grace-end correction (PLAN-v4 prep, README decision 20):** a review still running at
  window_end + grace is not counted, so its elapsed time is not counted as busy time either: busy time
  is clipped at that review's first `review_start`. (Before, it counted as busy time but not as a
  review, biasing V low by up to one review per window.)
  Half-windows split at the window midpoint; the second half includes the grace period; a review
  belongs to the half in which it ended.
* Review-time CV = sample sd / mean of `duration_s` over the counted reviews. The pilot's value sets
  the pre-registered flag `review_cv_ok` (CV <= 0.5), which decides whether the V-constancy claim is
  confirmatory (PLAN-v4 section 1).
* b_review = review bounces / reviews. b_hidden = (escaped + integration failures) / approvals with a
  merge-queue outcome. b_other = (rebase conflicts + visible fails) / the same approvals. b = all
  bounces / reviews.
* **Task supply (README decision 19):** the supply is the number of tasks in the window's TASKS.json,
  read from the harness's `note` "task_supply n=<N>" or, failing that, the length of `task_order` in
  the run directory's reset.json. The exhaustion time is the harness's `note` "tasks_exhausted" or,
  failing that, the `claim` that brings the number of distinct claimed tasks to the supply. If it falls
  before window_end, the window is flagged `supply_truncated` and lambda and every attempt-based
  measure (attempts, attempts_per_hour, worker_hours, lam) use [warm-up end, exhaustion] only, so a
  supply shortfall cannot look like coordination drag. `attempts_full`, `worker_hours_full`,
  `lam_full` keep the whole counting window; finished / censored / rework_open are over the whole
  counting window (finished + censored + rework_open = attempts_full).
  **Flag (PLAN-v4.1 section 6.6, README decision 30):** a window whose tasks ran out more than 10 min
  before window_end (before minute 110 of a 120-min window) is also flagged `supply_flagged`; score.py
  reports P1 and O2 with and without the flagged windows.
* Merge-queue ("CI") time per change: from max(approval, previous change's queue exit) to its merge or
  queue bounce, FIFO.

**One cloud session per task (harness README "Worker model"; README decisions 39-43).** The harness runs N
*slots*; each runs one session at a time, and a session works on one task and its rework. `worker_start`
marks a slot opening; `slot_busy` / `slot_idle` mark a session occupying it; `session_launch`,
`session_message` (kind rework / probe) and `session_timeout` record the sessions. Accounting:

* worker-hours = **slot-open hours** (busy + idle) after warm-up, down-time excluded, as before; lambda =
  first submissions per slot-hour. A slot is idle only between a READY and the next hand-out (seconds) or
  when there is nothing left to hand out (supply exhaustion, which truncates the counting window anyway),
  so slot-open time is the fleet-size exposure the rivals' N x hours assumes. `slot_busy_hours` and
  `slot_busy_share` (busy / open, over the same counting window) are reported beside it.
* start-up time = a task's first `session_launch` -> its branch first seen (`claim`, the session's first
  push; the prompt tells it to push the branch before any work). Older logs: first claim - worker start.
* supply exhaustion = the end of the task list: the harness's `tasks_exhausted` note (logged when the last
  task is handed to a slot), else the `session_launch` that brings the distinct launched tasks to the supply,
  else (older logs) the claim that does.
* `session_timeouts` (no READY within the timeout: the task is abandoned, never re-launched), those before a
  first submission (`timeouts_before_submit`: work lost without an attempt), `rework_messages`, and
  `rework_wait_min_mean` (bounce -> the follow-up message reaching the session, i.e. waiting for a slot).
  Per PR, `abandoned` flags a task whose session timed out after its first submission.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import re
import statistics
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import BOUNCE_CAUSES, parse_t, rate_ci  # noqa: E402

MQ_CAUSES = ("rebase_conflict", "visible_fail", "escaped_defect", "integration_failure")
REVIEW_CV_MAX = 0.5   # PLAN-v4 section 1: V constancy is confirmatory only if the pilot's review-time CV <= 0.5
SUPPLY_FLAG_BEFORE_END_MIN = 10.0  # PLAN-v4.1 section 6.6: flagged if the tasks ran out before minute 110 of 120
SUPPLY_RE = re.compile(r"^task_supply\s+n=(\d+)")
EXHAUSTED_RE = re.compile(r"^tasks_exhausted\b")


def load_run(run_dir):
    d = Path(run_dir)
    run = json.loads((d / "run.json").read_text())
    events = [json.loads(line) for line in (d / "events.jsonl").read_text().splitlines() if line.strip()]
    return run, events


def _overlap(a0, a1, b0, b1):
    return max(0.0, min(a1, b1) - max(a0, b0))


def _cv(xs):
    xs = [float(x) for x in xs if x is not None and x > 0]
    if len(xs) < 2:
        return math.nan
    m = statistics.fmean(xs)
    return statistics.stdev(xs) / m if m > 0 else math.nan


def derive_window(run, events, supply=None, supply_source=None):
    """supply: number of tasks in the window's task list if known from outside the log (reset.json);
    a `task_supply` note in the log takes precedence."""
    ws = parse_t(run["window_start"])
    we = parse_t(run["window_end"]) - ws
    warm = 60.0 * float(run["warmup_min"])
    grace = 60.0 * float(run["grace_min"])
    end_all = we + grace
    hours = (we - warm) / 3600.0

    tasks = {}
    heads = {}
    open_reviews = {}
    reviews = []
    busy_iv = []
    busy_since = None
    have_busy_events = False
    wait_steps = []           # (t, +1/-1) waiting-for-review depth
    first_review_seen = set()
    hidden_pre = {}
    tests_post = {}
    merges = {}
    mq_outcome = {}
    worker_iv = defaultdict(list)
    worker_alive = {}
    restarts = defaultdict(int)
    downs = 0
    review_errors = 0
    claim_races = 0
    usage_tokens = defaultdict(float)
    usage_cost = 0.0
    first_claim = {}
    worker_start_t = {}
    claims = []               # (t, task) of every logged claim
    exhausted_note_t = None
    cur_review = None         # (t of first review_start, key) of the review the reviewer is on
    slot_busy_iv = defaultdict(list)
    slot_busy_since = {}
    launch_t = {}             # task -> first session_launch time
    launches = []             # (t, task)
    first_claim_task = {}     # task -> first claim (branch first pushed)
    timeouts = []             # (t, task)
    rework_msgs = []          # (t, task)

    def task_row(task):
        if task not in tasks:
            tasks[task] = dict(task=task, submits=[], bounces=[], reviews=[], merge_t=None, merge_head=None)
        return tasks[task]

    for e in events:
        t = parse_t(e["t"]) - ws
        typ = e["type"]
        if typ == "note":
            m = SUPPLY_RE.match(e.get("text", ""))
            if m:
                supply, supply_source = int(m.group(1)), "note"
            elif EXHAUSTED_RE.match(e.get("text", "")) and exhausted_note_t is None:
                exhausted_note_t = t
            continue
        if typ in ("worker_start", "worker_restart"):
            w = e["worker"]
            if typ == "worker_restart":
                restarts[w] += 1
            if w not in worker_alive:
                worker_alive[w] = max(t, 0.0)
                worker_start_t.setdefault(w, max(t, 0.0))
            continue
        if typ == "worker_down":
            w = e["worker"]
            downs += 1
            if w in worker_alive:
                worker_iv[w].append((worker_alive.pop(w), max(t, 0.0)))
            continue
        if typ == "usage":
            usage_tokens[e["worker"]] += (e.get("tokens_in") or 0) + (e.get("tokens_out") or 0)
            usage_cost += e.get("cost_usd_est") or 0.0
            continue
        if typ == "slot_busy":
            slot_busy_since.setdefault(e["slot"], max(t, 0.0))
            continue
        if typ == "slot_idle":
            if e["slot"] in slot_busy_since:
                slot_busy_iv[e["slot"]].append((slot_busy_since.pop(e["slot"]), max(t, 0.0)))
            continue
        if t > end_all:
            continue
        if typ == "session_launch":
            launch_t.setdefault(e["task"], t)
            launches.append((t, e["task"]))
            continue
        if typ == "session_timeout":
            timeouts.append((t, e["task"]))
            continue
        if typ == "session_message":
            if e["kind"] == "rework":
                rework_msgs.append((t, e["task"]))
            continue
        if typ == "claim":
            first_claim.setdefault(e["worker"], t)
            first_claim_task.setdefault(e["task"], t)
            claims.append((t, e["task"]))
        elif typ == "claim_race":
            claim_races += 1
        elif typ == "submit":
            if t > we:  # workers stop at window_end; a late submit is logged but not analysed
                continue
            row = task_row(e["task"])
            row.setdefault("worker", e["worker"])
            row["submits"].append(dict(t=t, head=e["head"], attempt_no=e["attempt_no"], k=e["k"], m=e["m"],
                                       lines=e["lines_changed"], files=e["files"]))
            heads[e["head"]] = (e["task"], e["attempt_no"])
            wait_steps.append((t, +1))
        elif typ == "review_start":
            key = (e["task"], e["head"])
            if key not in open_reviews:
                open_reviews[key] = (t, e["queue_depth"])
            if cur_review is None or cur_review[1] != key:
                cur_review = (t, key)
            if key not in first_review_seen and e["head"] in heads:
                first_review_seen.add(key)
                wait_steps.append((t, -1))
        elif typ == "review_error":
            review_errors += 1
        elif typ == "review_end":
            key = (e["task"], e["head"])
            t0, depth = open_reviews.pop(key, (t - e.get("duration_s", 0.0), None))
            rv = dict(task=e["task"], head=e["head"], attempt_no=heads.get(e["head"], (None, None))[1],
                      t_start=t0, t_end=t, depth=depth, verdict=e["verdict"], duration_s=e.get("duration_s"),
                      tokens=(e.get("tokens_in") or 0) + (e.get("tokens_out") or 0))
            reviews.append(rv)
            task_row(e["task"])["reviews"].append(rv)
            cur_review = None
        elif typ == "reviewer_busy":
            have_busy_events = True
            if busy_since is None:
                busy_since = t
        elif typ == "reviewer_idle":
            have_busy_events = True
            cur_review = None
            if busy_since is not None:
                busy_iv.append((busy_since, t))
                busy_since = None
        elif typ == "hidden_pre":
            hidden_pre[e["head"]] = (t, e["passed"])
        elif typ == "tests_post":
            tests_post[e["head"]] = (t, e["visible_passed"], e["hidden_passed"])
        elif typ == "bounce":
            task_row(e["task"])["bounces"].append(dict(t=t, head=e["head"], cause=e["cause"]))
            if e["cause"] in MQ_CAUSES:
                mq_outcome[e["head"]] = (t, e["cause"])
        elif typ == "merge":
            row = task_row(e["task"])
            if row["merge_t"] is None:
                row["merge_t"], row["merge_head"] = t, e["head"]
            merges[e["head"]] = t
            mq_outcome[e["head"]] = (t, "merge")

    if busy_since is not None:
        busy_iv.append((busy_since, end_all))
    for w, t0 in worker_alive.items():
        worker_iv[w].append((t0, we))
    for sl, t0 in slot_busy_since.items():
        slot_busy_iv[sl].append((t0, we))
    if not have_busy_events:
        busy_iv = [(r["t_start"], r["t_end"]) for r in reviews]
    busy_iv = [(max(0.0, a), min(end_all, b)) for a, b in busy_iv if b > 0 and a < end_all]
    # Grace-end correction: the review running at window_end + grace is not counted, so neither is its
    # elapsed time. Reviews are serial, so busy time after its first review_start belongs to it alone.
    open_at_end = cur_review is not None and cur_review[0] <= end_all
    busy_clipped_s = 0.0
    if open_at_end:
        cap = cur_review[0]
        before = sum(b - a for a, b in busy_iv)
        busy_iv = [(a, min(b, cap)) for a, b in busy_iv if min(b, cap) > a]
        busy_clipped_s = before - sum(b - a for a, b in busy_iv)

    # ------------------------------------------------------------------ task supply
    t_exhausted = None
    if supply is not None and supply > 0:
        seen = set()
        # session logs: the end of the list is the last hand-out (launch); older logs: the last claim
        for t, task in sorted(launches or claims, key=lambda x: x[0]):
            seen.add(task)
            if len(seen) >= supply:
                t_exhausted = t
                break
    if exhausted_note_t is not None:
        t_exhausted = exhausted_note_t if t_exhausted is None else min(t_exhausted, exhausted_note_t)
    truncated = t_exhausted is not None and t_exhausted < we
    t_count_end = max(warm, t_exhausted) if truncated else we   # attempt-based measures end here

    # ------------------------------------------------------------------ reviewer
    busy_h = sum(b - a for a, b in busy_iv) / 3600
    n_rev = len(reviews)
    V = n_rev / busy_h if busy_h > 0 else math.nan
    mid = we / 2
    halves = []
    for h0, h1 in ((0.0, mid), (mid, end_all)):
        n = sum(1 for r in reviews if (h0 <= r["t_end"] < h1) or (h1 == end_all and r["t_end"] == end_all))
        bh = sum(_overlap(a, b, h0, h1) for a, b in busy_iv) / 3600
        halves.append(dict(n=n, busy_h=bh, V=n / bh if bh > 0 else math.nan, V_ci=list(rate_ci(n, bh))))
    util = sum(_overlap(a, b, warm, we) for a, b in busy_iv) / max(we - warm, 1e-9)

    # waiting-queue depth over [warm, we]
    wait_steps.sort()
    depth, last, nonempty, area = 0, warm, 0.0, 0.0
    for t, d in wait_steps:
        if t > warm:
            seg = min(t, we) - last
            if seg > 0:
                nonempty += seg * (depth >= 1)
                area += seg * depth
                last = min(t, we)
        depth += d
    if we > last:
        nonempty += (we - last) * (depth >= 1)
        area += (we - last) * depth
    span = max(we - warm, 1e-9)

    # ------------------------------------------------------------------ bounces
    bounces = defaultdict(int)
    for row in tasks.values():
        for b in row["bounces"]:
            bounces[b["cause"]] += 1
    approvals = [r for r in reviews if r["verdict"] == "approve"]
    appr_resolved = [r for r in approvals if r["head"] in mq_outcome]
    n_ar = len(appr_resolved)
    tot_b = sum(bounces.values())

    # merge-queue time per change (FIFO)
    ci_times = []
    prev = -math.inf
    for r in sorted(appr_resolved, key=lambda r: r["t_end"]):
        fin = mq_outcome[r["head"]][0]
        start = max(r["t_end"], prev)
        if fin >= start:
            ci_times.append(fin - start)
            prev = fin

    # ------------------------------------------------------------------ per-PR table
    prs = []
    for task, row in tasks.items():
        if not row["submits"]:
            continue
        first = row["submits"][0]
        if first["attempt_no"] != 1:
            continue  # a task first submitted in an earlier run; not analysable here
        rv1 = [r for r in row["reviews"] if r["head"] == first["head"]]
        merged_green = (row["merge_t"] is not None
                        and tests_post.get(row["merge_head"], (None, True, True))[2] is not False)
        bounced = len(row["bounces"]) > 0
        last_sub = row["submits"][-1]["t"]
        last_b = row["bounces"][-1]["t"] if row["bounces"] else -math.inf
        if merged_green:
            status = "finished"
        elif last_b >= last_sub:
            status = "rework_open"
        else:
            status = "censored"
        prs.append(dict(
            run_id=run["run_id"], n_workers=run["n_workers"], task=task, worker=row.get("worker"),
            t_submit_min=first["t"] / 60, counted=warm <= first["t"] <= we, k=first["k"], m=first["m"],
            lines=first["lines"], n_files=len(first["files"]), attempts=len(row["submits"]),
            reviews=len(row["reviews"]),
            depth_first_review=rv1[0]["depth"] if rv1 else None,
            t_first_review_min=rv1[0]["t_start"] / 60 if rv1 else None,
            bounced=bounced, first_cause=row["bounces"][0]["cause"] if bounced else None,
            n_bounces=len(row["bounces"]),
            escaped=any(b["cause"] == "escaped_defect" for b in row["bounces"]),
            integration_failure=any(b["cause"] == "integration_failure" for b in row["bounces"]),
            finished=merged_green, status=status, resolved=bool(bounced or merged_green),
            merge_min=row["merge_t"] / 60 if row["merge_t"] is not None else None))

    appr_rows = []
    for r in approvals:
        if r["head"] not in hidden_pre:
            continue
        appr_rows.append(dict(run_id=run["run_id"], n_workers=run["n_workers"], task=r["task"], head=r["head"],
                              attempt_no=r["attempt_no"], depth=r["depth"], t_review_min=r["t_start"] / 60,
                              escaped=not hidden_pre[r["head"]][1]))

    for p in prs:
        p["counted_supply"] = p["counted"] and p["t_submit_min"] * 60 <= t_count_end
    counted = [p for p in prs if p["counted"]]
    worker_h_full = sum(_overlap(a, b, warm, we) for iv in worker_iv.values() for a, b in iv) / 3600
    worker_h_post = sum(_overlap(a, b, warm, t_count_end) for iv in worker_iv.values() for a, b in iv) / 3600
    worker_h_all = sum(b - a for iv in worker_iv.values() for a, b in iv) / 3600
    attempts_full = len(counted)
    attempts = sum(1 for p in prs if p["counted_supply"])
    attempt_hours = (t_count_end - warm) / 3600.0
    durations = [r["duration_s"] if r["duration_s"] else r["t_end"] - r["t_start"] for r in reviews]
    resolved_first = [p for p in prs if p["resolved"]]
    if launch_t:
        startup = [first_claim_task[k] - launch_t[k] for k in first_claim_task if k in launch_t]
        startup_source = "session_launch -> branch first pushed"
    else:
        startup = [first_claim[w] - worker_start_t[w] for w in first_claim if w in worker_start_t]
        startup_source = "worker_start -> first claim" if startup else None
    slot_h_post = sum(_overlap(a, b, warm, t_count_end) for iv in slot_busy_iv.values() for a, b in iv) / 3600
    submitted = {task for task, row in tasks.items() if row["submits"]}
    first_sub_t = {task: row["submits"][0]["t"] for task, row in tasks.items() if row["submits"]}
    timed_out = {task: t for t, task in timeouts}
    for p in prs:
        p["abandoned"] = p["task"] in timed_out and timed_out[p["task"]] >= p["t_submit_min"] * 60
    waits = []
    msgs_by_task = defaultdict(list)
    for t, task in rework_msgs:
        msgs_by_task[task].append(t)
    for task, row in tasks.items():
        ms = sorted(msgs_by_task.get(task, []))
        for b in row["bounces"]:
            nxt = [m for m in ms if m >= b["t"]]
            if nxt:
                waits.append(nxt[0] - b["t"])

    s = dict(
        run_id=run["run_id"], kind=run["kind"], n_workers=run["n_workers"],
        window_min=we / 60, warmup_min=warm / 60, grace_min=grace / 60, hours=hours,
        task_supply=supply, task_supply_source=supply_source if supply is not None else None,
        tasks_claimed=len({task for _, task in claims}),
        t_exhausted_min=t_exhausted / 60 if t_exhausted is not None else None,
        supply_truncated=truncated, attempt_window_end_min=t_count_end / 60, attempt_hours=attempt_hours,
        supply_flagged=bool(t_exhausted is not None and t_exhausted < we - SUPPLY_FLAG_BEFORE_END_MIN * 60),
        attempts=attempts, attempts_full=attempts_full, attempts_all=len(prs),
        worker_hours=worker_h_post, worker_hours_full=worker_h_full, worker_hours_all=worker_h_all,
        lam=attempts / worker_h_post if worker_h_post > 0 else math.nan,
        lam_full=attempts_full / worker_h_full if worker_h_full > 0 else math.nan,
        attempts_per_hour=attempts / attempt_hours if attempt_hours > 0 else math.nan,
        reviews=n_rev, first_reviews=sum(1 for r in reviews if r["attempt_no"] == 1),
        re_reviews=sum(1 for r in reviews if (r["attempt_no"] or 1) > 1),
        approvals=len(approvals), approvals_resolved=n_ar,
        busy_hours=busy_h, V=V, V_ci=list(rate_ci(n_rev, busy_h)), V_half=halves,
        review_open_at_end=open_at_end, busy_clipped_min=busy_clipped_s / 60,
        review_durations_s=durations, review_time_cv=_cv(durations),
        reviewer_util=util, queue_nonempty_share=nonempty / span, mean_waiting_depth=area / span,
        bounces={c: bounces.get(c, 0) for c in BOUNCE_CAUSES}, bounces_total=tot_b,
        b_review=bounces.get("review", 0) / n_rev if n_rev else math.nan,
        b_hidden=(bounces.get("escaped_defect", 0) + bounces.get("integration_failure", 0)) / n_ar if n_ar else math.nan,
        b_other=(bounces.get("rebase_conflict", 0) + bounces.get("visible_fail", 0)) / n_ar if n_ar else math.nan,
        b=tot_b / n_rev if n_rev else math.nan,
        escaped=bounces.get("escaped_defect", 0), integration_failures=bounces.get("integration_failure", 0),
        escape_rate=bounces.get("escaped_defect", 0) / n_ar if n_ar else math.nan,
        finished=sum(1 for p in counted if p["finished"]),
        finished_all=sum(1 for p in prs if p["finished"]),
        censored=sum(1 for p in counted if p["status"] == "censored"),
        rework_open=sum(1 for p in counted if p["status"] == "rework_open"),
        censored_all=sum(1 for p in prs if p["status"] == "censored"),
        r0_first=(sum(1 for p in resolved_first if p["bounced"]) / len(resolved_first)) if resolved_first else math.nan,
        n_resolved_first=len(resolved_first), n_bounced_first=sum(1 for p in resolved_first if p["bounced"]),
        ci_time_min=(sum(ci_times) / len(ci_times) / 60) if ci_times else math.nan, n_ci=len(ci_times),
        review_min_mean=(sum(r["t_end"] - r["t_start"] for r in reviews) / n_rev / 60) if n_rev else math.nan,
        review_tokens_mean=(sum(r["tokens"] for r in reviews) / n_rev) if n_rev else math.nan,
        worker_tokens_per_attempt=(sum(usage_tokens.values()) / len(prs)) if prs and usage_tokens else math.nan,
        worker_tokens_per_hour={w: usage_tokens[w] / (sum(b - a for a, b in worker_iv[w]) / 3600)
                                for w in usage_tokens if sum(b - a for a, b in worker_iv[w]) > 0},
        usage_cost_usd=usage_cost, startup_min_mean=(sum(startup) / len(startup) / 60) if startup else math.nan,
        startup_source=startup_source, n_startup=len(startup),
        slot_busy_hours=slot_h_post if slot_busy_iv else math.nan,
        slot_busy_share=(slot_h_post / worker_h_post) if slot_busy_iv and worker_h_post > 0 else math.nan,
        session_launches=len(launches), tasks_launched=len(launch_t), session_timeouts=len(timeouts),
        timeouts_before_submit=sum(1 for t, task in timeouts if task not in first_sub_t or first_sub_t[task] > t),
        abandoned_after_submit=sum(1 for p in prs if p["abandoned"]),
        rework_messages=len(rework_msgs),
        rework_wait_min_mean=(sum(waits) / len(waits) / 60) if waits else math.nan,
        phase=_phase(run),
        review_errors=review_errors, claim_races=claim_races, worker_downs=downs,
        max_restarts=max(restarts.values()) if restarts else 0,
        void_worker_restarts=(max(restarts.values()) if restarts else 0) > 2,
    )
    return dict(summary=s, prs=prs, approvals=appr_rows)


def _phase(run):
    m = re.search(r"\bphase=([\w-]+)", run.get("notes") or "")
    return m.group(1) if m else None


def supply_from_reset(run_dir):
    """Length of the window's task order from the harness's reset.json, if present."""
    f = Path(run_dir) / "reset.json"
    if not f.exists():
        return None
    try:
        order = json.loads(f.read_text()).get("task_order")
    except (json.JSONDecodeError, AttributeError):
        return None
    return len(order) if isinstance(order, list) and order else None


def derive_dir(run_dir):
    run, events = load_run(run_dir)
    n = supply_from_reset(run_dir)
    return derive_window(run, events, supply=n, supply_source="reset.json" if n is not None else None)


# ---------------------------------------------------------------------- pilot
LIVE_KINDS = ("trial", "pilot")   # T1 and T2: the only live reviews that may enter the pilot (PLAN-v4.1 section 6.3)


def pilot_params(derived, n_pilot=None, allow_nonlive=False):
    """Pool trial + pilot windows into the pre-registered pilot outputs (PLAN-v4 section 2: T1 + T2).
    lambda comes from kind == 'pilot' windows only (T1 is a throttling test at N = 1 then 12); V, b, r0,
    CI time, review time and its CV, and review cost are pooled over every window given (T1's PRs are
    reviewed too). review_cv_ok = (review-time CV <= 0.5) is the pre-registered flag that makes the
    reviewer-pace test (Vdur) confirmatory; it is fixed here, before the first sweep window.

    Live reviews only (PLAN-v4.1 sections 6.2-6.3, README decision 31): V, b, r0 and the review-time CV come
    from the review_end events of the T1 and T2 run logs given here and nothing else. The offline calibration
    reviews live in a separate calibration log (README "Calibration-review log") that only predict.py reads,
    for abort rule 4; they are never pooled into V, b or the CV. Runs whose kind is not trial or pilot
    (a sweep window or a dry run) are refused unless allow_nonlive (dry runs of the chain only)."""
    S = [d["summary"] for d in derived]
    bad = [s["run_id"] for s in S if s["kind"] not in LIVE_KINDS or s.get("phase") == "t0"]
    if bad and not allow_nonlive:
        raise SystemExit(f"--pilot takes live T1 / T2 runs only (kind trial or pilot, not T0); got {bad}. "
                         "Use --allow-nonlive only for a dry run of the analysis chain.")
    pil = [s for s in S if s["kind"] == "pilot"] or S
    ns = sorted({s["n_workers"] for s in pil})
    if n_pilot is None:
        if len(ns) != 1:
            raise SystemExit(f"pilot windows have several sizes {ns}; pass --n-pilot")
        n_pilot = ns[0]
    att = sum(s["attempts"] for s in pil)
    wh = sum(s["worker_hours"] for s in pil)
    nrev = sum(s["reviews"] for s in S)
    bh = sum(s["busy_hours"] for s in S)
    nar = sum(s["approvals_resolved"] for s in S)
    bc = {c: sum(s["bounces"][c] for s in S) for c in S[0]["bounces"]}
    nci = sum(s["n_ci"] for s in S)
    nres = sum(s["n_resolved_first"] for s in S)
    lam_ci = rate_ci(att, wh)
    durs = [x for s in S for x in s.get("review_durations_s", [])]
    rcv = _cv(durs)
    out = dict(
        n_pilot=n_pilot,
        lambda_pilot=att / wh if wh else math.nan, lambda_ci=list(lam_ci), attempts=att, worker_hours=wh,
        V=nrev / bh if bh else math.nan, V_ci=list(rate_ci(nrev, bh)), reviews=nrev, busy_hours=bh,
        bounces=bc,
        b_review=bc["review"] / nrev if nrev else math.nan,
        b_hidden=(bc["escaped_defect"] + bc["integration_failure"]) / nar if nar else math.nan,
        b_other=(bc["rebase_conflict"] + bc["visible_fail"]) / nar if nar else math.nan,
        b=sum(bc.values()) / nrev if nrev else math.nan,
        escape_rate=bc["escaped_defect"] / nar if nar else math.nan, approvals_resolved=nar,
        r0=sum(s["n_bounced_first"] for s in S) / nres if nres else math.nan, n_resolved_first=nres,
        completion=(sum(s["finished"] for s in pil) / sum(s.get("attempts_full", s["attempts"]) for s in pil))
        if sum(s.get("attempts_full", s["attempts"]) for s in pil) else math.nan,
        supply_truncated_windows=[s["run_id"] for s in S if s.get("supply_truncated")],
        ci_time_min=(sum(s["ci_time_min"] * s["n_ci"] for s in S if s["n_ci"]) / nci) if nci else math.nan,
        review_min_mean=(sum(s["review_min_mean"] * s["reviews"] for s in S if s["reviews"]) / nrev) if nrev else math.nan,
        review_time_cv=rcv, n_review_durations=len(durs), review_cv_max=REVIEW_CV_MAX,
        review_cv_ok=bool(rcv == rcv and rcv <= REVIEW_CV_MAX),
        review_tokens_mean=(sum(s["review_tokens_mean"] * s["reviews"] for s in S if s["reviews"]) / nrev) if nrev else math.nan,
        worker_tokens_per_attempt=_nanmean([s["worker_tokens_per_attempt"] for s in S]),
        startup_min=_nanmean([s["startup_min_mean"] for s in S]),
        slot_busy_share=_nanmean([s.get("slot_busy_share", math.nan) for s in pil]),
        session_timeouts=sum(s.get("session_timeouts", 0) for s in S),
        rework_wait_min=_nanmean([s.get("rework_wait_min_mean", math.nan) for s in S]),
        runs=[s["run_id"] for s in S],
        review_source="live T1 + T2 review_end events only; calibration reviews not pooled (PLAN-v4.1 6.2-6.3)"
        + ("" if not bad else f"; NON-LIVE runs included for a dry run: {bad}"),
    )
    return out


def _nanmean(xs):
    xs = [x for x in xs if x is not None and not (isinstance(x, float) and math.isnan(x))]
    return sum(xs) / len(xs) if xs else math.nan


def _clean(o):
    if isinstance(o, float) and (math.isnan(o) or math.isinf(o)):
        return None
    if isinstance(o, dict):
        return {k: _clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_clean(v) for v in o]
    return o


def write_csv(path, rows):
    if not rows:
        return
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        for r in rows:
            w.writerow({k: (json.dumps(v) if isinstance(v, (list, dict)) else v) for k, v in r.items()})


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("runs", nargs="+", help="run directories (each with run.json + events.jsonl)")
    ap.add_argument("--pilot", action="store_true", help="pool the runs into pilot parameters (T1 + T2)")
    ap.add_argument("--n-pilot", type=int, default=None)
    ap.add_argument("--allow-nonlive", action="store_true",
                    help="--pilot only: accept runs whose kind is not trial/pilot (dry runs of the chain; never for the study)")
    ap.add_argument("--out", default=None, help="write JSON here")
    ap.add_argument("--csv-dir", default=None, help="also write per-window, per-PR and per-approval CSVs")
    a = ap.parse_args()
    for r in a.runs:
        if not Path(r).is_dir():
            raise SystemExit(f"{r}: not a run directory. derive.py reads harness run directories only; a calibration "
                             "log is read by predict.py --calibration (abort rule 4) and never pooled into the pilot.")
    derived = [derive_dir(r) for r in a.runs]
    if a.pilot:
        res = pilot_params(derived, a.n_pilot, allow_nonlive=a.allow_nonlive)
    else:
        res = dict(windows=[d["summary"] for d in derived], prs=[p for d in derived for p in d["prs"]],
                   approvals=[x for d in derived for x in d["approvals"]])
    if a.csv_dir:
        cd = Path(a.csv_dir)
        cd.mkdir(parents=True, exist_ok=True)
        write_csv(cd / "prs.csv", [p for d in derived for p in d["prs"]])
        write_csv(cd / "approvals.csv", [x for d in derived for x in d["approvals"]])
        flat = []
        for d in derived:
            s = dict(d["summary"])
            s.update({f"bounce_{k}": v for k, v in s.pop("bounces").items()})
            s["V_half1"], s["V_half2"] = s["V_half"][0]["V"], s["V_half"][1]["V"]
            s.pop("V_half")
            s.pop("worker_tokens_per_hour")
            s.pop("review_durations_s", None)
            flat.append(s)
        write_csv(cd / "windows.csv", flat)
    txt = json.dumps(_clean(res), indent=2)
    if a.out:
        Path(a.out).write_text(txt + "\n")
    if a.pilot:
        print(txt)
    else:
        print(f"{'run':28} {'N':>2} {'att':>4} {'lam':>5} {'rev':>4} {'V':>5} {'b_rev':>5} {'b_hid':>5} "
              f"{'b':>5} {'fin':>4} {'cens':>4} {'esc':>3} {'intf':>4} {'q>0':>4} {'rCV':>4}  supply")
        for d in derived:
            s = d["summary"]
            if s["supply_truncated"]:
                sup = (f"{'FLAGGED, ' if s['supply_flagged'] else ''}"
                       f"TRUNCATED: {s['task_supply']} tasks all handed out at min {s['t_exhausted_min']:.1f}; "
                       f"attempts/lambda to that minute (whole window: {s['attempts_full']}, {s['lam_full']:.2f})")
            elif s["task_supply"] is None:
                sup = "unknown (no task_supply note or reset.json)"
            else:
                sup = (f"ok ({s['tasks_launched']}/{s['task_supply']} launched)" if s["session_launches"]
                       else f"ok ({s['tasks_claimed']}/{s['task_supply']} claimed)")
            print(f"{s['run_id'][:28]:28} {s['n_workers']:>2} {s['attempts']:>4} {s['lam']:>5.2f} {s['reviews']:>4} "
                  f"{s['V']:>5.1f} {s['b_review']:>5.2f} {s['b_hidden']:>5.2f} {s['b']:>5.2f} {s['finished']:>4} "
                  f"{s['censored']:>4} {s['escaped']:>3} {s['integration_failures']:>4} {s['queue_nonempty_share']:>4.2f} "
                  f"{s['review_time_cv']:>4.2f}  {sup}")


if __name__ == "__main__":
    main()
