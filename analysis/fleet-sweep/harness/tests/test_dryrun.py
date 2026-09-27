"""End-to-end accelerated dry run, and the hidden-test containment check."""
import subprocess
import time
from pathlib import Path

from harness import config as config_mod
from harness.dryrun import dry_run
from harness.events import read_events
from harness.schema import validate_events, validate_run_json

HERE = Path(__file__).resolve().parent.parent


def _all_blobs_and_paths(git_dir: Path):
    """Every (path, content) reachable from any ref in a repo."""
    out = subprocess.run(["git", "--git-dir", str(git_dir), "rev-list", "--all", "--objects"],
                         capture_output=True, text=True, check=True).stdout.splitlines()
    paths, blobs = set(), []
    for line in out:
        sha, _, path = line.partition(" ")
        if path:
            paths.add(path)
        typ = subprocess.run(["git", "--git-dir", str(git_dir), "cat-file", "-t", sha],
                             capture_output=True, text=True).stdout.strip()
        if typ == "blob":
            blobs.append(subprocess.run(["git", "--git-dir", str(git_dir), "cat-file", "blob", sha],
                                        capture_output=True).stdout)
    return paths, blobs


def test_end_to_end_dry_run(tmp_path):
    cfg = config_mod.load(HERE / "config.dryrun.toml")
    cfg.run.output_dir = str(tmp_path / "runs")
    cfg.run.window_min = 60
    cfg.sim.time_scale = 150
    cfg.run.n_workers = 4
    cfg.sim.n_toy_tasks = 20
    t0 = time.monotonic()
    run_dir, summary, errs = dry_run(cfg, "e2e", seed=42)
    took = time.monotonic() - t0
    assert errs == []
    assert took < 120, f"dry run took {took:.0f}s"
    assert validate_events(run_dir / "events.jsonl") == []
    assert validate_run_json(run_dir / "run.json") == []
    assert summary["worker_start"] == 4
    assert summary["claims"] >= 4 and summary["submits"] >= 4
    assert summary["reviews"] >= 3 and summary["merges"] >= 1
    evs = read_events(run_dir / "events.jsonl")
    assert evs[0]["type"] == "note"

    # Hidden tests never reach any branch: not on the remote, not in the orchestrator's clone.
    hidden_root = run_dir / "_sandbox" / "toy" / "tasks" / "hidden"
    hidden_files = list(hidden_root.rglob("*.py"))
    assert hidden_files
    hidden_contents = {p.read_bytes() for p in hidden_files}
    for git_dir in (run_dir / "_sandbox" / "toy" / "remote.git", run_dir / "_sandbox" / "work" / ".git"):
        paths, blobs = _all_blobs_and_paths(git_dir)
        assert not any("test_task_" in p or "_hidden_acceptance" in p for p in paths), git_dir
        assert not (hidden_contents & set(blobs)), git_dir
