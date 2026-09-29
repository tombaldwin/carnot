"""Simulated sessions and reviewer for dry runs. No model is ever called.

One SimSession per task. SimCloudLauncher starts them for the orchestrator's slots, against a local bare repo.
With ``[launcher] session_per = "slot"`` a simulated cloud session holds several SimSessions: its launch starts
the first task's, and a ``task`` follow-up starts the next task's in the same session (start-up
``followup_startup_mean_s``: no provisioning); rework goes to the SimSession of the task it names.

  launch    after a start-up delay (exp., mean startup_mean_s: provisioning and clone), push the branch
            claude/task-<id> at origin/main (the prompt's step 1), then work (exp. with rate
            rate_per_hour) and push one commit whose message starts "READY: <id>"; then wait
  message   a follow-up (rework) wakes the session: after rework_factor of a fresh task's time it merges
            origin/main into its branch, rebuilds the change, and pushes a new "READY: <id>" commit;
            a probe (T0) makes it push an empty "PROBE: <id>" commit
  stall     with p_stall the session never pushes READY, so the orchestrator's session timeout fires;
            the same happens when the reference patch cannot be applied or max_reworks is reached

The change itself is the task's reference patch (correct) or a synthetic note file (wrong: hidden tests
fail because the feature is missing), optionally plus a write to a shared hotspot file (textual conflict
with other such changes), a shared-constant edit (semantic: breaks other tasks after rebase) or a visible
test break. A shared oracle records what each head really is, so SimReviewer can play a reviewer who
catches some defects and misses others.
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
from .launchers import LaunchError, LaunchResult
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


class SimSession:
    """One task's session. Git work happens in the clone of the slot it currently runs on, under that
    slot's lock, so a session that timed out cannot interleave with the slot's next session."""

    def __init__(self, task_id: str, session_id: str, cfg: Config, clock: Clock, oracle: Oracle,
                 rng: random.Random, stop: threading.Event | None = None, startup_mean_s: float | None = None):
        self.task, self.session_id = task_id, session_id
        self.cfg, self.sim = cfg, cfg.sim
        self.clock, self.oracle, self.rng = clock, oracle, rng
        self.halt = stop or threading.Event()
        self.ref_dir = cfg.path(cfg.tasks.reference_dir)
        self.branch = f"{cfg.repo.branch_prefix}{task_id}"
        self.repo: Repo | None = None
        self.slot_lock: threading.Lock = threading.Lock()
        self.stats = {"submits": 0, "reworks": 0, "git_errors": 0, "apply_3way": 0, "union_resolved": 0,
                      "unappliable": 0, "rework_capped": 0, "stalled": 0, "probes": 0}
        self.reworks = 0
        self.unappliable: list[dict] = []
        self.given_up = False
        self.error: str | None = None
        self.startup_mean_s = self.sim.startup_mean_s if startup_mean_s is None else startup_mean_s

    def bind(self, repo: Repo, slot_lock: threading.Lock) -> "SimSession":
        self.repo, self.slot_lock = repo, slot_lock
        return self

    # -- git helpers
    def fetch(self):
        self.repo.run("fetch", "-q", "--prune", "origin", "+refs/heads/*:refs/remotes/origin/*")

    def _sleep(self, virtual_s: float) -> bool:
        return self.clock.sleep(virtual_s, self.halt)

    def work_time(self, factor: float = 1.0) -> float:
        return self.rng.expovariate(self.sim.rate_per_hour / 3600.0) * factor

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

    def sim_edit(self, name: str) -> dict | None:
        return getattr(self.sim, name, None) or None

    # -- the session's life (thread bodies)
    def _guard(self, fn, *a):
        try:
            fn(*a)
        except GitError as e:
            self.stats["git_errors"] += 1
            self.error = None if self.halt.is_set() else f"git: {e}"
        except Exception:
            import traceback
            self.error = traceback.format_exc()

    def run_first(self):
        self._guard(self._first)

    def _first(self):
        if self._sleep(self.rng.expovariate(1.0 / max(self.startup_mean_s, 1e-9))):
            return
        stall = self.rng.random() < self.sim.p_stall
        with self.slot_lock:
            self.start_branch()
        if stall:
            self.stats["stalled"] += 1
            return
        if self._sleep(self.work_time()):
            return
        r = self.rng.random
        wrong, conflict = r() < self.sim.p_wrong, r() < self.sim.p_conflict
        semantic, vbreak = r() < self.sim.p_semantic, r() < self.sim.p_visible_break
        try:
            with self.slot_lock:
                self.submit(wrong=wrong, conflict=conflict, semantic=semantic, vbreak=vbreak)
        except PatchConflict as e:
            self._give_up("first", str(e))

    def run_message(self, kind: str):
        self._guard(self._message, kind)

    def _message(self, kind: str):
        if kind == "probe":
            if self._sleep(30):
                return
            with self.slot_lock:
                self.probe()
            return
        if self.given_up:
            return
        if self.sim.max_reworks and self.reworks >= self.sim.max_reworks:
            self.stats["rework_capped"] += 1
            self.given_up = True
            return
        self.reworks += 1
        if self._sleep(self.work_time(self.sim.rework_factor)):
            return
        wrong = self.rng.random() < self.sim.p_wrong * self.sim.p_wrong_rework_factor
        try:
            with self.slot_lock:
                self.rework(wrong=wrong)
        except PatchConflict as e:
            self._give_up("rework", str(e))

    def _give_up(self, stage: str, err: str) -> None:
        self.stats["unappliable"] += 1
        self.unappliable.append({"task": self.task, "stage": stage, "error": err})
        self.given_up = True

    # -- git steps (also called directly by the tests)
    def start_branch(self) -> str:
        """The prompt's step 1: create claude/task-<id> from origin/main and push it."""
        self.fetch()
        main = self.repo.sha("origin/main")
        self.repo.run("push", "-q", "origin", f"{main}:refs/heads/{self.branch}")
        return main

    def submit(self, wrong=False, conflict=False, semantic=False, vbreak=False, branch: str | None = None,
               message: str | None = None) -> str | None:
        self.fetch()
        br = branch or self.branch
        tip = self.repo.try_sha(f"origin/{br}") or self.repo.sha("origin/main")
        self._checkout(tip)
        truth = self._apply_change(wrong=wrong, conflict=conflict, semantic=semantic, vbreak=vbreak)
        tree = self.repo.out("write-tree")
        c = self.repo.commit_tree(tree, [tip], message or f"READY: {self.task}\n\nSession: {self.session_id}")
        return self._push_ready(c, truth, br)

    def rework(self, wrong=False) -> str | None:
        """Merge origin/main into the branch and rebuild the change on it (a merge commit, READY:)."""
        self.fetch()
        tip = self.repo.sha(f"origin/{self.branch}")
        main = self.repo.sha("origin/main")
        self._checkout(main)
        # Undo a shared-constant edit that reached main (the later session repairs the interaction).
        se = self.sim_edit("semantic_edit")
        if se:
            self._edit(se["file"], se["replace"], se["find"])
        truth = self._apply_change(wrong=wrong, conflict=False, semantic=False, vbreak=False)
        tree = self.repo.out("write-tree")
        c = self.repo.commit_tree(tree, [tip, main], f"READY: {self.task} (rework)\n\nSession: {self.session_id}")
        head = self._push_ready(c, truth, self.branch)
        if head:
            self.stats["reworks"] += 1
        return head

    def probe(self) -> str | None:
        self.fetch()
        tip = self.repo.sha(f"origin/{self.branch}")
        c = self.repo.commit_tree(self.repo.out("rev-parse", tip + "^{tree}"), [tip], f"PROBE: {self.task}")
        p = self.repo.run("push", "-q", "origin", f"{c}:refs/heads/{self.branch}", check=False)
        if p.returncode == 0:
            self.stats["probes"] += 1
            return c
        return None

    def _push_ready(self, c: str, truth: dict, branch: str) -> str | None:
        if self.halt.is_set():
            return None
        p = self.repo.run("push", "-q", "origin", f"{c}:refs/heads/{branch}", check=False)
        if p.returncode == 0:
            self.oracle.put(c, task=self.task, session=self.session_id, **truth)
            self.stats["submits"] += 1
            return c
        return None

    def _apply_change(self, wrong: bool, conflict: bool, semantic: bool, vbreak: bool) -> dict:
        tid = self.task
        truth = {"wrong": wrong, "conflict": False, "semantic": False, "visible_break": False}
        patch = self.ref_dir / f"{tid}.patch"
        if not wrong and patch.exists():
            self._apply_patch(patch)
        else:
            truth["wrong"] = True
            notes = self.repo.path / "sim_notes"
            notes.mkdir(exist_ok=True)
            (notes / f"task-{tid}.md").write_text(f"Work in progress for task {tid} ({self.session_id}).\n")
        if conflict:
            (self.repo.path / HOTSPOT).write_text(f"last touched by task {tid} ({self.session_id})\n")
            truth["conflict"] = True
        se = self.sim_edit("semantic_edit")
        if semantic and se:
            truth["semantic"] = self._edit(se["file"], se["find"], se["replace"])
        ve = self.sim_edit("visible_break_edit")
        if vbreak and ve:
            truth["visible_break"] = self._edit(ve["file"], ve["find"], ve["replace"])
        self.repo.run("add", "-A")
        return truth

    def _apply_patch(self, patch: Path) -> None:
        """Apply the reference patch to the checked-out tree. Real-task patches are cut against the frozen
        base, so on a main that already carries other tasks a plain apply can fail; fall back to a three-way
        apply (base blobs are in the repo). If that also fails, the patch cannot be applied without a
        human-style resolution: raise PatchConflict so the caller can record it."""
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
        raise PatchConflict(f"task {self.task}: reference patch does not apply to {self.repo.sha('HEAD')[:12]}: "
                            f"{p.stderr.strip()[-300:]}")

    def _union_resolve(self) -> bool:
        """Resolve the conflict markers a three-way apply left by keeping both sides (main's first, then the
        task's), as a worker would for two additions at the same place (a registry entry, a CLI sub-command).
        Returns False if there is nothing to resolve."""
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


class SimCloudLauncher:
    """Stands in for `claude --cloud` (launch) and `claude -p ... --cloud <id>` (follow-up): each launch
    starts a SimSession thread and returns at once with a session id; a follow-up wakes that session. One
    clone of the bare remote per slot, reused by whichever session runs there."""

    accepts_no_session_id = False

    def __init__(self, cfg: Config, clock: Clock, log: EventLog, oracle: Oracle, clones_dir: Path,
                 run_id: str, seed: int):
        self.cfg, self.clock, self.log, self.oracle = cfg, clock, log, oracle
        self.clones_dir, self.run_id, self.seed = clones_dir, run_id, seed
        self.stop_ev = threading.Event()
        self.sessions: dict[str, SimSession] = {}          # task -> its SimSession
        self.by_id: dict[str, SimSession] = {}             # cloud session id -> its first task's SimSession
        self.groups: dict[str, dict[str, SimSession]] = {} # cloud session id -> task -> SimSession
        self.threads: list[threading.Thread] = []
        self._repos: dict[str, tuple[Repo, threading.Lock]] = {}
        self._lock = threading.Lock()
        self.launches: list[tuple[str, str, str]] = []     # (slot, task, name)
        self.messages: list[tuple[str, str, str]] = []     # (slot, task, kind)

    def slot_repo(self, slot: str) -> tuple[Repo, threading.Lock]:
        with self._lock:
            if slot not in self._repos:
                r = clone(self.cfg.repo.remote_url, self.clones_dir / slot, (f"sim-{slot}", f"{slot}@sim.local"))
                self._repos[slot] = (r, threading.Lock())
            return self._repos[slot]

    def _spawn(self, name, fn, *a):
        t = threading.Thread(target=fn, args=a, name=name, daemon=True)
        t.start()
        self.threads.append(t)

    def launch(self, slot: str, task, prompt: str, name: str) -> LaunchResult:
        if f"READY: {task.id}" not in prompt or f"{self.cfg.repo.branch_prefix}{task.id}" not in prompt:
            raise LaunchError("prompt lacks the READY: message or the branch name")
        sid = f"sim-{self.run_id}-{slot}-{task.id}"   # unique: a task is launched at most once per session
        repo, lk = self.slot_repo(slot)
        s = SimSession(task.id, sid, self.cfg, self.clock, self.oracle, random.Random(f"{self.seed}-{task.id}"),
                       threading.Event()).bind(repo, lk)
        with self._lock:
            self.sessions[task.id] = s
            self.by_id[sid] = s
            self.groups[sid] = {task.id: s}
            self.launches.append((slot, task.id, name))
        if self.stop_ev.is_set():
            s.halt.set()
        self._spawn(f"sim-{sid}", s.run_first)
        return LaunchResult(sid, "sim")

    def send(self, slot: str, task_id: str, session_id: str | None, message: str, kind: str = "rework") -> None:
        group = self.groups.get(session_id or "")
        if group is None:
            raise LaunchError(f"no such session {session_id}")
        repo, lk = self.slot_repo(slot)
        if kind == "task":
            b = f"{self.cfg.repo.branch_prefix}{task_id}"
            if f"READY: {task_id}" not in message or b not in message or "origin/main" not in message:
                raise LaunchError("task message lacks the READY: message, the branch name or origin/main")
            first = next(iter(group.values()))
            s = SimSession(task_id, session_id, self.cfg, self.clock, self.oracle,
                           random.Random(f"{self.seed}-{task_id}"), first.halt,
                           startup_mean_s=self.sim_followup_startup()).bind(repo, lk)
            with self._lock:
                self.sessions[task_id] = s
                group[task_id] = s
                self.messages.append((slot, task_id, kind))
            self._spawn(f"sim-{session_id}-{task_id}", s.run_first)
            return
        s = group.get(task_id)
        if s is None:
            raise LaunchError(f"session {session_id} never had task {task_id}")
        s.bind(repo, lk)
        with self._lock:
            self.messages.append((slot, task_id, kind))
        self._spawn(f"sim-{session_id}-{kind}", s.run_message, kind)

    def sim_followup_startup(self) -> float:
        return self.cfg.sim.followup_startup_mean_s

    def stop(self, session_id: str | None) -> None:
        s = self.by_id.get(session_id or "")
        if s is not None:
            s.halt.set()          # the group's SimSessions share one halt event

    def stop_all(self, orch=None) -> None:
        self.stop_ev.set()
        for s in list(self.sessions.values()):
            s.halt.set()
        for t in list(self.threads):
            t.join(timeout=30)
