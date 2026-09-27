import json
import random
import sys
import threading
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from harness import config as config_mod  # noqa: E402
from harness.clock import Clock  # noqa: E402
from harness.dryrun import prepare_toy_config  # noqa: E402
from harness.events import EventLog, read_events  # noqa: E402
from harness.orchestrator import Orchestrator  # noqa: E402
from harness.reset import reset, work_repo  # noqa: E402
from harness.review import ReviewError, ReviewResult, Reviewer  # noqa: E402
from harness.sim import Oracle, SimWorker  # noqa: E402
from harness.tasks import load_tasks  # noqa: E402

HARNESS_DIR = Path(__file__).resolve().parent.parent


class ScriptedReviewer(Reviewer):
    """verdicts: {task: [verdict, ...]} consumed in order; default approve.
    'error' raises. Records the packets it saw."""

    def __init__(self, verdicts=None, delay_s=0.0):
        self.verdicts = {k: list(v) for k, v in (verdicts or {}).items()}
        self.delay_s = delay_s
        self.packets = []
        self.lock = threading.Lock()
        self.active = 0
        self.max_active = 0

    def review(self, packet):
        with self.lock:
            self.active += 1
            self.max_active = max(self.max_active, self.active)
        try:
            self.packets.append(packet)
            time.sleep(self.delay_s)
            q = self.verdicts.get(packet.task.id)
            v = q.pop(0) if q else "approve"
            if v == "error":
                raise ReviewError("scripted failure")
            return ReviewResult(v, f"scripted {v}", 10, 5)
        finally:
            with self.lock:
                self.active -= 1


class Env:
    def __init__(self, tmp: Path, reviewer=None, n_tasks=10, scale=600.0, reviewer_cfg=None, sim_cfg=None):
        base = config_mod.from_dict({
            "run": {"kind": "dry-run", "n_workers": 2, "output_dir": str(tmp / "runs"),
                    "poll_interval_s": 5, "window_min": 90, "warmup_min": 10, "grace_min": 10,
                    "worker_model": "sim-worker", "reviewer_model": "sim-reviewer"},
            "tests": {"timeout_s": 60},
            "reviewer": {"mode": "sim", "retry_backoff_s": 5, **(reviewer_cfg or {})},
            "launcher": {"mode": "sim"},
            "sim": {"time_scale": scale, "n_toy_tasks": n_tasks, **(sim_cfg or {})},
        }, HARNESS_DIR)
        self.cfg = prepare_toy_config(base, tmp / "sandbox")
        self.run_id = "t"
        self.run_dir = tmp / "runs" / self.run_id
        self.info = reset(self.cfg, self.run_id, 1234, self.run_dir)
        self.clock = Clock(scale)
        self.log = EventLog(self.run_dir / "events.jsonl", self.clock)
        self.repo = work_repo(self.cfg)
        self.oracle = Oracle()
        self.reviewer = reviewer or ScriptedReviewer()
        self.orch = Orchestrator(self.cfg, self.run_id, self.run_dir, self.clock, self.log, self.repo,
                                 load_tasks(self.cfg), self.reviewer,
                                 sandbox_commit=self.info["sandbox_commit"], seed=1234)
        self.stop = threading.Event()
        self.tmp = tmp

    def worker(self, wid):
        return SimWorker(wid, self.cfg, self.clock, self.oracle, self.tmp / "workers" / wid,
                         random.Random(wid), self.stop)

    def start(self):
        self.orch.phase = "window"
        self.orch.window_start = self.clock.now()
        self.orch.start_threads()

    def events(self, type_=None):
        evs = read_events(self.log.path) if self.log.path.exists() else []
        return [e for e in evs if type_ is None or e["type"] == type_]

    def wait_for(self, pred, timeout=30.0):
        t0 = time.monotonic()
        while time.monotonic() - t0 < timeout:
            evs = self.events()
            if pred(evs):
                return evs
            if self.orch.errors:
                raise AssertionError(self.orch.errors)
            time.sleep(0.05)
        raise AssertionError(f"timed out; events: {json.dumps(self.events()[-15:], indent=1)}")

    def close(self):
        self.stop.set()
        self.orch.shutdown()


@pytest.fixture
def make_env(tmp_path):
    envs = []

    def make(**kw):
        e = Env(tmp_path / f"e{len(envs)}", **kw)
        envs.append(e)
        return e
    yield make
    for e in envs:
        e.close()


def count(evs, type_, **match):
    return sum(1 for e in evs if e["type"] == type_ and all(e.get(k) == v for k, v in match.items()))
