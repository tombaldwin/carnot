"""Watcher, review queue, merge queue and window control.

Threads (all share one EventLog and one local clone of the sandbox remote):

  watcher   git fetch every poll interval; logs claim / claim_race / submit
  prep      runs the visible tests on each submitted head (for the review packet)
  reviewer  one review at a time, FIFO, never shows the reviewer the queue
  merger    serial merge queue: hidden_pre -> rebase -> tests_post -> merge
"""
from __future__ import annotations

import collections
import dataclasses as dc
import datetime as dt
import json
import os
import re
import subprocess
import tempfile
import threading
import time
import traceback
from pathlib import Path

from .clock import Clock, iso
from .config import Config
from .events import EventLog
from .gitops import GitError, Repo
from .review import ReviewError, ReviewPacket, Reviewer, make_packet
from .tasks import Task, TestResult, TestRunner

FEEDBACK_FILE = "FEEDBACK.md"
READY_RE = re.compile(r"^READY:", re.M)
WORKER_RE = re.compile(r"^Worker:\s*(\S+)\s*$", re.M | re.I)

# FEEDBACK.md wording by cause (PLAN-v3 s5: say the cause, never name hidden tests).
CAUSE_TEXT = {
    "review": "The reviewer requested changes.",
    "rebase_conflict": (
        "Your change does not apply cleanly to the current main (merge conflict). "
        "Update your branch from the current main, resolve the conflicts, and re-submit."),
    "visible_fail": (
        "After your change was applied to the current main, the visible test suite failed. "
        "Update your branch from the current main, make the visible tests pass, and re-submit."),
    "escaped_defect": (
        "Acceptance check failed. Your change does not yet meet the task's acceptance criteria. "
        "Re-read the task text and acceptance criteria, fix the change, and re-submit."),
    "integration_failure": (
        "Acceptance check failed after your change was applied to the current main "
        "(it passed on your branch as submitted). Your change interacts with work merged since "
        "you started. Update your branch from the current main, fix the interaction, and re-submit."),
}
FEEDBACK_FOOTER = (
    "When you have fixed it: delete FEEDBACK.md, commit with a message starting `READY:` "
    "(keep the `Worker:` trailer), and push this branch. The change will be reviewed again.")


@dc.dataclass
class Change:
    """One submission (one READY: head)."""
    task: str
    worker: str
    branch: str
    head: str
    attempt_no: int
    files: list[str]
    submitted_at: dt.datetime
    visible: TestResult | None = None
    prepared: threading.Event = dc.field(default_factory=threading.Event)
    review_failures: int = 0


@dc.dataclass
class TaskState:
    task: str
    branch: str
    worker: str
    last_head: str
    ignored: bool = False        # claimed after the window closed
    submits: int = 0
    merged: bool = False
    files: list[str] = dc.field(default_factory=list)


class Orchestrator:
    def __init__(self, cfg: Config, run_id: str, run_dir: Path, clock: Clock, log: EventLog,
                 repo: Repo, tasks: dict[str, Task], reviewer: Reviewer,
                 sandbox_commit: str, seed: int, kind: str | None = None):
        self.cfg, self.run_id, self.run_dir = cfg, run_id, run_dir
        self.clock, self.log, self.repo, self.tasks, self.reviewer = clock, log, repo, tasks, reviewer
        self.sandbox_commit, self.seed = sandbox_commit, seed
        self.kind = kind or cfg.run.kind
        self.tests = TestRunner(cfg, repo)
        self.pfx = cfg.repo.branch_prefix
        self.rpfx = cfg.repo.race_prefix

        self.lock = threading.RLock()
        self.cv = threading.Condition(self.lock)
        self.states: dict[str, TaskState] = {}
        self.inflight: dict[str, list[str]] = {}     # task -> files; submitted, not merged
        self.prep_q: collections.deque[Change] = collections.deque()
        self.review_q: collections.deque[Change] = collections.deque()
        self.merge_q: collections.deque[Change] = collections.deque()
        self.reviewing: Change | None = None
        self.merging: Change | None = None
        self.merged_tasks: list[str] = []
        self.seen_races: set[str] = set()
        self.phase = "setup"                          # setup | window | grace | done
        self.stop = threading.Event()
        self.threads: list[threading.Thread] = []
        self.errors: list[str] = []
        self.void_reasons: list[str] = []
        self.downtime_s = 0.0
        self._down_since: dt.datetime | None = None
        self.window_start: dt.datetime | None = None
        self.window_end: dt.datetime | None = None
        # Task supply (PLAN-v4 prep): the window's task list is reset.json's task_order (= TASKS.json on main),
        # else the catalogue. The watcher notes the moment the last unclaimed task is claimed.
        self.supply_ids: set[str] = self._supply_ids()
        self.claimed_supply: set[str] = set()
        self.exhausted_logged = False

    # ------------------------------------------------------------------ helpers
    def _supply_ids(self) -> set[str]:
        reset = self.run_dir / "reset.json"
        if reset.exists():
            try:
                order = json.loads(reset.read_text()).get("task_order")
                if isinstance(order, list) and order:
                    return {str(x) for x in order}
            except (json.JSONDecodeError, AttributeError):
                pass
        return set(self.tasks)

    def _fetch(self) -> None:
        self.repo.run("fetch", "-q", "--prune", "origin",
                      "+refs/heads/main:refs/remotes/origin/main",
                      "+refs/heads/claude/*:refs/remotes/origin/claude/*")

    def _diffstat(self, base: str, head: str) -> tuple[int, list[str]]:
        spec = [base, head, "--", ".", f":(exclude){FEEDBACK_FILE}"]
        lines = 0
        files = []
        for row in self.repo.out("diff", "--numstat", "--no-renames", *spec).splitlines():
            a, d, f = row.split("\t", 2)
            lines += (int(a) if a != "-" else 0) + (int(d) if d != "-" else 0)
            files.append(f)
        return lines, sorted(files)

    def _worker_of(self, commits: list[str]) -> str:
        for c in commits:
            m = WORKER_RE.search(self.repo.message(c))
            if m:
                return m.group(1)
        if commits:
            return self.repo.out("log", "-1", "--format=%an", commits[-1]) or "unknown"
        return "unknown"

    def queue_depth(self) -> int:
        with self.lock:
            return len(self.review_q)

    def _run_thread(self, name, fn):
        def wrapper():
            try:
                fn()
            except Exception:
                tb = traceback.format_exc()
                self.errors.append(f"{name}: {tb}")
                try:
                    self.log.note(f"harness error in {name}: {tb.splitlines()[-1]}")
                except Exception:
                    pass
        t = threading.Thread(target=wrapper, name=name, daemon=True)
        t.start()
        self.threads.append(t)

    # ------------------------------------------------------------------ watcher
    def poll(self) -> None:
        self._fetch()
        refs = self.repo.refs("refs/remotes/origin/claude/")
        main = self.repo.sha("origin/main")
        for ref, sha in sorted(refs.items()):
            branch = ref.removeprefix("refs/remotes/origin/")
            if branch.startswith(self.rpfx):
                self._race_marker(branch)
            elif branch.startswith(self.pfx):
                self._branch_seen(branch, sha, main)

    def _race_marker(self, branch: str) -> None:
        if branch in self.seen_races:
            return
        self.seen_races.add(branch)
        rest = branch[len(self.rpfx):]
        task, _, worker = rest.rpartition("-")
        if self.phase == "window" and task:
            self.log.emit("claim_race", worker=worker or "unknown", task=task)
        try:
            self.repo.run("push", "-q", "origin", "--delete", branch)
        except GitError:
            pass

    def _branch_seen(self, branch: str, sha: str, main: str) -> None:
        task = branch[len(self.pfx):]
        st = self.states.get(task)
        if st is None:
            own = self.repo.out("rev-list", "--reverse", sha, "^" + main).split()
            worker = self._worker_of(own)
            st = TaskState(task, branch, worker, last_head=main)
            self.states[task] = st
            if self.phase != "window":
                st.ignored = True
                self.log.note(f"claim of task {task} by {worker} outside the window; ignored")
                return
            if task not in self.tasks:
                self.log.note(f"branch {branch} does not match a task id in the catalogue")
            self.log.emit("claim", worker=worker, task=task, branch=branch)
            if task in self.supply_ids:
                self.claimed_supply.add(task)
                if not self.exhausted_logged and self.claimed_supply >= self.supply_ids:
                    self.exhausted_logged = True
                    self.log.note(f"tasks_exhausted n={len(self.supply_ids)}")
        if st.ignored or sha == st.last_head:
            return
        new = self.repo.out("rev-list", sha, "^" + st.last_head, "^origin/main").split()
        st.last_head = sha
        ready = [c for c in new if READY_RE.match(self.repo.message(c))]
        if not ready:
            return
        head = ready[0]  # newest READY: commit in this push
        if self.phase != "window":
            self.log.note(f"submission of task {task} head {head[:12]} after window end; not queued")
            return
        self._submit(st, head)

    def _submit(self, st: TaskState, head: str) -> None:
        base = self.repo.out("merge-base", "origin/main", head)
        lines, files = self._diffstat(base, head)
        worker = self._worker_of([head]) if WORKER_RE.search(self.repo.message(head)) else st.worker
        with self.lock:
            others = {t: f for t, f in self.inflight.items() if t != st.task}
            k = len(others)
            fs = set(files)
            m = sum(1 for f in others.values() if fs & set(f))
            st.submits += 1
            st.files = files
            self.inflight[st.task] = files
            self.log.emit("submit", worker=worker, task=st.task, branch=st.branch, head=head,
                          attempt_no=st.submits, lines_changed=lines, files=files, k=k, m=m)
            ch = Change(st.task, worker, st.branch, head, st.submits, files, self.clock.now())
            self.prep_q.append(ch)
            for i, old in enumerate(self.review_q):
                if old.task == st.task:
                    self.review_q[i] = ch
                    self.log.note(f"task {st.task}: head {old.head[:12]} superseded in review queue by {head[:12]}")
                    break
            else:
                self.review_q.append(ch)
            self.cv.notify_all()

    def _watch_loop(self) -> None:
        while not self.stop.is_set():
            try:
                self.poll()
            except GitError as e:
                self.log.note(f"watcher git error: {str(e)[:300]}")
            if self.clock.sleep(self.cfg.run.poll_interval_s, self.stop):
                return

    # ------------------------------------------------------------------ prep
    def _prep_loop(self) -> None:
        while True:
            with self.lock:
                while not self.prep_q and not self.stop.is_set():
                    self.cv.wait(0.2)
                if self.stop.is_set() and not self.prep_q:
                    return
                ch = self.prep_q.popleft()
            try:
                ch.visible, _ = self.tests.run(ch.head, visible=True, hidden_tasks=[])
            except Exception as e:  # a broken checkout counts as a visible failure
                ch.visible = TestResult(False, f"harness could not run visible tests: {e}", 0.0)
            self.log.note(f"visible_pre task={ch.task} head={ch.head} passed={str(ch.visible.passed).lower()}")
            ch.prepared.set()
            with self.lock:
                self.cv.notify_all()

    # ------------------------------------------------------------------ review
    def _packet(self, ch: Change) -> ReviewPacket:
        base = self.repo.out("merge-base", "origin/main", ch.head)
        task = self.tasks.get(ch.task) or Task(ch.task, "", "(task not in catalogue)", [])
        vis = ch.visible or TestResult(False, "not run", 0.0)
        pk = make_packet(self.cfg, self.repo, task, ch.head, base, vis.passed, vis.output)
        pk.files = ch.files          # as logged at submit
        return pk

    def _review_allowed(self) -> bool:
        return self.phase == "window" or (self.phase == "grace" and self.cfg.run.grace_reviews)

    def _review_loop(self) -> None:
        busy = False
        rc = self.cfg.reviewer
        while True:
            with self.lock:
                while not self.stop.is_set() and not (
                        self.review_q and self.review_q[0].prepared.is_set() and self._review_allowed()):
                    self.cv.wait(0.2)
                if self.stop.is_set():
                    return
                ch = self.review_q.popleft()
                depth = len(self.review_q)
                self.reviewing = ch
            if not busy:
                self.log.emit("reviewer_busy")
                busy = True
            packet = self._packet(ch)
            result = None
            for attempt in range(1 + rc.retries):
                self.log.emit("review_start", task=ch.task, head=ch.head, queue_depth=depth)
                t0 = self.clock.now()
                try:
                    result = self.reviewer.review(packet)
                except Exception as e:  # ReviewError or anything unexpected
                    msg = str(e) if isinstance(e, ReviewError) else f"{type(e).__name__}: {e}"
                    self.log.emit("review_error", task=ch.task, head=ch.head, error=msg[:1000])
                    if self._down_since is None:
                        self._down_since = t0
                    continue
                dur = (self.clock.now() - t0).total_seconds()
                self.log.emit("review_end", task=ch.task, head=ch.head, verdict=result.verdict,
                              reason=result.reason, tokens_in=result.tokens_in,
                              tokens_out=result.tokens_out, duration_s=round(dur, 3))
                self._end_downtime()
                break
            with self.lock:
                self.reviewing = None
            if result is None:
                # Retried once and still failing: put it back at the front and back off.
                with self.lock:
                    self.review_q.appendleft(ch)
                self.log.emit("reviewer_idle")
                busy = False
                self._check_downtime()
                if self.clock.sleep(rc.retry_backoff_s, self.stop):
                    return
                continue
            if result.verdict == "approve":
                with self.lock:
                    self.merge_q.append(ch)
                    self.cv.notify_all()
            else:
                self.bounce(ch, "review", result.reason)
            with self.lock:
                empty = not (self.review_q and self.review_q[0].prepared.is_set())
            if empty:
                self.log.emit("reviewer_idle")
                busy = False

    def _end_downtime(self) -> None:
        if self._down_since is not None:
            self.downtime_s += (self.clock.now() - self._down_since).total_seconds()
            self._down_since = None
            self._check_downtime()

    def _check_downtime(self) -> None:
        total = self.downtime_s
        if self._down_since is not None:
            total += (self.clock.now() - self._down_since).total_seconds()
        limit = self.cfg.reviewer.max_downtime_min * 60
        if total > limit and not any("reviewer downtime" in v for v in self.void_reasons):
            reason = f"reviewer downtime {total / 60:.1f} min > {self.cfg.reviewer.max_downtime_min} min"
            self.void_reasons.append(reason)
            self.log.note(f"VOID: {reason}")

    # ------------------------------------------------------------------ merge queue
    def _merge_loop(self) -> None:
        busy = False
        while True:
            with self.lock:
                while not self.merge_q and not self.stop.is_set():
                    self.cv.wait(0.2)
                if self.stop.is_set():
                    return
                ch = self.merge_q.popleft()
                self.merging = ch
            if not busy:
                self.log.emit("queue_busy")
                busy = True
            try:
                self.merge_one(ch)
            finally:
                with self.lock:
                    self.merging = None
                    empty = not self.merge_q
            if empty:
                self.log.emit("queue_idle")
                busy = False

    def _without_feedback(self, tree: str) -> str:
        """``tree`` minus FEEDBACK.md (so it never reaches main)."""
        if not self.repo.run("ls-tree", tree, FEEDBACK_FILE, check=True).stdout.strip():
            return tree
        with tempfile.TemporaryDirectory(prefix="fleet-idx-") as td:
            env_idx = os.path.join(td, "index")
            env = dict(self.repo.env, GIT_INDEX_FILE=env_idx)
            def g(*a):
                p = subprocess.run(["git", *a], cwd=self.repo.path, env=env, capture_output=True, text=True, check=True)
                return p.stdout.strip()
            with self.repo.lock:
                g("read-tree", tree)
                g("rm", "-q", "--cached", FEEDBACK_FILE)
                return g("write-tree")

    def merge_one(self, ch: Change) -> None:
        """Serial merge of one approved change. Logs an ``mq_timing`` note with the real
        (wall-clock) seconds spent in each step, for the merge-queue precondition."""
        tm: dict = {"hidden_pre_s": 0.0, "rebase_s": 0.0, "post_export_s": 0.0, "visible_post_s": 0.0,
                    "hidden_post_s": 0.0, "n_hidden_post": 0, "merge_s": 0.0}
        t_start = time.monotonic()
        outcome = "gave_up"
        try:
            outcome = self._merge_steps(ch, tm)
        finally:
            tm["total_s"] = time.monotonic() - t_start
            self.log.note("mq_timing task=%s head=%s outcome=%s %s" % (
                ch.task, ch.head[:12], outcome,
                " ".join(f"{k}={v:.3f}" if isinstance(v, float) else f"{k}={v}" for k, v in tm.items())))

    def _post_scope(self, ch: Change) -> list[str]:
        scope = [ch.task]
        mode = self.cfg.tests.post_hidden_scope
        if mode == "merged":
            scope += [t for t in self.merged_tasks if t != ch.task]
        elif mode != "task":
            raise ValueError(f"unknown post_hidden_scope {mode!r}")
        return scope

    def _merge_steps(self, ch: Change, tm: dict) -> str:
        # 1. hidden tests on the exact approved head
        t0 = time.monotonic()
        _, hid = self.tests.run(ch.head, visible=False, hidden_tasks=[ch.task])
        tm["hidden_pre_s"] = time.monotonic() - t0
        self.log.emit("hidden_pre", task=ch.task, head=ch.head, passed=hid.passed)
        if not hid.passed:
            self.bounce(ch, "escaped_defect")
            return "escaped_defect"
        for _ in range(3):
            # 2. rebase on current main (a three-way merge of the change's net diff; squash)
            t0 = time.monotonic()
            self._fetch()
            main = self.repo.sha("origin/main")
            base = self.repo.out("merge-base", main, ch.head)
            p = self.repo.run("merge-tree", "--write-tree", "--no-messages", f"--merge-base={base}",
                              main, ch.head, check=False)
            if p.returncode == 1:
                tm["rebase_s"] += time.monotonic() - t0
                self.log.emit("rebase", task=ch.task, head=ch.head, new_head=None, conflict=True)
                self.bounce(ch, "rebase_conflict")
                return "rebase_conflict"
            if p.returncode != 0:
                raise GitError(["merge-tree"], p.returncode, p.stdout, p.stderr)
            tree = self._without_feedback(p.stdout.split()[0])
            title = self.tasks[ch.task].title if ch.task in self.tasks else ""
            msg = (f"merge task-{ch.task}: {title}\n\nApproved head {ch.head} "
                   f"(attempt {ch.attempt_no}, worker {ch.worker}).\n")
            new_head = self.repo.commit_tree(tree, [main], msg)
            tm["rebase_s"] += time.monotonic() - t0
            self.log.emit("rebase", task=ch.task, head=ch.head, new_head=new_head, conflict=False)
            # 3. visible + hidden tests after rebase
            scope = self._post_scope(ch)
            tt: dict = {}
            vis, hid = self.tests.run(new_head, visible=True, hidden_tasks=scope, timing=tt)
            tm["post_export_s"] += tt.get("export_s", 0.0)
            tm["visible_post_s"] += tt.get("visible_s", 0.0)
            tm["hidden_post_s"] += tt.get("hidden_s", 0.0)
            tm["n_hidden_post"] = len(scope)
            self.log.emit("tests_post", task=ch.task, head=ch.head, visible_passed=vis.passed,
                          hidden_passed=hid.passed)
            if not vis.passed:
                self.bounce(ch, "visible_fail", _tail(vis.output))
                return "visible_fail"
            if not hid.passed:
                self.bounce(ch, "integration_failure")
                return "integration_failure"
            # 4. merge (fast-forward main to the rebased commit)
            t0 = time.monotonic()
            push = self.repo.run("push", "-q", "origin", f"{new_head}:refs/heads/main", check=False)
            tm["merge_s"] += time.monotonic() - t0
            if push.returncode != 0:
                self.log.note(f"task {ch.task}: main moved under the merge queue; redoing rebase")
                continue
            self.log.emit("merge", task=ch.task, head=ch.head, main_sha=new_head)
            with self.lock:
                self.merged_tasks.append(ch.task)
                self.inflight.pop(ch.task, None)
                if ch.task in self.states:
                    self.states[ch.task].merged = True
            return "merged"
        self.log.note(f"task {ch.task}: gave up merging after 3 attempts to push main")
        return "gave_up"

    # ------------------------------------------------------------------ bounce
    def feedback_text(self, ch: Change, cause: str, detail: str = "") -> str:
        parts = [f"# Feedback: task {ch.task}, attempt {ch.attempt_no}", "",
                 f"Reviewed head: {ch.head}", "", f"Cause: {cause}", "", CAUSE_TEXT[cause]]
        if detail:
            parts += ["", "Details:", "", detail.strip()]
        parts += ["", FEEDBACK_FOOTER, ""]
        return "\n".join(parts)

    def bounce(self, ch: Change, cause: str, detail: str = "") -> None:
        text = self.feedback_text(ch, cause, detail)
        ref = f"refs/remotes/origin/{ch.branch}"
        for _ in range(5):
            self.repo.run("fetch", "-q", "origin", f"+refs/heads/{ch.branch}:{ref}")
            tip = self.repo.sha(ref)
            blob = self.repo.out("hash-object", "-w", "--stdin", input=text)
            tree = self._with_file(self.repo.out("rev-parse", tip + "^{tree}"), FEEDBACK_FILE, blob)
            c = self.repo.commit_tree(tree, [tip], f"FEEDBACK: {cause} (task {ch.task}, head {ch.head[:12]})")
            if self.repo.run("push", "-q", "origin", f"{c}:refs/heads/{ch.branch}", check=False).returncode == 0:
                st = self.states.get(ch.task)
                if st and st.last_head == tip:
                    st.last_head = c
                self.log.emit("bounce", task=ch.task, head=ch.head, cause=cause)
                return
        self.log.note(f"task {ch.task}: could not push FEEDBACK.md after 5 tries; bounce {cause} not delivered")
        self.log.emit("bounce", task=ch.task, head=ch.head, cause=cause)

    def _with_file(self, tree: str, name: str, blob: str) -> str:
        with tempfile.TemporaryDirectory(prefix="fleet-idx-") as td:
            env = dict(self.repo.env, GIT_INDEX_FILE=os.path.join(td, "index"))
            def g(*a):
                return subprocess.run(["git", *a], cwd=self.repo.path, env=env, capture_output=True,
                                      text=True, check=True).stdout.strip()
            with self.repo.lock:
                g("read-tree", tree)
                g("update-index", "--add", "--cacheinfo", f"100644,{blob},{name}")
                return g("write-tree")

    # ------------------------------------------------------------------ window
    def idle(self) -> bool:
        with self.lock:
            return (not self.prep_q and not self.review_q and not self.merge_q
                    and self.reviewing is None and self.merging is None)

    def start_threads(self) -> None:
        for name, fn in (("watcher", self._watch_loop), ("prep", self._prep_loop),
                         ("reviewer", self._review_loop), ("merger", self._merge_loop)):
            self._run_thread(name, fn)

    def shutdown(self) -> None:
        self.stop.set()
        with self.lock:
            self.cv.notify_all()
        for t in self.threads:
            t.join(timeout=max(5.0, self.cfg.tests.timeout_s))

    def run_window(self, launcher) -> dict:
        rc = self.cfg.run
        self._fetch()
        self.log.note(f"run {self.run_id}: sandbox_commit={self.sandbox_commit} "
                      f"main={self.repo.sha('origin/main')} seed={self.seed}")
        self.window_start = self.clock.now()
        self.window_end = self.window_start + dt.timedelta(minutes=rc.window_min)
        warm_end = self.window_start + dt.timedelta(minutes=rc.warmup_min)
        grace_end = self.window_end + dt.timedelta(minutes=rc.grace_min)
        self.phase = "window"
        self.log.note("window_start")
        self.log.note(f"task_supply n={len(self.supply_ids)}")
        self.start_threads()
        self._start_workers(launcher)

        self.clock.sleep_until(warm_end)
        self.log.note("warmup_end")
        self.clock.sleep_until(self.window_end)
        # Stop accepting new claims and submissions; stop the workers.
        with self.lock:
            self.phase = "grace"
            self.cv.notify_all()
        self.log.note("window_end")
        launcher.stop_all(self)
        try:
            self.poll()  # log anything pushed just before the end
        except GitError:
            pass
        while self.clock.now() < grace_end:
            if rc.end_grace_early_when_idle and self.idle():
                self.log.note("grace ended early: nothing left to review or merge")
                break
            if not self.cfg.run.grace_reviews and not self.merge_q and self.merging is None:
                break
            self.clock.sleep(min(30.0, max(0.0, (grace_end - self.clock.now()).total_seconds())))
        self.phase = "done"
        with self.lock:
            open_ch = self.reviewing
        if open_ch is not None:
            # Not counted by the analysis, and neither is its busy time (derive.py grace-end correction).
            self.log.note(f"review_open_at_grace_end task={open_ch.task} head={open_ch.head}")
        self.log.note("grace_end")
        self.shutdown()
        self._check_claude_dir()
        self._check_downtime()
        return self.write_run_json()

    def _start_workers(self, launcher) -> None:
        """All workers at window start, or, with ``[run] start_schedule`` (T1: [[0, 1], [30, 12]]),
        each group at its minute from a launcher thread, so the window's own timing (warm-up end,
        window end) is never held up by the operator typing session ids."""
        sched = self.cfg.run.start_schedule
        if not sched:
            launcher.start_all(self)
            return
        stages, started = [], 0
        for minute, upto in sched:
            ids = [f"w{i}" for i in range(started + 1, int(upto) + 1)]
            started = int(upto)
            stages.append((self.window_start + dt.timedelta(minutes=float(minute)), minute, ids))

        def run_stages():
            for at, minute, ids in stages:
                if self.clock.sleep_until(at, self.stop):
                    return
                if self.phase != "window":
                    self.log.note(f"start_schedule: minute {minute} group {ids} not started (window over)")
                    return
                self.log.note(f"start_schedule minute={minute} workers={','.join(ids)}")
                launcher.start(ids, self)
        self._run_thread("launcher", run_stages)

    def _check_claude_dir(self) -> None:
        try:
            self._fetch()
            d = self.repo.out("diff", "--stat", self.sandbox_commit, "origin/main", "--", ".claude")
            if d:
                self.log.note(f".claude/ changed on main during the window: {d.splitlines()[-1]}")
        except GitError as e:
            self.log.note(f".claude/ check failed: {e}")

    def write_run_json(self) -> dict:
        notes = [self.cfg.run.notes] if self.cfg.run.notes else []
        reset = self.run_dir / "reset.json"
        if reset.exists():
            r = json.loads(reset.read_text())
            notes.append(f"tasks_commit={r.get('tasks_commit')}")
        notes += [f"VOID: {v}" for v in self.void_reasons]
        if self.errors:
            notes.append(f"harness errors: {len(self.errors)} (see events note lines)")
        run = {
            "run_id": self.run_id, "kind": self.kind, "n_workers": self.cfg.run.n_workers,
            "window_start": iso(self.window_start), "window_end": iso(self.window_end),
            "warmup_min": self.cfg.run.warmup_min, "grace_min": self.cfg.run.grace_min,
            "task_order_seed": self.seed, "sandbox_commit": self.sandbox_commit,
            "harness_commit": harness_commit(), "worker_model": self.cfg.run.worker_model,
            "reviewer_model": self.cfg.run.reviewer_model, "notes": "; ".join(notes),
        }
        (self.run_dir / "run.json").write_text(json.dumps(run, indent=2) + "\n")
        return run


def _tail(text: str, n: int = 40) -> str:
    return "\n".join(text.strip().splitlines()[-n:])


def harness_commit() -> str:
    here = Path(__file__).resolve().parent.parent
    try:
        sha = subprocess.run(["git", "rev-parse", "HEAD"], cwd=here, capture_output=True, text=True,
                             check=True).stdout.strip()
        dirty = subprocess.run(["git", "status", "--porcelain", "--", "."], cwd=here,
                               capture_output=True, text=True).stdout.strip()
        return sha + ("-dirty" if dirty else "")
    except (subprocess.CalledProcessError, FileNotFoundError):
        return "unknown"
