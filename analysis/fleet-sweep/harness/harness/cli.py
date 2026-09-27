"""python -m harness {reset,run,dry-run,status,log,validate-tasks,validate-log,make-toy} --config FILE"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import config as config_mod
from .clock import Clock
from .events import EventLog, SchemaError, read_events
from .report import format_summary, summarize
from .schema import EVENT_FIELDS, validate_events, validate_run_json

HERE = Path(__file__).resolve().parent.parent


def _cfg(args) -> config_mod.Config:
    return config_mod.load(args.config)


def cmd_reset(args) -> int:
    from .reset import reset
    cfg = _cfg(args)
    seed = args.seed if args.seed is not None else cfg.run.task_order_seed
    run_dir = cfg.path(cfg.run.output_dir) / args.run_id
    info = reset(cfg, args.run_id, seed, run_dir)
    print(json.dumps(info, indent=2))
    return 0


def cmd_run(args) -> int:
    from .launchers import CommandLauncher, ManualLauncher, require_verified
    from .orchestrator import Orchestrator
    from .reset import work_repo
    from .review import CommandReviewer
    from .tasks import load_tasks
    cfg = _cfg(args)
    require_verified(cfg)
    run_dir = cfg.path(cfg.run.output_dir) / args.run_id
    reset_file = run_dir / "reset.json"
    if not reset_file.exists():
        print(f"no {reset_file}: run `python -m harness reset --run-id {args.run_id}` first", file=sys.stderr)
        return 2
    if (run_dir / "events.jsonl").exists():
        print(f"{run_dir}/events.jsonl already exists; a window is never re-run into the same id", file=sys.stderr)
        return 2
    rj = json.loads(reset_file.read_text())
    clock = Clock(1.0)
    log = EventLog(run_dir / "events.jsonl", clock, echo=True)
    if args.meter_start is not None:
        log.emit("meter", credits_left_usd=args.meter_start, source="operator, before window")
    repo = work_repo(cfg)
    reviewer = CommandReviewer(cfg)
    orch = Orchestrator(cfg, args.run_id, run_dir, clock, log, repo, load_tasks(cfg), reviewer,
                        sandbox_commit=rj["sandbox_commit"], seed=rj["seed"])
    launcher = (CommandLauncher(cfg, log, args.run_id, run_dir) if cfg.launcher.mode == "command"
                else ManualLauncher(cfg, log, args.run_id, run_dir))
    run = orch.run_window(launcher)
    print(json.dumps(run, indent=2))
    print(format_summary(summarize(read_events(run_dir / "events.jsonl"))))
    print("Now read the credit meter and record it with: python -m harness log --type meter ...")
    return 0


def cmd_dry_run(args) -> int:
    from .dryrun import dry_run
    cfg = _cfg(args)
    if args.scale:
        cfg.sim.time_scale = args.scale
    if args.window_min:
        cfg.run.window_min = args.window_min
    if args.workers:
        cfg.run.n_workers = args.workers
    run_dir, summary, errs = dry_run(cfg, args.run_id, args.seed, keep_sandbox=not args.clean, echo=args.echo)
    print(f"run dir: {run_dir}")
    print(format_summary(summary))
    if errs:
        print("\nPROBLEMS:\n  " + "\n  ".join(errs), file=sys.stderr)
        return 1
    print("\nevents.jsonl and run.json conform to SCHEMA.md")
    return 0


def _latest_run(cfg) -> Path | None:
    root = cfg.path(cfg.run.output_dir)
    runs = sorted((p for p in root.glob("*/events.jsonl")), key=lambda p: p.stat().st_mtime)
    return runs[-1].parent if runs else None


def cmd_status(args) -> int:
    cfg = _cfg(args)
    run_dir = (cfg.path(cfg.run.output_dir) / args.run_id) if args.run_id else _latest_run(cfg)
    if not run_dir or not (run_dir / "events.jsonl").exists():
        print("no run found")
        return 1
    ev = read_events(run_dir / "events.jsonl")
    print(f"run: {run_dir.name}   events: {len(ev)}   last: {ev[-1]['t'] if ev else '-'}")
    print(format_summary(summarize(ev)))
    if (run_dir / "run.json").exists():
        print("run.json:", (run_dir / "run.json").read_text().strip())
    else:
        print("run.json not written yet (window still open?)")
    return 0


def _parse_value(v: str):
    try:
        return json.loads(v)
    except json.JSONDecodeError:
        return v


def cmd_log(args) -> int:
    """Operator entries: meter readings, notes, worker_down / worker_restart."""
    cfg = _cfg(args)
    run_dir = cfg.path(cfg.run.output_dir) / args.run_id
    fields = {}
    for kv in args.field or []:
        k, _, v = kv.partition("=")
        fields[k] = _parse_value(v)
        if EVENT_FIELDS.get(args.type, {}).get(k, "").startswith("str") and not isinstance(fields[k], str):
            fields[k] = v
    try:
        ev = EventLog(run_dir / "events.jsonl", Clock(1.0)).emit(args.type, **fields)
    except SchemaError as e:
        print(f"not logged: {e}", file=sys.stderr)
        return 2
    print(json.dumps(ev))
    return 0


def cmd_validate_tasks(args) -> int:
    from .reset import work_repo
    from .validate import validate_tasks
    cfg = _cfg(args)
    repo = work_repo(cfg)
    repo.run("fetch", "-q", "--tags", "origin")
    base = repo.try_sha(f"refs/tags/{cfg.repo.base_ref}") or repo.sha(cfg.repo.base_ref)
    rows = validate_tasks(cfg, repo, base)
    bad = [r for r in rows if not r["ok"]]
    for r in rows:
        print(json.dumps(r))
    print(f"{len(rows) - len(bad)}/{len(rows)} tasks valid")
    return 1 if bad else 0


def cmd_validate_log(args) -> int:
    d = Path(args.run_dir)
    errs = validate_events(d / "events.jsonl") + validate_run_json(d / "run.json")
    print("\n".join(errs) if errs else "ok")
    return 1 if errs else 0


def cmd_make_toy(args) -> int:
    from .toygen import generate
    print(json.dumps(generate(Path(args.dest), args.n_tasks, force=args.force), indent=2))
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m harness")
    sub = ap.add_subparsers(dest="cmd", required=True)

    def add(name, fn, cfg_default="config.toml"):
        p = sub.add_parser(name)
        p.add_argument("--config", default=str(HERE / cfg_default))
        p.set_defaults(fn=fn)
        return p

    p = add("reset", cmd_reset)
    p.add_argument("--run-id", required=True)
    p.add_argument("--seed", type=int)
    p = add("run", cmd_run)
    p.add_argument("--run-id", required=True)
    p.add_argument("--meter-start", type=float, help="credit meter reading ($) just before the window")
    p = add("dry-run", cmd_dry_run, "config.dryrun.toml")
    p.add_argument("--run-id")
    p.add_argument("--seed", type=int)
    p.add_argument("--scale", type=float, help="virtual seconds per real second")
    p.add_argument("--window-min", type=float)
    p.add_argument("--workers", type=int)
    p.add_argument("--clean", action="store_true", help="delete the toy sandbox afterwards")
    p.add_argument("--echo", action="store_true", help="print events as they are logged")
    p = add("status", cmd_status)
    p.add_argument("--run-id")
    p = add("log", cmd_log)
    p.add_argument("--run-id", required=True)
    p.add_argument("--type", required=True, choices=sorted(EVENT_FIELDS))
    p.add_argument("--field", action="append", help="key=value (value parsed as JSON if possible)")
    add("validate-tasks", cmd_validate_tasks)
    p = sub.add_parser("validate-log")
    p.add_argument("run_dir")
    p.set_defaults(fn=cmd_validate_log)
    p = sub.add_parser("make-toy")
    p.add_argument("dest")
    p.add_argument("--n-tasks", type=int, default=10)
    p.add_argument("--force", action="store_true")
    p.set_defaults(fn=cmd_make_toy)

    args = ap.parse_args(argv)
    return args.fn(args)
