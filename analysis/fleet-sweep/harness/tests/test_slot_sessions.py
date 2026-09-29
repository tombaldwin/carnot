"""One cloud session per slot (`[launcher] session_per = "slot"`): the slot's session is launched with its first
task, later tasks reach the same session as `session_message kind=task`, the session is retired after
tasks_per_session tasks, a timeout or a failed follow-up, and rework goes to the session that did the task.
Task mode (`session_per = "task"`, the default) is covered by the rest of the suite, unchanged."""
import collections
import json
import sys
from pathlib import Path

import pytest
from conftest import FakeLauncher, ScriptedReviewer, count

from harness import config as config_mod
from harness.dryrun import dry_run
from harness.events import read_events
from harness.launchers import LaunchError, next_task_message
from harness.schema import validate_events, validate_run_json
from harness.tasks import Task
from harness.throttle import task_legs

HERE = Path(__file__).resolve().parent.parent
SLOT = {"session_per": "slot"}
LONG = {"task_timeout_min": 600}


class FailTaskOnce(FakeLauncher):
    """The first task hand-out by follow-up fails (both of the harness's delivery attempts)."""

    def __init__(self):
        super().__init__()
        self.task_failures = 0

    def send(self, slot, task_id, session_id, message, kind="rework"):
        if kind == "task" and self.task_failures < 2:
            self.task_failures += 1
            raise LaunchError("scripted follow-up failure")
        super().send(slot, task_id, session_id, message, kind)


def _env(make_env, tasks, n_slots=1, launcher=None, run_cfg=None, launcher_cfg=None):
    env = make_env(reviewer=ScriptedReviewer(), launcher=launcher or FakeLauncher(), n_slots=n_slots,
                   run_cfg={**LONG, **(run_cfg or {})}, launcher_cfg={**SLOT, **(launcher_cfg or {})})
    o = env.orch
    o.pending = collections.deque(tasks)
    o.phase = "window"
    o.window_start = env.clock.now()
    o.open_slots([f"s{i}" for i in range(1, n_slots + 1)])
    # dispatcher and watcher only: nothing is reviewed or merged, so no bounce interferes
    o._run_thread("watcher", o._watch_loop)
    o._run_thread("dispatcher", o._dispatch_loop)
    return env


def _handed(evs, task):
    return any(e["type"] == "session_launch" and e["task"] == task or
               e["type"] == "session_message" and e["kind"] == "task" and e["task"] == task for e in evs)


def _do(env, task, slot="s1", ready=True):
    """Wait until ``task`` is handed out, then play its session: push the branch and (optionally) READY."""
    env.wait_for(lambda evs: _handed(evs, task))
    s = env.sim_session(task, slot)
    s.start_branch()
    if ready:
        s.submit()
        env.wait_for(lambda evs: count(evs, "submit", task=task) == 1)


def test_one_session_per_slot_with_follow_up_tasks_and_retirement(make_env):
    env = _env(make_env, ["001", "002", "003", "004", "005"], launcher_cfg={"tasks_per_session": 3})
    for t in ["001", "002", "003", "004"]:
        _do(env, t)
    evs = env.wait_for(lambda evs: _handed(evs, "005"))
    launches = [(e["task"], e["session_id"]) for e in evs if e["type"] == "session_launch"]
    msgs = [(e["task"], e["session_id"], e["slot"]) for e in evs if e["type"] == "session_message"]
    assert launches == [("001", "sess-001"), ("004", "sess-004")]         # a fresh session after 3 tasks
    assert msgs == [("002", "sess-001", "s1"), ("003", "sess-001", "s1"), ("005", "sess-004", "s1")]
    assert all(e["kind"] == "task" for e in evs if e["type"] == "session_message")
    assert any(e["type"] == "note" and e["text"].startswith("session_retired slot=s1 session=sess-001 tasks=3 "
                                                          "reason=tasks_per_session") for e in evs)
    # the follow-up carries the next task's full prompt
    m = [x for x in env.launcher.sent if x["task"] == "002"][0]["message"]
    assert "new, independent task" in m and "claude/task-002" in m and "READY: 002" in m and "origin/main" in m
    # claims and submits are attributed as before (branch + READY), to the slot
    assert [e["task"] for e in evs if e["type"] == "claim"] == ["001", "002", "003", "004"]
    assert env.orch.session_summary().startswith("session_per=slot sessions_launched=2 tasks_handed_out=5 "
                                                 "tasks_per_session_mean=2.50 tasks_per_session=3,2")
    assert validate_events(env.log.path) == []
    # start-up of a follow-up task runs from its kind=task message
    run = dict(window_start=evs[0]["t"], window_end="2099-01-01T00:00:00.000Z")
    legs = {x["task"]: x for x in task_legs(run, evs)}
    assert legs["002"]["followup"] and not legs["001"]["followup"]


def test_rework_waits_for_the_session_that_did_the_task(make_env):
    env = _env(make_env, ["001", "002", "003"], n_slots=2)
    _do(env, "001", "s1")
    env.wait_for(lambda evs: _handed(evs, "002"))
    _do(env, "002", "s2")                         # s2's session (sess-002) goes idle: nothing left for it
    _do(env, "003", "s1", ready=False)            # sess-001 works on 003
    ch = next(c for c in env.orch.review_q if c.task == "001")
    env.orch.bounce(ch, "review", "fix it")
    for _ in range(10):                           # s2 is free, but 001's session is busy on s1: rework waits
        env.orch.dispatch_once()
    assert count(env.events(), "session_message", kind="rework") == 0
    assert "001" in env.orch.rework_q
    env.sim_session("003", "s1").submit()
    evs = env.wait_for(lambda evs: count(evs, "session_message", kind="rework") == 1)
    rw = next(e for e in evs if e["type"] == "session_message" and e["kind"] == "rework")
    assert (rw["task"], rw["slot"], rw["session_id"]) == ("001", "s1", "sess-001")
    assert validate_events(env.log.path) == []


def test_timeout_retires_the_session_which_still_gets_its_rework(make_env):
    env = _env(make_env, ["001", "002", "003"])
    _do(env, "001")
    _do(env, "002", ready=False)                  # sess-001 never finishes 002
    env.orch.cfg.run.task_timeout_min = 0.01      # ...and times out now
    evs = env.wait_for(lambda evs: count(evs, "session_timeout", task="002") == 1)
    env.orch.cfg.run.task_timeout_min = 600
    evs = env.wait_for(lambda evs: count(evs, "session_launch", task="003") == 1)
    assert next(e for e in evs if e["type"] == "session_launch" and e["task"] == "003")["session_id"] == "sess-003"
    assert any(e["type"] == "note" and "session=sess-001" in e["text"] and "reason=timeout" in e["text"]
               for e in evs)
    s3 = env.sim_session("003", "s1")
    s3.start_branch()
    ch = next(c for c in env.orch.review_q if c.task == "001")
    env.orch.bounce(ch, "review", "fix it")
    s3.submit()                                   # s1 frees; the retired sess-001 gets 001's rework there
    evs = env.wait_for(lambda evs: count(evs, "session_message", kind="rework") == 1)
    rw = next(e for e in evs if e["type"] == "session_message" and e["kind"] == "rework")
    assert (rw["task"], rw["slot"], rw["session_id"]) == ("001", "s1", "sess-001")
    assert validate_events(env.log.path) == []


def test_failed_follow_up_requeues_the_task_on_a_fresh_session(make_env):
    env = _env(make_env, ["001", "002", "003"], launcher=FailTaskOnce())
    _do(env, "001")
    evs = env.wait_for(lambda evs: count(evs, "session_launch", task="002") == 1)
    assert count(evs, "session_message", kind="task") == 0          # the hand-out to sess-001 failed
    assert any(e["type"] == "note" and e["text"] == "task_requeued task=002 reason=followup_failed" for e in evs)
    assert any(e["type"] == "note" and "session=sess-001" in e["text"] and "reason=followup_failed" in e["text"]
               and "reachable=false" in e["text"] for e in evs)
    assert next(e for e in evs if e["type"] == "session_launch" and e["task"] == "002")["attempt_no"] == 1
    # rework for a task of the unreachable session is abandoned, not sent anywhere
    _do(env, "002")
    ch = next(c for c in env.orch.review_q if c.task == "001")
    env.orch.bounce(ch, "review", "fix it")
    evs = env.wait_for(lambda evs: any(e["type"] == "note" and e["text"].startswith("task_abandoned task=001")
                                       and "unreachable" in e["text"] for e in evs))
    assert count(evs, "session_message", kind="rework") == 0
    # 002's own claim still counts although its branch name was seen before its (re)launch
    assert count(evs, "claim", task="002") == 1
    assert validate_events(env.log.path) == []


def test_next_task_message_template():
    cfg = config_mod.load(HERE / "config.t1b.toml")
    m = next_task_message(cfg, Task("T042", "Add a thing", "Do the thing.", ["it works", "tests pass"]))
    for s in ("new, independent task", "Task T042: Add a thing", "Do the thing.", "- it works",
              "claude/task-T042", "READY: T042", "git checkout -b claude/task-T042 origin/main",
              "git merge origin/main", "Never push to `main`", "then stop"):
        assert s in m, s
    assert "{" not in m


def test_config_rejects_unknown_session_per():
    cfg = config_mod.load(HERE / "config.t1b.toml")
    cfg.launcher.session_per = "window"
    assert any("session_per" in p for p in config_mod.phase_problems(cfg))
    cfg.launcher.session_per = "slot"
    cfg.launcher.tasks_per_session = 0
    assert any("tasks_per_session" in p for p in config_mod.phase_problems(cfg))


def test_slot_mode_dry_run_passes_the_validators(tmp_path):
    cfg = config_mod.load(HERE / "config.dryrun.toml")
    cfg.run.output_dir = str(tmp_path / "runs")
    cfg.run.window_min = 60
    cfg.run.n_workers = 3
    cfg.launcher.session_per = "slot"
    cfg.launcher.tasks_per_session = 3
    cfg.sim.time_scale = 400
    cfg.sim.n_toy_tasks = 12
    run_dir, summary, errs = dry_run(cfg, "slot", seed=4)
    assert not errs, errs
    ev = read_events(run_dir / "events.jsonl")
    launches = [e for e in ev if e["type"] == "session_launch"]
    handoffs = [e for e in ev if e["type"] == "session_message" and e["kind"] == "task"]
    assert handoffs and len(launches) < len(launches) + len(handoffs)
    # every slot's sessions: a launch, then follow-ups on the same session id, at most 3 tasks each
    per = collections.Counter(e["session_id"] for e in launches + handoffs)
    assert max(per.values()) <= 3 and set(per) == {e["session_id"] for e in launches}
    run = json.loads((run_dir / "run.json").read_text())
    assert validate_run_json(run_dir / "run.json") == []
    assert f"session_per=slot sessions_launched={len(launches)} tasks_handed_out={len(launches) + len(handoffs)}" \
        in run["notes"]
    sys.path.insert(0, str(HERE / "tests"))
    from test_parallel_reviewers import _analysis_errors
    assert _analysis_errors(run_dir) == []
