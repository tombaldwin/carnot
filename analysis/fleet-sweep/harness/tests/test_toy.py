"""The generated toy tasks satisfy PLAN-v3's validation rule, and reset does what it says."""
import json

from harness.reset import remote_heads
from harness.validate import validate_tasks


def test_toy_tasks_validate(make_env):
    env = make_env(n_tasks=12)
    rows = validate_tasks(env.cfg, env.repo, env.info["sandbox_commit"])
    assert len(rows) == 12
    assert all(r["ok"] for r in rows), [r for r in rows if not r["ok"]]


def test_reset_first_task_goes_first(make_env):
    import dataclasses as dc
    from harness.reset import reset
    env = make_env()
    cfg = dc.replace(env.cfg, run=dc.replace(env.cfg.run, first_task="007"))
    info = reset(cfg, "t0", 5, env.run_dir.parent / "t0")
    assert info["task_order"][0] == "007" and sorted(info["task_order"]) == sorted(env.orch.tasks)


def test_reset_restores_main_and_deletes_task_branches(make_env):
    from harness.reset import reset
    env = make_env()
    s = env.session("001", "s1")
    s.submit()
    s.submit(branch="claude/some-name", message="READY: 001")
    assert any(b.startswith("claude/task-") for b in remote_heads(env.repo))
    info = reset(env.cfg, "again", 99, env.run_dir.parent / "again")
    heads = remote_heads(env.repo)
    assert not any(b.startswith("claude/") for b in heads)
    assert heads["main"] == info["tasks_commit"]
    env.repo.run("fetch", "-q", "origin")
    tasks = json.loads(env.repo.out("show", "origin/main:TASKS.json"))
    assert tasks["seed"] == 99 and [t["id"] for t in tasks["tasks"]] == info["task_order"]
    assert set(tasks["tasks"][0]) == {"id", "title", "text", "acceptance"}   # nothing hidden
    parent = env.repo.out("rev-parse", "origin/main^")
    assert parent == info["sandbox_commit"]
