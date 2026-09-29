"""One cloud session per task: starting a task's session, and sending it a follow-up message.

The orchestrator owns the slots and decides what runs where; a launcher only carries out two actions:

  launch(slot, task, prompt, name) -> LaunchResult   start a session for one task (its prompt holds the task)
  send(slot, task_id, session_id, message, kind)     queue a follow-up message (rework / probe) into it
  stop(session_id), stop_all()                       best effort; no stop command is documented

``RoutineLauncher`` (mode = "routine", the default for real phases since 2026-09-28) re-arms the slot's Claude
Code routine with the task prompt (harness/routines.py) and finds the new run's session id afterwards;
``CommandLauncher`` runs the ``[launcher] launch_command`` (``claude --cloud``) / ``followup_command`` templates;
``ManualLauncher`` prints the exact command for the operator and waits for the session id;
``sim.SimCloudLauncher`` is the dry-run / test stand-in. Nothing here is exercised against the real product
by the tests or by dry runs: `run` refuses mode = command while ``verified`` / ``followup_verified`` are false,
and mode = routine while they are false, except in phase t0 (the check that sets ``verified``).

Rendered prompts, follow-up messages and raw launch output contain task text, so they are written only to
``[launcher] prompt_dir`` (default: session-prompts/ beside the task file), which must be outside the public
repo.
"""
from __future__ import annotations

import dataclasses as dc
import datetime as dt
import os
import re
import shutil
import signal
import subprocess
import threading
import time
from pathlib import Path

from . import routines as rt
from .config import Config, routine_group
from .gitops import Repo, clone
from .tasks import Task

VISIBLE_CMD_TEXT = "python -m pytest -q"
_COMMENT = re.compile(r"\A\s*<!--.*?-->\s*", re.S)


class NotVerified(SystemExit):
    pass


class LaunchError(RuntimeError):
    pass


@dc.dataclass
class LaunchResult:
    session_id: str | None
    detached_by: str            # id | timer | exit | timeout | manual | sim | routine
    returncode: int | None = None
    output_file: str | None = None
    detail: str = ""            # appended to the launch_detail note
    pending: object = None      # routine mode: handle for launcher.discover_session_id (id found later)


def require_verified(cfg: Config) -> None:
    problems = []
    if cfg.reviewer.mode == "command" and not cfg.reviewer.verified:
        problems.append("[reviewer] command is UNVERIFIED (set verified = true after the CLI check, PLAN-v4 s7)")
    lc = cfg.launcher
    if lc.mode == "command":
        if not lc.verified:
            problems.append("[launcher] launch_command / session_id_regex / detach are UNVERIFIED "
                            "(set verified = true after T0; until then use mode = \"manual\")")
        if not lc.followup_verified:
            problems.append("[launcher] followup_command is UNVERIFIED (set followup_verified = true after T0)")
        if not lc.launch_command:
            problems.append("[launcher] launch_command is empty")
        if "(?P<session_id>" not in lc.session_id_regex:
            problems.append("[launcher] session_id_regex needs a group named session_id")
    if lc.mode == "routine":
        if not lc.verified and cfg.run.phase != "t0":
            problems.append("[launcher] routine mode is UNVERIFIED (T0 checks it; then set verified = true)")
        if not lc.followup_verified:
            problems.append("[launcher] followup_command is UNVERIFIED (set followup_verified = true after T0)")
        r = lc.routine
        for k in ("environment_id", "source_url"):
            if not getattr(r, k):
                problems.append(f"[launcher.routine] {k} is empty")
        if not any("{instruction}" in c for c in r.runner):
            problems.append("[launcher.routine] runner needs an {instruction} placeholder")
        missing = [s for s in slot_ids(cfg) if s not in slot_routines(cfg)]
        if missing:
            problems.append(f"no routine for slot(s) {','.join(missing)}: run `python -m harness routines-setup "
                            f"--config <this config>` first (state file {routine_state_path(cfg)})")
    if lc.mode == "sim" or cfg.reviewer.mode == "sim":
        problems.append("sim components are for dry-run only")
    if problems:
        raise NotVerified("refusing a real run:\n  " + "\n  ".join(problems))


# ----------------------------------------------------------------------------- prompts
def _template(cfg: Config, rel: str) -> str:
    return _COMMENT.sub("", cfg.path(rel).read_text(), count=1)


def branch_for(cfg: Config, task_id: str) -> str:
    return f"{cfg.repo.branch_prefix}{task_id}"


def task_prompt(cfg: Config, task: Task) -> str:
    if cfg.launcher.mode == "routine":
        return _template(cfg, cfg.launcher.routine_prompt).format(
            task_id=task.id, branch=branch_for(cfg, task.id)).strip()
    crit = "\n".join(f"- {c}" for c in task.acceptance) or "- (none given)"
    return _template(cfg, cfg.launcher.worker_prompt).format(
        task_id=task.id, title=task.title, text=task.text.strip(), acceptance=crit,
        branch=branch_for(cfg, task.id), budget_min=f"{cfg.run.task_budget_min:g}", visible_cmd=VISIBLE_CMD_TEXT)


def harness_doc(cfg: Config) -> str:
    """HARNESS.md for routine mode (reset writes it to main)."""
    return _template(cfg, cfg.launcher.harness_doc).format(
        budget_min=f"{cfg.run.task_budget_min:g}", visible_cmd=VISIBLE_CMD_TEXT).lstrip("\n")


def rework_message(cfg: Config, task_id: str, attempt_no: int, head: str, cause: str, cause_text: str,
                   detail: str = "") -> str:
    details = f"\nDetails:\n\n{detail.strip()}\n" if detail.strip() else ""
    return _template(cfg, cfg.launcher.rework_prompt).format(
        task_id=task_id, attempt_no=attempt_no, head_short=head[:12], cause=cause, cause_text=cause_text,
        details=details, branch=branch_for(cfg, task_id), budget_min=f"{cfg.run.task_budget_min:g}",
        visible_cmd=VISIBLE_CMD_TEXT)


def probe_message(cfg: Config, task_id: str) -> str:
    """T0 only: a follow-up that asks for an empty PROBE: commit, to check that follow-ups arrive."""
    b = branch_for(cfg, task_id)
    return (f"Harness check for task {task_id}. This is not review feedback and needs no change to the code. "
            f"Push one empty commit to `{b}` with a message starting `PROBE: {task_id}`, for example "
            f"`git commit --allow-empty -m \"PROBE: {task_id}\" && git push origin {b}`, then stop.")


def public_repo_root() -> Path | None:
    here = Path(__file__).resolve().parent
    p = subprocess.run(["git", "rev-parse", "--show-toplevel"], cwd=here, capture_output=True, text=True)
    return Path(p.stdout.strip()).resolve() if p.returncode == 0 else None


def inside_public_repo(p: Path) -> Path | None:
    """The public repo root if ``p`` (resolved) is it or lies inside it, else None."""
    p = Path(p).resolve()
    root = public_repo_root()
    return root if root is not None and (p == root or root in p.parents) else None


def private_base_dir(cfg: Config) -> Path:
    """[launcher] prompt_dir (default session-prompts/ beside the task file): the private work area."""
    base = cfg.path(cfg.launcher.prompt_dir) if cfg.launcher.prompt_dir else \
        cfg.path(cfg.tasks.task_file).parent / "session-prompts"
    return base.resolve()


def private_prompt_dir(cfg: Config, run_id: str) -> Path:
    d = (private_base_dir(cfg) / run_id).resolve()
    root = inside_public_repo(d)
    if root is not None:
        raise SystemExit(f"[launcher] prompt_dir {d} is inside the public repo {root}; rendered prompts hold "
                         "task text, so point it at a private path (the tasks repo's gitignored dryrun/)")
    d.mkdir(parents=True, exist_ok=True)
    return d


# ----------------------------------------------------------------------------- CLI pinning
_VERSION = re.compile(r"\d+(?:\.\d+)+")


def _cli_version(exe: str, run_fn) -> str:
    """``<exe> --version`` -> "2.1.283"; SystemExit if it does not run or prints no version."""
    try:
        p = run_fn([exe, "--version"], capture_output=True, text=True, timeout=60, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError) as e:
        raise SystemExit(f"pin_cli: `{exe} --version` did not run: {e}")
    m = _VERSION.search(p.stdout or "")
    if p.returncode != 0 or not m:
        raise SystemExit(f"pin_cli: `{exe} --version` exited {p.returncode}: {(p.stdout + p.stderr).strip()[:200]!r}")
    return m.group(0)


def _swap_exe(cmd: list, exe: str, first_only: bool = True) -> list:
    """``cmd`` with its bare `claude` executable replaced by ``exe``: element 0 (first_only), or else the
    first `claude` before any {placeholder} (a wrapper such as `script -q /dev/null claude ...`)."""
    cmd = list(cmd)
    for i, c in enumerate(cmd):
        if c == "claude":
            cmd[i] = exe
            break
        if first_only or "{" in c:
            break
    return cmd


def pin_cli(cfg: Config, dest_dir: Path | None = None, which_fn=shutil.which, run_fn=subprocess.run) -> dict:
    """Copy the `claude` executable into the private area and point every CLI command of this config at the
    copy, so an auto-update of the installed CLI mid-window cannot break process starts (T1, 2026-09-28).

    The executable is ``which_fn("claude")`` resolved through symlinks; the copy is
    ``<dest_dir>/claude-<version>`` (dest_dir default: ``<[launcher] prompt_dir base>/bin``, refused inside the
    public repo), made executable and checked with ``--version`` (exit 0). An existing copy of the same version
    and size is reused. Rewrites, in place: [launcher.routine] runner, [launcher] followup_command,
    [reviewer] command (a leading `claude`), and [launcher] launch_command (its `claude`, also behind a
    wrapper). Returns {path, version, source, reused}."""
    found = which_fn("claude")
    if not found:
        raise SystemExit("pin_cli: no `claude` on PATH (set [run] pin_cli = false to skip pinning)")
    src = Path(os.path.realpath(found))
    d = Path(dest_dir) if dest_dir is not None else private_base_dir(cfg) / "bin"
    d = d.resolve()
    root = inside_public_repo(d)
    if root is not None:
        raise SystemExit(f"pin_cli: {d} is inside the public repo {root}; the pinned CLI goes in the private "
                         "work area ([launcher] prompt_dir)")
    d.mkdir(parents=True, exist_ok=True)
    version = _cli_version(str(src), run_fn)
    dest = d / f"claude-{version}"
    reused = False
    if dest.is_file() and dest.stat().st_size == src.stat().st_size and os.access(dest, os.X_OK):
        try:
            reused = _cli_version(str(dest), run_fn) == version
        except SystemExit:
            reused = False
    if not reused:
        tmp = d / f".claude-{version}.{os.getpid()}.tmp"
        shutil.copy2(src, tmp)
        os.chmod(tmp, os.stat(tmp).st_mode | 0o755)
        try:
            got = _cli_version(str(tmp), run_fn)
        except SystemExit:
            tmp.unlink(missing_ok=True)
            raise
        if got != version:          # the install changed under us: name the copy by what it is
            version, dest = got, d / f"claude-{got}"
        os.replace(tmp, dest)
    exe = str(dest)
    lc = cfg.launcher
    lc.routine.runner = _swap_exe(lc.routine.runner, exe)
    lc.followup_command = _swap_exe(lc.followup_command, exe)
    cfg.reviewer.command = _swap_exe(cfg.reviewer.command, exe)
    if lc.launch_command:
        lc.launch_command = _swap_exe(lc.launch_command, exe, first_only=False)
    return {"path": exe, "version": version, "source": str(src), "reused": reused}


def slot_ids(cfg: Config) -> list[str]:
    return [f"s{i}" for i in range(1, cfg.run.n_workers + 1)]


def routine_state_path(cfg: Config) -> Path:
    """Private state file (slot routine ids): [launcher.routine] state_file, else routines-state.json beside
    [repo] work_dir (not inside it: reset runs git clean there). Refused inside the public repo."""
    r = cfg.launcher.routine
    p = cfg.path(r.state_file) if r.state_file else cfg.path(cfg.repo.work_dir).parent / "routines-state.json"
    p = p.resolve()
    root = inside_public_repo(p)
    if root is not None:
        raise SystemExit(f"[launcher.routine] state file {p} is inside the public repo {root}; trigger ids are "
                         "private: point it at the private work dir")
    return p


def slot_routines(cfg: Config) -> dict[str, str]:
    """slot -> trigger id for this config's phase: the state file's entries named <prefix>-<group>-s<k> (group =
    config.routine_group(phase): the sweep's K variants share one set),
    overridden by [launcher.routine] slot_routines."""
    r = cfg.launcher.routine
    state = rt.load_state(routine_state_path(cfg))["routines"]
    out = {}
    for k in range(1, cfg.run.n_workers + 1):
        e = state.get(rt.routine_name(r.name_prefix, routine_group(cfg.run.phase), k))
        if isinstance(e, dict) and e.get("trigger_id"):
            out[f"s{k}"] = e["trigger_id"]
    out.update({str(k): str(v) for k, v in r.slot_routines.items()})
    return out


def routine_client(cfg: Config, log_dir: Path | None = None, run_fn=subprocess.run) -> "rt.RoutineClient":
    r = cfg.launcher.routine
    job = rt.JobSpec(r.environment_id, r.model or cfg.run.worker_model, r.source_url, list(r.allowed_tools))
    return rt.RoutineClient(job, r.runner, cwd=log_dir, timeout_s=r.runner_timeout_s, run_fn=run_fn,
                            log_dir=log_dir)


START_RETRIES, START_RETRY_WAIT_S = 3, 5.0    # a CLI process that cannot start (OSError) is retried


def run_followup(cfg: Config, msg_dir: Path, run_id: str, slot: str, task_id: str, session_id: str | None,
                 message: str, kind: str, cwd, run_fn=subprocess.run, sleep_fn=time.sleep) -> None:
    """``[launcher] followup_command`` (`claude -p "<message>" --cloud <session id>`) for one message. A process
    that cannot start is retried START_RETRIES times, START_RETRY_WAIT_S apart."""
    if not session_id:
        raise LaunchError("no session id for a follow-up")
    lc = cfg.launcher
    mf = msg_dir / f"{run_id}-{slot}-{task_id}-{kind}-{int(time.time())}.msg.md"
    mf.write_text(message)
    cmd = [c.format(message="" if lc.followup_stdin else message, message_file=str(mf), session_id=session_id,
                    slot=slot, task=task_id) for c in lc.followup_command]
    p = rt.start_with_retries(
        lambda: run_fn(cmd, cwd=str(cwd), input=message if lc.followup_stdin else None,
                       capture_output=True, text=True, timeout=lc.followup_timeout_s),
        START_RETRIES, START_RETRY_WAIT_S, sleep_fn,
        lambda e, n: LaunchError(f"follow-up could not start ({n} attempts): {e}"),
        lambda e: LaunchError(f"follow-up timed out after {lc.followup_timeout_s}s"))
    (msg_dir / (mf.name + ".out")).write_text((p.stdout or "") + (p.stderr or ""))
    if p.returncode != 0:
        raise LaunchError(f"follow-up exited {p.returncode}: {(p.stderr or '').strip()[:300]}")


def launch_clone(cfg: Config) -> Repo:
    """The local clone sessions are launched from: origin = [repo] remote_url, branch main at origin/main."""
    return clone(cfg.repo.remote_url, cfg.path(cfg.launcher.launch_dir),
                 (cfg.repo.git_user_name, cfg.repo.git_user_email))


def sync_launch_clone(repo: Repo) -> str:
    repo.run("fetch", "-q", "--prune", "origin", "+refs/heads/main:refs/remotes/origin/main")
    repo.run("checkout", "-q", "--force", "-B", "main", "origin/main")
    repo.run("branch", "-q", "--set-upstream-to=origin/main", "main", check=False)
    return repo.sha("HEAD")


# ----------------------------------------------------------------------------- manual
class ManualLauncher:
    """The operator runs each command by hand (T0, or until the launcher is verified). The harness writes the
    prompt / message to a private file, prints the exact command, and waits for the session id (launch) or
    for Enter (follow-up). Questions are serialised, so several slots do not interleave them."""

    accepts_no_session_id = True     # the operator can reach a session from the web UI without an id

    def __init__(self, cfg: Config, log, run_id: str, input_fn=input):
        self.cfg, self.log, self.run_id, self.input = cfg, log, run_id, input_fn
        self.dir = private_prompt_dir(cfg, run_id)
        self.repo = launch_clone(cfg)
        self.lock = threading.Lock()

    def launch(self, slot: str, task: Task, prompt: str, name: str) -> LaunchResult:
        f = self.dir / f"{name}.prompt.md"
        f.write_text(prompt)
        with self.lock:
            sha = sync_launch_clone(self.repo)
            print(f"\n[{slot}] launch task {task.id} now (launch clone at main {sha[:12]}):\n"
                  f"  cd {self.repo.path} && claude --cloud \"$(cat {f})\" --model {self.cfg.run.worker_model} "
                  f"--name {name}")
            sid = self.input(f"[{slot}] session id for {name} (Enter if none shown): ").strip() or None
        return LaunchResult(sid, "manual", None, str(f))

    def send(self, slot: str, task_id: str, session_id: str | None, message: str, kind: str = "rework") -> None:
        f = self.dir / f"{self.run_id}-{slot}-{task_id}-{kind}-{int(time.time())}.msg.md"
        f.write_text(message)
        with self.lock:
            print(f"\n[{slot}] send the {kind} message for task {task_id} to session {session_id}:\n"
                  f"  claude -p \"$(cat {f})\" --cloud {session_id}")
            ans = self.input(f"[{slot}] Enter once sent (type 'fail' if it could not be sent): ").strip().lower()
        if ans == "fail":
            raise LaunchError("operator reported the follow-up could not be sent")

    def stop(self, session_id: str | None) -> None:
        pass

    def stop_all(self, orch=None) -> None:
        print("WINDOW END: stop or archive every open session now. New READY pushes are no longer accepted; "
              "reviews and merges continue through the grace period.")


# ----------------------------------------------------------------------------- command (UNVERIFIED)
class CommandLauncher:
    def __init__(self, cfg: Config, log, run_id: str):
        self.cfg, self.log, self.run_id = cfg, log, run_id
        self.lc = cfg.launcher
        self.dir = private_prompt_dir(cfg, run_id)
        self.repo = launch_clone(cfg)
        self.clone_lock = threading.Lock()
        self.id_re = re.compile(self.lc.session_id_regex)
        self.procs: list[subprocess.Popen] = []

    def _fmt(self, tpl: list[str], **kw) -> list[str]:
        return [c.format(**kw) for c in tpl]

    def launch(self, slot: str, task: Task, prompt: str, name: str) -> LaunchResult:
        lc = self.lc
        pf = self.dir / f"{name}.prompt.md"
        pf.write_text(prompt)
        out_file = self.dir / f"{name}.launch.log"
        cmd = self._fmt(lc.launch_command, prompt=prompt, prompt_file=str(pf), model=self.cfg.run.worker_model,
                        name=name, run_id=self.run_id, slot=slot, task=task.id)
        # The launch clones the current directory's remote at the current branch: main, synced to origin/main.
        # The lock covers only the sync and the process start, so slots launch in parallel.
        with self.clone_lock:
            sync_launch_clone(self.repo)
            p = subprocess.Popen(cmd, cwd=self.repo.path, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                 stdin=subprocess.DEVNULL, text=True, start_new_session=True)
        buf: list[str] = []
        seen = threading.Event()
        sid: list[str] = []

        def reader():
            for line in p.stdout:
                buf.append(line)
                if not sid:
                    m = self.id_re.search("".join(buf))
                    if m:
                        sid.append(m.group("session_id"))
                        seen.set()
        th = threading.Thread(target=reader, daemon=True)
        th.start()
        t0 = time.monotonic()
        how = "timeout"
        while time.monotonic() - t0 < lc.launch_timeout_s:
            if p.poll() is not None:
                th.join(timeout=5)
                how = "exit"
                break
            if lc.detach == "on_id" and seen.is_set():
                how = "id"
                break
            if lc.detach == "after_s" and time.monotonic() - t0 >= lc.detach_after_s:
                how = "timer"
                break
            time.sleep(0.2)
        rc = p.poll()
        if rc is None:
            if lc.detach_signal == "int":
                os.killpg(p.pid, signal.SIGINT)
            elif lc.detach_signal == "term":
                os.killpg(p.pid, signal.SIGTERM)
            self.procs.append(p)
        out_file.write_text("".join(buf))
        session_id = sid[0] if sid else None
        if session_id is None and rc not in (None, 0):
            raise LaunchError(f"launch exited {rc} without a session id (output in {out_file})")
        return LaunchResult(session_id, how, rc, str(out_file))

    def send(self, slot: str, task_id: str, session_id: str | None, message: str, kind: str = "rework") -> None:
        run_followup(self.cfg, self.dir, self.run_id, slot, task_id, session_id, message, kind, self.repo.path)

    def stop(self, session_id: str | None) -> None:
        if not (self.lc.stop_command and session_id):
            return
        subprocess.run(self._fmt(self.lc.stop_command, session_id=session_id), capture_output=True, text=True,
                       timeout=120)

    def stop_all(self, orch=None) -> None:
        for p in self.procs:
            if p.poll() is None:
                try:
                    os.killpg(p.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
        if not self.lc.stop_command:
            print("WINDOW END: no stop_command configured; stop or archive the open sessions by hand now.")


# ----------------------------------------------------------------------------- routine
@dc.dataclass
class PendingRun:
    slot: str
    trigger_id: str
    run_once_at: dt.datetime
    rearmed_at: dt.datetime


class RoutineLauncher:
    """mode = "routine": one Claude Code routine per slot (created by `harness routines-setup`). A launch
    re-arms the slot's routine (prompt + run_once_at = now + lead_s) and returns with no session id; the
    orchestrator then calls ``discover_session_id``, which polls list_runs for the run created after
    run_once_at. Follow-ups go through ``[launcher] followup_command`` with that run's id (cse_...). At window
    end (and on any exit of `run`) every slot routine is disabled.

    ``client``, ``now_fn``, ``sleep_fn`` and ``followup_run_fn`` are injectable for tests."""

    accepts_no_session_id = False

    def __init__(self, cfg: Config, log, run_id: str, client=None, now_fn=rt.utcnow, sleep_fn=time.sleep,
                 followup_run_fn=subprocess.run, routines: dict[str, str] | None = None):
        self.cfg, self.log, self.run_id = cfg, log, run_id
        self.lc, self.r = cfg.launcher, cfg.launcher.routine
        self.dir = private_prompt_dir(cfg, run_id)
        self.client = client or routine_client(cfg, self.dir)
        self.now, self.sleep, self.followup_run_fn = now_fn, sleep_fn, followup_run_fn
        self.routines = dict(routines if routines is not None else slot_routines(cfg))
        missing = [s for s in slot_ids(cfg) if s not in self.routines]
        if missing:
            raise SystemExit(f"no routine for slot(s) {','.join(missing)}: run `python -m harness routines-setup` first")
        self.lock = threading.Lock()
        self.updates: dict[str, list[dt.datetime]] = {}    # trigger -> update times (rate budget)
        self.claimed: set[str] = set()                     # run ids already attributed
        self.disabled_all = False
        self.closing = threading.Event()                   # set at window end: no new re-arms

    # -- helpers
    def _wait(self, seconds: float, should_stop=None) -> bool:
        """Sleep up to ``seconds`` real seconds; True if should_stop() became true."""
        end = self.now() + dt.timedelta(seconds=max(0.0, seconds))
        while self.now() < end:
            if should_stop is not None and should_stop():
                return True
            self.sleep(min(1.0, max(0.0, (end - self.now()).total_seconds())))
        return bool(should_stop and should_stop())

    def _take_budget(self, trigger: str) -> dt.datetime:
        """At most max_updates_per_hour updates per routine in any hour: wait for the oldest to age out.
        Returns the entry taken (for ``_refund_budget``)."""
        while True:
            with self.lock:
                now = self.now()
                ts = [t for t in self.updates.get(trigger, []) if now - t < dt.timedelta(hours=1)]
                self.updates[trigger] = ts
                if len(ts) < self.r.max_updates_per_hour:
                    ts.append(now)
                    return now
                wait = (ts[0] + dt.timedelta(hours=1) - now).total_seconds() + 1
            self.log.note(f"routine_rate_wait trigger={trigger} wait_s={wait:.0f}")
            if self._wait(wait, self.closing.is_set):
                # T1, 2026-09-28: a slot parked here kept the process alive 20 min past grace end
                raise LaunchError(f"window closed while {trigger} waited for its update budget")

    def _refund_budget(self, trigger: str, taken: dt.datetime) -> None:
        """Give back an entry for an update that never reached the API (the runner never started)."""
        with self.lock:
            ts = self.updates.get(trigger, [])
            if taken in ts:
                ts.remove(taken)

    # -- launcher interface
    def launch(self, slot: str, task, prompt: str, name: str) -> LaunchResult:
        trig = self.routines[slot]
        (self.dir / f"{name}.prompt.md").write_text(prompt)
        taken = self._take_budget(trig)
        if self.closing.is_set():
            self._refund_budget(trig, taken)
            raise LaunchError(f"window closed: {trig} not re-armed")
        at = self.now() + dt.timedelta(seconds=self.r.lead_s)
        at = at.replace(microsecond=0) + dt.timedelta(seconds=1)       # whole seconds, never earlier
        try:
            self.client.rearm(trig, prompt, at)
        except rt.RoutineNotStarted as e:
            # The runner never ran (T1 incident: the CLI binary replaced mid-run): no update reached the API,
            # so it does not count against the routine's hourly budget.
            self._refund_budget(trig, taken)
            self.log.note(f"routine_budget_refund trigger={trig} reason=not_started")
            raise LaunchError(f"re-arm of {trig} failed: {e}") from e
        except rt.RoutineError as e:
            raise LaunchError(f"re-arm of {trig} failed: {e}") from e
        if self.closing.is_set():
            # The window closed while this re-arm was in flight; disable_all may already have run and the
            # re-arm re-enabled the routine (T0c, 2026-09-28: T007 launched after window end). Undo it.
            self._disable_one(slot, trig, "late_rearm")
            raise LaunchError(f"window closed during the re-arm of {trig}; disabled again")
        return LaunchResult(None, "routine", 0, None, detail=f"trigger={trig} run_once_at={rt.rfc3339(at)}",
                            pending=PendingRun(slot, trig, at, self.now()))

    def discover_session_id(self, pending: PendingRun, should_stop=None) -> str | None:
        """Poll list_runs (lazily) for the run the re-arm created; its id, or None (timeout / stopped)."""
        r = self.r
        first = (pending.run_once_at - self.now()).total_seconds() + r.first_poll_delay_s
        if self._wait(first, should_stop):
            return None
        deadline = pending.run_once_at + dt.timedelta(seconds=r.session_id_timeout_s)
        not_before = pending.run_once_at - dt.timedelta(seconds=r.created_skew_s)
        while True:
            try:
                runs = self.client.list_runs(pending.trigger_id)
            except rt.RoutineError as e:
                self.log.note(f"routine_list_runs_failed slot={pending.slot} trigger={pending.trigger_id} "
                              f"error={str(e)[:200]}")
                runs = []
            with self.lock:
                run = rt.new_run_after(runs, not_before, self.claimed)
                if run is not None:
                    self.claimed.add(run["id"])
                    return run["id"]
            if self.now() >= deadline:
                return None
            if self._wait(min(r.poll_s, max(0.0, (deadline - self.now()).total_seconds())), should_stop):
                return None

    def send(self, slot: str, task_id: str, session_id: str | None, message: str, kind: str = "rework") -> None:
        run_followup(self.cfg, self.dir, self.run_id, slot, task_id, session_id, message, kind, self.dir,
                     run_fn=self.followup_run_fn, sleep_fn=self.sleep)

    def stop(self, session_id: str | None) -> None:
        pass                                   # no stop is documented; the slot's routine is simply re-armed

    def _disable_one(self, slot: str, trig: str, why: str = "") -> str | None:
        err = None
        for _ in range(2):
            try:
                self.client.disable(trig)
                err = None
                break
            except rt.RoutineError as e:
                err = str(e)
        tag = f" why={why}" if why else ""
        if err is None:
            self.log.note(f"routine_disabled slot={slot} trigger={trig}{tag}")
        else:
            self.log.note(f"routine_disable_failed slot={slot} trigger={trig}{tag} error={err[:200]}")
            print(f"ROUTINE NOT DISABLED (disable it by hand at claude.ai/code/routines): {slot} {trig}: {err[:200]}")
        return err

    def disable_all(self) -> list[tuple[str, str, str]]:
        """Disable every slot routine (enabled: false). Returns [(slot, trigger, error)] for failures. Sets
        ``closing`` first, so no re-arm starts after this and one in flight disables its routine again."""
        self.closing.set()
        failed = []
        for slot, trig in sorted(self.routines.items(), key=lambda x: int(x[0][1:]) if x[0][1:].isdigit() else 0):
            err = self._disable_one(slot, trig)
            if err is not None:
                failed.append((slot, trig, err))
        self.disabled_all = True
        if failed:
            print("ROUTINES NOT DISABLED (disable them by hand at claude.ai/code/routines, or they may fire):\n  "
                  + "\n  ".join(f"{s} {t}: {e[:200]}" for s, t, e in failed))
        return failed

    def stop_all(self, orch=None) -> None:
        self.disable_all()
        print("WINDOW END: slot routines disabled. Stop or archive the open routine sessions by hand now; new READY "
              "pushes are no longer accepted, reviews and merges continue through the grace period.")
