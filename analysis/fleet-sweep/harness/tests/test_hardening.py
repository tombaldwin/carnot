"""Hardening after the T1 incident (2026-09-28 17:49 UTC: an auto-update replaced the `claude` binary, every
process start failed, one slot abandoned ~15 tasks in a second and burnt its routine's hourly budget):
transient start failures are retried, a re-arm that never started refunds its budget entry, a slot backs off
after a failed launch and is marked down after repeated failures, a global breaker pauses dispatch, and `run`
pins the CLI to a private copy. No test calls `claude`."""
import datetime as dt
import json
import subprocess
from pathlib import Path

import pytest

from conftest import FakeLauncher, count
from test_routines import FakeAPI, FakeTime, _client, _launcher

from harness import cli
from harness import config as config_mod
from harness import routines as rt
from harness.launchers import LaunchError, pin_cli, run_followup
from harness.review import CommandReviewer, ReviewPacket
from harness.schema import validate_events
from harness.tasks import Task

HERE = Path(__file__).resolve().parent.parent


def _flaky(run_fn, fails: int):
    """``run_fn`` that raises FileNotFoundError (as while the binary is being replaced) ``fails`` times first."""
    state = {"n": 0}

    def fn(cmd, **kw):
        state["n"] += 1
        if state["n"] <= fails:
            raise FileNotFoundError(2, "No such file or directory", cmd[0])
        return run_fn(cmd, **kw)
    fn.state = state
    return fn


# ----------------------------------------------------------------------------- transient start failures
def test_routine_call_retries_a_process_that_could_not_start():
    api = FakeAPI()
    slept = []
    c = rt.RoutineClient(rt.JobSpec("env", "m", "src"), run_fn=_flaky(api.run_fn, 2), sleep_fn=slept.append)
    tid = api.add("x")
    assert c.get(tid)["id"] == tid
    assert slept == [5, 5] and api.actions() == ["get"]


def test_routine_call_gives_up_after_three_retries_with_not_started():
    slept = []
    c = rt.RoutineClient(rt.JobSpec("env", "m", "src"), run_fn=_flaky(None, 99), sleep_fn=slept.append)
    with pytest.raises(rt.RoutineNotStarted, match=r"could not start \(4 attempts\)"):
        c.get("trig_x")
    assert slept == [5, 5, 5]


def test_followup_retries_start_then_delivers_or_raises(tmp_path):
    cfg = config_mod.from_dict({})
    sent, slept = [], []

    def ok(cmd, **kw):
        sent.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, "", "")
    run_followup(cfg, tmp_path, "r", "s1", "T1", "cse_1", "msg", "rework", tmp_path, run_fn=_flaky(ok, 1),
                 sleep_fn=slept.append)
    assert len(sent) == 1 and slept == [5.0]
    with pytest.raises(LaunchError, match="follow-up could not start"):
        run_followup(cfg, tmp_path, "r", "s1", "T1", "cse_1", "msg", "rework", tmp_path,
                     run_fn=_flaky(ok, 99), sleep_fn=slept.append)


def test_reviewer_retries_a_process_that_could_not_start(tmp_path):
    cfg = config_mod.from_dict({"reviewer": {"job": "diff", "command": ["claude", "-p"]}})
    cfg.reviewer.prompt_template = str(HERE / "prompts/reviewer-diff.md")
    out = json.dumps({"result": "APPROVE\nfine", "usage": {"input_tokens": 3, "output_tokens": 1}})
    slept = []
    run_fn = _flaky(lambda cmd, **kw: subprocess.CompletedProcess(cmd, 0, out, ""), 1)
    r = CommandReviewer(cfg, run_fn=run_fn, sleep_fn=slept.append)
    pk = ReviewPacket(Task("001", "t", "x", ["a"]), "abc", "def", "diff\n+1\n", ["x"], True, "ok")
    assert r.review(pk).verdict == "approve" and slept == [5]


# ----------------------------------------------------------------------------- rate budget refund
def test_rearm_that_never_started_refunds_its_budget_entry(tmp_path):
    ft = FakeTime()
    api = FakeAPI(now_fn=ft.now)
    rl, tids, _, log = _launcher(tmp_path, api, ft, max_updates_per_hour=2)
    rl.client.run_fn, rl.client.sleep = _flaky(api.run_fn, 99), ft.sleep
    t = Task("T1", "", "x", [])
    for name in ("a", "b", "c"):
        with pytest.raises(LaunchError, match="could not start"):
            rl.launch("s1", t, "P", name)
    assert rl.updates[tids["s1"]] == []                                  # nothing counted
    assert not any(n.startswith("routine_rate_wait") for n in log.notes)
    assert sum(n.startswith("routine_budget_refund") for n in log.notes) == 3
    rl.client.run_fn = api.run_fn                                        # the binary is back
    rl.launch("s1", t, "P", "d")
    assert len(rl.updates[tids["s1"]]) == 1
    api.status["update"] = 429                                           # a call that ran keeps its entry
    with pytest.raises(LaunchError, match="HTTP 429"):
        rl.launch("s1", t, "P", "e")
    assert len(rl.updates[tids["s1"]]) == 2


# ----------------------------------------------------------------------------- slot back-off and breakers
class ManualClock:
    def __init__(self):
        self.t = dt.datetime(2026, 10, 1, 12, 0, tzinfo=dt.timezone.utc)

    def now(self):
        return self.t

    def advance(self, s):
        self.t += dt.timedelta(seconds=s)


def _orch(make_env, fl, n_slots=1, **run):
    env = make_env(launcher=fl, n_slots=n_slots, run_cfg=run)
    clk = ManualClock()
    env.orch.clock = clk
    env.orch.phase = "window"
    env.orch.open_slots([f"s{i}" for i in range(1, n_slots + 1)])
    return env, clk


def _notes(evs, prefix):
    return [e["text"] for e in evs if e["type"] == "note" and e["text"].startswith(prefix)]


def test_failed_launch_backs_the_slot_off_then_success_resets(make_env):
    fl = FakeLauncher(fail_launch=2)                  # both attempts of the first task fail
    env, clk = _orch(make_env, fl, launch_fail_backoff_s=60)
    env.orch.dispatch_once()
    env.wait_for(lambda evs: _notes(evs, "slot_backoff"))
    assert env.orch.slots["s1"].fail_streak == 1 and len(_notes(env.events(), "task_abandoned")) == 1
    env.orch.dispatch_once()
    clk.advance(59)
    env.orch.dispatch_once()
    assert count(env.events(), "slot_busy") == 1      # no new task during the back-off
    clk.advance(2)
    env.orch.dispatch_once()
    env.wait_for(lambda evs: count(evs, "session_launch") == 1)
    assert count(env.events(), "slot_busy") == 2 and env.orch.slots["s1"].fail_streak == 0
    assert validate_events(env.log.path) == []


def test_repeated_launch_failures_mark_the_slot_down_and_stop_dispatch(make_env, capsys):
    fl = FakeLauncher(fail_launch=1000)
    env, clk = _orch(make_env, fl, launch_fail_backoff_s=10, max_consecutive_launch_failures=3)
    for k in range(1, 4):
        env.orch.dispatch_once()
        env.wait_for(lambda evs: len(_notes(evs, "task_abandoned")) == k
                     and (len(_notes(evs, "slot_backoff")) == k or count(evs, "worker_down") == 1))
        clk.advance(11)
    evs = env.events()
    down = [e for e in evs if e["type"] == "worker_down"]
    assert len(down) == 1 and down[0]["worker"] == "s1"
    assert down[0]["reason"].startswith("launch_failures n=3 last_error=scripted launch failure")
    clk.advance(3600)
    env.orch.dispatch_once()
    assert count(env.events(), "slot_busy") == 3 and env.orch.slots["s1"].down
    assert "SLOT s1 DOWN" in capsys.readouterr().out
    assert validate_events(env.log.path) == []


def test_failures_on_several_slots_pause_all_dispatch(make_env, capsys):
    fl = FakeLauncher(fail_launch=6)                  # the first task on each of 3 slots fails (2 attempts each)
    env, clk = _orch(make_env, fl, n_slots=3, launch_fail_backoff_s=60, max_consecutive_launch_failures=10)
    env.orch.dispatch_once()
    evs = env.wait_for(lambda evs: _notes(evs, "dispatch_paused"))
    p = _notes(evs, "dispatch_paused")[0]
    assert "reason=launch_failures slots=s1,s2,s3" in p and "pause_s=60" in p
    env.wait_for(lambda evs: len(_notes(evs, "task_abandoned")) == 3)
    clk.advance(30)
    env.orch.dispatch_once()
    assert count(env.events(), "slot_busy") == 3
    clk.advance(31)
    env.orch.dispatch_once()
    evs = env.wait_for(lambda evs: count(evs, "session_launch") == 3)
    assert _notes(evs, "dispatch_resumed") and count(evs, "slot_busy") == 6
    assert "DISPATCH PAUSED" in capsys.readouterr().out


# ----------------------------------------------------------------------------- CLI pinning
def _fake_cli(tmp_path, version="2.1.283"):
    """An installed-CLI stand-in: a script behind a symlink on a fake PATH (never the real `claude`)."""
    inst = tmp_path / "install"
    inst.mkdir(exist_ok=True)
    exe = inst / "claude.exe"
    exe.write_text(f"#!/bin/sh\necho '{version} (Claude Code)'\n")
    exe.chmod(0o755)
    bindir = tmp_path / "path-bin"
    bindir.mkdir(exist_ok=True)
    link = bindir / "claude"
    if not link.is_symlink():
        link.symlink_to(exe)
    return str(link), exe


def _t1_cfg(tmp_path):
    cfg = config_mod.load(HERE / "config.t1.toml")
    cfg.launcher.prompt_dir = str(tmp_path / "private" / "prompts")
    return cfg


def test_pin_cli_copies_checks_and_substitutes_every_command(tmp_path):
    cfg = _t1_cfg(tmp_path)
    link, exe = _fake_cli(tmp_path)
    before = (list(cfg.launcher.routine.runner), list(cfg.launcher.followup_command),
              list(cfg.reviewer.command), list(cfg.launcher.launch_command))
    info = pin_cli(cfg, which_fn=lambda name: link)
    dest = tmp_path / "private" / "prompts" / "bin" / "claude-2.1.283"
    assert info == {"path": str(dest), "version": "2.1.283", "source": str(exe.resolve()), "reused": False}
    assert dest.read_bytes() == exe.read_bytes() and dest.stat().st_mode & 0o111
    assert cfg.launcher.routine.runner == [str(dest)] + before[0][1:]
    assert cfg.launcher.followup_command == [str(dest)] + before[1][1:]
    assert cfg.reviewer.command == [str(dest)] + before[2][1:]
    # T1's launch_command wraps claude in `script -q /dev/null`; its claude is swapped, nothing else
    i = before[3].index("claude")
    assert cfg.launcher.launch_command == before[3][:i] + [str(dest)] + before[3][i + 1:]
    # a second run reuses the copy of the same version
    mtime = dest.stat().st_mtime_ns
    cfg2 = _t1_cfg(tmp_path)
    assert pin_cli(cfg2, which_fn=lambda name: link)["reused"] is True and dest.stat().st_mtime_ns == mtime
    # a new version gets its own copy; the old one is left
    _, exe = _fake_cli(tmp_path, "2.1.284")
    assert pin_cli(_t1_cfg(tmp_path), which_fn=lambda name: link)["path"].endswith("claude-2.1.284")
    assert dest.exists()


def test_pin_cli_refuses_the_public_repo_and_a_broken_binary(tmp_path):
    cfg = _t1_cfg(tmp_path)
    link, exe = _fake_cli(tmp_path)
    inside = HERE / "pinned-cli-test"
    with pytest.raises(SystemExit, match="inside the public repo"):
        pin_cli(cfg, dest_dir=inside, which_fn=lambda name: link)
    assert not inside.exists()
    exe.write_text("#!/bin/sh\nexit 3\n")
    with pytest.raises(SystemExit, match="--version"):
        pin_cli(cfg, which_fn=lambda name: link)
    with pytest.raises(SystemExit, match="no `claude` on PATH"):
        pin_cli(cfg, which_fn=lambda name: None)
    assert cfg.reviewer.command[0] == "claude"                            # untouched on failure


class _Stop(Exception):
    pass


def _run_until_work_repo(tmp_path, monkeypatch, cfg):
    """cmd_run as far as the work clone (after pinning and the first log lines), then stop."""
    cfg.launcher.mode = "manual"
    cfg.run.output_dir = str(tmp_path / "runs")
    run_dir = tmp_path / "runs" / "x"
    run_dir.mkdir(parents=True)
    (run_dir / "reset.json").write_text(json.dumps({"sandbox_commit": "abc", "seed": 1}))
    monkeypatch.setattr(cli, "_cfg", lambda args: cfg)
    import harness.reset

    def stop(cfg):
        raise _Stop()
    monkeypatch.setattr(harness.reset, "work_repo", stop)
    with pytest.raises(_Stop):
        cli.main(["run", "--config", "unused.toml", "--run-id", "x", "--yes"])
    return [json.loads(l) for l in (run_dir / "events.jsonl").read_text().splitlines()]


def test_run_pins_the_cli_and_logs_it(tmp_path, monkeypatch):
    cfg = _t1_cfg(tmp_path)
    link, _ = _fake_cli(tmp_path)
    monkeypatch.setattr(cli, "pin_cli_fn", lambda c: pin_cli(c, which_fn=lambda name: link))
    evs = _run_until_work_repo(tmp_path, monkeypatch, cfg)
    note = evs[0]["text"]
    assert note.startswith("cli_pinned path=") and "version=2.1.283" in note and "source=" in note
    assert cfg.launcher.routine.runner[0].endswith("claude-2.1.283")
    assert cfg.reviewer.command[0].endswith("claude-2.1.283")


def test_run_with_pin_cli_false_leaves_commands_untouched(tmp_path, monkeypatch):
    cfg = _t1_cfg(tmp_path)
    cfg.run.pin_cli = False
    before = (list(cfg.launcher.routine.runner), list(cfg.launcher.followup_command),
              list(cfg.reviewer.command), list(cfg.launcher.launch_command))

    def never(c):
        raise AssertionError("pin_cli called with pin_cli = false")
    monkeypatch.setattr(cli, "pin_cli_fn", never)
    evs = _run_until_work_repo(tmp_path, monkeypatch, cfg)
    assert evs[0]["text"].startswith("cli_pinned disabled")
    assert before == (cfg.launcher.routine.runner, cfg.launcher.followup_command, cfg.reviewer.command,
                      cfg.launcher.launch_command)
    assert config_mod.RunCfg().pin_cli is True
