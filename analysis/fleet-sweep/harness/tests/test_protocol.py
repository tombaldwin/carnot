"""Session-per-task protocol as the orchestrator sees it: slots, launches in the seeded order, branch first
pushed (claim), READY frees the slot, rework as a follow-up message (never a push to the branch), session
timeouts, READY on a branch the session named itself, the T0 probe, and task-supply exhaustion."""
import json
import time

from conftest import FakeLauncher, ScriptedReviewer, count

from harness.launchers import rework_message
from harness.orchestrator import CAUSE_TEXT
from harness.schema import validate_events


def _order(env):
    return json.loads((env.run_dir / "reset.json").read_text())["task_order"]


def test_dispatch_hands_out_tasks_in_seeded_order_one_session_per_slot(make_env):
    fl = FakeLauncher()
    env = make_env(launcher=fl, n_slots=2)
    env.start(dispatch=True)
    env.orch.open_slots(["s1", "s2"])
    evs = env.wait_for(lambda evs: count(evs, "session_launch") == 2)
    order = _order(env)
    launches = [e for e in evs if e["type"] == "session_launch"]
    # launches run in parallel threads, so their log order may differ; the assignment is the seeded order
    assert {e["slot"]: e["task"] for e in launches} == {"s1": order[0], "s2": order[1]}
    assert [e["slot"] for e in evs if e["type"] == "slot_busy"] == ["s1", "s2"]
    assert all(e["attempt_no"] == 1 and e["session_id"] == f"sess-{e['task']}" for e in launches)
    assert count(evs, "worker_start") == 2 and count(evs, "slot_busy") == 2
    time.sleep(0.5)                                   # both slots busy: nothing else is launched
    assert count(env.events(), "session_launch") == 2
    # the prompt carries the task, the branch, the READY: message and the budget; the name is run-slot-task
    t0 = order[0]
    p = fl.launched[0]
    task = env.orch.tasks[t0]
    assert task.text.strip() in p["prompt"] and f"claude/task-{t0}" in p["prompt"]
    assert f"READY: {t0}" in p["prompt"] and "20 minutes" in p["prompt"] and "never ask" in p["prompt"].lower()
    assert "<!--" not in p["prompt"]
    assert p["name"] == f"t-s1-{t0}"
    assert validate_events(env.log.path) == []


def test_branch_push_is_claim_and_ready_frees_slot_for_next_task(make_env):
    fl = FakeLauncher()
    env = make_env(launcher=fl, n_slots=1)
    env.start(dispatch=True)
    env.orch.open_slots(["s1"])
    order = _order(env)
    env.wait_for(lambda evs: count(evs, "session_launch") == 1)
    s = env.sim_session(order[0], "s1")
    s.start_branch()
    evs = env.wait_for(lambda evs: count(evs, "claim") == 1)
    assert count(evs, "claim", worker="s1", task=order[0], branch=f"claude/task-{order[0]}") == 1
    assert count(evs, "submit") == 0                     # a branch push is not a submission
    head = s.submit()
    evs = env.wait_for(lambda evs: count(evs, "session_launch") == 2)
    sub = [e for e in evs if e["type"] == "submit"][0]
    assert sub["worker"] == "s1" and sub["head"] == head and sub["attempt_no"] == 1
    i_sub = evs.index(sub)
    i_idle = next(i for i, e in enumerate(evs) if e["type"] == "slot_idle")
    i_l2 = [i for i, e in enumerate(evs) if e["type"] == "session_launch"][1]
    assert i_sub < i_idle < i_l2
    assert evs[i_l2]["task"] == order[1] and evs[i_l2]["slot"] == "s1"
    assert validate_events(env.log.path) == []


def test_bounce_is_a_followup_message_not_a_push(make_env):
    rev = ScriptedReviewer({"001": ["request_changes", "approve"]})
    fl = FakeLauncher()
    env = make_env(reviewer=rev, launcher=fl, n_slots=1)
    s = env.session("001", "s1")
    head = s.submit()
    env.start(dispatch=True)
    env.wait_for(lambda evs: count(evs, "bounce", task="001", cause="review") == 1)
    # nothing was pushed to the session's branch
    env.repo.run("fetch", "-q", "origin")
    assert env.repo.sha("origin/claude/task-001") == head
    # the slot freed on READY went to a new task; the rework waits until a slot frees
    env.wait_for(lambda evs: count(evs, "session_launch") == 2)
    assert not fl.sent
    other = fl.launched[-1]["task"]
    s2 = env.sim_session(other, "s1")
    s2.start_branch()
    s2.submit()
    evs = env.wait_for(lambda evs: count(evs, "session_message", task="001", kind="rework") == 1)
    msg = [e for e in evs if e["type"] == "session_message"][0]
    assert msg["slot"] == "s1" and msg["session_id"] == "sess-001"
    assert count(evs, "session_launch") == 2                  # rework goes before the next new task
    sent = fl.sent[0]
    assert sent["task"] == "001" and sent["session_id"] == "sess-001" and sent["kind"] == "rework"
    assert "Cause: review" in sent["message"] and "scripted request_changes" in sent["message"]
    assert "READY: 001" in sent["message"] and "claude/task-001" in sent["message"]
    # the session reworks on the same branch; it is reviewed again and merged
    s.rework()
    evs = env.wait_for(lambda evs: count(evs, "merge", task="001") == 1)
    assert [e["attempt_no"] for e in evs if e["type"] == "submit" and e["task"] == "001"] == [1, 2]
    assert count(evs, "review_end", task="001") == 2
    assert validate_events(env.log.path) == []


def test_rework_message_never_names_hidden_tests(make_env):
    env = make_env()
    for cause in ("escaped_defect", "integration_failure"):
        m = rework_message(env.cfg, "001", 1, "a" * 40, cause, CAUSE_TEXT[cause])
        assert "Acceptance check failed" in m and "test_" not in m and "hidden" not in m.lower()


def test_session_timeout_abandons_task_and_frees_slot(make_env):
    fl = FakeLauncher()
    env = make_env(launcher=fl, n_slots=1, run_cfg={"task_timeout_min": 3})
    env.start(dispatch=True)
    env.orch.open_slots(["s1"])
    order = _order(env)
    evs = env.wait_for(lambda evs: count(evs, "session_timeout") >= 1 and count(evs, "session_launch") >= 2)
    to = [e for e in evs if e["type"] == "session_timeout"][0]
    assert to["task"] == order[0] and to["slot"] == "s1" and to["session_id"] == f"sess-{order[0]}"
    assert any(e["text"].startswith(f"task_abandoned task={order[0]}") for e in evs if e["type"] == "note")
    assert f"sess-{order[0]}" in fl.stopped
    assert [e["task"] for e in evs if e["type"] == "session_launch"][:2] == order[:2]
    # a late READY from the abandoned session is ignored, and the task is never launched again
    s = env.sim_session(order[0], "s9")
    s.start_branch()
    s.submit()
    evs = env.wait_for(lambda evs: any("READY for abandoned task" in e["text"] for e in evs if e["type"] == "note"))
    assert count(evs, "submit", task=order[0]) == 0
    assert [e["task"] for e in evs if e["type"] == "session_launch"].count(order[0]) == 1
    assert validate_events(env.log.path) == []


def test_launch_failure_is_retried_once(make_env):
    fl = FakeLauncher(fail_launch=1)
    env = make_env(launcher=fl, n_slots=1)
    env.start(dispatch=True)
    env.orch.open_slots(["s1"])
    evs = env.wait_for(lambda evs: count(evs, "session_launch") == 1)
    e = [x for x in evs if x["type"] == "session_launch"][0]
    assert e["attempt_no"] == 2
    assert any(x["text"].startswith("session_launch_failed") for x in evs if x["type"] == "note")


def test_ready_on_a_branch_the_session_named_itself(make_env):
    env = make_env()
    s = env.session("004", "s1", start_branch=False)
    head = s.submit(branch="claude/fix-palindrome-x1", message="READY: 004 palindrome\n\nSession: x")
    env.orch.poll()
    evs = env.events()
    assert count(evs, "claim", task="004", branch="claude/fix-palindrome-x1", worker="s1") == 1
    assert count(evs, "submit", task="004", head=head, branch="claude/fix-palindrome-x1") == 1
    assert any("not claude/task-004" in e["text"] for e in evs if e["type"] == "note")
    assert count(evs, "slot_idle", slot="s1") == 1


def test_submit_fields_k_m(make_env):
    env = make_env()
    s1, s2, s3 = env.session("001", "s1"), env.session("004", "s2"), env.session("005", "s3")
    s1.submit(conflict=True)
    env.orch.poll()
    s2.submit(conflict=True)       # shares SIM_HOTSPOT.txt with 001
    s3.submit()
    env.orch.poll()
    subs = {e["task"]: e for e in env.events("submit")}
    a, b, c = subs["001"], subs["004"], subs["005"]
    assert a["attempt_no"] == 1 and a["k"] == 0 and a["m"] == 0
    assert a["worker"] == "s1" and a["branch"] == "claude/task-001"
    assert sorted(a["files"]) == ["SIM_HOTSPOT.txt", "toylib/slugify.py"]
    assert a["lines_changed"] == 5 + 1
    assert b["k"] == 1 and b["m"] == 1
    assert c["k"] == 2 and c["m"] == 0
    assert validate_events(env.log.path) == []


def test_ready_after_window_end_is_not_queued(make_env):
    env = make_env()
    s = env.session("002", "s1")
    env.orch.poll()
    env.orch.phase = "grace"
    s.submit()
    env.orch.poll()
    evs = env.events()
    assert count(evs, "submit") == 0
    assert any("after window end" in e["text"] for e in evs if e["type"] == "note")


def test_branch_of_a_task_never_launched_is_ignored(make_env):
    env = make_env()
    env.orch.phase = "window"
    s = env.sim_session("003", "s1")
    s.start_branch()
    s.submit()
    env.orch.poll()
    evs = env.events()
    assert count(evs, "claim") == 0 and count(evs, "submit") == 0
    assert any("never launched" in e["text"] for e in evs if e["type"] == "note")


def test_tasks_exhausted_note_at_the_last_launch(make_env):
    """Supply exhaustion = the end of the task list: noted right after the last task's session is launched."""
    fl = FakeLauncher()
    env = make_env(launcher=fl, n_slots=4, n_tasks=3)
    env.start(dispatch=True)
    env.orch.open_slots(["s1", "s2", "s3", "s4"])
    evs = env.wait_for(lambda evs: any(e["type"] == "note" and e["text"] == "tasks_exhausted n=3" for e in evs))
    assert count(evs, "slot_busy") == 3
    i_ex = next(i for i, e in enumerate(evs) if e["type"] == "note" and e["text"].startswith("tasks_exhausted"))
    assert evs[i_ex - 1]["type"] == "slot_busy" and evs[i_ex - 1]["slot"] == "s3"


def test_probe_followup_holds_the_slot_until_acknowledged(make_env):
    fl = FakeLauncher()
    env = make_env(launcher=fl, n_slots=1, run_cfg={"probe_followup": True})
    env.start(dispatch=True)
    env.orch.open_slots(["s1"])
    order = _order(env)
    env.wait_for(lambda evs: count(evs, "session_launch") == 1)
    s = env.sim_session(order[0], "s1")
    s.start_branch()
    s.submit()
    evs = env.wait_for(lambda evs: count(evs, "session_message", kind="probe") == 1)
    assert count(evs, "submit") == 1 and count(evs, "slot_idle") == 0 and count(evs, "session_launch") == 1
    assert fl.sent[0]["kind"] == "probe" and f"PROBE: {order[0]}" in fl.sent[0]["message"]
    s.probe()
    evs = env.wait_for(lambda evs: count(evs, "session_launch") == 2)
    assert any(e["text"].startswith(f"probe_ack task={order[0]}") for e in evs if e["type"] == "note")
    assert count(evs, "submit") == 1          # the PROBE: commit is not a submission
    assert validate_events(env.log.path) == []
