"""Slots and sessions, watcher, review queue, merge queue and window control.

Worker model (harness README, "Worker model: one cloud session per task"): N **slots**, each running one
cloud session at a time. A session works on exactly one task, whose text is in its launch prompt.

Threads (all share one EventLog and one local clone of the sandbox remote):

  dispatcher  hands work to free slots: first queued rework (a follow-up message to the task's own
              session), else the next task in the window's seeded order (a new session); abandons a
              session with no READY within task_timeout_min of its launch / message
  launch-*    one short-lived thread per launch or follow-up (a launch can take minutes)
  watcher     git fetch every poll interval; logs claim (branch first pushed) and submit (a READY: commit);
              a READY frees the task's slot
  prep        runs the visible tests on each submitted head (logged as visible_pre)
  reviewer-r1..rK  K reviewer threads ([reviewer] parallel, PLAN-v6 section 6) on one FIFO queue; each
              reviews one change at a time, no task is under review by two at once, and a reviewer never
              sees the queue
  merger      serial merge queue: hidden_pre -> rebase -> tests_post -> merge

The orchestrator never pushes to a session's branch: a bounce is queued as rework and delivered as a
follow-up message when a slot frees.
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
from .launchers import LaunchError, probe_message, rework_message, task_prompt
from .review import ReviewError, ReviewPacket, Reviewer, make_packet
from .tasks import Task, TestResult, TestRunner

FEEDBACK_FILE = "FEEDBACK.md"   # no longer written; still excluded from diffs and from main, defensively
READY_RE = re.compile(r"^READY:", re.M)
READY_ID_RE = re.compile(r"^READY:\s*(?:task[-_ ]?)?([A-Za-z0-9_.-]+)")
PROBE_RE = re.compile(r"^PROBE:")

# Rework message wording by cause (PLAN-v3 s5: say the cause, never name hidden tests).
CAUSE_TEXT = {
    "review": "The reviewer requested changes.",
    "rebase_conflict": (
        "Your change does not apply cleanly to the current main (merge conflict). "
        "Merge the current main into your branch, resolve the conflicts, and re-submit."),
    "visible_fail": (
        "After your change was applied to the current main, the visible test suite failed. "
        "Merge the current main into your branch, make the visible tests pass, and re-submit."),
    "escaped_defect": (
        "Acceptance check failed. Your change does not yet meet the task's acceptance criteria. "
        "Re-read the task text and acceptance criteria, fix the change, and re-submit."),
    "integration_failure": (
        "Acceptance check failed after your change was applied to the current main "
        "(it passed on your branch as submitted). Your change interacts with work merged since "
        "you started. Merge the current main into your branch, fix the interaction, and re-submit."),
}


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
    submits: int = 0
    merged: bool = False
    files: list[str] = dc.field(default_factory=list)


@dc.dataclass
class Slot:
    id: str
    open: bool = False
    task: str | None = None            # task whose session occupies the slot
    since: dt.datetime | None = None   # launch / message time: the session timeout runs from here
    kind: str | None = None            # launch | rework | probe
    fail_streak: int = 0               # consecutive tasks whose launch failed on this slot
    backoff_until: dt.datetime | None = None   # no new work before this (after a failed launch)
    down: bool = False                 # marked down (worker_down) after too many failed launches


@dc.dataclass
class Session:
    task: str
    slot: str | None                    # slot it occupies now (None: waiting for review / rework)
    last_slot: str
    session_id: str | None = None
    launches: int = 0
    status: str = "starting"            # starting | working | submitted | rework_queued | probe | abandoned
    feedback: collections.deque = dc.field(default_factory=collections.deque)   # messages to deliver
    probe_sent_at: dt.datetime | None = None
    id_pending: bool = False            # routine mode: the session id is still being looked up


class Orchestrator:
    def __init__(self, cfg: Config, run_id: str, run_dir: Path, clock: Clock, log: EventLog,
                 repo: Repo, tasks: dict[str, Task], reviewer: Reviewer,
                 sandbox_commit: str, seed: int, kind: str | None = None, launcher=None):
        self.cfg, self.run_id, self.run_dir = cfg, run_id, run_dir
        self.clock, self.log, self.repo, self.tasks, self.reviewer = clock, log, repo, tasks, reviewer
        self.sandbox_commit, self.seed = sandbox_commit, seed
        self.kind = kind or cfg.run.kind
        self.launcher = launcher
        self.tests = TestRunner(cfg, repo)
        self.pfx = cfg.repo.branch_prefix

        self.lock = threading.RLock()
        self.cv = threading.Condition(self.lock)
        self.states: dict[str, TaskState] = {}
        self.branch_heads: dict[str, str] = {}       # attributed claude/* branch -> last seen sha
        self.unattributed: set[str] = set()
        self.inflight: dict[str, list[str]] = {}     # task -> files; submitted, not merged, not abandoned
        self.prep_q: collections.deque[Change] = collections.deque()
        self.review_q: collections.deque[Change] = collections.deque()
        self.merge_q: collections.deque[Change] = collections.deque()
        self.n_reviewers = max(1, int(cfg.reviewer.parallel))
        self.reviewer_ids = [f"r{i}" for i in range(1, self.n_reviewers + 1)]
        self.reviewing: dict[str, Change] = {}       # reviewer id -> change under review
        self.merging: Change | None = None
        self.merged_tasks: list[str] = []
        # slots and sessions
        self.slots: dict[str, Slot] = {f"s{i}": Slot(f"s{i}") for i in range(1, cfg.run.n_workers + 1)}
        self.sessions: dict[str, Session] = {}
        self.rework_q: collections.deque[str] = collections.deque()
        self.order: list[str] = self._task_order()
        self.pending: collections.deque[str] = collections.deque(self.order)
        self.supply_ids: set[str] = set(self.order)
        self.exhausted_logged = False
        self.probe_done = False
        self.phase = "setup"                          # setup | window | grace | done
        self.stop = threading.Event()
        self.threads: list[threading.Thread] = []
        self.errors: list[str] = []
        self.void_reasons: list[str] = []
        self.downtime_s = 0.0                        # summed over reviewers (closed stretches)
        self.downtime_by: dict[str, float] = {r: 0.0 for r in self.reviewer_ids}
        self._down_since: dict[str, dt.datetime] = {}   # reviewer id -> start of its current downtime
        self.window_start: dt.datetime | None = None
        self.window_end: dt.datetime | None = None
        self.launch_failures: collections.deque = collections.deque()   # (time, slot) of failed launches
        self.dispatch_paused_until: dt.datetime | None = None

    # ------------------------------------------------------------------ helpers
    def _task_order(self) -> list[str]:
        """The window's seeded order: reset.json's task_order (= TASKS.json on main, with [run] first_task
        first), else the catalogue."""
        reset = self.run_dir / "reset.json"
        if reset.exists():
            try:
                order = json.loads(reset.read_text()).get("task_order")
                if isinstance(order, list) and order:
                    return [str(x) for x in order]
            except (json.JSONDecodeError, AttributeError):
                pass
        return sorted(self.tasks)

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

    def queue_depth(self) -> int:
        with self.lock:
            return len(self.review_q)

    def _run_thread(self, name, fn, *args):
        def wrapper():
            try:
                fn(*args)
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

    def busy_slots(self) -> int:
        with self.lock:
            return sum(1 for s in self.slots.values() if s.task is not None)

    # ------------------------------------------------------------------ slots
    def open_slots(self, ids: list[str]) -> None:
        with self.lock:
            for sid in ids:
                sl = self.slots[sid]
                if not sl.open:
                    sl.open = True
                    self.log.emit("worker_start", worker=sid, session_id=None)
            self.cv.notify_all()

    def _occupy(self, sl: Slot, task: str, kind: str) -> None:
        sl.task, sl.since, sl.kind = task, self.clock.now(), kind
        self.log.emit("slot_busy", slot=sl.id)

    def _free(self, sl: Slot) -> None:
        if sl.task is None:
            return
        sess = self.sessions.get(sl.task)
        if sess is not None and sess.slot == sl.id:
            sess.slot = None
        sl.task, sl.since, sl.kind = None, None, None
        self.log.emit("slot_idle", slot=sl.id)
        self.cv.notify_all()

    def _abandon(self, task: str, why: str) -> None:
        """Never re-launched in this window. A change already waiting for review stays there."""
        sess = self.sessions[task]
        sess.status = "abandoned"
        sess.feedback.clear()
        if task in self.rework_q:
            self.rework_q.remove(task)
        if not any(c.task == task for c in list(self.review_q) + list(self.merge_q)) and \
                not any(c.task == task for c in self.reviewing.values()) and \
                not (self.merging and self.merging.task == task):
            self.inflight.pop(task, None)
        self.log.note(f"task_abandoned task={task} session={sess.session_id} reason={why}")

    def adopt_session(self, slot: str, task: str, session_id: str | None) -> Session:
        """Record a session started outside the dispatcher (tests): the slot is taken, the task leaves the
        pending list, session_launch is logged."""
        with self.lock:
            sl = self.slots[slot]
            if not sl.open:
                self.open_slots([slot])
            if sl.task is not None:
                raise RuntimeError(f"slot {slot} is busy with {sl.task}")
            if task in self.pending:
                self.pending.remove(task)
            self._occupy(sl, task, "launch")
            sess = self.sessions.get(task) or Session(task, slot, slot)
            sess.slot, sess.last_slot, sess.session_id = slot, slot, session_id
            sess.launches += 1
            sess.status = "working"
            self.sessions[task] = sess
            self.log.emit("session_launch", slot=slot, task=task, session_id=session_id, attempt_no=sess.launches)
            self._note_exhausted()
            return sess

    def _note_exhausted(self) -> None:
        if not self.pending and not self.exhausted_logged and self.phase == "window":
            self.exhausted_logged = True
            self.log.note(f"tasks_exhausted n={len(self.supply_ids)}")

    # ------------------------------------------------------------------ dispatcher
    def dispatch_once(self) -> None:
        """One pass: time out stale sessions, then fill free open slots (rework first, then new tasks)."""
        with self.lock:
            now = self.clock.now()
            limit = dt.timedelta(minutes=self.cfg.run.task_timeout_min)
            for sl in self.slots.values():
                if sl.task is None or sl.since is None:
                    continue
                sess = self.sessions.get(sl.task)
                if sl.kind == "probe":
                    if now - sl.since > dt.timedelta(minutes=self.cfg.run.probe_timeout_min):
                        self.log.note(f"probe_timeout task={sl.task} session={sess.session_id if sess else None}")
                        if sess is not None and sess.status == "probe":
                            sess.status = "submitted"
                        self._free(sl)
                    continue
                if now - sl.since > limit and sess is not None and sess.status in ("starting", "working"):
                    self.log.emit("session_timeout", slot=sl.id, task=sl.task, session_id=sess.session_id)
                    self._abandon(sl.task, f"timeout {self.cfg.run.task_timeout_min:g} min")
                    self._stop_session(sess)
                    self._free(sl)
            if self.phase != "window" or self.launcher is None:
                return
            if self.dispatch_paused_until is not None:
                if now < self.dispatch_paused_until:
                    return
                self.dispatch_paused_until = None
                self.log.note("dispatch_resumed")
            for sl in sorted(self.slots.values(), key=lambda s: int(s.id[1:])):
                if not sl.open or sl.task is not None or sl.down:
                    continue
                if sl.backoff_until is not None:
                    if now < sl.backoff_until:
                        continue
                    sl.backoff_until = None
                task = self._next_rework()
                if task is not None:
                    sess = self.sessions[task]
                    msg = sess.feedback.popleft()
                    sess.slot, sess.last_slot, sess.status = sl.id, sl.id, "working"
                    self._occupy(sl, task, "rework")
                    self._run_thread(f"send-{sl.id}-{task}", self._do_send, sl.id, task, msg, "rework")
                    continue
                if not self.pending:
                    break
                task = self.pending.popleft()
                sess = Session(task, sl.id, sl.id)
                self.sessions[task] = sess
                self._occupy(sl, task, "launch")
                self._note_exhausted()
                self._run_thread(f"launch-{sl.id}-{task}", self._do_launch, sl.id, task)

    def _next_rework(self) -> str | None:
        for task in list(self.rework_q):
            sess = self.sessions.get(task)
            if sess is None or sess.status == "abandoned" or not sess.feedback:
                self.rework_q.remove(task)
                continue
            if sess.slot is not None:      # its session is still busy elsewhere; keep it queued
                continue
            if sess.id_pending:            # routine mode: its session id is not known yet; keep it queued
                continue
            self.rework_q.remove(task)
            return task
        return None

    def _do_launch(self, slot: str, task: str) -> None:
        t = self.tasks.get(task) or Task(task, "", "(task not in catalogue)", [])
        prompt = task_prompt(self.cfg, t)
        name = f"{self.run_id}-{slot}-{task}"
        sess = self.sessions[task]
        last_err = ""
        for n in range(1 + max(0, self.cfg.launcher.launch_retries)):
            with self.lock:
                if sess.status == "abandoned" or self.stop.is_set():
                    return
                sess.launches += 1
            try:
                res = self.launcher.launch(slot, t, prompt, name)
            except (LaunchError, OSError, subprocess.SubprocessError) as e:
                last_err = str(e)
                self.log.note(f"session_launch_failed slot={slot} task={task} attempt={sess.launches} "
                              f"error={str(e)[:300]}")
                continue
            with self.lock:
                self.slots[slot].fail_streak = 0
                sess.session_id = res.session_id
                if sess.status == "starting":
                    sess.status = "working"
                self.log.emit("session_launch", slot=slot, task=task, session_id=res.session_id,
                              attempt_no=sess.launches)
                self.log.note(f"launch_detail slot={slot} task={task} detached_by={res.detached_by} "
                              f"returncode={res.returncode} session_id_seen={str(res.session_id is not None).lower()}"
                              + (f" {res.detail}" if res.detail else ""))
                discover = (res.session_id is None and res.pending is not None
                            and hasattr(self.launcher, "discover_session_id"))
                if discover:
                    sess.id_pending = True
            if discover:
                self._discover_session_id(slot, task, res.pending)
            return
        with self.lock:
            self._abandon(task, "launch failed")
            sl = self.slots[slot]
            if sl.task == task:
                self._free(sl)
            if self.phase == "window" and not self.stop.is_set():
                self._launch_failed(sl, last_err)

    def _launch_failed(self, sl: Slot, err: str) -> None:
        """After a task's launch failed on ``sl`` (all attempts; lock held): back the slot off, mark it down
        after max_consecutive_launch_failures in a row, and pause all dispatch if launches fail on several
        slots at once (T1 incident, 2026-09-28: a replaced CLI binary made one slot abandon ~15 tasks in a
        second and burn its routine's hourly budget)."""
        rc = self.cfg.run
        now = self.clock.now()
        sl.fail_streak += 1
        backoff = dt.timedelta(seconds=max(0.0, rc.launch_fail_backoff_s))
        sl.backoff_until = now + backoff
        err1 = " ".join(err.split())[:200] or "unknown"
        if rc.max_consecutive_launch_failures > 0 and sl.fail_streak >= rc.max_consecutive_launch_failures \
                and not sl.down:
            sl.down = True
            self.log.emit("worker_down", worker=sl.id,
                          reason=f"launch_failures n={sl.fail_streak} last_error={err1}")
            print(f"\n*** SLOT {sl.id} DOWN: {sl.fail_streak} consecutive launch failures; no more work is sent "
                  f"to it this window. Last error: {err1}\n", flush=True)
        else:
            self.log.note(f"slot_backoff slot={sl.id} fail_streak={sl.fail_streak} "
                          f"backoff_s={backoff.total_seconds():g}")
        if rc.launch_fail_breaker_slots <= 0:
            return
        win = dt.timedelta(seconds=rc.launch_fail_breaker_window_s)
        self.launch_failures.append((now, sl.id))
        while self.launch_failures and now - self.launch_failures[0][0] > win:
            self.launch_failures.popleft()
        slots = sorted({s for _, s in self.launch_failures}, key=lambda x: int(x[1:]))
        if len(slots) >= rc.launch_fail_breaker_slots:
            self.dispatch_paused_until = now + backoff
            self.launch_failures.clear()
            self.log.note(f"dispatch_paused reason=launch_failures slots={','.join(slots)} "
                          f"window_s={rc.launch_fail_breaker_window_s:g} pause_s={backoff.total_seconds():g} "
                          f"last_error={err1}")
            print(f"\n*** DISPATCH PAUSED {backoff.total_seconds():g}s: launches failed on slots "
                  f"{','.join(slots)} within {rc.launch_fail_breaker_window_s:g}s. Last error: {err1}\n", flush=True)

    def _discover_session_id(self, slot: str, task: str, pending) -> None:
        """Routine mode: the launch returned before its run existed. Look the run's session id up (the launcher
        polls lazily) and record it; rework for the task waits in the queue until this ends."""
        sess = self.sessions[task]
        t0 = self.clock.now()

        def should_stop() -> bool:
            return self.stop.is_set() or self.phase != "window" or sess.status == "abandoned"
        sid = None
        err = None
        try:
            sid = self.launcher.discover_session_id(pending, should_stop)
        except Exception as e:  # never leave the task waiting for an id forever
            err = str(e)
        with self.lock:
            sess.id_pending = False
            if sid and sess.session_id is None:
                sess.session_id = sid
            took = (self.clock.now() - t0).total_seconds()
            if sid:
                self.log.note(f"launch_detail slot={slot} task={task} session_id_found=true session_id={sid} "
                              f"after_s={took:.0f}")
            else:
                self.log.note(f"launch_detail slot={slot} task={task} session_id_found=false after_s={took:.0f}"
                              + (f" error={err[:200]}" if err else ""))
            self.cv.notify_all()

    def _do_send(self, slot: str, task: str, message: str, kind: str) -> None:
        sess = self.sessions[task]
        err = None
        with self.lock:     # routine mode: a probe may come before the id lookup ends (rework never does)
            while sess.session_id is None and sess.id_pending and not self.stop.is_set():
                self.cv.wait(0.5)
        if sess.session_id is None and not getattr(self.launcher, "accepts_no_session_id", False):
            err = "no session id recorded for this task's session"
        else:
            for _ in range(2):
                try:
                    self.launcher.send(slot, task, sess.session_id, message, kind)
                    err = None
                    break
                except (LaunchError, OSError, subprocess.SubprocessError) as e:
                    err = str(e)
        with self.lock:
            sl = self.slots[slot]
            if err is None:
                self.log.emit("session_message", slot=slot, task=task, session_id=sess.session_id, kind=kind)
                if sl.task == task:
                    sl.since = self.clock.now()     # the timeout runs from the message
                return
            self.log.note(f"followup_failed slot={slot} task={task} kind={kind} session={sess.session_id} "
                          f"error={err[:300]}")
            if kind == "rework":
                print(f"\n*** REWORK NOT DELIVERED: task {task} (session {sess.session_id}) on {slot}; task "
                      f"abandoned. Error: {err[:200]}\n", flush=True)
                self._abandon(task, "follow-up not delivered")
            elif sess.status == "probe":
                sess.status = "submitted"
            if sl.task == task:
                self._free(sl)

    def _stop_session(self, sess: Session) -> None:
        try:
            if self.launcher is not None:
                self.launcher.stop(sess.session_id)
        except Exception as e:  # best effort
            self.log.note(f"stop of session {sess.session_id} failed: {str(e)[:200]}")

    def _dispatch_loop(self) -> None:
        while not self.stop.is_set():
            self.dispatch_once()
            with self.lock:
                self.cv.wait(0.2)

    # ------------------------------------------------------------------ watcher
    def _task_of(self, branch: str, sha: str, main: str) -> str | None:
        if branch.startswith(self.pfx):
            return branch[len(self.pfx):]
        if not self.cfg.repo.accept_other_claude_branches:
            return None
        for c in self.repo.out("rev-list", sha, "^" + main).split():
            m = READY_ID_RE.match(self.repo.message(c))
            if m:
                return m.group(1)
        return None

    def poll(self) -> None:
        self._fetch()
        refs = self.repo.refs("refs/remotes/origin/claude/")
        main = self.repo.sha("origin/main")
        for ref, sha in sorted(refs.items()):
            branch = ref.removeprefix("refs/remotes/origin/")
            if self.branch_heads.get(branch) == sha:
                continue
            first = branch not in self.branch_heads
            task = self._task_of(branch, sha, main)
            if task is None:
                if branch not in self.unattributed:
                    self.unattributed.add(branch)
                    self.log.note(f"unattributed branch {branch}: no task id in its name or in a READY: commit")
                continue
            if first and not branch.startswith(self.pfx):
                self.log.note(f"task {task} pushed on branch {branch}, not {self.pfx}{task}")
            self._branch_seen(task, branch, sha, main, first)

    def _branch_seen(self, task: str, branch: str, sha: str, main: str, first: bool) -> None:
        prev = self.branch_heads.get(branch)
        self.branch_heads[branch] = sha
        sess = self.sessions.get(task)
        st = self.states.get(task)
        if first and st is None:
            if self.phase != "window":
                self.log.note(f"branch {branch} of task {task} first seen outside the window; ignored")
                st = TaskState(task, branch, "unknown", last_head=main)
                self.states[task] = st
                return
            if sess is None:
                self.log.note(f"branch {branch}: task {task} was never launched in this window; ignored")
                return
            st = TaskState(task, branch, sess.last_slot, last_head=main)
            self.states[task] = st
            self.log.emit("claim", worker=sess.last_slot, task=task, branch=branch)
        if st is None or sess is None:
            return
        new = self.repo.out("rev-list", sha, *(["^" + prev] if prev else []), "^origin/main").split()
        msgs = [(c, self.repo.message(c)) for c in new]
        if self.cfg.run.probe_followup and any(PROBE_RE.match(m) for _, m in msgs):
            self._probe_ack(task, sess)
        ready = [c for c, m in msgs if READY_RE.match(m)]
        if not ready:
            return
        head = ready[0]  # newest READY: commit in this push
        if self.phase != "window":
            self.log.note(f"submission of task {task} head {head[:12]} after window end; not queued")
            return
        with self.lock:
            if sess.status == "abandoned":
                self.log.note(f"READY for abandoned task {task} head {head[:12]} (session {sess.session_id}); "
                              "ignored")
                return
            worker = sess.slot or sess.last_slot
            st.branch = branch
            self._submit(st, head, worker)
            sl = self.slots.get(sess.slot) if sess.slot else None
            if self.cfg.run.probe_followup and not self.probe_done and sl is not None:
                self.probe_done = True
                sess.status = "probe"
                sl.kind, sl.since = "probe", self.clock.now()
                self._run_thread(f"probe-{sl.id}-{task}", self._do_send, sl.id, task,
                                 probe_message(self.cfg, task), "probe")
            else:
                sess.status = "submitted"
                if sl is not None:
                    self._free(sl)

    def _probe_ack(self, task: str, sess: Session) -> None:
        with self.lock:
            sl = self.slots.get(sess.slot) if sess.slot else None
            since = sl.since if sl is not None and sl.kind == "probe" else None
            delay = (self.clock.now() - since).total_seconds() if since else None
            self.log.note(f"probe_ack task={task} session={sess.session_id} delay_s="
                          f"{'%.0f' % delay if delay is not None else 'na'}")
            if sess.status == "probe":
                sess.status = "submitted"
            if sl is not None and sl.kind == "probe":
                self._free(sl)

    def _submit(self, st: TaskState, head: str, worker: str) -> None:
        base = self.repo.out("merge-base", "origin/main", head)
        lines, files = self._diffstat(base, head)
        with self.lock:
            others = {t: f for t, f in self.inflight.items() if t != st.task}
            k = len(others)
            fs = set(files)
            m = sum(1 for f in others.values() if fs & set(f))
            st.submits += 1
            st.files = files
            st.last_head = head
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

    def _take_review(self, rid: str) -> Change | None:
        """(Lock held.) The first queued change that is prepared and whose task is not under review by another
        reviewer; removed from the queue and recorded as ``rid``'s. Unprepared changes are skipped, not waited on."""
        if not self._review_allowed():
            return None
        busy_tasks = {c.task for c in self.reviewing.values()}
        for i, ch in enumerate(self.review_q):
            if ch.task in busy_tasks or not ch.prepared.is_set():
                continue
            del self.review_q[i]
            self.reviewing[rid] = ch
            return ch
        return None

    def _requeue(self, ch: Change) -> None:
        """(Lock held.) Put a change whose review failed back at the front, unless a newer head of its task has
        been queued meanwhile (the supersede rule of ``_submit``)."""
        newer = next((c for c in self.review_q if c.task == ch.task), None)
        if newer is not None:
            self.log.note(f"task {ch.task}: head {ch.head[:12]} superseded in review queue by {newer.head[:12]}")
            return
        self.review_q.appendleft(ch)

    def _review_loop(self, rid: str = "r1") -> None:
        """One reviewer thread (``rid`` = r1..rK). Its reviewer_busy / reviewer_idle pair brackets each stretch
        in which it holds a change; its downtime, retry and back-off are its own."""
        busy = False
        rc = self.cfg.reviewer
        while True:
            with self.lock:
                ch = None if self.stop.is_set() else self._take_review(rid)
                if ch is None and busy:
                    self.log.emit("reviewer_idle", reviewer=rid)
                    busy = False
                while ch is None and not self.stop.is_set():
                    self.cv.wait(0.2)
                    ch = None if self.stop.is_set() else self._take_review(rid)
                if ch is None:
                    return
                depth = len(self.review_q)
                if not busy:
                    self.log.emit("reviewer_busy", reviewer=rid)
                    busy = True
            packet = self._packet(ch)
            packet.reviewer_id = rid
            result = None
            for attempt in range(1 + rc.retries):
                self.log.emit("review_start", task=ch.task, head=ch.head, queue_depth=depth, reviewer=rid)
                t0 = self.clock.now()
                try:
                    result = self.reviewer.review(packet)
                except Exception as e:  # ReviewError or anything unexpected
                    msg = str(e) if isinstance(e, ReviewError) else f"{type(e).__name__}: {e}"
                    self.log.emit("review_error", task=ch.task, head=ch.head, error=msg[:1000], reviewer=rid)
                    with self.lock:
                        self._down_since.setdefault(rid, t0)
                    continue
                dur = (self.clock.now() - t0).total_seconds()
                self.log.emit("review_end", task=ch.task, head=ch.head, verdict=result.verdict,
                              reason=result.reason, tokens_in=result.tokens_in,
                              tokens_out=result.tokens_out, duration_s=round(dur, 3), reviewer=rid)
                self._end_downtime(rid)
                break
            if result is None:
                # Retried and still failing: put it back at the front (another reviewer may take it) and back off.
                with self.lock:
                    self.reviewing.pop(rid, None)
                    self._requeue(ch)
                    self.log.emit("reviewer_idle", reviewer=rid)
                    busy = False
                    self.cv.notify_all()
                self._check_downtime()
                if self.clock.sleep(rc.retry_backoff_s, self.stop):
                    return
                continue
            with self.lock:
                self.reviewing.pop(rid, None)
                if result.verdict == "approve":
                    self.merge_q.append(ch)        # approvals enter the merge queue in the order reviews finish
                self.cv.notify_all()
            if result.verdict != "approve":
                self.bounce(ch, "review", result.reason)

    def _end_downtime(self, rid: str) -> None:
        with self.lock:
            since = self._down_since.pop(rid, None)
            if since is None:
                return
            d = (self.clock.now() - since).total_seconds()
            self.downtime_by[rid] = self.downtime_by.get(rid, 0.0) + d
            self.downtime_s += d
        self._check_downtime()

    def reviewer_downtime_s(self) -> float:
        """Summed reviewer downtime so far (closed stretches plus open ones), over all K reviewers."""
        with self.lock:
            now = self.clock.now()
            return self.downtime_s + sum((now - t).total_seconds() for t in self._down_since.values())

    def _check_downtime(self) -> None:
        """VOID when the reviewers' combined capacity was down more than max_downtime_min: summed downtime / K."""
        total = self.reviewer_downtime_s() / self.n_reviewers
        limit = self.cfg.reviewer.max_downtime_min * 60
        with self.lock:
            if total > limit and not any("reviewer downtime" in v for v in self.void_reasons):
                reason = (f"reviewer downtime {total / 60:.1f} min > {self.cfg.reviewer.max_downtime_min} min"
                          + (f" (summed over {self.n_reviewers} reviewers / {self.n_reviewers})"
                             if self.n_reviewers > 1 else ""))
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
        return rework_message(self.cfg, ch.task, ch.attempt_no, ch.head, cause, CAUSE_TEXT[cause], detail)

    def bounce(self, ch: Change, cause: str, detail: str = "") -> None:
        """Log the bounce and queue the rework for the task's own session. Nothing is pushed to its branch;
        the message goes out as a follow-up when a slot frees (dispatcher)."""
        text = self.feedback_text(ch, cause, detail)
        with self.lock:
            self.log.emit("bounce", task=ch.task, head=ch.head, cause=cause)
            sess = self.sessions.get(ch.task)
            if sess is None:
                self.log.note(f"task {ch.task}: bounce {cause} but no session is known; rework not sent")
                return
            if sess.status == "abandoned":
                self.log.note(f"task {ch.task}: bounce {cause} for an abandoned task; rework not sent")
                return
            if self.phase != "window":
                self.log.note(f"task {ch.task}: bounce {cause} after window end; rework not sent")
                return
            sess.feedback.clear()          # only the newest feedback matters
            sess.feedback.append(text)
            if sess.status != "probe":
                sess.status = "rework_queued"
            if ch.task not in self.rework_q:
                self.rework_q.append(ch.task)
            self.cv.notify_all()

    # ------------------------------------------------------------------ window
    def idle(self) -> bool:
        with self.lock:
            return (not self.prep_q and not self.review_q and not self.merge_q
                    and not self.reviewing and self.merging is None)

    def start_threads(self, dispatch: bool = False) -> None:
        loops = [("watcher", self._watch_loop), ("prep", self._prep_loop), ("merger", self._merge_loop)]
        if dispatch:
            loops.append(("dispatcher", self._dispatch_loop))
        for name, fn in loops:
            self._run_thread(name, fn)
        for rid in self.reviewer_ids:
            self._run_thread(f"reviewer-{rid}", self._review_loop, rid)

    def shutdown(self) -> None:
        self.stop.set()
        with self.lock:
            self.cv.notify_all()
        for t in list(self.threads):
            t.join(timeout=max(5.0, self.cfg.tests.timeout_s))

    def run_window(self, launcher=None) -> dict:
        rc = self.cfg.run
        if launcher is not None:
            self.launcher = launcher
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
        self.log.note(f"slots n={rc.n_workers} task_timeout_min={rc.task_timeout_min:g} "
                      f"task_budget_min={rc.task_budget_min:g}")
        self.log.note(f"reviewers n={self.n_reviewers} ids={','.join(self.reviewer_ids)}")
        self.start_threads(dispatch=True)
        self._open_scheduled_slots()

        self.clock.sleep_until(warm_end)
        self.log.note("warmup_end")
        self.clock.sleep_until(self.window_end)
        # Stop handing out work and accepting submissions; stop the sessions.
        with self.lock:
            self.phase = "grace"
            self.cv.notify_all()
        self.log.note("window_end")
        with self.lock:
            for sl in self.slots.values():
                if sl.task is not None:
                    sess = self.sessions.get(sl.task)
                    self.log.note(f"session_open_at_window_end slot={sl.id} task={sl.task} kind={sl.kind} "
                                  f"session={sess.session_id if sess else None}")
                    self._free(sl)
            if self.rework_q:
                self.log.note(f"rework_not_sent_at_window_end n={len(self.rework_q)} "
                              f"tasks={','.join(self.rework_q)}")
        self.launcher.stop_all(self)
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
            open_now = sorted(self.reviewing.items(), key=lambda kv: int(kv[0][1:]))
        for rid, open_ch in open_now:
            # Not counted by the analysis, and neither is that reviewer's busy time after it began (derive.py
            # grace-end correction, per reviewer).
            self.log.note(f"review_open_at_grace_end task={open_ch.task} head={open_ch.head} reviewer={rid}")
        self.log.note("grace_end")
        self.shutdown()
        self._check_claude_dir()
        self._check_downtime()
        with self.lock:
            now = self.clock.now()
            for rid in self.reviewer_ids:
                d = self.downtime_by.get(rid, 0.0)
                if rid in self._down_since:
                    d += (now - self._down_since[rid]).total_seconds()
                if d > 0:
                    self.log.note(f"reviewer_downtime reviewer={rid} s={d:.0f}")
        return self.write_run_json()

    def _open_scheduled_slots(self) -> None:
        """All slots at window start, or, with ``[run] start_schedule`` (T1: [[0, 1], [30, 12]]), each group
        at its minute from a thread, so the window's own timing is never held up."""
        sched = self.cfg.run.start_schedule
        ids_all = [f"s{i}" for i in range(1, self.cfg.run.n_workers + 1)]
        if not sched:
            self.open_slots(ids_all)
            return
        stages, started = [], 0
        for minute, upto in sched:
            ids = ids_all[started:int(upto)]
            started = int(upto)
            stages.append((self.window_start + dt.timedelta(minutes=float(minute)), minute, ids))

        def run_stages():
            for at, minute, ids in stages:
                if self.clock.sleep_until(at, self.stop):
                    return
                if self.phase != "window":
                    self.log.note(f"start_schedule: minute {minute} slots {ids} not opened (window over)")
                    return
                self.log.note(f"start_schedule minute={minute} slots={','.join(ids)}")
                self.open_slots(ids)
        self._run_thread("slot-schedule", run_stages)

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
        if self.cfg.run.phase:
            notes.append(f"phase={self.cfg.run.phase}")
        notes.append("worker_model_design=session-per-task")
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
            "n_reviewers": self.n_reviewers,
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
