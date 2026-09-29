"""Per-phase configs (PLAN-v4 section 7, PLAN-v6 sections 5-6): the shipped files match their phases, `run`
refuses a config whose kind / N / window / K differ from the named phase, prints the banner and asks for
confirmation; T1's start schedule starts 1 worker, then the rest at minute 30 (T1b: at minute 90)."""
import datetime as dt
import tomllib
from pathlib import Path

import re

import pytest

from harness import cli
from harness import config as config_mod
from harness.dryrun import dry_run
from harness.events import read_events

HERE = Path(__file__).resolve().parent.parent
PHASE_FILES = {"t0": "config.t0.toml", "t1": "config.t1.toml", "t2": "config.t2.toml", "t1b": "config.t1b.toml",
               **{f"sweep-n{n}-k{k}": f"config.sweep-n{n}-k{k}.toml" for n in (1, 12) for k in (1, 3)}}


@pytest.mark.parametrize("phase,fname", sorted(PHASE_FILES.items()))
def test_shipped_phase_configs_match_their_phase(phase, fname):
    cfg = config_mod.load(HERE / fname)
    assert cfg.run.phase == phase
    assert config_mod.phase_problems(cfg) == []
    assert config_mod.schedule_problems(cfg.run.start_schedule, cfg.run.n_workers) == []
    assert cfg.run.worker_model == "claude-haiku-4-5" and cfg.run.reviewer_model == "claude-opus-5-5"
    assert cfg.repo.base_ref == "sandbox-v1" and cfg.repo.remote_url.endswith("tombaldwin/carnot-sandbox.git")
    assert cfg.reviewer.job == "checkout" and cfg.reviewer.verified is True   # CLI checked 2026-09-28
    # routine launcher (2026-09-28: `claude --cloud` sessions cannot push); launch unverified until the T0 repeat
    assert cfg.launcher.mode == "command" and cfg.launcher.verified and cfg.launcher.followup_verified
    lc = cfg.launcher.launch_command            # the #81776 workaround: clone from GitHub, never a bundle
    assert "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1" in lc and lc[lc.index("--ref") + 1] == "main"
    assert lc.index("CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1") < lc.index("claude") < lc.index("--cloud")
    r = cfg.launcher.routine
    assert r.environment_id.startswith("env_") and r.model == cfg.run.worker_model and r.lead_s == 30
    assert config_mod.repo_slug(r.source_url) == "tombaldwin/carnot-sandbox"
    assert "{instruction}" in r.runner and "RemoteTrigger" in r.runner and r.slot_routines == {}
    assert cfg.run.task_timeout_min == 25 and cfg.run.task_budget_min == 20
    # one session per slot (T0d, 2026-09-29: about $0.50 per session provisioned); later tasks by follow-up
    assert cfg.launcher.session_per == "slot" and cfg.launcher.tasks_per_session == 4
    assert cfg.path(cfg.launcher.next_task_prompt).exists()


def _without_run_and_k(d):
    d = {k: v for k, v in d.items() if k != "run"}
    d["reviewer"] = {k: v for k, v in d["reviewer"].items() if k != "parallel"}
    return d


def test_phase_files_differ_only_in_run_and_reviewers():
    data = {p: tomllib.loads((HERE / f).read_text()) for p, f in PHASE_FILES.items()}
    ref = _without_run_and_k(data["t2"])
    for p, d in data.items():
        assert _without_run_and_k(d) == ref, p
    shapes = {p: (d["run"]["kind"], d["run"]["n_workers"], d["run"]["window_min"], d["run"]["warmup_min"],
                  d["run"]["grace_min"], d["reviewer"]["parallel"]) for p, d in data.items()}
    assert shapes == {"t0": ("trial", 1, 45, 0, 10, 1), "t1": ("trial", 12, 60, 10, 10, 1),
                      "t2": ("pilot", 1, 60, 10, 10, 1), "t1b": ("trial", 12, 120, 0, 10, 3),
                      **{f"sweep-n{n}-k{k}": ("sweep", n, 45, 5, 10, k) for n in (1, 12) for k in (1, 3)}}
    assert data["t1b"]["run"]["start_schedule"] == [[0, 1], [90, 12]]
    assert not (HERE / "config.sweep-n1.toml").exists() and not (HERE / "config.sweep-n12.toml").exists()   # v5
    assert data["t0"]["run"]["first_task"] == "T145" and data["t0"]["run"]["probe_followup"] is True
    assert all(d["run"]["first_task"] == "" and d["run"]["probe_followup"] is False for p, d in data.items() if p != "t0")
    assert not (HERE / "config.toml").exists()     # the PLAN-v3 defaults are gone


def test_runcfg_defaults_are_plan_v4():
    rc = config_mod.RunCfg()
    assert (rc.window_min, rc.worker_model, rc.n_workers) == (120, "claude-haiku-4-5", 1)
    assert config_mod.RepoCfg().base_ref == "sandbox-v1"


def _write(tmp_path, fname, **run):
    """The shipped phase config with [run] fields replaced, the reviewer marked verified and the launcher
    manual (these tests are about the phase checks, not the launcher)."""
    text = re.sub(r'(\[launcher\][\s\S]*?\n)mode = "\w+"', r'\1mode = "manual"', (HERE / fname).read_text(), count=1)
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
    ("config.sweep-n12-k3.toml", "window_min", 90),
    ("config.sweep-n1-k1.toml", "worker_model", "claude-sonnet-5"),
    ("config.sweep-n12-k1.toml", "warmup_min", 10),
    ("config.t1b.toml", "start_schedule", [[0, 1], [30, 12]]),
    ("config.t1.toml", "start_schedule", [[0, 12]]),
    ("config.t2.toml", "phase", "t3"),
    ("config.sweep-n12-k3.toml", "task_timeout_min", 40),
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
    p = _write(tmp_path, "config.sweep-n12-k3.toml")
    monkeypatch.setattr(cli, "input_fn", lambda prompt: "yes")     # must type the phase name
    rc = cli.main(["run", "--config", str(p), "--run-id", "x"])
    out = capsys.readouterr()
    assert rc == 2 and "not confirmed" in out.err
    for s in ("phase sweep-n12-k3", "kind           sweep", "N (slots)      12", "window         45 min (warm-up 5",
              "timeout 25 min", "claude-haiku-4-5", "claude-opus-5-5", "sandbox-v1", "K = 3 in parallel",
              "one per slot (up to 4 tasks each"):
        assert s in out.out, s
    monkeypatch.setattr(cli, "input_fn", lambda prompt: "sweep-n12-k3")
    rc = cli.main(["run", "--config", str(p), "--run-id", "x"])
    assert rc == 2 and "run `python -m harness reset" in capsys.readouterr().err   # confirmed; stops at reset.json


def test_real_commands_need_an_explicit_config():
    with pytest.raises(SystemExit):
        cli.main(["run", "--run-id", "x"])


def test_run_refuses_unverified_command_launcher(tmp_path, capsys):
    p = _write(tmp_path, "config.t1.toml")
    p.write_text(p.read_text().replace('mode = "manual"', 'mode = "command"')
                 .replace("verified = true            # command path", "verified = false            # command path"))
    with pytest.raises(SystemExit) as ei:
        cli.main(["run", "--config", str(p), "--run-id", "x", "--yes"])
    assert "UNVERIFIED" in str(ei.value) and "launch_command" in str(ei.value)


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


@pytest.mark.parametrize("fname,k,msg", [
    ("config.sweep-n12-k3.toml", 1, "pre-registered with 3 reviewers"),
    ("config.sweep-n1-k1.toml", 3, "pre-registered with 1 reviewers"),
    ("config.sweep-n12-k1.toml", 2, "a sweep window runs K = 1 or 3 reviewers"),
    ("config.t1b.toml", 1, "pre-registered with 3 reviewers"),
])
def test_run_refuses_wrong_reviewer_count(tmp_path, capsys, fname, k, msg):
    p = _write(tmp_path, fname)
    p.write_text(p.read_text().replace("\nparallel = ", f"\nparallel = {k}  # was ", 1))
    assert config_mod.load(p).reviewer.parallel == k
    assert cli.main(["run", "--config", str(p), "--run-id", "x", "--yes"]) == 2
    assert msg in capsys.readouterr().err


def test_sweep_k_variants_share_slot_routines():
    assert config_mod.routine_group("sweep-n12-k1") == config_mod.routine_group("sweep-n12-k3") == "sweep-n12"
    assert config_mod.routine_group("sweep-n1-k3") == "sweep-n1"
    assert config_mod.routine_group("t1b") == "t1b" and config_mod.routine_group("t1") == "t1"
