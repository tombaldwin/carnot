"""python -m harness {reset,run,calibrate,throttle,dry-run,status,log,validate-tasks,validate-log,make-toy} --config FILE

Real phases use config.t0.toml, config.t1.toml, config.t2.toml, config.sweep-n1.toml or
config.sweep-n12.toml (PLAN-v4 section 7); there is no default real config, so --config is required."""
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


def input_fn(prompt: str) -> str:   # patched in tests
    return input(prompt)


def run_banner(cfg, run_id: str) -> str:
    rc = cfg.run
    sched = rc.start_schedule or [[0, rc.n_workers]]
    return "\n".join([
        f"run {run_id}: phase {rc.phase}",
        f"  kind           {rc.kind}",
        f"  N (slots)      {rc.n_workers}   start schedule (minute, slots) {sched}",
        f"  window         {rc.window_min:g} min (warm-up {rc.warmup_min:g}, grace {rc.grace_min:g})",
        f"  sessions       one per task; timeout {rc.task_timeout_min:g} min, budget {rc.task_budget_min:g} min"
        + (f"; first task {rc.first_task}" if rc.first_task else "") + ("; probe follow-up" if rc.probe_followup else ""),
        f"  launcher       {cfg.launcher.mode}   launch clone {cfg.path(cfg.launcher.launch_dir)}",
        f"  worker model   {rc.worker_model}",
        f"  reviewer model {rc.reviewer_model}   review job {cfg.reviewer.job}",
        f"  base_ref       {cfg.repo.base_ref}   remote {cfg.repo.remote_url}",
    ])


def cmd_run(args) -> int:
    from .launchers import CommandLauncher, ManualLauncher, require_verified
    from .orchestrator import Orchestrator
    from .reset import work_repo
    from .review import CommandReviewer
    from .tasks import load_tasks
    from .config import phase_problems, schedule_problems
    cfg = _cfg(args)
    require_verified(cfg)
    probs = phase_problems(cfg) + schedule_problems(cfg.run.start_schedule, cfg.run.n_workers)
    if probs:
        print("refusing a real run: the config does not match its pre-registered phase:\n  " + "\n  ".join(probs),
              file=sys.stderr)
        return 2
    print(run_banner(cfg, args.run_id))
    if not args.yes:
        ans = input_fn("Type the phase name to confirm and start the window: ").strip()
        if ans != cfg.run.phase:
            print("not confirmed; nothing started", file=sys.stderr)
            return 2
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
    reviewer = CommandReviewer(cfg, repo)
    launcher = (CommandLauncher(cfg, log, args.run_id) if cfg.launcher.mode == "command"
                else ManualLauncher(cfg, log, args.run_id, input_fn=input_fn))
    orch = Orchestrator(cfg, args.run_id, run_dir, clock, log, repo, load_tasks(cfg), reviewer,
                        sandbox_commit=rj["sandbox_commit"], seed=rj["seed"], launcher=launcher)
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
    """Operator entries: meter readings, notes, worker_down / worker_restart (worker = slot id, s1..sN)."""
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


def cmd_calibrate(args) -> int:
    from .calibrate import calibrate, parse_task_list, refuse_if_public
    from .launchers import NotVerified
    from .reset import work_repo
    from .tasks import load_tasks
    cfg = _cfg(args)
    if cfg.reviewer.mode != "command":
        print("calibrate runs the command reviewer (the live review path); [reviewer] mode is "
              f"{cfg.reviewer.mode!r}", file=sys.stderr)
        return 2
    if not cfg.reviewer.verified and not args.allow_unverified:
        raise NotVerified("refusing calibration: [reviewer] command is UNVERIFIED (set verified = true after "
                          "the CLI check, PLAN-v4 s7; --allow-unverified only for a fake command in tests)")
    ids = parse_task_list(args.tasks, load_tasks(cfg))
    repo = work_repo(cfg)
    summary = calibrate(cfg, repo, ids, [v.strip() for v in args.variants.split(",") if v.strip()],
                        Path(args.out), args.job, parallel=args.parallel,
                        mutant_dir=Path(args.mutant_dir) if args.mutant_dir else None,
                        check_hidden=not args.no_check_hidden, seed=args.seed,
                        detail_out=Path(args.detail_out) if args.detail_out else None)
    print(json.dumps(summary, indent=2))
    return 0 if summary["reviews"] else 1


def cmd_throttle(args) -> int:
    from .throttle import format_report, throttle_report
    r = throttle_report(Path(args.run_dir), skip_min=args.skip_min)
    print(format_report(r))
    if args.json:
        Path(args.json).write_text(json.dumps(r, indent=2) + "\n")
    return 0


def cmd_make_toy(args) -> int:
    from .toygen import generate
    print(json.dumps(generate(Path(args.dest), args.n_tasks, force=args.force), indent=2))
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m harness")
    sub = ap.add_subparsers(dest="cmd", required=True)

    def add(name, fn, cfg_default=None):
        p = sub.add_parser(name)
        if cfg_default:
            p.add_argument("--config", default=str(HERE / cfg_default))
        else:
            p.add_argument("--config", required=True,
                           help="a phase config: config.t0.toml, config.t1.toml, config.t2.toml, config.sweep-n1.toml, config.sweep-n12.toml")
        p.set_defaults(fn=fn)
        return p

    p = add("reset", cmd_reset)
    p.add_argument("--run-id", required=True)
    p.add_argument("--seed", type=int)
    p = add("run", cmd_run)
    p.add_argument("--run-id", required=True)
    p.add_argument("--meter-start", type=float, help="credit meter reading ($) just before the window")
    p.add_argument("--yes", action="store_true", help="skip the confirmation prompt (the banner is still printed)")
    p = add("calibrate", cmd_calibrate)
    p.add_argument("--tasks", required=True, help="comma-separated ids, @file (one id per line) or 'all'")
    p.add_argument("--variants", default="reference,mutant", help="reference, mutant, or both (comma-separated)")
    p.add_argument("--out", required=True, help="calibration-review log (JSONL), appended to; rows already there are skipped")
    p.add_argument("--job", required=True, help="review-job version label written to every row (e.g. v2-checkout)")
    p.add_argument("--parallel", type=int, default=1,
                   help="reviews at once (calibration only: V uses call durations, never queueing)")
    p.add_argument("--mutant-dir", help="private dir for mutant patches (default: mutants/ beside [tasks] reference_dir)")
    p.add_argument("--no-check-hidden", action="store_true", help="keep mutants without checking the hidden tests fail")
    p.add_argument("--seed", type=int, default=1, help="mutant choice seed")
    p.add_argument("--detail-out", help="private JSONL with each review's reason and head (never in the public repo)")
    p.add_argument("--allow-unverified", action="store_true", help=argparse.SUPPRESS)
    p = sub.add_parser("throttle", help="abort rule 1 from T1's log (activity per slot-minute, 1 vs 12)")
    p.add_argument("run_dir")
    p.add_argument("--skip-min", type=float, default=5.0)
    p.add_argument("--json")
    p.set_defaults(fn=cmd_throttle)
    p = add("dry-run", cmd_dry_run, "config.dryrun.toml")
    p.add_argument("--run-id")
    p.add_argument("--seed", type=int)
    p.add_argument("--scale", type=float, help="virtual seconds per real second")
    p.add_argument("--window-min", type=float)
    p.add_argument("--workers", type=int, help="slots")
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
