"""Harness changes after the review of PLAN-v7: branch attribution (case-insensitive, suffixes, ambiguity),
`merged_main` on submit, reviewer rate-limit notes, the `log --plan-usage` shortcut, and session retirement by
message count. Offline: toy sandbox, fake reviewer commands."""
import collections
import json
import sys

import pytest
from conftest import FakeLauncher, ScriptedReviewer, count

from harness import cli
from harness import config as config_mod
from harness.review import CommandReviewer, ReviewPacket, ReviewRateLimited, is_rate_limit_text
from harness.schema import validate_events
from harness.tasks import Task


# ---------------------------------------------------------------------------------------------- branch attribution
def test_branch_names_are_matched_case_insensitively_with_suffixes(make_env):
    env = make_env()
    o = env.orch
    for tid in ("T039", "T06", "T066", "T1", "T1-2"):
        o.tasks[tid] = Task(tid, "t", "x", [])
    assert o.task_for_branch("claude/task-T039") == ("T039", "exact")
    assert o.task_for_branch("claude/task-t039") == ("T039", "case")
    assert o.task_for_branch("claude/Task-T039") == ("T039", "exact")
    assert o.task_for_branch("claude/task-t039-fix-the-parser") == ("T039", "suffix")   # T0e's mis-named branch
    assert o.task_for_branch("claude/task-t066_x") == ("T066", "suffix")                # not T06: '6' is no separator
    assert o.task_for_branch("claude/task-t06.v2") == ("T06", "suffix")
    assert o.task_for_branch("claude/task-t1-2-x") == (None, "ambiguous")               # T1 and T1-2 both fit
    assert o.task_for_branch("claude/task-t0399") == ("t0399", "unknown")               # no known id: as before
    assert o.task_for_branch("claude/other") == (None, "")


def test_a_suffixed_lower_case_branch_is_claimed_and_submitted(make_env):
    env = make_env()
    w = env.session("001", "s1", start_branch=False)
    w.branch = "claude/task-001-add-slugify"          # a session that appended to its branch name
    w.start_branch()
    env.start()
    head = w.submit()
    evs = env.wait_for(lambda evs: count(evs, "submit") == 1)
    assert count(evs, "claim", task="001", branch="claude/task-001-add-slugify") == 1
    assert next(e for e in evs if e["type"] == "submit")["head"] == head
    assert any(e["type"] == "note" and e["text"] == "task 001 pushed on branch claude/task-001-add-slugify, "
               "not claude/task-001" for e in evs)
    assert not any(e["type"] == "note" and "never launched" in e["text"] for e in evs)


def test_an_ambiguous_branch_is_not_attributed(make_env):
    env = make_env()
    env.orch.tasks["001-b"] = Task("001-b", "t", "x", [])
    w = env.session("001", "s1", start_branch=False)
    w.branch = "claude/task-001-b-x"
    w.start_branch()
    env.orch.phase = "window"
    env.orch.poll()
    evs = env.events()
    assert count(evs, "claim") == 0
    assert any(e["type"] == "note" and e["text"].startswith("ambiguous branch claude/task-001-b-x") for e in evs)


# ---------------------------------------------------------------------------------------------- merged_main
def test_submit_logs_whether_the_branch_merged_main(make_env):
    env = make_env()
    a = env.session("004", "s1")
    b = env.session("005", "s2")
    env.start()
    a.submit()
    env.wait_for(lambda evs: count(evs, "merge", task="004") == 1)      # main moves
    b.submit()
    env.wait_for(lambda evs: count(evs, "submit", task="005") == 1)
    env.orch.sessions["005"].status = "working"
    b.rework()                                                            # merges origin/main into its branch
    evs = env.wait_for(lambda evs: count(evs, "submit", task="005") == 2)
    subs = [e for e in evs if e["type"] == "submit"]
    assert [(e["task"], e["attempt_no"], e["merged_main"]) for e in subs] == [
        ("004", 1, False), ("005", 1, False), ("005", 2, True)]
    assert validate_events(env.log.path) == []


# ---------------------------------------------------------------------------------------------- reviewer rate limits
def test_rate_limit_text():
    for t in ("Claude AI usage limit reached|1759300000", "API Error: 429 Too Many Requests", "rate_limit_error",
              "Overloaded", "You've hit your limit · resets at 5pm"):
        assert is_rate_limit_text(t), t
    for t in ("reviewer exit 1: Traceback ... KeyError", "no verdict in reviewer output"):
        assert not is_rate_limit_text(t), t


def _packet():
    return ReviewPacket(Task("001", "t", "x", ["a"]), "abc", "def", "diff\n+1\n", ["x"], True, "ok")


@pytest.mark.parametrize("script", [
    "import sys; sys.stderr.write('API Error: 429 rate_limit_error'); sys.exit(1)",
    "import json; print(json.dumps({'is_error': True, 'result': 'Claude AI usage limit reached|1759300000'}))",
])
def test_command_reviewer_raises_rate_limited(tmp_path, script):
    f = tmp_path / "fake.py"          # a file: the command template is .format()ted, so no braces inline
    f.write_text(script)
    cfg = config_mod.from_dict({"reviewer": {"job": "diff", "prompt_on_stdin": False,
                                             "command": [sys.executable, str(f)]}})
    cfg.reviewer.prompt_template = str(__import__("pathlib").Path(__file__).parent.parent / "prompts/reviewer-diff.md")
    with pytest.raises(ReviewRateLimited):
        CommandReviewer(cfg).review(_packet())


class LimitedReviewer(ScriptedReviewer):
    def review(self, packet):
        if not getattr(self, "failed", False):
            self.failed = True
            raise ReviewRateLimited("reviewer exit 1: API Error: 429 rate_limit_error")
        return super().review(packet)


def test_rate_limited_review_is_noted(make_env):
    env = make_env(reviewer=LimitedReviewer())
    w = env.session("001", "s1")
    env.start()
    w.submit()
    evs = env.wait_for(lambda evs: count(evs, "review_end") == 1)
    notes = [e["text"] for e in evs if e["type"] == "note" and e["text"].startswith("reviewer_rate_limited")]
    assert len(notes) == 1 and "reviewer=r1 task=001" in notes[0] and "429" in notes[0]


# ---------------------------------------------------------------------------------------------- plan usage
def test_log_plan_usage(tmp_path, capsys):
    cfgf = tmp_path / "c.toml"
    cfgf.write_text(f'[run]\noutput_dir = "{tmp_path / "runs"}"\n')
    assert cli.main(["log", "--config", str(cfgf), "--run-id", "T1b", "--plan-usage",
                     "session=37% week=12%  src=claude.ai/settings"]) == 0
    ev = json.loads((tmp_path / "runs" / "T1b" / "events.jsonl").read_text().splitlines()[-1])
    assert ev["type"] == "note" and ev["text"] == "plan_usage session=37% week=12% src=claude.ai/settings"
    assert cli.main(["log", "--config", str(cfgf), "--run-id", "T1b"]) == 2       # neither --type nor --plan-usage


# ---------------------------------------------------------------------------------------------- messages per session
def _slot_env(make_env, tasks, **launcher_cfg):
    env = make_env(reviewer=ScriptedReviewer(), launcher=FakeLauncher(), n_slots=1, run_cfg={"task_timeout_min": 600},
                   launcher_cfg={"session_per": "slot", **launcher_cfg})
    o = env.orch
    o.pending = collections.deque(tasks)
    o.phase = "window"
    o.window_start = env.clock.now()
    o.open_slots(["s1"])
    o._run_thread("watcher", o._watch_loop)
    o._run_thread("dispatcher", o._dispatch_loop)
    return env


def _handed(evs, task):
    return any(e["type"] == "session_launch" and e["task"] == task or
               e["type"] == "session_message" and e["kind"] == "task" and e["task"] == task for e in evs)


def test_session_retired_by_message_count(make_env):
    env = _slot_env(make_env, ["001", "002", "003", "004"], tasks_per_session=4, messages_per_session=3)
    for t in ["001", "002"]:
        env.wait_for(lambda evs: _handed(evs, t))
        s = env.sim_session(t, "s1")
        s.start_branch()
        s.submit()
        env.wait_for(lambda evs: count(evs, "submit", task=t) == 1)
    # a rework message to the same session: its third message
    ch = next(c for c in env.orch.review_q if c.task == "001")
    env.orch.bounce(ch, "review", "fix it")
    env.wait_for(lambda evs: _handed(evs, "003"))                  # 003 went out first (its hand-out was queued)
    s3 = env.sim_session("003", "s1")
    s3.start_branch()
    s3.submit()
    evs = env.wait_for(lambda evs: count(evs, "session_message", kind="rework") == 1)
    env.orch.sessions["001"].status = "working"
    env.sim_session("001", "s1").rework()
    evs = env.wait_for(lambda evs: _handed(evs, "004"))
    launches = [e["task"] for e in evs if e["type"] == "session_launch"]
    assert launches == ["001", "004"]           # 3 tasks + 1 rework >= 3 messages: fresh session for 004
    assert any(e["type"] == "note" and "reason=messages_per_session" in e["text"] for e in evs)
