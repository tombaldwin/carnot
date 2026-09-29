"""Routine launcher (`[launcher] mode = "routine"`): the RemoteTrigger calls made through a `claude -p` runner
(instruction, output parsing, re-arm body and validation, create), session id discovery, the rate budget,
disabling at window end, routines-setup and the state file, the run guards, the orchestrator path (session_launch
with a null id, launch_detail when the id is found, rework held until then) and the worker prompt. No test
calls `claude`: every runner is a fake that answers like the API."""
import copy
import datetime as dt
import json
import subprocess
import time
import uuid
from pathlib import Path

import re

import pytest

from conftest import ScriptedReviewer, count

from harness import cli
from harness import config as config_mod
from harness import routines as rt
from harness.launchers import (LaunchError, NotVerified, RoutineLauncher, require_verified, routine_client,
                               routine_state_path, slot_routines, task_prompt)
from harness.schema import validate_events
from harness.tasks import Task

HERE = Path(__file__).resolve().parent.parent
ENV_ID = "env_01REHbnKNqfeaJdQr9VjHaX5"
SRC = "https://github.com/tombaldwin/carnot-sandbox"


# ----------------------------------------------------------------------------- fakes
class FakeTime:
    def __init__(self, start=dt.datetime(2026, 10, 1, 12, 0, 0, 250000, tzinfo=dt.timezone.utc)):
        self.t = start
        self.slept = 0.0

    def now(self):
        return self.t

    def sleep(self, s):
        self.slept += s
        self.t += dt.timedelta(seconds=max(s, 0.001))


class FakeAPI:
    """Answers RemoteTrigger tool inputs like the API (shapes from the 2026-09-28 probe). Runs appear
    ``fire_delay_s`` after run_once_at, and only once the fake clock has reached that time."""

    def __init__(self, now_fn=rt.utcnow, fire_delay_s=45.0):
        self.now = now_fn
        self.fire_delay_s = fire_delay_s
        self.triggers: dict[str, dict] = {}
        self.runs: dict[str, list[dict]] = {}
        self.calls: list[dict] = []
        self.runs_visible = True
        self.tamper = None           # fn(trigger) applied to the copy returned for a re-arm
        self.status = {}             # action -> HTTP status to return instead of 200
        self.fail_disable: set[str] = set()
        self.n = 0

    def add(self, name="r", enabled=False) -> str:
        self.n += 1
        tid = f"trig_{self.n:02d}"
        self.triggers[tid] = {"id": tid, "name": name, "enabled": enabled, "run_once_at": None,
                              "mcp_connections": [{"name": "Gmail"}], "job_config": {}}
        return tid

    def handle(self, inp):
        a = inp["action"]
        if a in self.status:
            return self.status[a], {"error": {"message": "scripted"}}
        if a == "create":
            b = inp["body"]
            tid = self.add(b["name"], b.get("enabled", False))
            t = self.triggers[tid]
            t.update({k: v for k, v in b.items() if k != "mcp_connections"})
            t["mcp_connections"] = [{"name": "auto-attached"}]
            return 200, {"outcome": "CREATE_TRIGGER_OUTCOME_CREATED", "trigger": copy.deepcopy(t)}
        if a == "list":
            return 200, {"data": [copy.deepcopy(t) for t in self.triggers.values()], "has_more": False,
                         "next_cursor": None}
        tid = inp.get("trigger_id")
        if tid not in self.triggers:
            return 404, {"error": {"message": "not found"}}
        t = self.triggers[tid]
        if a == "get":
            return 200, {"trigger": copy.deepcopy(t)}
        if a == "list_runs":
            now = self.now()
            vis = [r for r in self.runs.get(tid, []) if self.runs_visible and rt.parse_time(r["created_at"]) <= now]
            return 200, {"note": "(data)", "trigger_id": tid, "next_cursor": None,
                         "data": sorted(vis, key=lambda r: r["created_at"], reverse=True)}
        if a == "update":
            b = inp["body"]
            if b.get("enabled") is False and tid in self.fail_disable:
                return 500, {"error": {"message": "scripted disable failure"}}
            for k, v in b.items():
                if k == "clear_mcp_connections":
                    t["mcp_connections"] = []
                else:
                    t[k] = copy.deepcopy(v)
            out = copy.deepcopy(t)
            if b.get("enabled") is True and "run_once_at" in b:
                self.n += 1
                at = rt.parse_time(b["run_once_at"]) + dt.timedelta(seconds=self.fire_delay_s)
                self.runs.setdefault(tid, []).append(
                    {"id": f"cse_{self.n:02d}", "created_at": at.strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
                     "status": "active", "title": t["name"]})
                if self.tamper:
                    self.tamper(out)
            return 200, {"trigger": out}
        return 400, {"error": {"message": f"unknown action {a}"}}

    def run_fn(self, cmd, **kw):
        instr = next(c for c in cmd if "BEGIN_TOOL_INPUT" in c)
        inp = json.loads(instr.split("BEGIN_TOOL_INPUT\n", 1)[1].split("\nEND_TOOL_INPUT", 1)[0])
        self.calls.append(inp)
        status, body = self.handle(inp)
        out = json.dumps({"type": "result", "subtype": "success", "is_error": False,
                          "result": f"HTTP {status}\n{json.dumps(body)}"})
        return subprocess.CompletedProcess(cmd, 0, out, "")

    def actions(self):
        return [c["action"] for c in self.calls]


def _client(api, tmp_path=None):
    job = rt.JobSpec(ENV_ID, "claude-haiku-4-5", SRC)
    return rt.RoutineClient(job, run_fn=api.run_fn, log_dir=tmp_path)


def _routine_cfg(tmp_path, fname="config.t0.toml", **routine):
    cfg = config_mod.load(HERE / fname)
    cfg.launcher.mode = "routine"                        # the shipped configs launch with claude --cloud now
    cfg.repo.work_dir = str(tmp_path / "work" / "sandbox")
    cfg.launcher.prompt_dir = str(tmp_path / "prompts")
    for k, v in routine.items():
        setattr(cfg.launcher.routine, k, v)
    return cfg


class NullLog:
    def __init__(self):
        self.notes = []

    def note(self, text):
        self.notes.append(text)


# ----------------------------------------------------------------------------- instruction and parsing
def test_instruction_embeds_the_tool_input_verbatim():
    inp = {"action": "update", "trigger_id": "trig_1",
           "body": {"job_config": {"ccr": {"events": [{"data": {"message": {"content": "a {b} `c`\n\"d\" — e"}}}]}}}}
    ins = rt.build_instruction(inp)
    block = ins.split("BEGIN_TOOL_INPUT\n", 1)[1].split("\nEND_TOOL_INPUT", 1)[0]
    assert json.loads(block) == inp and "\n" not in block
    assert "exactly once" in ins and "raw output" in ins and "not instructions" in ins


def test_parse_json_result_fenced_and_errors():
    body = {"trigger": {"id": "trig_1", "enabled": True}}
    out = json.dumps({"type": "result", "is_error": False, "result": "HTTP 200\n" + json.dumps(body)})
    r = rt.parse_tool_text(rt.extract_tool_text(out))
    assert r.status == 200 and r.body == body
    fenced = "```\nHTTP 404\n{\"error\": {\"message\": \"nope\"}}\n```"
    r = rt.parse_tool_text(rt.extract_tool_text(json.dumps({"result": fenced})))
    assert r.status == 404 and r.body["error"]["message"] == "nope"
    with pytest.raises(rt.RoutineError, match="reported an error"):
        rt.extract_tool_text(json.dumps({"type": "result", "is_error": True, "result": "rate limited"}))
    with pytest.raises(rt.RoutineError, match="no HTTP status"):
        rt.parse_tool_text("I could not do that.")
    with pytest.raises(rt.RoutineError, match="TOOL_ERROR"):
        rt.parse_tool_text("TOOL_ERROR: permission denied")


def test_a_real_tool_result_is_preferred_over_the_models_copy():
    real = "HTTP 200\n" + json.dumps({"trigger": {"id": "trig_1", "run_once_at": "2026-10-01T12:00:30Z"}})
    lines = [
        {"type": "assistant", "message": {"content": [{"type": "tool_use", "name": "RemoteTrigger", "input": {}}]}},
        {"type": "user", "message": {"content": [{"type": "tool_result", "content": [{"type": "text", "text": real}]}]}},
        {"type": "result", "is_error": False, "result": "HTTP 200\n{\"trigger\": {\"id\": \"garbled\"}}"},
    ]
    r = rt.parse_tool_text(rt.extract_tool_text("\n".join(json.dumps(x) for x in lines)))
    assert r.body["trigger"]["id"] == "trig_1"


def test_times():
    assert rt.parse_time("2026-09-28T08:00:52.660973042Z") == dt.datetime(2026, 9, 28, 8, 0, 52, 660973,
                                                                          tzinfo=dt.timezone.utc)
    assert rt.rfc3339(dt.datetime(2026, 9, 28, 8, 0, 52, 999999, tzinfo=dt.timezone.utc)) == "2026-09-28T08:00:52Z"


# ----------------------------------------------------------------------------- client
def test_rearm_sends_the_verified_update_body(tmp_path):
    api = FakeAPI()
    tid = api.add()
    at = dt.datetime(2030, 1, 1, 10, 0, 30, tzinfo=dt.timezone.utc)
    trig = _client(api, tmp_path).rearm(tid, "PROMPT {x}\nline 2", at)
    assert trig["enabled"] is True
    call = api.calls[-1]
    assert call["action"] == "update" and call["trigger_id"] == tid
    b = call["body"]
    assert set(b) == {"run_once_at", "enabled", "job_config"} and b["run_once_at"] == "2030-01-01T10:00:30Z"
    assert b["enabled"] is True
    ccr = b["job_config"]["ccr"]
    assert set(ccr) == {"environment_id", "session_context", "events"} and ccr["environment_id"] == ENV_ID
    assert ccr["session_context"] == {"model": "claude-haiku-4-5", "sources": [{"git_repository": {"url": SRC}}],
                                      "allowed_tools": ["Bash", "Read", "Write", "Edit", "Glob", "Grep"]}
    ev = ccr["events"][0]["data"]
    assert ev["session_id"] == "" and ev["type"] == "user" and ev["parent_tool_use_id"] is None
    assert ev["message"] == {"role": "user", "content": "PROMPT {x}\nline 2"}
    assert ev["uuid"] == ev["uuid"].lower() and uuid.UUID(ev["uuid"]).version == 4
    # a fresh uuid per re-arm
    _client(api).rearm(tid, "p2", at)
    assert api.calls[-1]["body"]["job_config"]["ccr"]["events"][0]["data"]["uuid"] != ev["uuid"]
    assert list(tmp_path.glob("routine-*-rearm.log"))           # raw outputs kept in the private dir


@pytest.mark.parametrize("problem", ["run_once_at", "enabled", "uuid", "prompt", "http"])
def test_rearm_not_confirmed_disarms_and_raises(problem):
    api = FakeAPI()
    tid = api.add()
    if problem == "http":
        api.status["update"] = 500
    else:
        def tamper(t):
            ev = t["job_config"]["ccr"]["events"][0]["data"]
            if problem == "run_once_at":
                t["run_once_at"] = "2030-01-01T10:05:00Z"
            elif problem == "enabled":
                t["enabled"] = False
            elif problem == "uuid":
                ev["uuid"] = str(uuid.uuid4())
            else:
                ev["message"]["content"] = "a summarised prompt"
        api.tamper = tamper
    with pytest.raises(rt.RoutineError):
        _client(api).rearm(tid, "P", dt.datetime(2030, 1, 1, 10, 0, 30, tzinfo=dt.timezone.utc))
    if problem != "http":
        assert api.calls[-1]["body"] == {"enabled": False}        # the routine is disarmed again
        assert api.triggers[tid]["enabled"] is False


def test_create_is_disabled_then_clears_mcp_connections():
    api = FakeAPI()
    trig = _client(api).create("carnot-study2-t0-s1")
    assert api.actions() == ["create", "update"]
    cb = api.calls[0]["body"]
    assert cb["name"] == "carnot-study2-t0-s1" and cb["enabled"] is False and cb["mcp_connections"] == []
    assert "clear_mcp_connections" not in cb and cb["job_config"]["ccr"]["environment_id"] == ENV_ID
    assert api.calls[1]["body"] == {"clear_mcp_connections": True, "enabled": False}
    assert trig["enabled"] is False and trig["mcp_connections"] == []


def test_new_run_after_picks_the_first_run_of_this_rearm():
    at = dt.datetime(2026, 10, 1, 12, 0, 30, tzinfo=dt.timezone.utc)
    runs = [{"id": "cse_new2", "created_at": "2026-10-01T12:03:00.1Z"},
            {"id": "cse_new1", "created_at": "2026-10-01T12:01:15.5Z"},
            {"id": "cse_old", "created_at": "2026-10-01T11:40:00Z"}]
    assert rt.new_run_after(runs, at, set())["id"] == "cse_new1"
    assert rt.new_run_after(runs, at, {"cse_new1"})["id"] == "cse_new2"
    assert rt.new_run_after(runs[2:], at, set()) is None


# ----------------------------------------------------------------------------- launcher
def _launcher(tmp_path, api, ft, n=1, **routine):
    cfg = _routine_cfg(tmp_path, **routine)
    cfg.run.n_workers = n
    tids = {f"s{k}": api.add(f"carnot-study2-t0-s{k}") for k in range(1, n + 1)}
    sent = []

    def followup(cmd, **kw):
        sent.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, "", "")
    log = NullLog()
    rl = RoutineLauncher(cfg, log, "run1", client=_client(api), now_fn=ft.now, sleep_fn=ft.sleep,
                         followup_run_fn=followup, routines=tids)
    return rl, tids, sent, log


def test_launch_rearms_with_lead_and_discovers_the_session_id_lazily(tmp_path):
    ft = FakeTime()
    api = FakeAPI(now_fn=ft.now, fire_delay_s=60)
    rl, tids, sent, _ = _launcher(tmp_path, api, ft)
    api.runs[tids["s1"]] = [{"id": "cse_old", "created_at": "2026-10-01T11:00:00Z", "status": "idle"}]
    task = Task("T145", "title", "text", ["a"])
    res = rl.launch("s1", task, "PROMPT", "run1-s1-T145")
    assert res.session_id is None and res.detached_by == "routine" and res.pending is not None
    at = res.pending.run_once_at
    assert at.microsecond == 0 and 30 <= (at - ft.now()).total_seconds() <= 31
    assert f"trigger={tids['s1']}" in res.detail and f"run_once_at={rt.rfc3339(at)}" in res.detail
    assert (rl.dir / "run1-s1-T145.prompt.md").read_text() == "PROMPT"
    n_before = len(api.calls)
    sid = rl.discover_session_id(res.pending)
    assert sid == api.runs[tids["s1"]][-1]["id"] and sid != "cse_old"
    # no list_runs before run_once_at + first_poll_delay_s (40 s), then one every poll_s (20 s) until found
    polls = api.calls[n_before:]
    assert all(c["action"] == "list_runs" for c in polls) and len(polls) == 2   # +40 s (none yet), +60 s (found)
    assert ft.now() >= at + dt.timedelta(seconds=60)
    # follow-ups go to that session through the verified followup_command
    rl.send("s1", "T145", sid, "fix it", "rework")
    assert sent[-1][:2] == ["claude", "-p"] and sent[-1][-2:] == ["--cloud", sid]


def test_discovery_times_out_and_stops_on_request(tmp_path):
    ft = FakeTime()
    api = FakeAPI(now_fn=ft.now)
    rl, tids, _, log = _launcher(tmp_path, api, ft, session_id_timeout_s=120)
    api.runs_visible = False
    res = rl.launch("s1", Task("T1", "", "x", []), "P", "n")
    assert rl.discover_session_id(res.pending) is None
    assert ft.now() >= res.pending.run_once_at + dt.timedelta(seconds=120)
    n = len(api.calls)
    assert rl.discover_session_id(res.pending, should_stop=lambda: True) is None
    assert len(api.calls) == n                                       # stopped before any poll


def test_two_rearms_of_one_slot_never_share_a_run(tmp_path):
    ft = FakeTime()
    api = FakeAPI(now_fn=ft.now, fire_delay_s=45)
    rl, tids, _, _ = _launcher(tmp_path, api, ft)
    r1 = rl.launch("s1", Task("T1", "", "x", []), "P1", "a")
    s1 = rl.discover_session_id(r1.pending)
    ft.sleep(600)
    r2 = rl.launch("s1", Task("T2", "", "x", []), "P2", "b")
    s2 = rl.discover_session_id(r2.pending)
    assert s1 and s2 and s1 != s2


def test_launch_failure_is_a_launch_error(tmp_path):
    ft = FakeTime()
    api = FakeAPI(now_fn=ft.now)
    rl, _, _, _ = _launcher(tmp_path, api, ft)
    api.status["update"] = 429
    with pytest.raises(LaunchError, match="HTTP 429"):
        rl.launch("s1", Task("T1", "", "x", []), "P", "n")


def test_rate_budget_waits_for_an_update_to_age_out(tmp_path):
    ft = FakeTime()
    api = FakeAPI(now_fn=ft.now)
    rl, _, _, log = _launcher(tmp_path, api, ft, max_updates_per_hour=2)
    t = Task("T1", "", "x", [])
    rl.launch("s1", t, "P", "a")
    rl.launch("s1", t, "P", "b")
    t0 = ft.now()
    rl.launch("s1", t, "P", "c")
    assert (ft.now() - t0).total_seconds() >= 3599
    assert any(n.startswith("routine_rate_wait") for n in log.notes)


def test_stop_all_disables_every_slot_routine_and_reports_failures(tmp_path, capsys):
    ft = FakeTime()
    api = FakeAPI(now_fn=ft.now)
    rl, tids, _, log = _launcher(tmp_path, api, ft, n=3)
    for s, t in tids.items():
        api.triggers[t]["enabled"] = True
    api.fail_disable.add(tids["s2"])
    rl.stop_all()
    assert api.triggers[tids["s1"]]["enabled"] is False and api.triggers[tids["s3"]]["enabled"] is False
    assert rl.disabled_all
    assert any(n.startswith(f"routine_disable_failed slot=s2 trigger={tids['s2']}") for n in log.notes)
    assert sum(n.startswith("routine_disabled") for n in log.notes) == 2
    assert "ROUTINES NOT DISABLED" in capsys.readouterr().out


# ----------------------------------------------------------------------------- setup, state file, guards
def test_routines_setup_creates_disabled_routines_and_reuses_them(tmp_path):
    cfg = _routine_cfg(tmp_path, fname="config.t1.toml")
    api = FakeAPI()
    out = []
    ids = cli.routines_setup(cfg, _client(api), out=out.append)
    assert list(ids) == [f"s{k}" for k in range(1, 13)]
    assert api.actions().count("create") == 12 and api.actions().count("list") == 1   # one lookup by name
    names = {api.triggers[t]["name"] for t in ids.values()}
    assert names == {f"carnot-study2-t1-s{k}" for k in range(1, 13)}
    assert all(api.triggers[t]["enabled"] is False for t in ids.values())
    state = routine_state_path(cfg)
    assert state == (tmp_path / "work" / "routines-state.json").resolve() and state.exists()
    assert slot_routines(cfg) == ids
    # a second run looks them up (get) and creates nothing; one found enabled is disabled
    api.triggers[ids["s3"]]["enabled"] = True
    api.calls.clear()
    assert cli.routines_setup(cfg, _client(api), out=out.append) == ids
    assert "create" not in api.actions() and api.actions().count("get") == 12
    assert api.triggers[ids["s3"]]["enabled"] is False
    # [launcher.routine] slot_routines overrides the state file
    cfg.launcher.routine.slot_routines = {"s1": "trig_manual"}
    assert slot_routines(cfg)["s1"] == "trig_manual"


def test_routines_setup_dry_run_makes_no_calls(tmp_path):
    cfg = _routine_cfg(tmp_path)
    api = FakeAPI()
    out = []
    assert cli.routines_setup(cfg, _client(api), dry_run=True, out=out.append) == {}
    assert api.calls == [] and "would create" in out[0]


def test_routine_state_file_is_refused_inside_the_public_repo(tmp_path):
    cfg = _routine_cfg(tmp_path, state_file=str(HERE / "routines-state.json"))
    with pytest.raises(SystemExit, match="inside the public repo"):
        routine_state_path(cfg)


def test_run_guards_for_routine_mode(tmp_path):
    cfg = _routine_cfg(tmp_path)                         # t0
    cfg.launcher.verified = False
    with pytest.raises(NotVerified, match="routines-setup"):
        require_verified(cfg)                            # no slot routines yet
    rt.save_state(routine_state_path(cfg), {"routines": {"carnot-study2-t0-s1": {"trigger_id": "trig_x"}}})
    require_verified(cfg)                                # T0 may run the unverified routine path
    cfg1 = _routine_cfg(tmp_path, fname="config.t1.toml")
    cfg1.launcher.verified = False                       # shipped true since T0c; the guard must still refuse
    with pytest.raises(NotVerified, match="routine mode is UNVERIFIED"):
        require_verified(cfg1)
    cfg.launcher.routine.environment_id = ""
    with pytest.raises(NotVerified, match="environment_id is empty"):
        require_verified(cfg)


def test_phase_check_ties_the_routine_to_the_sandbox_and_worker_model(tmp_path):
    cfg = _routine_cfg(tmp_path)
    assert config_mod.phase_problems(cfg) == []
    cfg.launcher.routine.source_url = "https://github.com/tombaldwin/carnot"
    cfg.launcher.routine.model = "claude-sonnet-5"
    cfg.launcher.routine.lead_s = 5
    probs = " ".join(config_mod.phase_problems(cfg))
    assert "source_url" in probs and "worker_model" in probs and "lead_s" in probs


def test_run_with_routine_config_reaches_confirmation(tmp_path, capsys, monkeypatch):
    text = (HERE / "config.t0.toml").read_text()
    text = text.replace('work_dir = "/Users/tom/git/carnot-sandbox-tasks/work/sandbox"',
                        f'work_dir = "{tmp_path}/work/sandbox"')
    text = re.sub(r'(\[launcher\][\s\S]*?\n)mode = "\w+"', r'\1mode = "routine"', text, count=1)
    p = tmp_path / "config.t0.toml"
    p.write_text(text)
    cfg = config_mod.load(p)
    rt.save_state(routine_state_path(cfg), {"routines": {"carnot-study2-t0-s1": {"trigger_id": "trig_x"}}})
    monkeypatch.setattr(cli, "input_fn", lambda prompt: "no")
    assert cli.main(["run", "--config", str(p), "--run-id", "x"]) == 2
    out = capsys.readouterr().out
    assert "launcher       routine   lead 30 s" in out


# ----------------------------------------------------------------------------- orchestrator path
def _env_with_routines(make_env, tmp_path, api, reviewer=None, n_tasks=10, **routine):
    env = make_env(launcher=None, n_slots=1, n_tasks=n_tasks, reviewer=reviewer)
    env.cfg.launcher.mode = "routine"
    env.cfg.launcher.prompt_dir = str(tmp_path / "private-prompts")
    r = env.cfg.launcher.routine
    r.environment_id, r.source_url, r.lead_s = ENV_ID, SRC, 0
    r.first_poll_delay_s, r.poll_s, r.session_id_timeout_s = 0, 0.05, 60
    for k, v in routine.items():
        setattr(r, k, v)
    sent = []

    def followup(cmd, **kw):
        sent.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, "", "")
    tid = api.add("carnot-study2-x-s1")
    rl = RoutineLauncher(env.cfg, env.log, env.run_id, client=_client(api), followup_run_fn=followup,
                         routines={"s1": tid})
    env.orch.launcher = rl
    return env, rl, tid, sent


def test_orchestrator_logs_launch_with_null_id_then_the_found_id(make_env, tmp_path):
    api = FakeAPI(fire_delay_s=0)
    env, rl, tid, _ = _env_with_routines(make_env, tmp_path, api)
    env.start(dispatch=True)
    env.orch.open_slots(["s1"])
    evs = env.wait_for(lambda evs: any("session_id_found=true" in e.get("text", "") for e in evs))
    launch = [e for e in evs if e["type"] == "session_launch"][0]
    assert launch["session_id"] is None and launch["attempt_no"] == 1
    notes = [e["text"] for e in evs if e["type"] == "note" and e["text"].startswith("launch_detail")]
    assert "detached_by=routine" in notes[0] and f"trigger={tid}" in notes[0] and "run_once_at=" in notes[0]
    sid = api.runs[tid][0]["id"]
    assert f"session_id={sid}" in notes[1]
    assert env.orch.sessions[launch["task"]].session_id == sid
    assert api.triggers[tid]["enabled"] is True
    assert validate_events(env.log.path) == []


def test_rework_waits_for_the_session_id(make_env, tmp_path):
    api = FakeAPI(fire_delay_s=0)
    api.runs_visible = False
    rev = ScriptedReviewer({"001": ["request_changes", "approve"]})
    env, rl, tid, sent = _env_with_routines(make_env, tmp_path, api, reviewer=rev, n_tasks=1)
    env.start(dispatch=True)
    env.orch.open_slots(["s1"])
    env.wait_for(lambda evs: count(evs, "session_launch") == 1)
    s = env.sim_session("001", "s1")
    s.start_branch()
    s.submit()
    env.wait_for(lambda evs: count(evs, "bounce", task="001") == 1 and count(evs, "slot_idle") >= 1)
    time.sleep(0.5)
    assert count(env.events(), "session_message") == 0 and not sent       # held: no session id yet
    assert "001" in env.orch.rework_q
    api.runs_visible = True
    evs = env.wait_for(lambda evs: count(evs, "session_message", task="001", kind="rework") == 1)
    sid = api.runs[tid][0]["id"]
    assert [e for e in evs if e["type"] == "session_message"][0]["session_id"] == sid
    assert sent[0][-2:] == ["--cloud", sid]
    assert validate_events(env.log.path) == []


def test_unconfirmed_rearm_is_a_failed_launch_and_retried_once(make_env, tmp_path):
    api = FakeAPI(fire_delay_s=0)
    api.tamper = lambda t: t.update(run_once_at="2000-01-01T00:00:00Z")
    env, rl, tid, _ = _env_with_routines(make_env, tmp_path, api)
    env.start(dispatch=True)
    env.orch.open_slots(["s1"])
    evs = env.wait_for(lambda evs: sum(e["type"] == "note" and e["text"].startswith("task_abandoned")
                                       for e in evs) >= 1)
    fails = [e["text"] for e in evs if e["type"] == "note" and e["text"].startswith("session_launch_failed")]
    assert len(fails) >= 2 and "attempt=1" in fails[0] and "attempt=2" in fails[1] and "run_once_at" in fails[0]
    first = next(e["text"] for e in evs if e["type"] == "note" and e["text"].startswith("task_abandoned"))
    assert "launch failed" in first
    assert api.triggers[tid]["enabled"] is False                      # left disarmed


# ----------------------------------------------------------------------------- prompts
def test_worker_prompt_has_no_push_token_step_and_authorises_the_push(tmp_path):
    cfg = _routine_cfg(tmp_path)
    t = Task("T145", "Add {x}", "Use a dict like {'a': 1}.", ["{b} works"])
    short = task_prompt(cfg, t)                    # routine mode: the short prompt, rules in HARNESS.md
    assert "authorised to create the branch claude/task-T145" in short and "HARNESS.md" in short
    cfg.launcher.mode = "command"                  # worker.md (the full prompt) is the command-mode prompt
    p = task_prompt(cfg, t)
    assert "SANDBOX_PUSH_TOKEN" not in p and "credential.helper" not in p and "\n0. " not in p
    assert "authorised to create the branch below" in p and "without asking for confirmation" in p
    assert "claude/task-T145" in p and "READY: T145" in p and "{'a': 1}" in p and "<!--" not in p
    assert p.index("authorised") < p.index("## Task T145")


def test_persisted_tool_output_is_read_from_file_and_deleted(tmp_path):
    import json as _json
    from harness.routines import extract_tool_text, parse_tool_text
    full = tmp_path / "toolu_x.txt"
    full.write_text('HTTP 200\n{"data":[{"id":"trig_a","prompt":"line1\nline2"}]}')
    preview = (f"<persisted-output>\nOutput too large (234.4KB). Full output saved to: {full}\n\n"
               "Preview (first 2KB):\nHTTP 200\n{\"data\":[{\"id\":\"tr")
    line = _json.dumps({"type": "user", "message": {"content": [
        {"type": "tool_result", "tool_use_id": "toolu_x", "content": preview}]}})
    r = parse_tool_text(extract_tool_text(line + "\n"))
    assert r.status == 200 and r.body["data"][0]["id"] == "trig_a"
    assert not full.exists()


def test_routine_mode_prompt_is_short_and_reset_doc_renders():
    from harness.config import load
    from harness.launchers import task_prompt, harness_doc
    from pathlib import Path
    cfg = load(Path(__file__).resolve().parents[1] / "config.t0.toml")
    cfg.launcher.mode = "routine"                  # the shipped configs use command mode now

    class T:
        id, title, text, acceptance = "T145", "a title", "long task text " * 200, ["crit"]
    p = task_prompt(cfg, T)
    assert len(p) < 600 and "T145" in p and "claude/task-T145" in p and "HARNESS.md" in p
    assert "long task text" not in p and not p.endswith("\n")
    doc = harness_doc(cfg)
    assert "<TASK>" in doc and "{" not in doc and "READY: <TASK>" in doc and not doc.startswith("<!--")


def test_rearm_in_flight_at_window_end_is_disabled_again_and_later_launches_refused(tmp_path):
    ft = FakeTime()
    api = FakeAPI(now_fn=ft.now)
    rl, tids, _, log = _launcher(tmp_path, api, ft, n=1)
    real = api.handle

    def handle(inp):                     # window end lands while the re-arm call is in flight
        if inp["action"] == "update" and inp["body"].get("enabled") is True:
            rl.disable_all()
        return real(inp)
    api.handle = handle
    t = Task("T007", "t", "x", [])
    with pytest.raises(LaunchError, match="window closed during the re-arm"):
        rl.launch("s1", t, "p", "n1")
    assert api.triggers[tids["s1"]]["enabled"] is False
    assert any("why=late_rearm" in n for n in log.notes)
    api.handle = real
    n_calls = len(api.calls)
    with pytest.raises(LaunchError, match="window closed"):
        rl.launch("s1", t, "p", "n2")
    assert len(api.calls) == n_calls     # refused without calling the API


def test_rate_wait_ends_when_the_window_closes(tmp_path):
    import threading
    ft = FakeTime()
    api = FakeAPI(now_fn=ft.now)
    rl, tids, _, log = _launcher(tmp_path, api, ft, n=1)
    trig = tids["s1"]
    rl.updates[trig] = [ft.now()] * rl.r.max_updates_per_hour      # budget spent: next launch must wait ~1 h
    real_sleep = rl.sleep

    def sleep(s):                                                    # the window closes 10 s into the wait
        real_sleep(s)
        if ft.slept >= 10:
            rl.closing.set()
    rl.sleep = sleep
    with pytest.raises(LaunchError, match="waited for its update budget"):
        rl.launch("s1", Task("T1", "t", "x", []), "p", "n")
    assert ft.slept < 60
