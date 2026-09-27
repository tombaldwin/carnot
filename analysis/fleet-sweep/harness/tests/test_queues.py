"""Review queue ordering and each bounce cause in the merge queue."""
import re

from conftest import ScriptedReviewer, count

from harness.orchestrator import FEEDBACK_FILE
from harness.schema import validate_events


def _claim_all(env, pairs):
    """One adopted session per (slot, task); returns {slot: SimSession}."""
    return {slot: env.session(tid, slot) for slot, tid in pairs}


def test_review_queue_is_fifo_and_serial(make_env):
    rev = ScriptedReviewer(delay_s=0.3)
    env = make_env(reviewer=rev)
    tids = ["004", "005", "006", "008"]
    ws = _claim_all(env, [(f"s{i + 1}", t) for i, t in enumerate(tids)])
    for i, t in enumerate(tids):          # submit in a known order, one poll each
        ws[f"s{i + 1}"].submit()
        env.orch.poll()
    env.start()
    evs = env.wait_for(lambda evs: count(evs, "review_end") == 4)
    starts = [e for e in evs if e["type"] == "review_start"]
    assert [e["task"] for e in starts] == tids
    assert [e["queue_depth"] for e in starts] == [3, 2, 1, 0]
    assert rev.max_active == 1
    # the reviewer saw exactly one change per call, never the queue
    p = rev.packets[0]
    assert p.task.id == "004" and "toylib/palindrome.py" in p.diff and p.visible_passed
    assert validate_events(env.log.path) == []


def _feedback(env, task):
    """The rework message queued for the task's session (nothing is pushed to its branch)."""
    env.repo.run("fetch", "-q", "origin")
    tip = env.repo.sha(f"origin/claude/task-{task}")
    assert env.repo.message(tip).startswith("READY:")
    assert FEEDBACK_FILE not in env.repo.out("ls-tree", "--name-only", tip)
    sess = env.orch.sessions[task]
    assert sess.status == "rework_queued" and task in env.orch.rework_q
    return sess.feedback[-1]


def _no_hidden_leak(env, text):
    assert "test_task_" not in text
    assert "_hidden_acceptance" not in text
    assert not re.search(r"def test_", text)


def test_bounce_escaped_defect(make_env):
    env = make_env()
    ws = _claim_all(env, [("s1", "001")])
    env.start()
    ws["s1"].submit(wrong=True)      # scripted reviewer approves anyway
    evs = env.wait_for(lambda evs: count(evs, "bounce") == 1)
    assert count(evs, "hidden_pre", task="001", passed=False) == 1
    assert count(evs, "bounce", task="001", cause="escaped_defect") == 1
    assert count(evs, "rebase") == 0 and count(evs, "merge") == 0
    fb = _feedback(env, "001")
    assert "Acceptance check failed" in fb
    _no_hidden_leak(env, fb)


def test_bounce_rebase_conflict(make_env):
    env = make_env()
    ws = _claim_all(env, [("s1", "001"), ("s2", "004")])
    env.start()
    ws["s1"].submit(conflict=True)
    env.wait_for(lambda evs: count(evs, "merge", task="001") == 1)
    ws["s2"].submit(conflict=True)   # based on the old main, same hotspot file
    evs = env.wait_for(lambda evs: count(evs, "bounce", task="004") == 1)
    assert count(evs, "hidden_pre", task="004", passed=True) == 1
    assert count(evs, "rebase", task="004", conflict=True, new_head=None) == 1
    assert count(evs, "bounce", task="004", cause="rebase_conflict") == 1
    assert "conflict" in _feedback(env, "004")
    # the session merges main into its branch and re-submits; it merges this time
    assert ws["s2"].rework()
    env.wait_for(lambda evs: count(evs, "merge", task="004") == 1)


def test_bounce_visible_fail(make_env):
    env = make_env()
    ws = _claim_all(env, [("s1", "004")])
    env.start()
    ws["s1"].submit(vbreak=True)     # scripted reviewer approves despite red visible tests
    evs = env.wait_for(lambda evs: count(evs, "bounce") == 1)
    assert count(evs, "hidden_pre", task="004", passed=True) == 1
    assert count(evs, "tests_post", task="004", visible_passed=False) == 1
    assert count(evs, "bounce", task="004", cause="visible_fail") == 1
    assert any("visible_pre task=004" in e["text"] and "passed=false" in e["text"]
               for e in evs if e["type"] == "note")


def test_bounce_integration_failure(make_env):
    env = make_env()
    # 002 = pad_left (depends on shared.DEFAULT_WIDTH); 004 changes that constant on its branch
    ws = _claim_all(env, [("s1", "002"), ("s2", "004")])
    env.start()
    ws["s1"].submit()
    env.wait_for(lambda evs: count(evs, "merge", task="002") == 1)
    ws["s2"].submit(semantic=True)
    evs = env.wait_for(lambda evs: count(evs, "bounce", task="004") == 1)
    assert count(evs, "hidden_pre", task="004", passed=True) == 1      # fine on its own
    assert count(evs, "tests_post", task="004", visible_passed=True, hidden_passed=False) == 1
    assert count(evs, "bounce", task="004", cause="integration_failure") == 1
    fb = _feedback(env, "004")
    assert "Acceptance check failed" in fb
    _no_hidden_leak(env, fb)


def test_merge_is_squashed_on_main_and_green(make_env):
    env = make_env()
    ws = _claim_all(env, [("s1", "008")])
    env.start()
    head = ws["s1"].submit()
    evs = env.wait_for(lambda evs: count(evs, "merge") == 1)
    m = [e for e in evs if e["type"] == "merge"][0]
    assert m["head"] == head
    env.repo.run("fetch", "-q", "origin")
    assert env.repo.sha("origin/main") == m["main_sha"]
    files = env.repo.out("ls-tree", "-r", "--name-only", "origin/main").split()
    assert "toylib/roman.py" in files
    assert count(evs, "queue_busy") == 1 and count(evs, "queue_idle") == 1
    assert validate_events(env.log.path) == []
