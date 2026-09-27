"""Simulated workers and reviewer for dry runs. No model is ever called.

SimWorker follows the git-only worker protocol of PLAN-v3 s5 (the same one
prompts/worker.md gives the real workers) against a local bare repo:

  claim     push a new branch claude/task-<id> holding one empty commit
            "CLAIM: task-<id>" with a "Worker: <id>" trailer; if the push is
            rejected, (optionally) push a race marker claude/race-<id>-<worker>
            and take the next task
  submit    push a commit whose message starts "READY:"; do not wait
  rework    between tasks, fetch and look at every own branch whose tip is a
            "FEEDBACK:" commit; rebuild the change on current main and push a
            new "READY:" commit

The change itself is the task's reference patch (correct) or a synthetic note
file (wrong: hidden tests fail because the feature is missing), optionally plus a
write to a shared hotspot file (textual conflict with other such changes), a
shared-constant edit (semantic: breaks other tasks after rebase) or a visible
test break. A shared oracle records what each head really is, so SimReviewer
can play a reviewer who catches some defects and misses others.
"""
from __future__ import annotations

import json
import math
import random
import subprocess
import threading
from pathlib import Path

from .clock import Clock
from .config import Config
from .events import EventLog
from .gitops import GitError, Repo, clone
from .review import ReviewError, ReviewPacket, ReviewResult, Reviewer, parse_verdict

HOTSPOT = "SIM_HOTSPOT.txt"


class PatchConflict(RuntimeError):
    """The task's reference patch does not apply to the current main (sim only)."""


class Oracle:
    """head sha -> ground truth about the change (sim only)."""

    def __init__(self):
        self._d: dict[str, dict] = {}
        self._lock = threading.Lock()

    def put(self, head: str, **truth) -> None:
        with self._lock:
            self._d[head] = truth

    def get(self, head: str) -> dict:
        with self._lock:
            return dict(self._d.get(head, {}))


class SimWorker:
    def __init__(self, worker_id: str, cfg: Config, clock: Clock, oracle: Oracle, clone_dir: Path,
                 rng: random.Random, stop: threading.Event):
        self.id = worker_id
        self.cfg, self.sim = cfg, cfg.sim
        self.clock, self.oracle, self.rng, self.stop = clock, oracle, rng, stop
        self.repo: Repo = clone(cfg.repo.remote_url, clone_dir, (worker_id, f"{worker_id}@sim.local"))
        self.ref_dir = cfg.path(cfg.tasks.reference_dir)
        self.pfx = cfg.repo.branch_prefix
        self.mine: list[str] = []          # task ids this worker claimed
        self.done: set[str] = set()        # tasks whose branch tip we saw merged (not tracked; kept open)
        self.stats = {"claims": 0, "races": 0, "submits": 0, "reworks": 0, "git_errors": 0,
                      "apply_3way": 0, "union_resolved": 0, "unappliable": 0, "rework_capped": 0}
        self.rework_count: dict[str, int] = {}
        self.unappliable: list[dict] = []   # {task, stage, main, error}: patch would not apply
        self.given_up: set[str] = set()     # tasks this worker abandoned because of that
        self.error: str | None = None

    # -- git helpers
    def fetch(self):
        self.repo.run("fetch", "-q", "--prune", "origin", "+refs/heads/*:refs/remotes/origin/*")

    def claimed(self) -> set[str]:
        out = self.repo.out("ls-remote", "--heads", "origin", f"{self.pfx}*")
        return {l.split("\t")[1].removeprefix("refs/heads/" + self.pfx) for l in out.splitlines() if l}

    def task_order(self) -> list[dict]:
        txt = self.repo.out("show", f"origin/main:{self.cfg.repo.tasks_file_name}")
        return json.loads(txt)["tasks"]

    def _sleep(self, virtual_s: float) -> bool:
        return self.clock.sleep(virtual_s, self.stop)

    def work_time(self, factor: float = 1.0) -> float:
        return self.rng.expovariate(self.sim.rate_per_hour / 3600.0) * factor

    # -- protocol
    def run(self):
        try:
            self._run()
        except Exception:
            import traceback
            self.error = traceback.format_exc()
            raise

    def _run(self):
        while not self.stop.is_set():
            try:
                if self.rework_one():
                    continue
                tid = self.claim_next()
                if tid is None:
                    if self._sleep(60):
                        return
                    continue
                if self._sleep(self.work_time()):
                    return
                self.submit_first(tid)
            except GitError as e:
                self.stats["git_errors"] += 1
                self.last_git_error = str(e)
                if self.stop.is_set():
                    return
                if self._sleep(30):
                    return

    def claim_next(self) -> str | None:
        self.fetch()
        taken = self.claimed()
        for t in self.task_order():
            tid = t["id"]
            if tid in taken:
                continue
            if self.try_claim(tid):
                return tid
            taken.add(tid)
        return None

    def try_claim(self, tid: str) -> bool:
        main = self.repo.sha("origin/main")
        c = self.repo.commit_tree(self.repo.out("rev-parse", main + "^{tree}"), [main],
                                  f"CLAIM: task-{tid}\n\nWorker: {self.id}")
        p = self.repo.run("push", "-q", "origin", f"{c}:refs/heads/{self.pfx}{tid}", check=False)
        if p.returncode == 0:
            self.mine.append(tid)
            self.stats["claims"] += 1
            return True
        self.stats["races"] += 1
        if self.sim.race_markers:
            self.repo.run("push", "-q", "origin",
                          f"{c}:refs/heads/{self.cfg.repo.race_prefix}{tid}-{self.id}", check=False)
        return False

    def _checkout(self, sha: str):
        self.repo.run("checkout", "-q", "--detach", "--force", sha)
        self.repo.run("clean", "-q", "-fdx")

    def _edit(self, rel: str, find: str, replace: str) -> bool:
        p = self.repo.path / rel
        if not p.exists():
            return False
        s = p.read_text()
        if find not in s:
            return False
        p.write_text(s.replace(find, replace, 1))
        return True

    def _apply_change(self, tid: str, wrong: bool, conflict: bool, semantic: bool, vbreak: bool) -> dict:
        truth = {"wrong": wrong, "conflict": False, "semantic": False, "visible_break": False}
        patch = self.ref_dir / f"{tid}.patch"
        if not wrong and patch.exists():
            self._apply_patch(tid, patch)
        else:
            truth["wrong"] = True
            notes = self.repo.path / "sim_notes"
            notes.mkdir(exist_ok=True)
            (notes / f"task-{tid}.md").write_text(f"Work in progress for task {tid} by {self.id}.\n")
        if conflict:
            (self.repo.path / HOTSPOT).write_text(f"last touched by task {tid} ({self.id})\n")
            truth["conflict"] = True
        se = self.sim_edit("semantic_edit")
        if semantic and se:
            truth["semantic"] = self._edit(se["file"], se["find"], se["replace"])
        ve = self.sim_edit("visible_break_edit")
        if vbreak and ve:
            truth["visible_break"] = self._edit(ve["file"], ve["find"], ve["replace"])
        self.repo.run("add", "-A")
        return truth

    def _apply_patch(self, tid: str, patch: Path) -> None:
        """Apply the reference patch to the checked-out tree. Real-task patches are cut
        against the frozen base, so on a main that already carries other tasks a plain
        apply can fail; fall back to a three-way apply (base blobs are in the repo). If
        that also fails, the patch cannot be applied without a human-style resolution:
        raise PatchConflict so the caller can record it."""
        p = self.repo.run("apply", "--whitespace=nowarn", str(patch), check=False)
        if p.returncode == 0:
            return
        self.repo.run("checkout", "-q", "--force", "HEAD")
        self.repo.run("clean", "-q", "-fdx")
        p = self.repo.run("apply", "-3", "--whitespace=nowarn", str(patch), check=False)
        if p.returncode == 0:
            self.stats["apply_3way"] += 1
            return
        if self.sim.union_resolve and self._union_resolve():
            self.stats["union_resolved"] += 1
            return
        self.repo.run("reset", "-q", "--hard")
        self.repo.run("clean", "-q", "-fdx")
        raise PatchConflict(f"task {tid}: reference patch does not apply to {self.repo.sha('HEAD')[:12]}: "
                            f"{p.stderr.strip()[-300:]}")

    def _union_resolve(self) -> bool:
        """Resolve the conflict markers a three-way apply left by keeping both sides (main's
        first, then the task's), as a worker would for two additions at the same place (a
        registry entry, a CLI sub-command). Returns False if there is nothing to resolve."""
        files = self.repo.out("diff", "--name-only", "--diff-filter=U").split()
        if not files:
            return False
        for rel in files:
            fp = self.repo.path / rel
            out, state = [], "text"
            for line in fp.read_text().splitlines(keepends=True):
                if line.startswith("<<<<<<<"):
                    state = "ours"
                elif line.startswith("|||||||") and state == "ours":
                    state = "base"
                elif line.startswith("=======") and state in ("ours", "base"):
                    state = "theirs"
                elif line.startswith(">>>>>>>") and state == "theirs":
                    state = "text"
                elif state != "base":
                    out.append(line)
            fp.write_text("".join(out))
            self.repo.run("add", "--", rel)
        return True

    def sim_edit(self, name: str) -> dict | None:
        return getattr(self.sim, name, None) or None

    def submit_first(self, tid: str):
        r = self.rng.random
        try:
            self.submit(tid, wrong=r() < self.sim.p_wrong, conflict=r() < self.sim.p_conflict,
                        semantic=r() < self.sim.p_semantic, vbreak=r() < self.sim.p_visible_break)
        except PatchConflict as e:
            self._give_up(tid, "first", str(e))

    def _give_up(self, tid: str, stage: str, err: str) -> None:
        self.stats["unappliable"] += 1
        self.unappliable.append({"task": tid, "stage": stage, "error": err})
        self.given_up.add(tid)

    def submit(self, tid: str, wrong=False, conflict=False, semantic=False, vbreak=False) -> str | None:
        self.fetch()
        tip = self.repo.sha(f"origin/{self.pfx}{tid}")
        self._checkout(tip)
        truth = self._apply_change(tid, wrong=wrong, conflict=conflict, semantic=semantic, vbreak=vbreak)
        tree = self.repo.out("write-tree")
        c = self.repo.commit_tree(tree, [tip], f"READY: task {tid}\n\nWorker: {self.id}")
        return self._push_ready(tid, c, truth)

    def _push_ready(self, tid: str, c: str, truth: dict) -> str | None:
        if self.stop.is_set():
            return None
        p = self.repo.run("push", "-q", "origin", f"{c}:refs/heads/{self.pfx}{tid}", check=False)
        if p.returncode == 0:
            self.oracle.put(c, task=tid, worker=self.id, **truth)
            self.stats["submits"] += 1
            return c
        return None

    def rework_one(self) -> bool:
        """Fix one bounced branch if there is one. Returns True if it did."""
        self.fetch()
        for tid in list(self.mine):
            if tid in self.given_up:
                continue
            ref = f"origin/{self.pfx}{tid}"
            tip = self.repo.try_sha(ref)
            if not tip or not self.repo.message(tip).startswith("FEEDBACK:"):
                continue
            if self.sim.max_reworks and self.rework_count.get(tid, 0) >= self.sim.max_reworks:
                self.stats["rework_capped"] += 1
                self.given_up.add(tid)
                continue
            self.rework_count[tid] = self.rework_count.get(tid, 0) + 1
            if self._sleep(self.work_time(self.sim.rework_factor)):
                return True
            self.fetch()
            tip = self.repo.sha(ref)
            if not self.repo.message(tip).startswith("FEEDBACK:"):
                return True
            main = self.repo.sha("origin/main")
            self._checkout(main)
            # Undo a shared-constant edit that reached main (the later worker repairs the interaction).
            se = self.sim_edit("semantic_edit")
            if se:
                self._edit(se["file"], se["replace"], se["find"])
            wrong = self.rng.random() < self.sim.p_wrong * self.sim.p_wrong_rework_factor
            try:
                truth = self._apply_change(tid, wrong=wrong, conflict=False, semantic=False, vbreak=False)
            except PatchConflict as e:
                self._give_up(tid, "rework", str(e))
                return True
            tree = self.repo.out("write-tree")
            c = self.repo.commit_tree(tree, [tip, main], f"READY: task {tid} (rework)\n\nWorker: {self.id}")
            self._push_ready(tid, c, truth)
            self.stats["reworks"] += 1
            return True
        return False


class SimReviewer(Reviewer):
    """Approves or requests changes at configured rates, with lognormal service
    time. It can see the oracle and (through ``depth_probe``) the queue depth,
    which the real reviewer never can: it exists to give the analysis's
    escaped-defect test something to find."""

    def __init__(self, cfg: Config, clock: Clock, oracle: Oracle, rng: random.Random,
                 depth_probe=lambda: 0):
        self.sim, self.clock, self.oracle, self.rng = cfg.sim, clock, oracle, rng
        self.depth_probe = depth_probe
        self.stop = threading.Event()

    def _service_time(self) -> float:
        cv = self.sim.review_cv
        sigma = math.sqrt(math.log(1 + cv * cv))
        mu = math.log(self.sim.review_mean_s) - sigma * sigma / 2
        return self.rng.lognormvariate(mu, sigma)

    def review(self, packet: ReviewPacket) -> ReviewResult:
        s = self.sim
        t = self._service_time()
        if self.rng.random() < s.p_review_crash:
            self.clock.sleep(t * self.rng.random(), self.stop)
            raise ReviewError("simulated reviewer crash")
        self.clock.sleep(t, self.stop)
        truth = self.oracle.get(packet.head)
        r = self.rng.random()
        if not packet.visible_passed:
            ok = r >= s.p_catch_visible_fail
            why = "The visible tests fail on this change."
        elif truth.get("wrong"):
            p = max(s.p_catch_wrong_min, s.p_catch_wrong - s.catch_drop_per_depth * self.depth_probe())
            ok = r >= p
            why = "The change does not implement what the task asks."
        else:
            ok = r >= s.p_false_reject
            why = "Please add a docstring and tighten the edge-case handling."
        text = "APPROVE\nMeets the acceptance criteria." if ok else f"REQUEST_CHANGES\n{why}"
        verdict, reason = parse_verdict(text)
        return ReviewResult(verdict, reason, tokens_in=len(packet.diff) // 4 + 800,
                            tokens_out=self.rng.randint(80, 400))


class SimLauncher:
    def __init__(self, cfg: Config, clock: Clock, log: EventLog, oracle: Oracle, clones_dir: Path,
                 run_id: str, seed: int):
        self.cfg, self.clock, self.log, self.oracle = cfg, clock, log, oracle
        self.clones_dir, self.run_id, self.seed = clones_dir, run_id, seed
        self.stop = threading.Event()
        self.workers: list[SimWorker] = []
        self.threads: list[threading.Thread] = []

    def start_all(self, orch=None):
        for i in range(1, self.cfg.run.n_workers + 1):
            wid = f"w{i}"
            w = SimWorker(wid, self.cfg, self.clock, self.oracle, self.clones_dir / wid,
                          random.Random(f"{self.seed}-{wid}"), self.stop)
            self.workers.append(w)
            t = threading.Thread(target=w.run, name=f"sim-{wid}", daemon=True)
            t.start()
            self.threads.append(t)
            self.log.emit("worker_start", worker=wid, session_id=f"sim-{self.run_id}-{wid}")

    def stop_all(self, orch=None):
        self.stop.set()
        for t in self.threads:
            t.join(timeout=30)
