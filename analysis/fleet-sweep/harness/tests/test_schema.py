"""The SCHEMA.md validator itself."""
import json

from harness.schema import check_event, validate_events, validate_run_json

T = "2026-10-02T09:00:00.000Z"


def ev(type_, t=T, **f):
    return {"t": t, "type": type_, **f}


GOOD = [
    ev("worker_start", worker="s1", session_id=None),
    ev("slot_busy", slot="s1"),
    ev("session_launch", slot="s1", task="001", session_id="sess_1", attempt_no=1),
    ev("claim", worker="s1", task="001", branch="claude/task-001"),
    ev("submit", worker="s1", task="001", branch="claude/task-001", head="a", attempt_no=1,
       lines_changed=5, files=["x.py"], k=0, m=0),
    ev("slot_idle", slot="s1"),
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


def test_session_events(tmp_path):
    assert check_event(ev("session_message", slot="s1", task="1", session_id=None, kind="nudge"))
    assert not check_event(ev("session_message", slot="s1", task="1", session_id="x", kind="rework"))
    assert check_event(ev("session_launch", slot="s1", task="1", session_id="x"))           # attempt_no missing
    assert not check_event(ev("session_timeout", slot="s1", task="1", session_id=None))
    twice = GOOD[:2] + [ev("slot_busy", slot="s1")]
    assert any("already busy" in e for e in validate_events(_write(tmp_path, twice)))
    unl = GOOD[:1] + [ev("session_timeout", slot="s1", task="009", session_id=None)]
    assert any("never launched" in e for e in validate_events(_write(tmp_path, unl)))
    nob = GOOD[:1] + [ev("session_launch", slot="s1", task="1", session_id=None, attempt_no=1)]
    assert any("without slot_busy" in e for e in validate_events(_write(tmp_path, nob)))


def test_semantic_errors(tmp_path):
    bad = list(GOOD)
    bad.insert(5, ev("submit", worker="w1", task="001", branch="b", head="z", attempt_no=3,
                     lines_changed=1, files=[], k=0, m=0))
    errs = validate_events(_write(tmp_path, bad))
    assert any("attempt_no" in e for e in errs)
    two = GOOD[:8] + [ev("review_start", task="002", head="q", queue_depth=0)]
    assert any("one change at a time" in e for e in validate_events(_write(tmp_path, two)))
    back = [ev("note", text="a"), ev("note", t="2026-10-01T09:00:00.000Z", text="b")]
    assert any("backwards" in e for e in validate_events(_write(tmp_path, back)))
    unapproved = GOOD[:5] + [ev("merge", task="001", head="a", main_sha="b")]
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


def _k_log(extra):
    """GOOD's first submit, then reviewer events tagged r1 / r2 (PLAN-v6: K parallel reviewers)."""
    return GOOD[:5] + [ev("submit", worker="s1", task="002", branch="claude/task-002", head="c", attempt_no=1,
                          lines_changed=5, files=["y.py"], k=1, m=0)] + extra


def test_parallel_reviewers_valid(tmp_path):
    log = _k_log([
        ev("reviewer_busy", reviewer="r1"), ev("review_start", task="001", head="a", queue_depth=1, reviewer="r1"),
        ev("reviewer_busy", reviewer="r2"), ev("review_start", task="002", head="c", queue_depth=0, reviewer="r2"),
        ev("review_end", task="002", head="c", verdict="request_changes", reason="x", tokens_in=None,
           tokens_out=None, duration_s=3, reviewer="r2"),
        ev("reviewer_idle", reviewer="r2"),
        ev("review_end", task="001", head="a", verdict="approve", reason="ok", tokens_in=None, tokens_out=None,
           duration_s=5, reviewer="r1"),
        ev("reviewer_idle", reviewer="r1")])
    assert validate_events(_write(tmp_path, log), n_reviewers=2) == []
    assert any("more reviews open than n_reviewers" in e
               for e in validate_events(_write(tmp_path, log), n_reviewers=1))


def test_parallel_reviewer_errors(tmp_path):
    same_task = _k_log([
        ev("reviewer_busy", reviewer="r1"), ev("review_start", task="001", head="a", queue_depth=0, reviewer="r1"),
        ev("reviewer_busy", reviewer="r2"), ev("review_start", task="001", head="a", queue_depth=0, reviewer="r2")])
    assert any("already under review by another reviewer" in e for e in validate_events(_write(tmp_path, same_task)))
    unpaired = _k_log([ev("reviewer_busy", reviewer="r1"), ev("reviewer_busy", reviewer="r1")])
    assert any("already busy" in e for e in validate_events(_write(tmp_path, unpaired)))
    idle_mid = _k_log([ev("reviewer_busy", reviewer="r1"),
                       ev("review_start", task="001", head="a", queue_depth=0, reviewer="r1"),
                       ev("reviewer_idle", reviewer="r1")])
    assert any("during a review" in e for e in validate_events(_write(tmp_path, idle_mid)))
    outside = _k_log([ev("review_start", task="001", head="a", queue_depth=0, reviewer="r1")])
    assert any("outside a reviewer_busy" in e for e in validate_events(_write(tmp_path, outside)))
    wrong = _k_log([ev("reviewer_busy", reviewer="r1"),
                    ev("review_start", task="001", head="a", queue_depth=0, reviewer="r1"),
                    ev("review_end", task="001", head="a", verdict="approve", reason="", tokens_in=None,
                       tokens_out=None, duration_s=1, reviewer="r2")])
    assert any("under review by r2" in e for e in validate_events(_write(tmp_path, wrong)))
    assert check_event(ev("reviewer_busy", reviewer="reviewer-1"))
    assert check_event(ev("merge", task="1", head="a", main_sha="b", reviewer="r1"))   # only reviewer events


def test_run_json_n_reviewers(tmp_path):
    run = {"run_id": "x", "kind": "sweep", "n_workers": 12, "window_start": T, "window_end": T,
           "warmup_min": 5, "grace_min": 10, "task_order_seed": 1, "sandbox_commit": "a",
           "harness_commit": "b", "worker_model": "m", "reviewer_model": "r", "notes": "", "n_reviewers": 3}
    p = tmp_path / "run.json"
    p.write_text(json.dumps(run))
    assert validate_run_json(p) == []
    p.write_text(json.dumps({**run, "n_reviewers": 0}))
    assert validate_run_json(p)
