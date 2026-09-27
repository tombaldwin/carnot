"""Worker protocol as the orchestrator sees it: claim, claim race, submit, feedback, re-submit."""
from conftest import ScriptedReviewer, count

from harness.orchestrator import FEEDBACK_FILE
from harness.schema import validate_events


def test_claim_and_claim_race(make_env):
    env = make_env()
    env.orch.phase = "window"
    w1, w2 = env.worker("w1"), env.worker("w2")
    w1.fetch(); w2.fetch()
    assert w1.try_claim("003") is True
    assert w2.try_claim("003") is False           # push rejected: branch exists
    env.orch.poll()
    evs = env.events()
    assert count(evs, "claim", worker="w1", task="003", branch="claude/task-003") == 1
    assert count(evs, "claim_race", worker="w2", task="003") == 1
    assert count(evs, "claim") == 1
    # the race marker is removed from the remote and not double counted
    env.orch.poll()
    assert count(env.events(), "claim_race") == 1
    from harness.reset import remote_heads
    assert not [b for b in remote_heads(env.repo) if b.startswith("claude/race-")]


def test_claim_next_skips_claimed(make_env):
    env = make_env()
    w1, w2 = env.worker("w1"), env.worker("w2")
    a = w1.claim_next()
    b = w2.claim_next()
    assert a and b and a != b


def test_submit_fields_k_m(make_env):
    env = make_env()
    env.orch.phase = "window"
    w1, w2, w3 = env.worker("w1"), env.worker("w2"), env.worker("w3")
    for w, t in ((w1, "001"), (w2, "004"), (w3, "005")):
        w.fetch(); assert w.try_claim(t)
    w1.submit("001", conflict=True)
    env.orch.poll()
    w2.submit("004", conflict=True)       # shares SIM_HOTSPOT.txt with 001
    w3.submit("005")
    env.orch.poll()
    subs = {e["task"]: e for e in env.events("submit")}
    s1, s2, s3 = subs["001"], subs["004"], subs["005"]
    assert s1["attempt_no"] == 1 and s1["k"] == 0 and s1["m"] == 0
    assert s1["worker"] == "w1" and s1["branch"] == "claude/task-001"
    assert sorted(s1["files"]) == ["SIM_HOTSPOT.txt", "toylib/slugify.py"]
    assert s1["lines_changed"] == 5 + 1
    assert s2["k"] == 1 and s2["m"] == 1
    assert s3["k"] == 2 and s3["m"] == 0
    assert validate_events(env.log.path) == []


def test_non_ready_pushes_are_not_submissions(make_env):
    env = make_env()
    env.orch.phase = "window"
    w = env.worker("w1")
    w.fetch(); w.try_claim("001")
    env.orch.poll()
    assert count(env.events(), "submit") == 0


def test_feedback_then_resubmit_goes_through_review_again(make_env):
    rev = ScriptedReviewer({"001": ["request_changes", "approve"]})
    env = make_env(reviewer=rev)
    w = env.worker("w1")
    w.fetch(); w.try_claim("001")
    env.start()
    w.submit("001")
    env.wait_for(lambda evs: count(evs, "bounce", task="001", cause="review") == 1)
    # FEEDBACK.md is on the branch tip, with the reviewer's reason
    w.fetch()
    tip = w.repo.sha("origin/claude/task-001")
    assert w.repo.message(tip).startswith("FEEDBACK:")
    fb = w.repo.out("show", f"{tip}:{FEEDBACK_FILE}")
    assert "Cause: review" in fb and "scripted request_changes" in fb
    assert w.rework_one() is True
    evs = env.wait_for(lambda evs: count(evs, "merge", task="001") == 1)
    subs = [e for e in evs if e["type"] == "submit"]
    assert [s["attempt_no"] for s in subs] == [1, 2]
    assert count(evs, "review_end", task="001") == 2      # re-submission reviewed again
    # FEEDBACK.md never reaches main
    env.repo.run("fetch", "-q", "origin")
    assert FEEDBACK_FILE not in env.repo.out("ls-tree", "--name-only", "origin/main")
    assert validate_events(env.log.path) == []


def test_claims_after_window_end_are_ignored(make_env):
    env = make_env()
    env.orch.phase = "grace"
    w = env.worker("w1")
    w.fetch(); w.try_claim("002")
    w.submit("002")
    env.orch.poll()
    evs = env.events()
    assert count(evs, "claim") == 0 and count(evs, "submit") == 0
    assert any("outside the window" in e["text"] for e in evs if e["type"] == "note")
