#!/usr/bin/env python3
"""Check runs/<id>/events.jsonl (and run.json) against SCHEMA.md.

    python validate_schema.py runs/<run_id>            # a run directory
    python validate_schema.py path/to/events.jsonl     # just an event file

Errors (exit status 1): not JSON, unknown type, bad timestamp, missing or unexpected fields,
wrong value types, bad enum values, time going backwards, overlapping reviews, attempt_no out of
sequence, m > k, a merge/hidden_pre on a head that was never approved, a merge without a green
tests_post; for session-per-task logs: slot_busy on a busy slot / slot_idle on an idle one, a
session_launch or session_message on a slot that is not busy, a message or timeout for a task that was
never launched, and more slots busy at once than run.json's n_workers (the number of slots). Warnings (exit 0): things the analysis tolerates but a reader should know about,
e.g. events after window_end + grace, reviews still open at the end, no reviewer_busy/idle events.

Fields follow SCHEMA.md exactly, with the nullable values the harness uses (session_id, new_head,
tokens_*, cost_usd_est may be null). No extra fields are allowed: the analysis must not silently
depend on fields SCHEMA.md does not define.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import iso, parse_t  # noqa: E402

S, I, B, N, L = "str", "int", "bool", "num", "list[str]"
FIELDS = {
    "worker_start": {"worker": S, "session_id": S + "?"},
    "worker_down": {"worker": S, "reason": S},
    "worker_restart": {"worker": S, "reason": S},
    "slot_busy": {"slot": S}, "slot_idle": {"slot": S},
    "session_launch": {"slot": S, "task": S, "session_id": S + "?", "attempt_no": I},
    "session_message": {"slot": S, "task": S, "session_id": S + "?", "kind": S},
    "session_timeout": {"slot": S, "task": S, "session_id": S + "?"},
    "claim": {"worker": S, "task": S, "branch": S},
    "claim_race": {"worker": S, "task": S},   # retired with one session per task; older logs only
    "submit": {"worker": S, "task": S, "branch": S, "head": S, "attempt_no": I, "lines_changed": I,
               "files": L, "k": I, "m": I},
    "review_start": {"task": S, "head": S, "queue_depth": I},
    "review_end": {"task": S, "head": S, "verdict": S, "reason": S, "tokens_in": I + "?",
                   "tokens_out": I + "?", "duration_s": N},
    "review_error": {"task": S, "head": S, "error": S},
    "hidden_pre": {"task": S, "head": S, "passed": B},
    "rebase": {"task": S, "head": S, "new_head": S + "?", "conflict": B},
    "tests_post": {"task": S, "head": S, "visible_passed": B, "hidden_passed": B},
    "bounce": {"task": S, "head": S, "cause": S},
    "merge": {"task": S, "head": S, "main_sha": S},
    "queue_idle": {}, "queue_busy": {}, "reviewer_idle": {}, "reviewer_busy": {},
    "usage": {"worker": S, "tokens_in": I + "?", "tokens_out": I + "?", "cost_usd_est": N + "?"},
    "meter": {"credits_left_usd": N, "source": S},
    "note": {"text": S},
}
ENUMS = {("review_end", "verdict"): {"approve", "request_changes"},
         ("bounce", "cause"): {"review", "rebase_conflict", "visible_fail", "escaped_defect",
                               "integration_failure"},
         ("session_message", "kind"): {"rework", "probe", "task"}}
RUN_FIELDS = {"run_id": S, "kind": S, "n_workers": I, "window_start": S, "window_end": S,
              "warmup_min": N, "grace_min": N, "task_order_seed": I, "sandbox_commit": S,
              "harness_commit": S, "worker_model": S, "reviewer_model": S, "notes": S}
RUN_OPTIONAL = {"n_reviewers": I}   # PLAN-v6: K parallel reviewers (absent = 1, the serial reviewer)
# PLAN-v6: with K parallel reviewers every reviewer event names its reviewer ("r1".."rK"); optional otherwise
REVIEWER_FIELD_TYPES = ("review_start", "review_end", "review_error", "reviewer_busy", "reviewer_idle")
KINDS = {"sweep", "pilot", "trial", "dry-run"}
T_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}(Z|\+00:00)$")


def _ok(v, spec):
    opt = spec.endswith("?")
    base = spec.rstrip("?")
    if v is None:
        return opt
    if base == S:
        return isinstance(v, str)
    if base == I:
        return isinstance(v, int) and not isinstance(v, bool)
    if base == B:
        return isinstance(v, bool)
    if base == N:
        return isinstance(v, (int, float)) and not isinstance(v, bool)
    if base == L:
        return isinstance(v, list) and all(isinstance(x, str) for x in v)
    return False


def check_event(ev):
    if not isinstance(ev, dict):
        return ["not an object"]
    typ = ev.get("type")
    if typ not in FIELDS:
        return [f"unknown type {typ!r}"]
    errs = []
    if not isinstance(ev.get("t"), str) or not T_RE.match(ev["t"]):
        errs.append(f"{typ}: bad t {ev.get('t')!r}")
    spec = dict(FIELDS[typ])
    if typ in REVIEWER_FIELD_TYPES and "reviewer" in ev:
        spec["reviewer"] = S
    extra = set(ev) - set(spec) - {"t", "type"}
    missing = set(spec) - set(ev)
    if extra:
        errs.append(f"{typ}: unexpected fields {sorted(extra)}")
    if missing:
        errs.append(f"{typ}: missing fields {sorted(missing)}")
    for k, s in spec.items():
        if k in ev and not _ok(ev[k], s):
            errs.append(f"{typ}: {k}={ev[k]!r} is not {s}")
    for (et, f), allowed in ENUMS.items():
        if typ == et and ev.get(f) not in allowed:
            errs.append(f"{typ}: {f}={ev.get(f)!r} not in {sorted(allowed)}")
    if typ == "session_launch" and not errs and ev["attempt_no"] < 1:
        errs.append("session_launch: attempt_no < 1")
    if typ == "submit" and not errs:
        if ev["attempt_no"] < 1:
            errs.append("submit: attempt_no < 1")
        if not (0 <= ev["m"] <= ev["k"]):
            errs.append("submit: need 0 <= m <= k")
    return errs


def check_run(run):
    errs = []
    extra = set(run) - set(RUN_FIELDS) - set(RUN_OPTIONAL)
    missing = set(RUN_FIELDS) - set(run)
    if extra:
        errs.append(f"run.json: unexpected fields {sorted(extra)}")
    if missing:
        errs.append(f"run.json: missing fields {sorted(missing)}")
    for k, s in RUN_FIELDS.items():
        if k in run and not _ok(run[k], s):
            errs.append(f"run.json: {k}={run[k]!r} is not {s}")
    for k, s in RUN_OPTIONAL.items():
        if k in run and not _ok(run[k], s):
            errs.append(f"run.json: {k}={run[k]!r} is not {s}")
    if run.get("kind") not in KINDS:
        errs.append(f"run.json: kind {run.get('kind')!r} not in {sorted(KINDS)}")
    for k in ("window_start", "window_end"):
        if isinstance(run.get(k), str) and not T_RE.match(run[k]):
            errs.append(f"run.json: {k} not ISO 8601 with ms")
    return errs


def validate_events(events, run=None):
    """events: list of (line_no, dict). Returns (errors, warnings)."""
    errs, warns = [], []
    last_t = ""
    reviewing = {}            # reviewer id (None: the single serial reviewer) -> (task, head) under review
    n_rev = int(run.get("n_reviewers", 1)) if run and not check_run(run) else None
    approved = set()
    green = set()
    attempts = {}
    open_review_heads = set()
    types = set()
    busy = {}
    launched = set()
    n_slots = run.get("n_workers") if run and not check_run(run) else None
    end_iso = None
    if run and not check_run(run):
        end_iso = iso(parse_t(run["window_end"]) + 60 * run["grace_min"])
        start_iso = run["window_start"]
    for i, ev in events:
        typ = ev["type"]
        types.add(typ)
        t = ev["t"].replace("+00:00", "Z")
        if t < last_t:
            errs.append(f"line {i}: time goes backwards")
        last_t = t
        if end_iso and t > end_iso and typ not in ("note", "meter", "usage", "worker_down",
                                                   "reviewer_idle", "queue_idle"):
            warns.append(f"line {i}: {typ} after window_end + grace (ignored by the analysis)")
        if run and not check_run(run) and t < start_iso and typ not in ("note", "meter", "worker_start"):
            warns.append(f"line {i}: {typ} before window_start")
        key = (ev.get("task"), ev.get("head"))
        if typ == "submit":
            n = attempts.get(ev["task"], 0) + 1
            if ev["attempt_no"] != n:
                errs.append(f"line {i}: attempt_no {ev['attempt_no']} for task {ev['task']}, expected {n}")
            attempts[ev["task"]] = ev["attempt_no"]
        elif typ == "review_start":
            rid = ev.get("reviewer")
            if reviewing.get(rid) is not None:
                errs.append(f"line {i}: review_start while {reviewing[rid]} is under review by "
                            f"{rid or 'the reviewer'} (each reviewer reviews one change at a time)")
            if ev["task"] not in attempts:
                errs.append(f"line {i}: review of task {ev['task']} that was never submitted")
            if any(v == key for r, v in reviewing.items() if r != rid):
                errs.append(f"line {i}: {key} is already under review by another reviewer")
            reviewing[rid] = key
            if n_rev is not None and sum(v is not None for v in reviewing.values()) > n_rev:
                errs.append(f"line {i}: more reviews open than n_reviewers = {n_rev}")
            open_review_heads.add(key)
        elif typ in ("review_end", "review_error"):
            rid = ev.get("reviewer")
            if reviewing.get(rid) != key:
                errs.append(f"line {i}: {typ} for {key} but under review by {rid or 'the reviewer'} is {reviewing.get(rid)}")
            reviewing[rid] = None
            open_review_heads.discard(key)
            if typ == "review_end" and ev["verdict"] == "approve":
                approved.add(key)
        elif typ == "hidden_pre":
            if key not in approved:
                errs.append(f"line {i}: hidden_pre on a head that was never approved")
        elif typ == "tests_post":
            if ev["visible_passed"] and ev["hidden_passed"]:
                green.add(key)
        elif typ == "merge":
            if key not in approved:
                errs.append(f"line {i}: merge of a head that was never approved")
            if key not in green:
                errs.append(f"line {i}: merge without a green tests_post on that head")
        elif typ == "slot_busy":
            if busy.get(ev["slot"]):
                errs.append(f"line {i}: slot_busy for {ev['slot']}, which is already busy")
            busy[ev["slot"]] = True
            if n_slots is not None and sum(busy.values()) > n_slots:
                errs.append(f"line {i}: {sum(busy.values())} slots busy, more than n_workers = {n_slots}")
        elif typ == "slot_idle":
            if not busy.get(ev["slot"]):
                errs.append(f"line {i}: slot_idle for {ev['slot']}, which is not busy")
            busy[ev["slot"]] = False
        elif typ == "session_launch":
            if not busy.get(ev["slot"]):
                errs.append(f"line {i}: session_launch on slot {ev['slot']} without slot_busy")
            launched.add(ev["task"])
        elif typ == "session_message" and ev.get("kind") == "task":
            # one session per slot: a later task handed to the slot's session as a follow-up (its launch)
            if not busy.get(ev["slot"]):
                errs.append(f"line {i}: session_message on slot {ev['slot']} without slot_busy")
            launched.add(ev["task"])
        elif typ in ("session_message", "session_timeout"):
            if ev["task"] not in launched:
                errs.append(f"line {i}: {typ} for task {ev['task']} that was never launched")
            if typ == "session_message" and not busy.get(ev["slot"]):
                errs.append(f"line {i}: session_message on slot {ev['slot']} without slot_busy")
    for rid, v in reviewing.items():
        if v is not None:
            warns.append(f"review of {v} still open at end of log (reviewer busy time is clipped)")
    if "review_end" in types and not ({"reviewer_busy", "reviewer_idle"} & types):
        warns.append("no reviewer_busy/reviewer_idle events: V will use summed review durations")
    if "submit" not in types:
        warns.append("no submit events")
    return errs, warns


def validate_path(path):
    path = Path(path)
    run = None
    if path.is_dir():
        rj = path / "run.json"
        ev_path = path / "events.jsonl"
        if not rj.exists():
            return [f"{rj}: missing"], []
        try:
            run = json.loads(rj.read_text())
        except json.JSONDecodeError as e:
            return [f"run.json: not JSON ({e})"], []
    else:
        ev_path = path
    errs = check_run(run) if run is not None else []
    events = []
    for i, line in enumerate(ev_path.read_text().splitlines(), 1):
        if not line.strip():
            errs.append(f"line {i}: blank line")
            continue
        try:
            ev = json.loads(line)
        except json.JSONDecodeError as e:
            errs.append(f"line {i}: not JSON ({e})")
            continue
        e = check_event(ev)
        errs += [f"line {i}: {x}" for x in e]
        if not e:
            events.append((i, ev))
    if errs:
        return errs, []
    e2, w2 = validate_events(events, run)
    return errs + e2, w2


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if not argv:
        print(__doc__)
        return 2
    bad = 0
    for p in argv:
        errs, warns = validate_path(p)
        for w in warns:
            print(f"{p}: WARNING {w}")
        for e in errs:
            print(f"{p}: ERROR {e}")
        print(f"{p}: {'OK' if not errs else 'INVALID'} ({len(errs)} errors, {len(warns)} warnings)")
        bad += bool(errs)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
