"""One cloud session per task: starting a task's session, and sending it a follow-up message.

The orchestrator owns the slots and decides what runs where; a launcher only carries out two actions:

  launch(slot, task, prompt, name) -> LaunchResult   start a session for one task (its prompt holds the task)
  send(slot, task_id, session_id, message, kind)     queue a follow-up message (rework / probe) into it
  stop(session_id), stop_all()                       best effort; no stop command is documented

``CommandLauncher`` runs the UNVERIFIED ``[launcher] launch_command`` / ``followup_command`` templates;
``ManualLauncher`` prints the exact command for the operator and waits for the session id (T0);
``sim.SimCloudLauncher`` is the dry-run / test stand-in. Nothing here is exercised against the real product
by the tests or by dry runs: `run` refuses mode = command while ``verified`` / ``followup_verified`` are false.

Rendered prompts, follow-up messages and raw launch output contain task text, so they are written only to
``[launcher] prompt_dir`` (default: session-prompts/ beside the task file), which must be outside the public
repo.
"""
from __future__ import annotations

import dataclasses as dc
import os
import re
import signal
import subprocess
import threading
import time
from pathlib import Path

from .config import Config
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
    detached_by: str            # id | timer | exit | timeout | manual | sim
    returncode: int | None = None
    output_file: str | None = None


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
    crit = "\n".join(f"- {c}" for c in task.acceptance) or "- (none given)"
    return _template(cfg, cfg.launcher.worker_prompt).format(
        task_id=task.id, title=task.title, text=task.text.strip(), acceptance=crit,
        branch=branch_for(cfg, task.id), budget_min=f"{cfg.run.task_budget_min:g}", visible_cmd=VISIBLE_CMD_TEXT)


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


def private_prompt_dir(cfg: Config, run_id: str) -> Path:
    base = cfg.path(cfg.launcher.prompt_dir) if cfg.launcher.prompt_dir else \
        cfg.path(cfg.tasks.task_file).parent / "session-prompts"
    d = (base / run_id).resolve()
    root = public_repo_root()
    if root is not None and (d == root or root in d.parents):
        raise SystemExit(f"[launcher] prompt_dir {d} is inside the public repo {root}; rendered prompts hold "
                         "task text, so point it at a private path (the tasks repo's gitignored dryrun/)")
    d.mkdir(parents=True, exist_ok=True)
    return d


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
        if not session_id:
            raise LaunchError("no session id for a follow-up")
        lc = self.lc
        mf = self.dir / f"{self.run_id}-{slot}-{task_id}-{kind}-{int(time.time())}.msg.md"
        mf.write_text(message)
        cmd = self._fmt(lc.followup_command, message="" if lc.followup_stdin else message, message_file=str(mf),
                        session_id=session_id, slot=slot, task=task_id)
        try:
            p = subprocess.run(cmd, cwd=self.repo.path, input=message if lc.followup_stdin else None,
                               capture_output=True, text=True, timeout=lc.followup_timeout_s)
        except subprocess.TimeoutExpired as e:
            raise LaunchError(f"follow-up timed out after {lc.followup_timeout_s}s") from e
        (self.dir / (mf.name + ".out")).write_text(p.stdout + p.stderr)
        if p.returncode != 0:
            raise LaunchError(f"follow-up exited {p.returncode}: {p.stderr.strip()[:300]}")

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
