"""Starting and stopping the real worker sessions.

Nothing here is exercised against the real product by the tests or by dry runs.
``CommandLauncher`` runs the UNVERIFIED ``[launcher] start_command`` template;
``ManualLauncher`` has the operator start each session by hand and type its id.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

from .config import Config
from .events import EventLog


class NotVerified(SystemExit):
    pass


def require_verified(cfg: Config) -> None:
    problems = []
    if cfg.reviewer.mode == "command" and not cfg.reviewer.verified:
        problems.append("[reviewer] command is UNVERIFIED (set verified = true after checking PLAN-v3 s10)")
    if cfg.launcher.mode == "command" and not cfg.launcher.verified:
        problems.append("[launcher] start_command is UNVERIFIED (set verified = true after checking PLAN-v3 s10)")
    if cfg.launcher.mode == "command" and not cfg.launcher.start_command:
        problems.append("[launcher] start_command is empty")
    if cfg.launcher.mode == "sim" or cfg.reviewer.mode == "sim":
        problems.append("sim components are for dry-run only")
    if problems:
        raise NotVerified("refusing a real run:\n  " + "\n  ".join(problems))


def render_worker_prompts(cfg: Config, run_dir: Path) -> dict[str, Path]:
    """prompts/worker.md with {worker} filled in, one file per worker, kept in the run dir."""
    tpl = cfg.path(cfg.launcher.worker_prompt).read_text()
    out = {}
    d = run_dir / "worker-prompts"
    d.mkdir(parents=True, exist_ok=True)
    for i in range(1, cfg.run.n_workers + 1):
        wid = f"w{i}"
        p = d / f"{wid}.md"
        p.write_text(tpl.replace("{worker}", wid))
        out[wid] = p
    return out


class ManualLauncher:
    """The operator starts each cloud session by hand (with prompts/worker.md
    and worker id w1..wN) and types the session id here."""

    def __init__(self, cfg: Config, log: EventLog, run_id: str, run_dir: Path, input_fn=input):
        self.cfg, self.log, self.run_id, self.input = cfg, log, run_id, input_fn
        self.prompts = render_worker_prompts(cfg, run_dir)
        self.sessions: dict[str, str] = {}

    def start_all(self, orch=None):
        n = self.cfg.run.n_workers
        print(f"Start {n} worker sessions now, each with its own prompt file:")
        for wid, p in self.prompts.items():
            print(f"  {wid}: {p}")
        for i in range(1, n + 1):
            wid = f"w{i}"
            sid = self.input(f"session id for {wid} (Enter once it is running): ").strip() or None
            self.sessions[wid] = sid or ""
            self.log.emit("worker_start", worker=wid, session_id=sid)

    def stop_all(self, orch=None):
        print("WINDOW END: stop every worker session now. New claims and submissions are no longer "
              "accepted; reviews and merges continue through the grace period.")


class CommandLauncher:
    def __init__(self, cfg: Config, log: EventLog, run_id: str, run_dir: Path):
        self.cfg, self.log, self.run_id = cfg, log, run_id
        self.prompts = render_worker_prompts(cfg, run_dir)
        self.sessions: dict[str, str | None] = {}

    def _fmt(self, tpl: list[str], **kw) -> list[str]:
        return [c.format(**kw) for c in tpl]

    def start_all(self, orch=None):
        lc = self.cfg.launcher
        for i in range(1, self.cfg.run.n_workers + 1):
            wid = f"w{i}"
            cmd = self._fmt(lc.start_command, worker=wid, prompt_file=str(self.prompts[wid]),
                            remote_url=self.cfg.repo.remote_url, model=self.cfg.run.worker_model,
                            run_id=self.run_id)
            p = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
            if p.returncode != 0:
                self.log.emit("worker_down", worker=wid, reason=f"launch failed: {p.stderr.strip()[:300]}")
                continue
            m = re.search(lc.session_id_regex, p.stdout)
            sid = m.group("session_id") if m else None
            self.sessions[wid] = sid
            self.log.emit("worker_start", worker=wid, session_id=sid)

    def stop_all(self, orch=None):
        lc = self.cfg.launcher
        if not lc.stop_command:
            print("WINDOW END: no stop_command configured; stop the worker sessions by hand now.")
            return
        for wid, sid in self.sessions.items():
            cmd = self._fmt(lc.stop_command, worker=wid, session_id=sid or "")
            p = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
            if p.returncode != 0:
                self.log.note(f"stop of {wid} ({sid}) failed: {p.stderr.strip()[:300]}")
