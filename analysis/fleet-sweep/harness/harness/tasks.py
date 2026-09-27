"""Task catalogue, seeded window order, and test execution in temp checkouts.

Hidden tests live only on the local machine. They are copied into a throwaway
directory exported from a commit (``git archive``), run there, and deleted.
Nothing here ever stages, commits or pushes them.
"""
from __future__ import annotations

import dataclasses as dc
import json
import os
import random
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from .config import Config
from .gitops import Repo


@dc.dataclass
class Task:
    id: str
    title: str
    text: str
    acceptance: list[str]

    def public(self) -> dict:
        return {"id": self.id, "title": self.title, "text": self.text, "acceptance": self.acceptance}


def load_tasks(cfg: Config) -> dict[str, Task]:
    data = json.loads(cfg.path(cfg.tasks.task_file).read_text())
    items = data["tasks"] if isinstance(data, dict) else data
    out = {}
    for t in items:
        tid = str(t["id"])
        out[tid] = Task(tid, t.get("title", ""), t["text"], list(t.get("acceptance", [])))
    return out


def seeded_order(tasks: dict[str, Task], seed: int) -> list[Task]:
    ids = sorted(tasks)
    random.Random(seed).shuffle(ids)
    return [tasks[i] for i in ids]


def tasks_file_content(order: list[Task], seed: int, run_id: str) -> str:
    """What reset commits to main. Public task text only, never tests or solutions."""
    return json.dumps({"run_id": run_id, "seed": seed, "tasks": [t.public() for t in order]},
                      indent=2) + "\n"


@dc.dataclass
class TestResult:
    passed: bool
    output: str
    duration_s: float


class TestRunner:
    def __init__(self, cfg: Config, repo: Repo):
        self.cfg = cfg
        self.repo = repo
        self.hidden_root = cfg.path(cfg.tasks.hidden_tests_dir)
        self.python = cfg.tests.python or sys.executable

    def hidden_dir(self, task_id: str) -> Path:
        return self.hidden_root / task_id

    def _run(self, cmd_tpl: list[str], cwd: Path, env: dict | None = None, **fmt) -> TestResult:
        cmd = [c.format(python=self.python, **fmt) for c in cmd_tpl]
        t0 = time.monotonic()
        try:
            p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, env=env,
                               timeout=self.cfg.tests.timeout_s)
            ok, out = p.returncode == 0, (p.stdout + p.stderr)
        except subprocess.TimeoutExpired as e:
            ok, out = False, f"TIMEOUT after {self.cfg.tests.timeout_s}s\n{e.stdout or ''}"
        return TestResult(ok, out, time.monotonic() - t0)

    def _hidden_env(self, root: Path) -> dict | None:
        extra = [str(root / d) for d in self.cfg.tests.hidden_pythonpath]
        if not extra:
            return None
        env = dict(os.environ)
        env["PYTHONPATH"] = os.pathsep.join(extra + ([env["PYTHONPATH"]] if env.get("PYTHONPATH") else []))
        return env

    def run(self, sha: str, visible: bool, hidden_tasks: list[str],
            timing: dict | None = None) -> tuple[TestResult | None, TestResult | None]:
        """Export ``sha`` to a temp dir; run visible tests and/or the hidden tests
        of ``hidden_tasks`` there. Returns (visible, hidden); None where not run.
        If ``timing`` is given, real seconds for export / visible / hidden are added to it."""
        timing = {} if timing is None else timing
        with tempfile.TemporaryDirectory(prefix="fleet-tests-") as td:
            root = Path(td) / "co"
            t0 = time.monotonic()
            self.repo.export(sha, root)
            timing["export_s"] = timing.get("export_s", 0.0) + time.monotonic() - t0
            vis = self._run(self.cfg.tests.visible_cmd, root) if visible else None
            if vis is not None:
                timing["visible_s"] = vis.duration_s
            hid = None
            if hidden_tasks:
                hd = root / self.cfg.tests.hidden_subdir
                hd.mkdir(parents=True)
                for tid in hidden_tasks:
                    src = self.hidden_dir(tid)
                    if not src.is_dir():
                        raise FileNotFoundError(f"no hidden tests for task {tid} at {src}")
                    shutil.copytree(src, hd / f"task_{tid}")
                hid = self._run(self.cfg.tests.hidden_cmd, root, env=self._hidden_env(root),
                                hidden_dir=str(hd))
                timing["hidden_s"] = hid.duration_s
                timing["n_hidden_tasks"] = len(hidden_tasks)
            return vis, hid
