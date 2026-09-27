"""Task validation (PLAN-v3 s5): hidden tests fail on the base commit and pass
with the reference solution; visible tests pass with the reference solution.

Reference trees are built as loose git objects (temp index + commit-tree); no
ref is created and nothing is pushed.
"""
from __future__ import annotations

import os
import subprocess
import tempfile

from .config import Config
from .gitops import Repo
from .tasks import TestRunner, load_tasks


def reference_commit(repo: Repo, base: str, patch_path) -> str:
    with tempfile.TemporaryDirectory(prefix="fleet-idx-") as td:
        env = dict(repo.env, GIT_INDEX_FILE=os.path.join(td, "index"))
        def g(*a):
            return subprocess.run(["git", *a], cwd=repo.path, env=env, capture_output=True, text=True,
                                  check=True).stdout.strip()
        with repo.lock:
            g("read-tree", base)
            g("apply", "--cached", str(patch_path))
            tree = g("write-tree")
    return repo.commit_tree(tree, [base], "reference (validation only, never pushed)")


def validate_tasks(cfg: Config, repo: Repo, base: str) -> list[dict]:
    runner = TestRunner(cfg, repo)
    ref_dir = cfg.path(cfg.tasks.reference_dir)
    report = []
    for tid in sorted(load_tasks(cfg)):
        row = {"task": tid, "hidden_fails_on_base": None, "hidden_passes_with_reference": None,
               "visible_passes_with_reference": None, "ok": False, "problem": ""}
        try:
            _, h0 = runner.run(base, visible=False, hidden_tasks=[tid])
            row["hidden_fails_on_base"] = not h0.passed
            patch = ref_dir / f"{tid}.patch"
            if not patch.exists():
                row["problem"] = "no reference patch"
            else:
                ref = reference_commit(repo, base, patch)
                v1, h1 = runner.run(ref, visible=True, hidden_tasks=[tid])
                row["hidden_passes_with_reference"] = h1.passed
                row["visible_passes_with_reference"] = v1.passed
        except (FileNotFoundError, subprocess.CalledProcessError) as e:
            row["problem"] = str(e)[:300]
        row["ok"] = bool(row["hidden_fails_on_base"] and row["hidden_passes_with_reference"]
                         and row["visible_passes_with_reference"])
        report.append(row)
    return report
