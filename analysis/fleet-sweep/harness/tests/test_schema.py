"""The SCHEMA.md validator itself."""
import json

from harness.schema import check_event, validate_events, validate_run_json

T = "2026-10-02T09:00:00.000Z"


def ev(type_, t=T, **f):
    return {"t": t, "type": type_, **f}


GOOD = [
    ev("worker_start", worker="w1", session_id="s1"),
    ev("claim", worker="w1", task="001", branch="claude/task-001"),
    ev("submit", worker="w1", task="001", branch="claude/task-001", head="a", attempt_no=1,
       lines_changed=5, files=["x.py"], k=0, m=0),
    ev("reviewer_busy"),
    ev("review_start", task="001", head="a", queue_depth=0),
    ev("review_end", task="001", head="a", verdict="approve", reason="ok", tokens_in=None,
       tokens_out=None, duration_s=12.5),
    ev("reviewer_idle"),
    ev("queue_busy"),
    ev("hidden_pre", task="001", head="a", passed=True),
    ev("rebase", task="001", head="a", new_head="b", conflict=False),
    ev("tests_post", task="001", head="a", visible_passed=True, hidden_passed=True),
    ev("merge", task="001", head="a", main_sha="b"),
    ev("queue_idle"),
    ev("meter", credits_left_usd=212.4, source="operator"),
    ev("note", text="hi"),
]


def _write(tmp_path, events):
    p = tmp_path / "events.jsonl"
    p.write_text("".join(json.dumps(e) + "\n" for e in events))
    return p


def test_good_log(tmp_path):
    assert validate_events(_write(tmp_path, GOOD)) == []


def test_structural_errors():
    assert check_event(ev("claim", worker="w1", task="1", branch="b", extra=1))
    assert check_event(ev("claim", worker="w1", task="1"))
    assert check_event(ev("bounce", task="1", head="a", cause="because"))
    assert check_event(ev("review_end", task="1", head="a", verdict="yes", reason="", tokens_in=1,
                          tokens_out=1, duration_s=1))
    assert check_event(ev("note", t="2026-10-02 09:00:00", text="x"))
    assert check_event(ev("submit", worker="w", task="1", branch="b", head="h", attempt_no=True,
                          lines_changed=1, files=["x"], k=0, m=0))
    assert check_event(ev("nonsense"))


def test_semantic_errors(tmp_path):
    bad = list(GOOD)
    bad.insert(3, ev("submit", worker="w1", task="001", branch="b", head="z", attempt_no=3,
                     lines_changed=1, files=[], k=0, m=0))
    errs = validate_events(_write(tmp_path, bad))
    assert any("attempt_no" in e for e in errs)
    two = GOOD[:5] + [ev("review_start", task="002", head="q", queue_depth=0)]
    assert any("serial" in e for e in validate_events(_write(tmp_path, two)))
    back = [ev("note", text="a"), ev("note", t="2026-10-01T09:00:00.000Z", text="b")]
    assert any("backwards" in e for e in validate_events(_write(tmp_path, back)))
    unapproved = GOOD[:3] + [ev("merge", task="001", head="a", main_sha="b")]
    assert any("never approved" in e for e in validate_events(_write(tmp_path, unapproved)))


def test_run_json(tmp_path):
    run = {"run_id": "x", "kind": "dry-run", "n_workers": 2, "window_start": T, "window_end": T,
           "warmup_min": 10, "grace_min": 10, "task_order_seed": 1, "sandbox_commit": "a",
           "harness_commit": "b", "worker_model": "m", "reviewer_model": "r", "notes": ""}
    p = tmp_path / "run.json"
    p.write_text(json.dumps(run))
    assert validate_run_json(p) == []
    p.write_text(json.dumps({**run, "kind": "other", "extra": 1}))
    assert len(validate_run_json(p)) == 2
