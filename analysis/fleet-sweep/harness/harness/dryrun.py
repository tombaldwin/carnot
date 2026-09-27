"""Wire SimCloudLauncher (one SimSession per task) + SimReviewer + toy sandbox into a full accelerated window."""
from __future__ import annotations

import dataclasses as dc
import datetime as dt
import json
import random
import shutil
from pathlib import Path

from . import toygen
from .clock import Clock
from .config import Config
from .events import EventLog, read_events
from .orchestrator import Orchestrator
from .report import summarize
from .reset import reset, work_repo
from .schema import validate_events, validate_run_json
from .sim import Oracle, SimCloudLauncher, SimReviewer
from .tasks import load_tasks


def prepare_toy_config(cfg: Config, sandbox: Path) -> Config:
    """A copy of cfg pointed at a freshly generated toy sandbox."""
    info = toygen.generate(sandbox / "toy", cfg.sim.n_toy_tasks, force=True)
    cfg = dc.replace(cfg, repo=dc.replace(cfg.repo), tasks=dc.replace(cfg.tasks), run=dc.replace(cfg.run),
                     sim=dc.replace(cfg.sim), reviewer=dc.replace(cfg.reviewer), launcher=dc.replace(cfg.launcher))
    cfg.repo.remote_url = info["remote"]
    cfg.repo.work_dir = str(sandbox / "work")
    cfg.repo.base_ref = toygen.BASE_TAG
    cfg.tasks.task_file = info["task_file"]
    cfg.tasks.hidden_tests_dir = info["hidden_tests_dir"]
    cfg.tasks.reference_dir = info["reference_dir"]
    cfg.run.kind = "dry-run"
    cfg.reviewer.mode = "sim"
    cfg.launcher.mode = "sim"
    if not cfg.sim.semantic_edit:
        cfg.sim.semantic_edit = dict(toygen.SEMANTIC_EDIT)
    if not cfg.sim.visible_break_edit:
        cfg.sim.visible_break_edit = dict(toygen.VISIBLE_BREAK_EDIT)
    return cfg


def prepare_real_config(cfg: Config) -> Config:
    """A copy of cfg for a dry run on a real sandbox: sim workers and reviewer, and a
    remote that must be local (a path or file:// URL), so nothing is pushed anywhere else."""
    url = cfg.repo.remote_url
    if not url or "://" in url and not url.startswith("file://") or url.startswith("git@"):
        raise SystemExit(f"dry run with use_toy = false needs a local bare remote, not {url!r}")
    cfg = dc.replace(cfg, repo=dc.replace(cfg.repo), tasks=dc.replace(cfg.tasks), run=dc.replace(cfg.run),
                     sim=dc.replace(cfg.sim), reviewer=dc.replace(cfg.reviewer), launcher=dc.replace(cfg.launcher))
    cfg.run.kind = "dry-run"
    cfg.reviewer.mode = "sim"
    cfg.launcher.mode = "sim"
    return cfg


HARNESS_DIR = Path(__file__).resolve().parent.parent


def _harness_prompts(cfg: Config) -> None:
    """A dry-run config kept outside the harness (the tasks repo's dryrun/) uses the harness's own prompt
    templates unless it names others that exist."""
    for attr in ("worker_prompt", "rework_prompt"):
        if not cfg.path(getattr(cfg.launcher, attr)).exists():
            setattr(cfg.launcher, attr, str(HARNESS_DIR / getattr(cfg.launcher, attr)))


def dry_run(cfg: Config, run_id: str | None = None, seed: int | None = None,
            keep_sandbox: bool = True, echo: bool = False) -> tuple[Path, dict, list[str]]:
    run_id = run_id or dt.datetime.now().strftime("dry-%Y%m%d-%H%M%S")
    seed = cfg.run.task_order_seed if seed is None else seed
    run_dir = cfg.path(cfg.run.output_dir) / run_id
    if run_dir.exists():
        shutil.rmtree(run_dir)
    run_dir.mkdir(parents=True)
    sandbox = run_dir / "_sandbox"
    if cfg.sim.use_toy:
        cfg = prepare_toy_config(cfg, sandbox)
    else:
        cfg = prepare_real_config(cfg)
    _harness_prompts(cfg)

    info = reset(cfg, run_id, seed, run_dir)
    clock = Clock(cfg.sim.time_scale)
    log = EventLog(run_dir / "events.jsonl", clock, echo=echo)
    repo = work_repo(cfg)
    oracle = Oracle()
    reviewer = SimReviewer(cfg, clock, oracle, random.Random(f"{cfg.sim.seed}-reviewer"))
    launcher = SimCloudLauncher(cfg, clock, log, oracle, sandbox / "slots", run_id, cfg.sim.seed)
    orch = Orchestrator(cfg, run_id, run_dir, clock, log, repo, load_tasks(cfg), reviewer,
                        sandbox_commit=info["sandbox_commit"], seed=seed, kind="dry-run", launcher=launcher)
    reviewer.depth_probe = orch.queue_depth
    try:
        orch.run_window(launcher)
    finally:
        reviewer.stop.set()
        launcher.stop_all()
    errs = validate_events(run_dir / "events.jsonl") + validate_run_json(run_dir / "run.json")
    errs += [f"harness thread error: {e}" for e in orch.errors]
    errs += [f"sim session {s.session_id} crashed: {s.error}" for s in launcher.sessions.values()
             if s.error and not s.error.startswith("git:")]
    tot: dict = {}
    for s in launcher.sessions.values():
        for k, v in s.stats.items():
            tot[k] = tot.get(k, 0) + v
    unappliable = [dict(u, session=s.session_id) for s in launcher.sessions.values() for u in s.unappliable]
    if unappliable:
        (run_dir / "sim_unappliable.json").write_text(json.dumps(unappliable, indent=2) + "\n")
    summary = summarize(read_events(run_dir / "events.jsonl"))
    summary["sim_sessions"] = dict(n=len(launcher.sessions), **tot)
    if not keep_sandbox:
        shutil.rmtree(sandbox, ignore_errors=True)
    return run_dir, summary, errs
