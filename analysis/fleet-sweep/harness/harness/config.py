"""Configuration (TOML). Relative paths resolve against the config file's directory.

Anything marked UNVERIFIED depends on product facts that PLAN-v3 section 10 has
not yet checked (cloud session launch syntax, `claude -p` flags). A real `run`
refuses to start while a template it needs has ``verified = false``.
"""
from __future__ import annotations

import dataclasses as dc
import tomllib
from pathlib import Path
from typing import Any


@dc.dataclass
class RunCfg:
    kind: str = "sweep"                 # sweep | pilot | trial | dry-run
    n_workers: int = 3
    window_min: float = 90
    warmup_min: float = 10
    grace_min: float = 10
    task_order_seed: int = 1234         # overridden per window with --seed
    worker_model: str = "claude-sonnet-5"
    reviewer_model: str = "claude-opus-5-5"
    output_dir: str = "runs"            # runs/<run_id>/
    poll_interval_s: float = 15         # git fetch cadence (virtual seconds)
    end_grace_early_when_idle: bool = True
    grace_reviews: bool = True          # keep reviewing already-submitted changes during grace
    notes: str = ""


@dc.dataclass
class RepoCfg:
    remote_url: str = ""                # the sandbox repo the workers push to
    work_dir: str = "work"              # orchestrator's own clone (created if missing)
    base_ref: str = "study2-base"       # tag/commit main is reset to before each window
    branch_prefix: str = "claude/task-"
    race_prefix: str = "claude/race-"   # optional race markers: claude/race-<task>-<worker>
    tasks_file_name: str = "TASKS.json" # written to main at reset
    git_user_name: str = "fleet-harness"
    git_user_email: str = "harness@localhost"


@dc.dataclass
class TasksCfg:
    task_file: str = "tasks/tasks.json"  # {"tasks": [{"id","title","text","acceptance":[...]}]}
    hidden_tests_dir: str = "tasks/hidden"  # <dir>/<task id>/test_*.py ; never pushed
    reference_dir: str = "tasks/reference"  # <dir>/<task id>.patch ; used only by SimWorker


@dc.dataclass
class TestsCfg:
    python: str = ""                    # interpreter for tests; "" = the harness's own
    visible_cmd: list = dc.field(default_factory=lambda: ["{python}", "-m", "pytest", "-q", "-x", "-p", "no:cacheprovider", "tests"])
    hidden_cmd: list = dc.field(default_factory=lambda: ["{python}", "-m", "pytest", "-q", "-p", "no:cacheprovider", "--import-mode=importlib", "{hidden_dir}"])
    hidden_subdir: str = "_hidden_acceptance"  # where hidden tests are copied inside the temp checkout
    # (put it under the sandbox's tests/ dir, e.g. "tests/_hidden_acceptance", when hidden tests
    # need the visible suite's conftest.py fixtures)
    hidden_pythonpath: list = dc.field(default_factory=list)  # checkout-relative dirs prepended to
    # PYTHONPATH for hidden runs, e.g. ["tests", "."] so both `from helpers import` and
    # `from tests.helpers import` resolve under --import-mode=importlib
    timeout_s: float = 300              # real seconds per test command
    post_hidden_scope: str = "merged"   # "task" = this task's hidden tests; "merged" = also every task merged this window


@dc.dataclass
class ReviewerCfg:
    mode: str = "command"               # command | sim
    # UNVERIFIED: flags of the local `claude -p` call. Placeholders: {model} {prompt_file} {workdir}
    command: list = dc.field(default_factory=lambda: ["sh", "-c", "claude -p --model {model} < {prompt_file}"])
    output_format: str = "text"         # text | json  (json: expects {"result": ..., "usage": {...}}; UNVERIFIED)
    verified: bool = False
    prompt_template: str = "prompts/reviewer.md"
    timeout_s: float = 900              # real seconds per review call
    retries: int = 1                    # a crashed review is retried once
    retry_backoff_s: float = 60         # virtual seconds after a failed retry before trying that change again
    max_downtime_min: float = 10        # more than this voids the window
    max_diff_chars: int = 60000


@dc.dataclass
class LauncherCfg:
    mode: str = "manual"                # manual | command | sim
    # UNVERIFIED: how to start one cloud worker session from a script.
    # Placeholders: {worker} {prompt_file} {remote_url} {model} {run_id}
    start_command: list = dc.field(default_factory=list)
    stop_command: list = dc.field(default_factory=list)   # {worker} {session_id}
    session_id_regex: str = r"(?P<session_id>\S+)"      # UNVERIFIED: parsed from start_command stdout
    verified: bool = False
    worker_prompt: str = "prompts/worker.md"


@dc.dataclass
class SimCfg:
    time_scale: float = 90.0            # virtual seconds per real second
    use_toy: bool = True                # False: dry-run against [repo]/[tasks] as configured (a real
                                        # sandbox behind a local bare remote) instead of a toy sandbox
    seed: int = 7
    n_toy_tasks: int = 10
    # workers
    rate_per_hour: float = 3.0          # lambda: attempts per agent-hour (exp. work time mean 1/lambda)
    rework_factor: float = 0.5          # rework takes this fraction of a fresh task's time
    p_wrong: float = 0.15               # change fails its own hidden tests
    p_conflict: float = 0.12            # change touches the textual hotspot (rebase conflicts)
    p_semantic: float = 0.08            # change alters a shared constant (breaks other tasks after rebase)
    p_visible_break: float = 0.05       # change breaks a visible test
    p_wrong_rework_factor: float = 0.3  # a rework is wrong with p_wrong * this
    race_markers: bool = True           # push claude/race-<task>-<worker> on a rejected claim
    union_resolve: bool = False         # resolve a reference patch's conflicts with main by keeping both sides
    max_reworks: int = 0                # >0: a worker abandons a task after this many reworks
    # sandbox-specific edits used for the semantic / visible-break knobs: {file, find, replace}
    semantic_edit: dict = dc.field(default_factory=dict)
    visible_break_edit: dict = dc.field(default_factory=dict)
    # reviewer
    review_mean_s: float = 240          # virtual seconds per review (lognormal, cv below)
    review_cv: float = 0.4
    p_catch_wrong: float = 0.6          # at queue depth 0
    catch_drop_per_depth: float = 0.06  # skimming under load (sim only; the real reviewer never sees depth)
    p_catch_wrong_min: float = 0.1
    p_false_reject: float = 0.15
    p_catch_visible_fail: float = 0.9
    p_review_crash: float = 0.02


@dc.dataclass
class Config:
    run: RunCfg = dc.field(default_factory=RunCfg)
    repo: RepoCfg = dc.field(default_factory=RepoCfg)
    tasks: TasksCfg = dc.field(default_factory=TasksCfg)
    tests: TestsCfg = dc.field(default_factory=TestsCfg)
    reviewer: ReviewerCfg = dc.field(default_factory=ReviewerCfg)
    launcher: LauncherCfg = dc.field(default_factory=LauncherCfg)
    sim: SimCfg = dc.field(default_factory=SimCfg)
    base_dir: Path = dc.field(default_factory=Path.cwd)

    def path(self, p: str) -> Path:
        q = Path(p).expanduser()
        return q if q.is_absolute() else (self.base_dir / q).resolve()


def _build(cls, data: dict[str, Any], where: str):
    names = {f.name for f in dc.fields(cls)}
    unknown = set(data) - names
    if unknown:
        raise ValueError(f"unknown config keys in [{where}]: {sorted(unknown)}")
    return cls(**data)


SECTIONS = {"run": RunCfg, "repo": RepoCfg, "tasks": TasksCfg, "tests": TestsCfg,
            "reviewer": ReviewerCfg, "launcher": LauncherCfg, "sim": SimCfg}


def from_dict(data: dict[str, Any], base_dir: Path | None = None) -> Config:
    unknown = set(data) - set(SECTIONS)
    if unknown:
        raise ValueError(f"unknown config sections: {sorted(unknown)}")
    kw = {name: _build(cls, data.get(name, {}), name) for name, cls in SECTIONS.items()}
    return Config(**kw, base_dir=(base_dir or Path.cwd()).resolve())


def load(path: Path | str) -> Config:
    path = Path(path)
    with open(path, "rb") as f:
        data = tomllib.load(f)
    return from_dict(data, path.parent)
