"""Configuration (TOML). Relative paths resolve against the config file's directory.

Anything marked UNVERIFIED depends on product facts not yet checked on the paying account
(cloud session launch syntax, `claude -p` flags; PLAN-v4 section 7). A real `run` refuses to
start while a template it needs has ``verified = false``.

Real windows use one config per study phase (PLAN-v4 section 7): config.t0.toml, config.t1.toml,
config.t2.toml, config.sweep-n1.toml, config.sweep-n12.toml. ``[run] phase`` names the phase, and
``PHASES`` below is the pre-registered shape of each; `run` refuses a config whose kind / N (slots) /
window / warm-up / grace / start schedule / session timeout / first task do not match its phase.

Worker model (harness README "Worker model: one cloud session per task"): ``n_workers`` is the number
of **slots**. Each slot runs one cloud session at a time; a session works on exactly one task (and its
rework) and is launched by the harness with the task in its prompt.
"""
from __future__ import annotations

import dataclasses as dc
import tomllib
from pathlib import Path
from typing import Any


# PLAN-v4 section 7: the pre-registered shape of each real phase. `start_schedule` is a list of
# [minute, workers running from that minute] (cumulative); [] means all workers at minute 0.
# n_workers = slots. task_timeout_min: a session with no READY this long after its launch (or after a
# rework message) is abandoned. task_budget_min: the time budget stated in the prompt. T0 (a product check,
# not part of the pilot) starts with a fixed task and sends one probe follow-up after the first READY.
T0_FIRST_TASK = "T145"   # small (about 20 lines), one file, no textual conflict with any other task
_SESS = dict(task_timeout_min=25, task_budget_min=20)
PHASES = {
    "t0": dict(kind="trial", n_workers=1, window_min=45, warmup_min=0, grace_min=10, start_schedule=[],
               first_task=T0_FIRST_TASK, probe_followup=True, **_SESS),
    "t1": dict(kind="trial", n_workers=12, window_min=60, warmup_min=10, grace_min=10,
               start_schedule=[[0, 1], [30, 12]], first_task="", probe_followup=False, **_SESS),
    "t2": dict(kind="pilot", n_workers=1, window_min=60, warmup_min=10, grace_min=10, start_schedule=[],
               first_task="", probe_followup=False, **_SESS),
    "sweep-n1": dict(kind="sweep", n_workers=1, window_min=90, warmup_min=10, grace_min=10, start_schedule=[],
                     first_task="", probe_followup=False, **_SESS),
    "sweep-n12": dict(kind="sweep", n_workers=12, window_min=90, warmup_min=10, grace_min=10, start_schedule=[],
                      first_task="", probe_followup=False, **_SESS),
}
WORKER_MODEL = "claude-haiku-4-5"       # PLAN-v4 section 2 (model id string: UNVERIFIED until the CLI check)
REVIEWER_MODEL = "claude-opus-5-5"


@dc.dataclass
class RunCfg:
    kind: str = "sweep"                 # sweep | pilot | trial | dry-run
    phase: str = ""                     # t0 | t1 | t2 | sweep-n1 | sweep-n12 (required for a real `run`)
    n_workers: int = 1                  # slots: cloud sessions running at once (one task per session)
    window_min: float = 120
    warmup_min: float = 10
    grace_min: float = 10
    start_schedule: list = dc.field(default_factory=list)  # [[minute, slots open from then], ...]
    task_timeout_min: float = 25        # no READY within this of a launch / rework message: task abandoned
    task_budget_min: float = 20         # time budget stated in the session prompt
    first_task: str = ""                # T0: this task id goes first in the window's order
    probe_followup: bool = False        # T0: after the first READY, send one probe follow-up (checks delivery)
    probe_timeout_min: float = 10       # the probe holds the slot until its PROBE: commit or this long
    task_order_seed: int = 1234         # overridden per window with --seed
    worker_model: str = WORKER_MODEL
    reviewer_model: str = REVIEWER_MODEL
    output_dir: str = "runs"            # runs/<run_id>/
    poll_interval_s: float = 15         # git fetch cadence (virtual seconds)
    end_grace_early_when_idle: bool = True
    grace_reviews: bool = True          # keep reviewing already-submitted changes during grace
    notes: str = ""


@dc.dataclass
class RepoCfg:
    remote_url: str = ""                # the sandbox repo the workers push to
    work_dir: str = "work"              # orchestrator's own clone (created if missing)
    base_ref: str = "sandbox-v1"        # frozen tag main is reset to before each window
    branch_prefix: str = "claude/task-"   # the branch each session is told to create: claude/task-<id>
    # A READY: <id> commit on any other claude/* branch is still attributed to task <id> (a session may name
    # its branch itself; UNVERIFIED, T0 checks). reset deletes every claude/* branch.
    accept_other_claude_branches: bool = True
    tasks_file_name: str = "TASKS.json" # written to main at reset
    git_user_name: str = "fleet-harness"
    git_user_email: str = "harness@localhost"


@dc.dataclass
class TasksCfg:
    task_file: str = "tasks/tasks.json"  # {"tasks": [{"id","title","text","acceptance":[...]}]}
    hidden_tests_dir: str = "tasks/hidden"  # <dir>/<task id>/test_*.py ; never pushed
    reference_dir: str = "tasks/reference"  # <dir>/<task id>.patch ; used only by SimSession / calibrate


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
    # The review job (PLAN-v4 section 7, frozen before T1):
    #   "checkout" - the reviewer gets a fresh temporary checkout of the exact submitted head (cwd), the task
    #                text and acceptance criteria and the diff, and may read files and run the visible tests
    #                there (tools limited by `allowed_tools`). The pre-registered job.
    #   "diff"     - SUPERSEDED: the diff-only packet in an empty temp dir (prompts/reviewer-diff.md).
    job: str = "checkout"
    # UNVERIFIED: flags of the local `claude -p` call. Placeholders: {model} {prompt_file} {workdir}
    # (temp dir holding the prompt) {checkout} (the change's checkout; "" for the diff job)
    # {allowed_tools} (the list below, joined by allowed_tools_sep) {python} (tests interpreter).
    command: list = dc.field(default_factory=lambda: [
        "claude", "-p", "--model", "{model}", "--output-format", "json",
        "--allowedTools", "{allowed_tools}", "--no-session-persistence"])
    prompt_on_stdin: bool = True        # feed the rendered prompt file on stdin
    cwd: str = "{checkout}"             # working directory of the call (same placeholders); diff job: "{workdir}"
    # Tool allow-list: read/search and running pytest in the checkout, nothing else. Entries may use
    # {python} and {checkout}. UNVERIFIED: the CLI's exact allow-list syntax.
    allowed_tools: list = dc.field(default_factory=lambda: [
        "Read", "Grep", "Glob", "Bash({python} -m pytest:*)"])
    allowed_tools_sep: str = ","
    strip_paths: list = dc.field(default_factory=lambda: [".claude", "CLAUDE.md"])  # removed from the checkout
    output_format: str = "json"         # text | json  (json: expects {"result": ..., "usage": {...}}; UNVERIFIED)
    verified: bool = False
    prompt_template: str = "prompts/reviewer.md"
    timeout_s: float = 900              # real seconds per review call
    retries: int = 1                    # a crashed review is retried once
    retry_backoff_s: float = 60         # virtual seconds after a failed retry before trying that change again
    max_downtime_min: float = 10        # more than this voids the window
    max_diff_chars: int = 60000


@dc.dataclass
class LauncherCfg:
    """How a task's cloud session is started and how rework reaches it (harness README, "Worker model").

    Documented (Claude Code 2.1.283): `claude --cloud "<prompt>"` creates a cloud session that clones the
    current directory's GitHub remote at the current branch and does not return at once (it shows
    provisioning progress); it accepts --model, --effort, --name. `claude -p "<message>" --cloud <id>`
    queues a follow-up message into a running session and exits without waiting.
    UNVERIFIED (T0 settles them): whether and where the launch prints a session id or URL, and how to
    detach from it; hence `session_id_regex`, `detach` and `detach_signal` are settings.
    """
    mode: str = "manual"                # manual | command | sim
    # A local clone whose origin is [repo] remote_url (the sandbox GitHub repo). Sessions are launched from
    # here, on branch main, synced to origin/main before each launch. Created if missing.
    launch_dir: str = "work/launch"
    # UNVERIFIED. Placeholders: {prompt} (the text) {prompt_file} {model} {name} {run_id} {slot} {task}
    launch_command: list = dc.field(default_factory=lambda: [
        "claude", "--cloud", "{prompt}", "--model", "{model}", "--name", "{name}"])
    # UNVERIFIED: what identifies the session in the launch output. Must have a group named session_id.
    session_id_regex: str = r"(?P<session_id>session_[A-Za-z0-9]{8,})"
    # UNVERIFIED: when the harness stops waiting for the launch command.
    #   on_id   - as soon as session_id_regex matches (or the command exits, or launch_timeout_s)
    #   after_s - after detach_after_s (or exit); the id is whatever matched by then
    #   exit    - when the command exits (or launch_timeout_s)
    detach: str = "on_id"
    detach_after_s: float = 120
    launch_timeout_s: float = 600       # never wait longer than this (real seconds)
    # UNVERIFIED: what to do with a launch command still running after detaching.
    #   none - leave it running (reaped at window end); int - send SIGINT; term - send SIGTERM
    detach_signal: str = "none"
    launch_retries: int = 1             # a launch that fails outright is retried this many times
    # UNVERIFIED (documented form): queue a follow-up message into a running session.
    # Placeholders: {message} {message_file} {session_id} {slot} {task}
    followup_command: list = dc.field(default_factory=lambda: [
        "claude", "-p", "{message}", "--cloud", "{session_id}"])
    followup_stdin: bool = False        # True: the message goes in on stdin instead of {message}
    followup_timeout_s: float = 120
    stop_command: list = dc.field(default_factory=list)   # optional; {session_id}; none is documented
    verified: bool = False              # launch_command + session_id_regex + detach checked at T0
    followup_verified: bool = False     # followup_command checked at T0
    worker_prompt: str = "prompts/worker.md"   # per-task session prompt (template)
    rework_prompt: str = "prompts/rework.md"   # follow-up message after a bounce (template)
    # Rendered prompts, follow-up messages and raw launch output hold task text: a PRIVATE directory, never
    # inside the public repo. "" = session-prompts/ beside [tasks] task_file.
    prompt_dir: str = ""


@dc.dataclass
class SimCfg:
    time_scale: float = 90.0            # virtual seconds per real second
    use_toy: bool = True                # False: dry-run against [repo]/[tasks] as configured (a real
                                        # sandbox behind a local bare remote) instead of a toy sandbox
    seed: int = 7
    n_toy_tasks: int = 10
    # sessions (SimSession: one per task)
    startup_mean_s: float = 60          # provisioning + clone before the session pushes its branch (exp.)
    rate_per_hour: float = 3.0          # lambda: work time after start-up is exp. with mean 1/lambda
    rework_factor: float = 0.5          # rework takes this fraction of a fresh task's time
    p_wrong: float = 0.15               # change fails its own hidden tests
    p_conflict: float = 0.12            # change touches the textual hotspot (rebase conflicts)
    p_semantic: float = 0.08            # change alters a shared constant (breaks other tasks after rebase)
    p_visible_break: float = 0.05       # change breaks a visible test
    p_wrong_rework_factor: float = 0.3  # a rework is wrong with p_wrong * this
    p_stall: float = 0.0                # a session never pushes READY (exercises the session timeout)
    union_resolve: bool = False         # resolve a reference patch's conflicts with main by keeping both sides
    max_reworks: int = 0                # >0: a session stops responding after this many reworks (-> timeout)
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


def phase_problems(cfg: Config) -> list[str]:
    """Differences between cfg.run and the pre-registered shape of its named phase (PHASES).
    Empty list = the config matches. A real `run` refuses to start otherwise."""
    rc = cfg.run
    if rc.phase not in PHASES:
        return [f"[run] phase = {rc.phase!r}: a real run must name one of {sorted(PHASES)}"]
    want = PHASES[rc.phase]
    probs = []
    for k, v in want.items():
        have = getattr(rc, k)
        if k == "start_schedule":
            have = [list(map(float, x)) for x in have]
            v = [list(map(float, x)) for x in v]
        elif isinstance(v, (int, float)) and not isinstance(v, bool):
            have = float(have)
            v = float(v)
        if have != v:
            probs.append(f"[run] {k} = {getattr(rc, k)!r}, but phase {rc.phase!r} is pre-registered with {want[k]!r}")
    if cfg.launcher.mode == "command" and cfg.launcher.detach not in ("on_id", "after_s", "exit"):
        probs.append(f"[launcher] detach = {cfg.launcher.detach!r}: one of on_id, after_s, exit")
    for k, v in (("worker_model", WORKER_MODEL), ("reviewer_model", REVIEWER_MODEL)):
        if getattr(rc, k) != v:
            probs.append(f"[run] {k} = {getattr(rc, k)!r}, but PLAN-v4 uses {v!r}")
    if cfg.reviewer.mode == "command" and cfg.reviewer.job != "checkout":
        probs.append(f"[reviewer] job = {cfg.reviewer.job!r}: the pre-registered review job is 'checkout' "
                     "(the diff job is superseded)")
    return probs


def schedule_problems(schedule: list, n_workers: int) -> list[str]:
    """start_schedule must be [[minute, cumulative slots], ...], minutes and counts increasing,
    ending at n_workers (the number of slots)."""
    if not schedule:
        return []
    probs = []
    last_m, last_n = -1.0, 0
    for item in schedule:
        if not (isinstance(item, (list, tuple)) and len(item) == 2):
            return [f"start_schedule entry {item!r} is not [minute, workers]"]
        m, n = float(item[0]), int(item[1])
        if m <= last_m or n <= last_n:
            probs.append("start_schedule minutes and worker counts must both increase")
        last_m, last_n = m, n
    if float(schedule[0][0]) != 0.0:
        probs.append("start_schedule must start at minute 0")
    if last_n != n_workers:
        probs.append(f"start_schedule ends at {last_n} workers, n_workers = {n_workers}")
    return probs
