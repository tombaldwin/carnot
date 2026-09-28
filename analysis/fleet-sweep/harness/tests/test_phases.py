"""Per-phase configs (PLAN-v4 section 7): the shipped files match their phases, `run` refuses a
config whose kind / N / window differ from the named phase, prints the banner and asks for
confirmation; T1's start schedule starts 1 worker, then the rest at minute 30."""
import datetime as dt
import tomllib
from pathlib import Path

import pytest

from harness import cli
from harness import config as config_mod
from harness.dryrun import dry_run
from harness.events import read_events

HERE = Path(__file__).resolve().parent.parent
PHASE_FILES = {"t0": "config.t0.toml", "t1": "config.t1.toml", "t2": "config.t2.toml", "sweep-n1": "config.sweep-n1.toml",
               "sweep-n12": "config.sweep-n12.toml"}


@pytest.mark.parametrize("phase,fname", sorted(PHASE_FILES.items()))
def test_shipped_phase_configs_match_their_phase(phase, fname):
    cfg = config_mod.load(HERE / fname)
    assert cfg.run.phase == phase
    assert config_mod.phase_problems(cfg) == []
    assert config_mod.schedule_problems(cfg.run.start_schedule, cfg.run.n_workers) == []
    assert cfg.run.worker_model == "claude-haiku-4-5" and cfg.run.reviewer_model == "claude-opus-5-5"
    assert cfg.repo.base_ref == "sandbox-v1" and cfg.repo.remote_url.endswith("tombaldwin/carnot-sandbox.git")
    assert cfg.reviewer.job == "checkout" and cfg.reviewer.verified is True   # CLI checked 2026-09-28
    assert cfg.launcher.mode == "manual" and not cfg.launcher.verified and not cfg.launcher.followup_verified
    assert cfg.run.task_timeout_min == 25 and cfg.run.task_budget_min == 20


def test_phase_files_differ_only_in_run():
    data = {p: tomllib.loads((HERE / f).read_text()) for p, f in PHASE_FILES.items()}
    ref = {k: v for k, v in data["t2"].items() if k != "run"}
    for p, d in data.items():
        assert {k: v for k, v in d.items() if k != "run"} == ref, p
    shapes = {p: (d["run"]["kind"], d["run"]["n_workers"], d["run"]["window_min"]) for p, d in data.items()}
    assert shapes == {"t0": ("trial", 1, 20), "t1": ("trial", 12, 60), "t2": ("pilot", 1, 60),
                      "sweep-n1": ("sweep", 1, 120), "sweep-n12": ("sweep", 12, 120)}
    assert data["t0"]["run"]["first_task"] == "T145" and data["t0"]["run"]["probe_followup"] is True
    assert all(d["run"]["first_task"] == "" and d["run"]["probe_followup"] is False for p, d in data.items() if p != "t0")
    assert not (HERE / "config.toml").exists()     # the PLAN-v3 defaults are gone


def test_runcfg_defaults_are_plan_v4():
    rc = config_mod.RunCfg()
    assert (rc.window_min, rc.worker_model, rc.n_workers) == (120, "claude-haiku-4-5", 1)
    assert config_mod.RepoCfg().base_ref == "sandbox-v1"


def _write(tmp_path, fname, **run):
    """The shipped phase config with [run] fields replaced and the reviewer marked verified."""
    text = (HERE / fname).read_text()
    for k, v in run.items():
        for ln in text.splitlines():
            if ln.startswith(f"{k} ="):
                val = str(v).lower() if isinstance(v, bool) else repr(v).replace("'", '"')
                text = text.replace(ln, f"{k} = {val}", 1)
                break
    text = text.replace("verified = false\nprompt_template", "verified = true\nprompt_template")
    p = tmp_path / fname
    p.write_text(text)
    (tmp_path / "prompts").mkdir(exist_ok=True)
    return p


@pytest.mark.parametrize("fname,field,value", [
    ("config.t2.toml", "kind", "sweep"),          # a T2 window run as a sweep would never enter the pilot
    ("config.t2.toml", "n_workers", 3),
    ("config.sweep-n12.toml", "window_min", 90),
    ("config.sweep-n1.toml", "worker_model", "claude-sonnet-5"),
    ("config.t1.toml", "start_schedule", [[0, 12]]),
    ("config.t2.toml", "phase", "t3"),
    ("config.sweep-n12.toml", "task_timeout_min", 40),
    ("config.t0.toml", "first_task", "T001"),
    ("config.t0.toml", "probe_followup", False),
])
def test_run_refuses_config_not_matching_its_phase(tmp_path, capsys, fname, field, value):
    p = _write(tmp_path, fname, **{field: value})
    rc = cli.main(["run", "--config", str(p), "--run-id", "x", "--yes"])
    assert rc == 2
    assert "does not match its pre-registered phase" in capsys.readouterr().err
    assert not (tmp_path / "../runs/x").exists()


def test_run_prints_banner_and_needs_confirmation(tmp_path, capsys, monkeypatch):
    p = _write(tmp_path, "config.sweep-n12.toml")
    monkeypatch.setattr(cli, "input_fn", lambda prompt: "yes")     # must type the phase name
    rc = cli.main(["run", "--config", str(p), "--run-id", "x"])
    out = capsys.readouterr()
    assert rc == 2 and "not confirmed" in out.err
    for s in ("phase sweep-n12", "kind           sweep", "N (slots)      12", "window         120 min", "timeout 25 min",
              "claude-haiku-4-5", "claude-opus-5-5", "sandbox-v1"):
        assert s in out.out, s
    monkeypatch.setattr(cli, "input_fn", lambda prompt: "sweep-n12")
    rc = cli.main(["run", "--config", str(p), "--run-id", "x"])
    assert rc == 2 and "run `python -m harness reset" in capsys.readouterr().err   # confirmed; stops at reset.json


def test_real_commands_need_an_explicit_config():
    with pytest.raises(SystemExit):
        cli.main(["run", "--run-id", "x"])


def test_run_refuses_unverified_command_launcher(tmp_path, capsys):
    p = _write(tmp_path, "config.t1.toml")
    p.write_text(p.read_text().replace('mode = "manual"', 'mode = "command"'))
    with pytest.raises(SystemExit) as ei:
        cli.main(["run", "--config", str(p), "--run-id", "x", "--yes"])
    assert "UNVERIFIED" in str(ei.value) and "followup_command" in str(ei.value)


def test_start_schedule_starts_groups_at_their_minute(tmp_path):
    cfg = config_mod.load(HERE / "config.dryrun.toml")
    cfg.run.output_dir = str(tmp_path / "runs")
    cfg.run.window_min = 40
    cfg.run.n_workers = 3
    cfg.run.start_schedule = [[0, 1], [20, 3]]
    cfg.sim.time_scale = 300
    cfg.sim.n_toy_tasks = 12
    run_dir, summary, errs = dry_run(cfg, "sched", seed=3)
    assert not errs, errs
    ev = read_events(run_dir / "events.jsonl")
    t = lambda e: dt.datetime.fromisoformat(e["t"].replace("Z", "+00:00"))
    w0 = t(next(e for e in ev if e["type"] == "note" and e["text"] == "window_start"))
    starts = {e["worker"]: (t(e) - w0).total_seconds() / 60 for e in ev if e["type"] == "worker_start"}
    assert set(starts) == {"s1", "s2", "s3"}
    assert starts["s1"] < 2 and 19.5 <= starts["s2"] < 23 and 19.5 <= starts["s3"] < 23
    assert any(e["type"] == "note" and e["text"].startswith("start_schedule minute=20") for e in ev)
    # no session runs on a slot before it opens
    first_launch = {}
    for e in ev:
        if e["type"] == "session_launch":
            first_launch.setdefault(e["slot"], (t(e) - w0).total_seconds() / 60)
    assert all(first_launch[s] >= starts[s] for s in first_launch)
