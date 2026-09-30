"""The event log contract of SCHEMA.md, and a validator for it.

Every event has exactly ``t`` and ``type`` plus the fields SCHEMA.md lists for
its type: no more, no fewer. ``EventLog.emit`` refuses anything else, so the
harness cannot write a log the analysis does not expect.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

STR, INT, BOOL, NUM, LIST_STR = "str", "int", "bool", "num", "list[str]"
OPT = "?"  # suffix: value may be null

EVENT_FIELDS: dict[str, dict[str, str]] = {
    # worker = slot id (s1..sN). worker_start: the slot opens (session_id null: sessions are per task).
    "worker_start": {"worker": STR, "session_id": STR + OPT},
    "worker_down": {"worker": STR, "reason": STR},
    "worker_restart": {"worker": STR, "reason": STR},
    "slot_busy": {"slot": STR},
    "slot_idle": {"slot": STR},
    "session_launch": {"slot": STR, "task": STR, "session_id": STR + OPT, "attempt_no": INT},
    "session_message": {"slot": STR, "task": STR, "session_id": STR + OPT, "kind": STR},
    "session_timeout": {"slot": STR, "task": STR, "session_id": STR + OPT},
    # claim: the task's branch first seen on the remote (its session's first push); worker = slot.
    "claim": {"worker": STR, "task": STR, "branch": STR},
    # claim_race: RETIRED (no claims with one session per task); kept so older logs still validate.
    "claim_race": {"worker": STR, "task": STR},
    "submit": {
        "worker": STR, "task": STR, "branch": STR, "head": STR, "attempt_no": INT,
        "lines_changed": INT, "files": LIST_STR, "k": INT, "m": INT,
    },
    "review_start": {"task": STR, "head": STR, "queue_depth": INT},
    "review_end": {
        "task": STR, "head": STR, "verdict": STR, "reason": STR,
        "tokens_in": INT + OPT, "tokens_out": INT + OPT, "duration_s": NUM,
    },
    "review_error": {"task": STR, "head": STR, "error": STR},
    "hidden_pre": {"task": STR, "head": STR, "passed": BOOL},
    "rebase": {"task": STR, "head": STR, "new_head": STR + OPT, "conflict": BOOL},
    "tests_post": {"task": STR, "head": STR, "visible_passed": BOOL, "hidden_passed": BOOL},
    "bounce": {"task": STR, "head": STR, "cause": STR},
    "merge": {"task": STR, "head": STR, "main_sha": STR},
    "queue_idle": {},
    "queue_busy": {},
    "reviewer_idle": {},
    "reviewer_busy": {},
    "usage": {"worker": STR, "tokens_in": INT + OPT, "tokens_out": INT + OPT, "cost_usd_est": NUM + OPT},
    "meter": {"credits_left_usd": NUM, "source": STR},
    "note": {"text": STR},
}

ENUMS = {
    ("review_end", "verdict"): {"approve", "request_changes"},
    ("bounce", "cause"): {"review", "rebase_conflict", "visible_fail", "escaped_defect", "integration_failure"},
    ("session_message", "kind"): {"rework", "probe", "task"},
}

RUN_FIELDS: dict[str, str] = {
    "run_id": STR, "kind": STR, "n_workers": INT, "window_start": STR, "window_end": STR,
    "warmup_min": NUM, "grace_min": NUM, "task_order_seed": INT, "sandbox_commit": STR,
    "harness_commit": STR, "worker_model": STR, "reviewer_model": STR, "notes": STR,
}
RUN_OPTIONAL: dict[str, str] = {"n_reviewers": INT}   # PLAN-v6: K parallel reviewers (absent = 1)
RUN_KINDS = {"sweep", "pilot", "trial", "dry-run"}
# PLAN-v6: these carry ``reviewer`` ("r1".."rK"); the harness always writes it, older logs lack it.
REVIEWER_FIELD_TYPES = ("review_start", "review_end", "review_error", "reviewer_busy", "reviewer_idle")
REVIEWER_RE = re.compile(r"^r[1-9]\d*$")

T_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$")


def _type_ok(value, spec: str) -> bool:
    optional = spec.endswith(OPT)
    base = spec.rstrip(OPT)
    if value is None:
        return optional
    if base == STR:
        return isinstance(value, str)
    if base == INT:
        return isinstance(value, int) and not isinstance(value, bool)
    if base == BOOL:
        return isinstance(value, bool)
    if base == NUM:
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if base == LIST_STR:
        return isinstance(value, list) and all(isinstance(x, str) for x in value)
    raise ValueError(spec)


def check_event(ev: dict) -> list[str]:
    """Problems with one event (empty list = valid)."""
    errs = []
    if not isinstance(ev, dict):
        return ["event is not an object"]
    typ = ev.get("type")
    if typ not in EVENT_FIELDS:
        return [f"unknown type {typ!r}"]
    if not isinstance(ev.get("t"), str) or not T_RE.match(ev["t"]):
        errs.append(f"{typ}: bad t {ev.get('t')!r}")
    spec = dict(EVENT_FIELDS[typ])
    if typ == "submit" and "merged_main" in ev:      # PLAN-v7: optional (absent in older logs)
        spec["merged_main"] = BOOL
    if typ in REVIEWER_FIELD_TYPES and "reviewer" in ev:
        spec["reviewer"] = STR
        if isinstance(ev["reviewer"], str) and not REVIEWER_RE.match(ev["reviewer"]):
            errs.append(f"{typ}: reviewer={ev['reviewer']!r} is not r1..rK")
    extra = set(ev) - set(spec) - {"t", "type"}
    missing = set(spec) - set(ev)
    if extra:
        errs.append(f"{typ}: unexpected fields {sorted(extra)}")
    if missing:
        errs.append(f"{typ}: missing fields {sorted(missing)}")
    for k, s in spec.items():
        if k in ev and not _type_ok(ev[k], s):
            errs.append(f"{typ}: field {k}={ev[k]!r} is not {s}")
    for (et, field), allowed in ENUMS.items():
        if typ == et and ev.get(field) not in allowed:
            errs.append(f"{typ}: {field}={ev.get(field)!r} not in {sorted(allowed)}")
    return errs


def validate_events(path: Path | str, semantic: bool = True, n_reviewers: int | None = None) -> list[str]:
    """Validate an events.jsonl file. Structural checks always; with ``semantic``
    also ordering and the invariants the harness is meant to keep. Reviews: each reviewer (``reviewer``
    field; none = the single serial reviewer) reviews one change at a time, no task is under review by two
    reviewers at once, at most ``n_reviewers`` reviews are open (if given), and each reviewer's
    reviewer_busy / reviewer_idle alternate, with every review inside its busy stretch."""
    errs: list[str] = []
    events = []
    for i, line in enumerate(Path(path).read_text().splitlines(), 1):
        if not line.strip():
            errs.append(f"line {i}: blank line")
            continue
        try:
            ev = json.loads(line)
        except json.JSONDecodeError as e:
            errs.append(f"line {i}: not JSON ({e})")
            continue
        errs += [f"line {i}: {e}" for e in check_event(ev)]
        events.append((i, ev))
    if not semantic or errs:
        return errs

    last_t = ""
    reviewing: dict = {}              # reviewer id (None: the serial reviewer) -> (task, head) under review
    rbusy: dict = {}                  # reviewer id -> busy
    approved: set[tuple[str, str]] = set()
    attempts: dict[str, int] = {}
    busy: dict[str, bool] = {}        # slot -> busy
    launched: set[str] = set()        # tasks with a session_launch
    for i, ev in events:
        typ = ev["type"]
        if ev["t"] < last_t:
            errs.append(f"line {i}: time goes backwards")
        last_t = ev["t"]
        key = (ev.get("task"), ev.get("head"))
        if typ == "submit":
            n = attempts.get(ev["task"], 0) + 1
            if ev["attempt_no"] != n:
                errs.append(f"line {i}: attempt_no {ev['attempt_no']} for task {ev['task']}, expected {n}")
            attempts[ev["task"]] = n
            if ev["m"] > ev["k"] or ev["k"] < 0:
                errs.append(f"line {i}: need 0 <= m <= k")
        elif typ == "review_start":
            rid = ev.get("reviewer")
            who = rid or "the reviewer"
            if reviewing.get(rid) is not None:
                errs.append(f"line {i}: review_start while {reviewing[rid]} still under review by {who} "
                            "(each reviewer reviews one change at a time)")
            if any(v is not None and v[0] == ev["task"] for r, v in reviewing.items() if r != rid):
                errs.append(f"line {i}: task {ev['task']} is already under review by another reviewer")
            if not rbusy.get(rid) and (rid in rbusy or rid is not None):
                errs.append(f"line {i}: review_start by {who} outside a reviewer_busy stretch")
            reviewing[rid] = key
            if n_reviewers is not None and sum(v is not None for v in reviewing.values()) > n_reviewers:
                errs.append(f"line {i}: more reviews open than n_reviewers = {n_reviewers}")
        elif typ in ("review_end", "review_error"):
            rid = ev.get("reviewer")
            if reviewing.get(rid) != key:
                errs.append(f"line {i}: {typ} for {key} but under review by {rid or 'the reviewer'} is "
                            f"{reviewing.get(rid)}")
            reviewing[rid] = None
            if typ == "review_end" and ev["verdict"] == "approve":
                approved.add(key)
        elif typ in ("reviewer_busy", "reviewer_idle"):
            rid = ev.get("reviewer")
            want = typ == "reviewer_busy"
            if bool(rbusy.get(rid)) == want:
                errs.append(f"line {i}: {typ} for {rid or 'the reviewer'}, which is already "
                            f"{'busy' if want else 'idle'}")
            if not want and reviewing.get(rid) is not None:
                errs.append(f"line {i}: reviewer_idle for {rid or 'the reviewer'} during a review")
            rbusy[rid] = want
        elif typ == "hidden_pre":
            if key not in approved:
                errs.append(f"line {i}: hidden_pre on a head that was never approved")
        elif typ == "merge":
            if key not in approved:
                errs.append(f"line {i}: merge of a head that was never approved")
        elif typ == "slot_busy":
            if busy.get(ev["slot"]):
                errs.append(f"line {i}: slot_busy for {ev['slot']}, which is already busy")
            busy[ev["slot"]] = True
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
    return errs


def validate_run_json(path: Path | str) -> list[str]:
    try:
        run = json.loads(Path(path).read_text())
    except (OSError, json.JSONDecodeError) as e:
        return [f"run.json unreadable: {e}"]
    errs = []
    extra = set(run) - set(RUN_FIELDS) - set(RUN_OPTIONAL)
    missing = set(RUN_FIELDS) - set(run)
    if extra:
        errs.append(f"run.json: unexpected fields {sorted(extra)}")
    if missing:
        errs.append(f"run.json: missing fields {sorted(missing)}")
    for k, s in RUN_FIELDS.items():
        if k in run and not _type_ok(run[k], s):
            errs.append(f"run.json: {k}={run[k]!r} is not {s}")
    for k, s in RUN_OPTIONAL.items():
        if k in run and not _type_ok(run[k], s):
            errs.append(f"run.json: {k}={run[k]!r} is not {s}")
    if isinstance(run.get("n_reviewers"), int) and run["n_reviewers"] < 1:
        errs.append("run.json: n_reviewers < 1")
    if run.get("kind") not in RUN_KINDS:
        errs.append(f"run.json: kind {run.get('kind')!r} not in {sorted(RUN_KINDS)}")
    for k in ("window_start", "window_end"):
        if isinstance(run.get(k), str) and not T_RE.match(run[k]):
            errs.append(f"run.json: {k} not ISO with ms")
    return errs
