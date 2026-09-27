"""Reset the sandbox remote before a window.

main := base_ref, every ``claude/*`` branch deleted (task branches, and any a session named itself), and
the window's seeded task order committed to main as TASKS.json (with ``[run] first_task``, T0's fixed task,
moved to the front). The orchestrator hands tasks to slots in this order.
"""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

from .clock import iso
from .config import Config
from .gitops import Repo, clone
from .tasks import load_tasks, seeded_order, tasks_file_content


def work_repo(cfg: Config) -> Repo:
    r = clone(cfg.repo.remote_url, cfg.path(cfg.repo.work_dir),
              (cfg.repo.git_user_name, cfg.repo.git_user_email))
    return r


def remote_heads(repo: Repo) -> dict[str, str]:
    """{branch name: sha} straight from the remote (not the local cache)."""
    out = repo.out("ls-remote", "--heads", "origin")
    res = {}
    for line in out.splitlines():
        sha, ref = line.split("\t")
        res[ref.removeprefix("refs/heads/")] = sha
    return res


def reset(cfg: Config, run_id: str, seed: int, run_dir: Path) -> dict:
    repo = work_repo(cfg)
    repo.run("fetch", "-q", "--prune", "--tags", "--force", "origin",
             "+refs/heads/*:refs/remotes/origin/*")
    base = repo.try_sha(f"refs/tags/{cfg.repo.base_ref}") or repo.try_sha(cfg.repo.base_ref) \
        or repo.try_sha(f"origin/{cfg.repo.base_ref}")
    if not base:
        raise SystemExit(f"reset: base_ref {cfg.repo.base_ref!r} not found in {cfg.repo.remote_url}")

    doomed = [b for b in remote_heads(repo) if b.startswith("claude/")]
    for i in range(0, len(doomed), 50):
        repo.run("push", "-q", "origin", "--delete", *doomed[i:i + 50])

    tasks = load_tasks(cfg)
    order = seeded_order(tasks, seed)
    first = cfg.run.first_task
    if first:
        if first not in tasks:
            raise SystemExit(f"reset: [run] first_task {first!r} is not in the task catalogue")
        order = [tasks[first]] + [t for t in order if t.id != first]
    content = tasks_file_content(order, seed, run_id)

    repo.run("checkout", "-q", "--detach", "--force", base)
    repo.run("clean", "-q", "-fdx")
    (repo.path / cfg.repo.tasks_file_name).write_text(content)
    repo.run("add", cfg.repo.tasks_file_name)
    repo.run("commit", "-q", "-m", f"harness: task order for {run_id} (seed {seed})")
    tasks_commit = repo.sha("HEAD")
    repo.run("push", "-q", "--force", "origin", "HEAD:refs/heads/main")
    repo.run("fetch", "-q", "--prune", "origin", "+refs/heads/*:refs/remotes/origin/*")

    left = [b for b in remote_heads(repo) if b.startswith("claude/")]
    if left or repo.sha("origin/main") != tasks_commit:
        raise SystemExit(f"reset did not take: leftover branches {left}")

    info = {
        "run_id": run_id, "seed": seed, "base_ref": cfg.repo.base_ref,
        "sandbox_commit": base, "tasks_commit": tasks_commit,
        "branches_deleted": len(doomed), "task_order": [t.id for t in order],
        "t": iso(dt.datetime.now(dt.timezone.utc)),
    }
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "reset.json").write_text(json.dumps(info, indent=2) + "\n")
    return info
