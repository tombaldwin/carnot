"""Abort rule 1 (throttling) from T1's log, without a product token source (PLAN-v4 section 7.4).

    python -m harness throttle runs/<T1 run id> [--skip-min 5] [--json out.json]

T1 runs one slot, then all twelve (``start_schedule = [[0, 1], [30, 12]]``). With one cloud session
per task, a slot launches a new session for every task. The harness logs no token usage, so the
pre-registered measure is *activity* per slot-minute:

* activity = session launches (``session_launch`` on that slot) + READY submissions (``submit``,
  whose ``worker`` is the slot). Older logs without ``session_launch`` count a slot's repeated
  ``worker_start`` events as launches instead (the long-running-worker design).
* a slot starts at its ``worker_start`` (the slot opens); exposure = slot-minutes. Phase A (one slot): from the first slot's start + ``skip_min`` to the
  moment a second slot starts. Phase B (all slots): from that moment (or a slot's own start +
  ``skip_min``, whichever is later) to window_end. Time between ``worker_down`` and
  ``worker_restart`` is excluded.
* rate = activity / slot-minutes per phase; ratio = rate(B) / rate(A).

**Pre-registered rule:** throttled if ratio < 0.8, i.e. per-slot activity at 12 more than 20%
below the single slot's. Reported beside it (not part of the rule): the median minutes from a
task's session launch (or, in older logs, its claim) to its first READY in each phase (a slower model
shows up as a longer time), a rough
95% interval for the ratio (log-normal approximation, sqrt(1/a + 1/b)), and the operator's
readings: ``meter`` events and ``note`` lines starting ``plan_usage`` or ``usage``.

If the log has ``usage`` events (a product token source, if one is found at T1), tokens per
slot-minute replace activity as the measure, with the same 0.8 threshold; the activity figures
are still reported.

The counts in phase A are small (one slot for 25 minutes: a handful of submits), so the interval
is wide and the rule can only catch a large drop; this is stated in the pre-registration.
"""
from __future__ import annotations

import datetime as dt
import json
import math
import statistics
from pathlib import Path

from .events import read_events

THRESHOLD = 0.8
SKIP_MIN = 5.0


def _t(s: str) -> dt.datetime:
    return dt.datetime.fromisoformat(s.replace("Z", "+00:00"))


def _overlap(a0, a1, b0, b1) -> float:
    return max(0.0, (min(a1, b1) - max(a0, b0)).total_seconds() / 60.0)


def throttle_report(run_dir: Path, skip_min: float = SKIP_MIN, threshold: float = THRESHOLD) -> dict:
    run_dir = Path(run_dir)
    ev = read_events(run_dir / "events.jsonl")
    rj = json.loads((run_dir / "run.json").read_text()) if (run_dir / "run.json").exists() else {}
    notes = [e for e in ev if e["type"] == "note"]
    if rj.get("window_start"):
        w0, w1 = _t(rj["window_start"]), _t(rj["window_end"])
    else:
        w0 = next(_t(e["t"]) for e in notes if e["text"] == "window_start")
        w1 = next(_t(e["t"]) for e in notes if e["text"] == "window_end")

    starts: dict[str, list[dt.datetime]] = {}
    for e in ev:
        if e["type"] == "worker_start":
            starts.setdefault(e["worker"], []).append(_t(e["t"]))
    if len(starts) < 2:
        raise SystemExit(f"{run_dir}: throttling needs a one-slot phase and a many-slot phase; "
                         f"found {len(starts)} worker slot(s)")
    first = {w: min(ts) for w, ts in starts.items()}
    order = sorted(first, key=first.get)
    w_a = order[0]
    t_split = min(first[w] for w in order[1:])

    # down intervals per worker
    down: dict[str, list[tuple[dt.datetime, dt.datetime]]] = {}
    open_: dict[str, dt.datetime] = {}
    for e in ev:
        if e["type"] == "worker_down":
            open_.setdefault(e["worker"], _t(e["t"]))
        elif e["type"] == "worker_restart" and e["worker"] in open_:
            down.setdefault(e["worker"], []).append((open_.pop(e["worker"]), _t(e["t"])))
    for w, t in open_.items():
        down.setdefault(w, []).append((t, w1))

    skip = dt.timedelta(minutes=skip_min)
    expo = {"A": {w_a: (first[w_a] + skip, t_split)}, "B": {}}
    for w in order:
        expo["B"][w] = (max(t_split, first[w] + skip), w1)

    def minutes(ph):
        tot = 0.0
        for w, (a, b) in expo[ph].items():
            if b <= a:
                continue
            m = (b - a).total_seconds() / 60.0
            m -= sum(_overlap(a, b, d0, d1) for d0, d1 in down.get(w, []))
            tot += max(0.0, m)
        return tot

    def in_phase(ph, w, t):
        iv = expo[ph].get(w)
        return iv is not None and iv[0] <= t < iv[1] and not any(d0 <= t < d1 for d0, d1 in down.get(w, []))

    res = {}
    session_launches = [(e["slot"], _t(e["t"])) for e in ev if e["type"] == "session_launch"]
    claims = {}   # task -> (slot, time the task started): its first session_launch, else its first claim
    for e in ev:
        if e["type"] == "session_launch" and e["task"] not in claims:
            claims[e["task"]] = (e["slot"], _t(e["t"]))
    if not session_launches:
        for e in ev:
            if e["type"] == "claim" and e["task"] not in claims:
                claims[e["task"]] = (e["worker"], _t(e["t"]))
    first_ready = {}
    for e in ev:
        if e["type"] == "submit" and e["task"] not in first_ready:
            first_ready[e["task"]] = _t(e["t"])
    for ph in ("A", "B"):
        mins = minutes(ph)
        if session_launches:
            launches = sum(1 for w, t in session_launches if in_phase(ph, w, t))
        else:
            launches = sum(1 for w, ts in starts.items() for t in ts if t != first[w] and in_phase(ph, w, t))
        submits = sum(1 for e in ev if e["type"] == "submit" and in_phase(ph, e["worker"], _t(e["t"])))
        tok = [e for e in ev if e["type"] == "usage" and in_phase(ph, e["worker"], _t(e["t"]))]
        tokens = sum((e.get("tokens_in") or 0) + (e.get("tokens_out") or 0) for e in tok)
        c2r = [(first_ready[t] - tc).total_seconds() / 60.0 for t, (w, tc) in claims.items()
               if t in first_ready and in_phase(ph, w, tc)]
        res[ph] = dict(slots=len(expo[ph]), slot_minutes=round(mins, 2), launches=launches, submits=submits,
                       activity=launches + submits,
                       activity_per_slot_min=(launches + submits) / mins if mins > 0 else None,
                       usage_events=len(tok), tokens=tokens, tokens_per_slot_min=tokens / mins if mins > 0 and tok else None,
                       claim_to_ready_n=len(c2r),
                       claim_to_ready_median_min=round(statistics.median(c2r), 2) if c2r else None)
    A, B = res["A"], res["B"]
    ratio = (B["activity_per_slot_min"] / A["activity_per_slot_min"]
             if A["activity_per_slot_min"] and B["activity_per_slot_min"] is not None else None)
    ci = None
    if ratio and A["activity"] and B["activity"]:
        se = math.sqrt(1 / A["activity"] + 1 / B["activity"])
        ci = [round(ratio * math.exp(-1.96 * se), 3), round(ratio * math.exp(1.96 * se), 3)]
    tok_ratio = (B["tokens_per_slot_min"] / A["tokens_per_slot_min"]
                 if A["tokens_per_slot_min"] and B["tokens_per_slot_min"] is not None else None)
    measure, used = ("tokens", tok_ratio) if tok_ratio is not None else ("activity", ratio)
    c2r_ratio = (B["claim_to_ready_median_min"] / A["claim_to_ready_median_min"]
                 if A["claim_to_ready_median_min"] and B["claim_to_ready_median_min"] is not None else None)
    readings = [dict(t=e["t"], type="meter", credits_left_usd=e["credits_left_usd"], source=e.get("source"))
                for e in ev if e["type"] == "meter"]
    readings += [dict(t=e["t"], type="note", text=e["text"]) for e in notes
                 if e["text"].startswith("plan_usage") or e["text"].startswith("usage")]
    if used is None:
        verdict = "NOT EVALUATED"
    else:
        verdict = "THROTTLED" if used < threshold else "OK"
    return dict(run_dir=str(run_dir), rule="abort rule 1: throttled if per-slot rate at the many-slot phase < "
                f"{threshold} x the one-slot phase", measure=measure, ratio=None if used is None else round(used, 3),
                activity_ratio=None if ratio is None else round(ratio, 3), activity_ratio_ci95_approx=ci,
                token_ratio=None if tok_ratio is None else round(tok_ratio, 3),
                claim_to_ready_ratio=None if c2r_ratio is None else round(c2r_ratio, 3),
                verdict=verdict, split_at=t_split.isoformat(), skip_min=skip_min, phases=res,
                operator_readings=readings)


def format_report(r: dict) -> str:
    A, B = r["phases"]["A"], r["phases"]["B"]
    L = [f"Throttling (abort rule 1), {r['run_dir']}",
         f"  one slot  : {A['activity']} activity ({A['launches']} launches + {A['submits']} READY) in "
         f"{A['slot_minutes']} slot-min -> {A['activity_per_slot_min']}; launch->READY median {A['claim_to_ready_median_min']} min (n={A['claim_to_ready_n']})",
         f"  {B['slots']} slots  : {B['activity']} activity ({B['launches']} launches + {B['submits']} READY) in "
         f"{B['slot_minutes']} slot-min -> {B['activity_per_slot_min']}; launch->READY median {B['claim_to_ready_median_min']} min (n={B['claim_to_ready_n']})",
         f"  activity ratio {r['activity_ratio']} (approx. 95% {r['activity_ratio_ci95_approx']}); token ratio {r['token_ratio']}; "
         f"launch->READY ratio {r['claim_to_ready_ratio']} (reported only)",
         f"  measure: {r['measure']}; ratio {r['ratio']} -> {r['verdict']}"]
    for x in r["operator_readings"]:
        L.append(f"  reading {x['t']}: " + (f"meter ${x['credits_left_usd']} ({x['source']})" if x["type"] == "meter" else x["text"]))
    return "\n".join(L)
