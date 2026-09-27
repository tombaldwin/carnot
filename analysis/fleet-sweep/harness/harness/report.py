"""Event counts for `status` and for dry-run summaries (not the analysis)."""
from __future__ import annotations

import collections


def summarize(events: list[dict]) -> dict:
    c = collections.Counter(e["type"] for e in events)
    bounces = collections.Counter(e["cause"] for e in events if e["type"] == "bounce")
    verdicts = collections.Counter(e["verdict"] for e in events if e["type"] == "review_end")
    submits = [e for e in events if e["type"] == "submit"]
    open_review = []
    for e in events:  # current reviewer queue, replayed
        if e["type"] == "submit":
            open_review.append(e["task"])
        elif e["type"] == "review_start" and e["task"] in open_review:
            open_review.remove(e["task"])
    return {
        "slots_opened": c["worker_start"],
        "session_launches": c["session_launch"],
        "rework_messages": sum(1 for e in events if e["type"] == "session_message" and e["kind"] == "rework"),
        "session_timeouts": c["session_timeout"],
        "max_busy_slots": _max_busy(events),
        "branches_seen": c["claim"],
        "submits": len(submits),
        "first_attempts": sum(1 for e in submits if e["attempt_no"] == 1),
        "resubmits": sum(1 for e in submits if e["attempt_no"] > 1),
        "reviews": c["review_end"],
        "approvals": verdicts["approve"],
        "request_changes": verdicts["request_changes"],
        "review_errors": c["review_error"],
        "hidden_pre_fail": sum(1 for e in events if e["type"] == "hidden_pre" and not e["passed"]),
        "rebase_conflicts": sum(1 for e in events if e["type"] == "rebase" and e["conflict"]),
        "bounces": dict(sorted(bounces.items())),
        "bounces_total": sum(bounces.values()),
        "merges": c["merge"],
        "max_queue_depth": max((e["queue_depth"] for e in events if e["type"] == "review_start"), default=0),
        "notes_void": [e["text"] for e in events if e["type"] == "note" and e["text"].startswith("VOID")],
        "harness_errors": sum(1 for e in events if e["type"] == "note" and e["text"].startswith("harness error")),
    }


def _max_busy(events: list[dict]) -> int:
    busy, top = set(), 0
    for e in events:
        if e["type"] == "slot_busy":
            busy.add(e["slot"])
        elif e["type"] == "slot_idle":
            busy.discard(e["slot"])
        top = max(top, len(busy))
    return top


def format_summary(s: dict) -> str:
    lines = [f"{k:>18}: {v}" for k, v in s.items()]
    return "\n".join(lines)
