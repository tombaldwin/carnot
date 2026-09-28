#!/usr/bin/env python3
"""Simulator parameters from the live trials T1 (1 slot 30 min, then 12 slots 30 min) and T0c (1 slot 45 min).

    PY=/Users/tom/git/carnot/analysis/aidev/.venv/bin/python
    $PY t1_params.py <runs>/T1-2026-09-28 --t0c <runs>/T0c-2026-09-28 \
        --exclude-b s2,s3 --private <outside this repo>/sim-params-T1.json \
        --public design-search/t1_params_public.json

Reads run.json + events.jsonl only. **Numbers only**: no review reason, task text, error text or note body is copied
anywhere. The private file adds per-task timing rows (task ids, times); the public file holds aggregates, quantiles
and counts without task ids, and is what `synth.V6_TRUTH` and the v6 design search are built from.

Phases of T1 (from the `start_schedule` notes): A = one slot (s1) from minute 0 to the second schedule entry (minute
30); B = from then to window_end, all slots except `--exclude-b` (slots lost to the 17:49 / 17:55 UTC CLI auto-update;
their `worker_down` events were logged late, so they are dropped from phase B entirely). T0c is a second one-slot
run on the same day and harness and is reported beside phase A (pooled where stated).

Per task cycle on a slot (routine launcher):
  re-arm   slot freed (slot_idle after READY / timeout) -> the next session_launch on that slot (the dispatcher's
           routine re-arm call, ~9 s, plus the dispatch poll); for rework: slot freed -> session_message
  start-up session_launch -> claim (branch first pushed): the re-arm lead, the routine firing delay, provisioning,
           clone and the first push; split at run_once_at (launch_detail note) into lead and fire->push
  coding   claim -> first READY (submit attempt_no = 1)
  rework   session_message (kind rework) -> the task's next submit
"""
from __future__ import annotations

import argparse
import json
import math
import re
import statistics
from collections import defaultdict
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[2]          # /Users/tom/git/carnot


def _t(s):
    from datetime import datetime
    return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()


def _q(xs, qs=(0.1, 0.25, 0.5, 0.75, 0.9)):
    xs = [x for x in xs if x is not None and x == x]
    if not xs:
        return {}
    return {f"p{int(q * 100)}": float(np.quantile(xs, q)) for q in qs}


def _stats(xs):
    xs = [float(x) for x in xs if x is not None and x == x]
    if not xs:
        return dict(n=0)
    lg = [math.log(x) for x in xs if x > 0]
    return dict(n=len(xs), mean=float(np.mean(xs)), median=float(np.median(xs)), sd=float(np.std(xs, ddof=1)) if len(xs) > 1 else 0.0,
                cv=float(np.std(xs, ddof=1) / np.mean(xs)) if len(xs) > 1 and np.mean(xs) > 0 else math.nan,
                min=min(xs), max=max(xs), log_mean=float(np.mean(lg)) if lg else math.nan,
                log_sd=float(np.std(lg, ddof=1)) if len(lg) > 1 else math.nan, **_q(xs))


def load(run_dir):
    run_dir = Path(run_dir)
    run = json.loads((run_dir / "run.json").read_text())
    ev = [json.loads(l) for l in (run_dir / "events.jsonl").read_text().splitlines() if l.strip()]
    return run, ev


LEAD_RE = re.compile(r"launch_detail slot=(\S+) task=(\S+) .*run_once_at=(\S+)")
SCHED_RE = re.compile(r"start_schedule minute=([\d.]+) slots=(\S+)")
MQ_RE = re.compile(r"mq_timing .*outcome=(\S+) .*total_s=([\d.]+)")


def cycles(run, ev, t0):
    """Per-task timing rows and per-slot busy intervals (seconds from window start)."""
    rows = {}
    slot_free_t = {}           # slot -> time it was last freed (slot_idle)
    run_once = {}
    rearm = []                 # (t_free, t_launch, slot, kind)
    msgs = defaultdict(list)
    for e in ev:
        t = _t(e["t"]) - t0
        ty = e["type"]
        if ty == "note":
            m = LEAD_RE.match(e.get("text", ""))
            if m and "run_once_at=" in e["text"]:
                run_once.setdefault(m.group(2), _t(m.group(3)) - t0)
            continue
        if ty == "slot_idle":
            slot_free_t[e["slot"]] = t
        elif ty == "session_launch":
            r = rows.setdefault(e["task"], dict(task=e["task"]))
            r.setdefault("launch", t)
            r.setdefault("slot", e["slot"])
            if e["slot"] in slot_free_t:
                rearm.append(dict(slot=e["slot"], kind="launch", t=t, gap=t - slot_free_t[e["slot"]]))
        elif ty == "session_message" and e.get("kind") == "rework":
            msgs[e["task"]].append(t)
            if e["slot"] in slot_free_t:
                rearm.append(dict(slot=e["slot"], kind="rework", t=t, gap=t - slot_free_t[e["slot"]]))
        elif ty == "claim":
            r = rows.setdefault(e["task"], dict(task=e["task"]))
            r.setdefault("claim", t)
        elif ty == "submit":
            r = rows.setdefault(e["task"], dict(task=e["task"]))
            r.setdefault("submits", []).append(dict(t=t, attempt=e["attempt_no"], slot=e["worker"]))
    for task, r in rows.items():
        if task in run_once:
            r["run_once_at"] = run_once[task]
        s1 = [s for s in r.get("submits", []) if s["attempt"] == 1]
        if s1:
            r["first_submit"] = s1[0]["t"]
        # rework legs: each rework message -> the next submit after it
        legs = []
        subs = sorted(s["t"] for s in r.get("submits", []))
        for m in msgs.get(task, []):
            nxt = [s for s in subs if s > m]
            legs.append(dict(msg=m, submit=nxt[0] if nxt else None))
        r["rework_legs"] = legs
    return rows, rearm


def review_rows(ev, t0):
    rv, starts = [], {}
    for e in ev:
        t = _t(e["t"]) - t0
        if e["type"] == "review_start":
            starts.setdefault((e["task"], e["head"]), (t, e["queue_depth"]))
        elif e["type"] == "review_end":
            st = starts.get((e["task"], e["head"]), (t - e.get("duration_s", 0), None))
            rv.append(dict(task=e["task"], head=e["head"], t_start=st[0], depth=st[1], t_end=t,
                           duration_s=e.get("duration_s"), verdict=e["verdict"]))
    return rv


def busy_intervals(ev, t0, end):
    iv, since = [], None
    for e in ev:
        t = _t(e["t"]) - t0
        if e["type"] == "reviewer_busy" and since is None:
            since = t
        elif e["type"] == "reviewer_idle" and since is not None:
            iv.append((since, t))
            since = None
    if since is not None:
        iv.append((since, end))
    return iv


def overlap(iv, a, b):
    return sum(max(0.0, min(y, b) - max(x, a)) for x, y in iv)


def waiting_depth(ev, t0):
    """Changes submitted (inside the window) and not yet handed to the reviewer, over time: [(t, depth)]."""
    steps, seen = [], set()
    we = None
    for e in ev:
        t = _t(e["t"]) - t0
        if e["type"] == "note" and e.get("text") == "window_end":
            we = t
        if e["type"] == "submit":
            steps.append((t, +1))
        elif e["type"] == "review_start":
            k = (e["task"], e["head"])
            if k not in seen:
                seen.add(k)
                steps.append((t, -1))
    steps.sort()
    d, out = 0, []
    for t, s in steps:
        d += s
        out.append((t, d))
    return out, we


def extract(t1_dir, t0c_dir=None, exclude_b=("s2", "s3")):
    run, ev = load(t1_dir)
    t0 = _t(run["window_start"])
    we = _t(run["window_end"]) - t0
    grace = 60 * float(run["grace_min"])
    sched = []
    for e in ev:
        if e["type"] == "note":
            m = SCHED_RE.match(e.get("text", ""))
            if m:
                sched.append((60 * float(m.group(1)), m.group(2).split(",")))
    split = sched[1][0] if len(sched) > 1 else 30 * 60
    rows, rearm = cycles(run, ev, t0)
    excl = set(exclude_b)
    slots_b = sorted({s for _, ss in sched for s in ss} - excl, key=lambda s: int(s[1:]))

    def phase_of(t, slot):
        if t < split:
            return "A" if slot == "s1" else None
        if t <= we and slot not in excl:
            return "B"
        return None

    out = dict(source="T1-2026-09-28 (+ T0c-2026-09-28)", window_min=we / 60, split_min=split / 60,
               phase_B_slots=len(slots_b), phase_B_excluded=sorted(excl), grace_min=grace / 60)
    per = {}
    private_rows = []
    for ph in ("A", "B"):
        su, co, lead, fire, rw = [], [], [], [], []
        for task, r in rows.items():
            if "launch" not in r or phase_of(r["launch"], r.get("slot")) != ph:
                continue
            if "claim" in r:
                su.append(r["claim"] - r["launch"])
                if "run_once_at" in r:
                    lead.append(r["run_once_at"] - r["launch"])
                    fire.append(r["claim"] - r["run_once_at"])
                if "first_submit" in r and r["first_submit"] <= we:
                    co.append(r["first_submit"] - r["claim"])
            private_rows.append(dict(phase=ph, **{k: v for k, v in r.items() if k != "submits"}))
        for task, r in rows.items():
            for lg in r["rework_legs"]:
                slot = next((s["slot"] for s in r.get("submits", []) if lg["submit"] is not None and s["t"] == lg["submit"]), r.get("slot"))
                if lg["submit"] is not None and phase_of(lg["msg"], slot) == ph and lg["submit"] <= we:
                    rw.append(lg["submit"] - lg["msg"])
        ra = [x["gap"] for x in rearm if phase_of(x["t"], x["slot"]) == ph and x["kind"] == "launch"]
        rr = [x["gap"] for x in rearm if phase_of(x["t"], x["slot"]) == ph and x["kind"] == "rework"]
        # first submissions per slot-hour in the phase (submit time in the phase, on a phase slot)
        subs1 = [s for r in rows.values() for s in r.get("submits", []) if s["attempt"] == 1 and phase_of(s["t"], s["slot"]) == ph]
        subs_all = [s for r in rows.values() for s in r.get("submits", []) if phase_of(s["t"], s["slot"]) == ph]
        launches = [r for r in rows.values() if "launch" in r and phase_of(r["launch"], r.get("slot")) == ph]
        slot_h = (split / 3600.0) if ph == "A" else len(slots_b) * (we - split) / 3600.0
        per[ph] = dict(slot_hours=slot_h, first_submits=len(subs1), submits=len(subs_all), launches=len(launches),
                       lam=len(subs1) / slot_h, submits_per_slot_hour=len(subs_all) / slot_h,
                       activity_per_slot_min=(len(launches) + len(subs_all)) / (slot_h * 60),
                       startup_s=_stats(su), lead_s=_stats(lead), fire_to_push_s=_stats(fire), coding_s=_stats(co),
                       rework_s=_stats(rw), rearm_launch_s=_stats(ra), rearm_rework_s=_stats(rr))
    out["phase"] = per
    # steady state of phase B (skip its first 5 min: every slot is in its first start-up)
    b0 = split + 300
    subs1_ss = [s for r in rows.values() for s in r.get("submits", []) if s["attempt"] == 1 and s["t"] >= b0
                and s["t"] <= we and s["slot"] not in excl]
    out["phase_B_steady_lam"] = len(subs1_ss) / (len(slots_b) * (we - b0) / 3600.0)
    a0 = 300
    subs1_a = [s for r in rows.values() for s in r.get("submits", []) if s["attempt"] == 1 and a0 <= s["t"] < split]
    out["phase_A_skip5_lam"] = len(subs1_a) / ((split - a0) / 3600.0)

    # ---------------------------------------------------------------- reviewer
    rv = review_rows(ev, t0)
    durs = [r["duration_s"] for r in rv if r["duration_s"]]
    first_head = {}
    for r in rows.values():
        for s in r.get("submits", []):
            pass
    att = {}
    for e in ev:
        if e["type"] == "submit":
            att[e["head"]] = e["attempt_no"]
    rv_first = [r for r in rv if att.get(r["head"]) == 1]
    rv_re = [r for r in rv if (att.get(r["head"]) or 1) > 1]
    iv = busy_intervals(ev, t0, we + grace)
    dep, _ = waiting_depth(ev, t0)
    def depth_at(t):
        d = 0
        for tt, dd in dep:
            if tt <= t:
                d = dd
            else:
                break
        return d
    # gap between one review's end and the next start while changes were waiting (dispatch + packet overhead)
    gaps = []
    rvs = sorted(rv, key=lambda r: r["t_start"])
    for a, b in zip(rvs, rvs[1:]):
        g = b["t_start"] - a["t_end"]
        if 0 <= g < 60 and depth_at(a["t_end"]) >= 1:
            gaps.append(g)
    # visible_pre (prep) time: submit -> review_start when the reviewer was idle and nothing waited
    sub_t = {}
    for e in ev:
        if e["type"] == "submit":
            sub_t[(e["task"], e["head"])] = _t(e["t"]) - t0
    prep = [r["t_start"] - sub_t[(r["task"], r["head"])] for r in rv if (r["task"], r["head"]) in sub_t and r["depth"] == 0
            and r["t_start"] - sub_t[(r["task"], r["head"])] < 30]
    out["review"] = dict(
        duration_s=_stats(durs), duration_first_s=_stats([r["duration_s"] for r in rv_first]),
        duration_rework_s=_stats([r["duration_s"] for r in rv_re]),
        quantiles_s={f"p{q}": float(np.quantile(durs, q / 100)) for q in range(0, 101, 5)},
        n_reviews=len(rv), request_changes=sum(r["verdict"] == "request_changes" for r in rv),
        request_changes_share=sum(r["verdict"] == "request_changes" for r in rv) / len(rv),
        first_attempt=dict(n=len(rv_first), request_changes=sum(r["verdict"] == "request_changes" for r in rv_first)),
        rework_attempt=dict(n=len(rv_re), request_changes=sum(r["verdict"] == "request_changes" for r in rv_re)),
        gap_between_reviews_s=_stats(gaps), prep_s=_stats(prep),
        busy_share_A=overlap(iv, 0, split) / split, busy_share_B=overlap(iv, split, we) / (we - split),
        busy_share_B_last20=overlap(iv, we - 1200, we) / 1200,
        busy_share_grace=overlap(iv, we, we + grace) / grace,
        review_errors=sum(1 for e in ev if e["type"] == "review_error"))
    # duration vs time / depth (does the reviewer slow under load? it never sees the queue)
    ra = np.array([[r["t_start"], r["depth"] or 0, r["duration_s"]] for r in rv if r["duration_s"]], float)
    if len(ra) > 5:
        out["review"]["corr_duration_depth"] = float(np.corrcoef(ra[:, 1], ra[:, 2])[0, 1])
        out["review"]["duration_B_mean_s"] = float(ra[ra[:, 0] >= split, 2].mean())
        out["review"]["duration_A_mean_s"] = float(ra[ra[:, 0] < split, 2].mean())
    out["queue"] = dict(
        depth_at_min={int(m): depth_at(m * 60) for m in range(0, int((we + grace) / 60) + 1, 5)},
        max_depth_window=max((d for t, d in dep if t <= we), default=0),
        depth_at_window_end=depth_at(we), depth_at_grace_end=depth_at(we + grace),
        review_start_depth_B=_stats([r["depth"] for r in rv if r["t_start"] >= split and r["depth"] is not None]))
    # submissions and reviews per hour in phase B (demand vs service)
    subB = [e for e in ev if e["type"] == "submit" and split <= _t(e["t"]) - t0 <= we]
    out["demand_B"] = dict(submits=len(subB), submits_per_hour=len(subB) / ((we - split) / 3600),
                           first_submits=sum(1 for e in subB if e["attempt_no"] == 1),
                           reviews_ended=sum(1 for r in rv if split <= r["t_end"] <= we),
                           reviews_per_hour=sum(1 for r in rv if split <= r["t_end"] <= we) / ((we - split) / 3600),
                           note="all slots incl. s2/s3 while they worked (the reviewer saw their submissions)")

    # ---------------------------------------------------------------- merge queue and bounces
    mq = []
    for e in ev:
        if e["type"] == "note":
            m = MQ_RE.match(e.get("text", ""))
            if m:
                mq.append((m.group(1), float(m.group(2))))
    out["merge_queue"] = dict(total_s=_stats([x for _, x in mq]), outcomes={o: sum(1 for oo, _ in mq if oo == o) for o in {o for o, _ in mq}})
    causes = defaultdict(int)
    for e in ev:
        if e["type"] == "bounce":
            causes[e["cause"]] += 1
    hp = [e for e in ev if e["type"] == "hidden_pre"]
    rb = [e for e in ev if e["type"] == "rebase"]
    tp = [e for e in ev if e["type"] == "tests_post"]
    out["bounces"] = dict(causes=dict(causes), hidden_pre=len(hp), hidden_pre_fail=sum(not e["passed"] for e in hp),
                          rebases=len(rb), rebase_conflicts=sum(bool(e["conflict"]) for e in rb), tests_post=len(tp),
                          visible_post_fail=sum(not e["visible_passed"] for e in tp),
                          hidden_post_fail=sum(e["visible_passed"] and not e["hidden_passed"] for e in tp),
                          merges=sum(1 for e in ev if e["type"] == "merge"))
    # collision exposure (j per first rebase) from derive, numbers only
    import sys
    sys.path.insert(0, str(HERE))
    from derive import derive_window
    d = derive_window(run, ev)
    prs = [p for p in d["prs"] if p.get("j") is not None]
    out["collisions"] = dict(first_passes=len(prs), collided_first=sum(bool(p["collided_first"]) for p in prs),
                             j=_stats([p["j"] for p in prs]),
                             j_collided=[p["j"] for p in prs if p["collided_first"]],
                             p_hat_per_merge_naive=(sum(bool(p["collided_first"]) for p in prs) / max(sum(p["j"] for p in prs), 1)))
    out["timeouts"] = sum(1 for e in ev if e["type"] == "session_timeout")
    meters = [e.get("credits_left_usd") for e in ev if e["type"] == "meter"]
    out["meter"] = meters
    out["abandoned_notes"] = sum(1 for e in ev if e["type"] == "note" and e.get("text", "").startswith("task_abandoned"))
    out["launch_failed_notes"] = sum(1 for e in ev if e["type"] == "note" and e.get("text", "").startswith("session_launch_failed"))

    # ---------------------------------------------------------------- throttling (abort rule 1), old and revised
    A, B = per["A"], per["B"]
    out["throttle"] = dict(
        activity_ratio=B["activity_per_slot_min"] / A["activity_per_slot_min"],
        lam_ratio=B["lam"] / A["lam"],
        startup_ratio_mean=B["startup_s"]["mean"] / A["startup_s"]["mean"],
        fire_to_push_ratio_mean=(B["fire_to_push_s"]["mean"] / A["fire_to_push_s"]["mean"]) if A["fire_to_push_s"].get("n") else None,
        coding_ratio_gmean=math.exp(B["coding_s"]["log_mean"] - A["coding_s"]["log_mean"]),
        coding_ratio_median=B["coding_s"]["median"] / A["coding_s"]["median"])
    # log-scale CIs (Welch on logs) for start-up and coding
    def welch_ratio(a, b):
        la, lb = np.log(a), np.log(b)
        se = math.sqrt(la.var(ddof=1) / len(la) + lb.var(ddof=1) / len(lb))
        from scipy import stats
        dfree = se ** 4 / ((la.var(ddof=1) / len(la)) ** 2 / (len(la) - 1) + (lb.var(ddof=1) / len(lb)) ** 2 / (len(lb) - 1))
        tq = stats.t.ppf(0.95, dfree)
        diff = lb.mean() - la.mean()
        return dict(ratio=math.exp(diff), ci90=[math.exp(diff - tq * se), math.exp(diff + tq * se)], se_log=se, df=dfree,
                    n_a=len(la), n_b=len(lb))
    samples = {}
    for ph in ("A", "B"):
        su, co, fi = [], [], []
        for r in rows.values():
            if "launch" in r and "claim" in r and phase_of(r["launch"], r.get("slot")) == ph:
                su.append(r["claim"] - r["launch"])
                if "run_once_at" in r:
                    fi.append(r["claim"] - r["run_once_at"])
                if "first_submit" in r and r["first_submit"] <= we:
                    co.append(r["first_submit"] - r["claim"])
        samples[ph] = dict(startup=np.array(su), coding=np.array(co), fire=np.array(fi))
    out["throttle"]["startup_welch"] = welch_ratio(samples["A"]["startup"], samples["B"]["startup"])
    out["throttle"]["fire_to_push_welch"] = welch_ratio(samples["A"]["fire"], samples["B"]["fire"])
    out["throttle"]["coding_welch"] = welch_ratio(samples["A"]["coding"], samples["B"]["coding"])

    # ---------------------------------------------------------------- T0c (one slot, 45 min)
    if t0c_dir:
        run0, ev0 = load(t0c_dir)
        s0 = _t(run0["window_start"])
        we0 = _t(run0["window_end"]) - s0
        rows0, rearm0 = cycles(run0, ev0, s0)
        su0 = [r["claim"] - r["launch"] for r in rows0.values() if "launch" in r and "claim" in r]
        fi0 = [r["claim"] - r["run_once_at"] for r in rows0.values() if "launch" in r and "claim" in r and "run_once_at" in r]
        co0 = [r["first_submit"] - r["claim"] for r in rows0.values() if "claim" in r and "first_submit" in r and r["first_submit"] <= we0]
        rw0 = [lg["submit"] - lg["msg"] for r in rows0.values() for lg in r["rework_legs"] if lg["submit"] is not None]
        rv0 = review_rows(ev0, s0)
        att0 = {e["head"]: e["attempt_no"] for e in ev0 if e["type"] == "submit"}
        first0 = sum(1 for r in rows0.values() if "first_submit" in r and r["first_submit"] <= we0)
        out["t0c"] = dict(window_min=we0 / 60, first_submits=first0, lam=first0 / (we0 / 3600), startup_s=_stats(su0),
                          fire_to_push_s=_stats(fi0), coding_s=_stats(co0), rework_s=_stats(rw0),
                          rearm_launch_s=_stats([x["gap"] for x in rearm0 if x["kind"] == "launch"]),
                          reviews=len(rv0), review_duration_s=_stats([r["duration_s"] for r in rv0]),
                          request_changes=sum(r["verdict"] == "request_changes" for r in rv0),
                          first_attempt=dict(n=sum(1 for r in rv0 if att0.get(r["head"]) == 1),
                                             request_changes=sum(1 for r in rv0 if att0.get(r["head"]) == 1 and r["verdict"] == "request_changes")),
                          meter=[e.get("credits_left_usd") for e in ev0 if e["type"] == "meter"])
        # one-slot pooled (T1 phase A + T0c)
        pa = np.concatenate([samples["A"]["startup"], np.array(su0)])
        pc = np.concatenate([samples["A"]["coding"], np.array(co0)])
        pf = np.concatenate([samples["A"]["fire"], np.array(fi0)])
        out["throttle"]["startup_welch_pooledA"] = welch_ratio(pa, samples["B"]["startup"])
        out["throttle"]["coding_welch_pooledA"] = welch_ratio(pc, samples["B"]["coding"])
        out["throttle"]["fire_to_push_welch_pooledA"] = welch_ratio(pf, samples["B"]["fire"])
        rv_all = durs + [r["duration_s"] for r in rv0 if r["duration_s"]]
        out["review_pooled_quantiles_s"] = {f"p{q}": float(np.quantile(rv_all, q / 100)) for q in range(0, 101, 5)}
        out["review_pooled"] = _stats(rv_all)
    priv = dict(out, task_rows=private_rows,
                review_rows=[dict(t_start=r["t_start"], t_end=r["t_end"], depth=r["depth"], duration_s=r["duration_s"],
                                  verdict=r["verdict"], attempt=att.get(r["head"])) for r in rv])
    return out, priv


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("t1")
    ap.add_argument("--t0c", default=None)
    ap.add_argument("--exclude-b", default="s2,s3")
    ap.add_argument("--private", required=True, help="private output (per-task rows); refused inside this repo")
    ap.add_argument("--public", required=True, help="public aggregates (no task ids, no text)")
    a = ap.parse_args()
    priv_path = Path(a.private).resolve()
    if str(priv_path).startswith(str(REPO_ROOT.resolve()) + "/"):
        raise SystemExit(f"refusing to write the private params inside {REPO_ROOT}")
    pub, priv = extract(a.t1, a.t0c, tuple(x for x in a.exclude_b.split(",") if x))
    priv_path.write_text(json.dumps(priv, indent=1, default=float) + "\n")
    Path(a.public).write_text(json.dumps(pub, indent=1, default=float) + "\n")
    print(json.dumps({k: pub[k] for k in ("phase", "throttle")}, indent=1, default=float)[:6000])


if __name__ == "__main__":
    main()
