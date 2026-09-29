# Fleet-sweep harness (study 2 orchestrator)

This runs one window of the study in PLAN-v4.md (protocol from PLAN-v3.md; v4.2 changes in PLAN-v4
section 7; K parallel reviewers and the v6 phases from PLAN-v6 sections 5-6): it resets the sandbox repo, watches
the workers' git branches, feeds K reviewers (`[reviewer] parallel`, default 1) one change each at a time from one
FIFO queue, runs the serial merge queue, and writes `runs/<run_id>/events.jsonl` and `run.json` in the
format SCHEMA.md sets out. Workers are **one cloud session per task** in N slots (below). It also runs the offline reviewer calibration through the live review path
(`calibrate`) and abort rule 1 (throttling, PLAN-v6's revised rule) on a T1 / T1b log (`throttle`). It needs only the Python 3.11 standard library and git 2.40 or later. pytest is used for its
own tests and for the sandbox's test suites.

Dry runs, and every test, use simulated sessions (`SimCloudLauncher`) and a simulated reviewer against a
local bare repo. **Nothing here calls `claude`, an Anthropic API, GitHub, or a cloud session unless you run
`reset`, `run`, `routines-setup`, `validate-tasks` or `calibrate` with a real config.** There is no default real config:
those commands need `--config` with one of the phase configs (below).

```sh
cd analysis/fleet-sweep/harness
python3.11 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m pytest -q                      # about 3 min
.venv/bin/python -m harness dry-run --run-id dry-1 # a 90-min window in about 1 min (toy sandbox, 4 slots)
.venv/bin/python -m harness status --config config.dryrun.toml --run-id dry-1
.venv/bin/python -m harness throttle runs/<T1b run id>    # abort rule 1 on a T1 / T1b log (no API)
```

## How it works

```
dispatcher --claude --cloud (task prompt)--> session (1 per task, in a slot) --git push--> remote
    ^   \--claude -p ... --cloud <cse id> (rework)--^                                             |
    |                                                                             git fetch |
    +-- rework queue <-- bounce <-- merge queue <-- approve -- reviewers <-- review queue <-- watcher
                                         |  (serial)          r1..rK, one change each, FIFO)
                                         +--> main
```

## Worker model: one cloud session per task

Product facts this rests on (Claude Code 2.1.283 docs): `claude --cloud "<prompt>"` creates a cloud session
that clones the current directory's GitHub remote at the current branch and does not return at once; it
takes `--model`, `--effort`, `--name`. A cloud session can push only to its current working branch
(`claude/`-prefixed branches are accepted); fetching works. `claude -p "<message>" --cloud <session-id>`
queues a message into a running session and exits without waiting. Idle timeout and concurrency limits are
undocumented; cloud sessions share rate limits with the whole account. What the launch prints, and how to
detach from it, is **UNVERIFIED** (T0, below).

- **Slots.** `[run] n_workers` is the number of slots, N. Each slot runs one session at a time, so at most
  N sessions are active. A slot opens at window start (or at its `start_schedule` minute) and is logged as
  `worker_start` (worker = `s1`..`sN`). `slot_busy` / `slot_idle` mark a session occupying it.
- **Hand-out** (`dispatcher` thread). The harness hands tasks to free slots in the window's seeded order
  (`reset.json` `task_order`, the TASKS.json committed at reset; T0's `first_task` first). No claims, no
  races, no scanning of TASKS.json by workers. Queued rework goes first (below), then the next task.
- **Launch (command mode, `[launcher] mode = "command"`, all phase configs from 2026-09-29).** Each task is
  launched as `script -q /dev/null env CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1 claude --ref main --cloud
  "<prompt>" --model <m> --name <n>` from `launch_dir`. Plain `claude --cloud` uploads a local bundle with no remote,
  so the session cannot push (anthropics/claude-code#81776); `--ref main` alone refuses, because the GitHub App
  preflight returns null for this repo; with both, the session clones the sandbox from GitHub and can push
  (verified 2026-09-29, CLI 2.1.284). The session id comes from the CLI's `View: …/session_…` line, and follow-ups
  use `claude -p "<msg>" --cloud <session_id>` as in T0. These sessions draw cloud-session credits; confirm with
  both meters at the next T0.
- **Launch (routine mode, `[launcher] mode = "routine"`, used 2026-09-28 only; kept in the code).** Routine runs
  bill plan usage, not cloud-session credits, which is why the configs moved to command mode. A
  `claude --cloud` session gets an uploaded copy of the repo with no remote and cannot push (git proxy 403,
  T0 2026-09-28). A Claude Code **routine** whose source is the sandbox's GitHub URL gets a real clone with
  origin and can push `claude/*` branches. So each slot `sK` has one routine, `carnot-study2-<phase>-sK`,
  created disabled by `python -m harness routines-setup --config <phase config>` (ids in a private state file,
  default `routines-state.json` beside `[repo] work_dir`, never in this repo). Launching a task **re-arms** the
  slot's routine: a partial update setting the rendered prompt (`prompts/worker.md`) as its first event,
  `enabled: true` and `run_once_at` = now + `lead_s` (30 s). One-off runs are exempt from the daily routine
  cap; each routine allows 30 fires per hour (re-arms included; the harness keeps to
  `max_updates_per_hour`). The harness cannot call the routines API itself: each call is a local
  `claude -p '<instruction>' --model claude-haiku-4-5 --tools RemoteTrigger ... --output-format json`
  (`[launcher.routine] runner`, ~9 s) told to make exactly one RemoteTrigger call with the embedded JSON and
  return the raw tool output (`harness/routines.py`). The returned trigger is checked (id, `enabled`,
  `run_once_at`, the event uuid and prompt); a mismatch or a non-200 disarms the routine and counts as a failed
  launch (`session_launch_failed` note, retried once, `attempt_no` as before). `session_launch` is logged when
  the re-arm is confirmed, with session_id null. The run starts 40-75 s after `run_once_at`; the harness then
  polls `list_runs` (from `run_once_at` + 40 s, every 20 s, only while that slot waits) for the first run created
  after `run_once_at` and logs its id (`cse_...`) in a `launch_detail ... session_id_found=true` note. Rework for
  a task waits in the queue until its id is known. **Start-up time (session_launch -> claim) therefore includes
  the lead and the routine firing delay** (SCHEMA.md). At window end, and whenever `run` exits, every slot
  routine is disabled (`routine_disabled` / `routine_disable_failed` notes; failures are printed so the operator
  can disable them by hand).
- **Pinned CLI and launch failures** (after the T1 incident, 2026-09-28 17:49 UTC: an auto-update replaced the
  `claude` binary mid-window, every process start failed with `No such file or directory`, and one slot
  abandoned ~15 tasks in a second and used up its routine's hourly budget).
  - `run` pins the CLI before the window (`[run] pin_cli = true`, the default): it resolves `claude` on PATH
    (through symlinks), copies it to `<[launcher] prompt_dir>/bin/claude-<version>` (private; refused inside
    this repo; an existing copy of the same version is reused, old ones are left), checks `<copy> --version`,
    logs `cli_pinned path=… version=… source=… reused=…`, and uses the copy in place of the bare `claude` in the
    routine runner, `followup_command`, the reviewer `command` and `launch_command`. `pin_cli = false`: `claude`
    from PATH, as before (a `cli_pinned disabled` note).
  - A CLI process that cannot start (OSError, e.g. the binary missing) is retried 3 times, 5 s apart, in the
    routine runner, the follow-up and the reviewer call. A re-arm whose runner never started gives back the
    routine's rate-budget entry (`routine_budget_refund` note).
  - After a task's launch fails (all attempts) its slot takes no work for `[run] launch_fail_backoff_s` (60 s;
    `slot_backoff` note). After `max_consecutive_launch_failures` (3) failed tasks in a row the slot is marked
    down for the rest of the window: `worker_down` (worker = slot, reason `launch_failures n=… last_error=…`),
    a console message, no more dispatch to it. A successful launch resets the count. If launches fail on
    `launch_fail_breaker_slots` (3) different slots within `launch_fail_breaker_window_s` (60 s), all dispatch
    pauses for the back-off (`dispatch_paused reason=launch_failures …`, then `dispatch_resumed`).
  - A rework follow-up that still fails is logged (`followup_failed … session=… error=…`, a console message)
    and the task is abandoned, as before.
- **Launch (`mode = "command"` / `"manual"`, kept for reference; its sessions cannot push).** From the launch clone (`[launcher] launch_dir`: a local clone whose origin is the sandbox
  GitHub repo, on branch `main`, synced to `origin/main` before each launch):
  `claude --cloud "<prompt>" --model claude-haiku-4-5 --name <run_id>-s<slot>-<task>`. The prompt
  (`prompts/worker.md`) holds the task text and acceptance criteria and tells the session: create
  `claude/task-<id>` from `origin/main` and push it at once, work only on it, run the visible tests, commit
  and push, make the final commit message start with `READY: <id>`, then stop; never ask questions; if
  `main` has moved, merge `origin/main` into the branch (never rebase or force-push); time budget 20 min.
  `session_launch` is logged when the launch returns or detaches, with the session id if one was seen.
  A launch that fails outright is retried once (`launch_retries`), then the task is abandoned.
- **Submit.** A commit whose message starts `READY:` on the task's branch, seen by the watcher. On READY the
  slot is freed and the next piece of work starts on it at once.
- **Rework.** When a change bounces (any cause) the harness never pushes to the session's branch. It queues
  the rework; when a slot frees, it sends the feedback (`prompts/rework.md`, cause wording as before, never a
  hidden-test name) as a follow-up message to **that task's own session**
  (`claude -p "<message>" --cloud <session_id>`; in routine mode the run's `cse_...` id, verified to reach
  routine sessions 2026-09-28), logged as `session_message` kind `rework`. That slot is then
  the session's until its next READY. The session need not be on the slot it started on.
- **Timeouts.** No READY within `task_timeout_min` (25) of the launch, or of a rework message: the session is
  abandoned (`session_timeout`, a `task_abandoned` note, the optional `stop_command`), its slot freed, and the
  task is never re-launched in the window. A later READY from it is noted and ignored.
- **Window end.** Busy slots are freed (`session_open_at_window_end` notes), queued rework is not sent
  (`rework_not_sent_at_window_end`), and the operator (or `stop_command`) stops the open sessions. Reviews and
  merges carry on through grace as before.
- **T0 probe.** With `[run] probe_followup = true` (T0 only) the first READY is followed by a probe message
  (kind `probe`) asking for an empty `PROBE: <id>` commit; the slot stays busy until it arrives (`probe_ack`
  note with the delay) or `probe_timeout_min` (10). This checks follow-up delivery even if nothing bounces.
- **Conflicts with main** are common (97 of 220 tasks conflict textually with at least one other, PLAN-v4
  section 7.7); the prompt and every rework message say to merge `origin/main` into the task branch.
- **Private files.** Rendered prompts, messages and the raw launch output hold task text; they are written
  only to `[launcher] prompt_dir` (default `session-prompts/` beside the task file), which must be outside
  this repo (the harness refuses otherwise).

**Watcher** (`orchestrator.poll`, every `poll_interval_s`). It fetches `main` and `claude/*`.
- A task's branch first seen is logged as `claim` (worker = the slot of its session): the session's first
  push. The task comes from the branch name `claude/task-<id>`, or, for another `claude/*` branch (a session
  that named its branch itself; UNVERIFIED, `accept_other_claude_branches = true`), from a `READY: <id>`
  commit on it, with a note. A branch of a task that was never launched in the window is noted and ignored.
- New `READY:` commits on a branch, not reachable from `main`, are logged as a `submit` (worker = the slot).
  If one push holds several, the newest is the submitted head. `attempt_no` counts submissions per task.
  `lines_changed` and `files` come from the diff against the merge base with `main`.
- When the last task in the window's list is handed to a slot, the dispatcher logs a `note`
  `tasks_exhausted n=<N>` straight after that slot's `slot_busy`: supply exhaustion is the end of the list.
- `k` is the number of *other* tasks in flight (submitted at least once, not merged). `m` is how
  many of those share a file with this change.
- Event times are the time the watcher saw the push, not git commit dates.

**Prep.** This thread runs the visible tests on each submitted head, for the record. It logs a `note` of the form `visible_pre task=… head=… passed=…`.

**Review queue.** A FIFO served by K reviewer threads `reviewer-r1..rK` (`[reviewer] parallel = K`, default 1 =
the serial reviewer of PLAN-v4/v5; PLAN-v6 section 6). Each thread takes the first queued change that has
finished its visible-test prep and whose task is not under review by another thread (an unprepared change is
skipped, not waited on; a newer head of a task under review waits for that review), so each reviewer reviews one
change at a time and no task is in two reviews at once. `queue_depth` counts the changes waiting, excluding every
change under review. Approvals enter the (serial) merge queue in the order reviews finish. Every reviewer event
carries `reviewer` (r1..rK, also at K = 1) and `run.json` carries `n_reviewers`. **Isolation:** each call gets its
own temporary directory (`mkdtemp`, prefix `fleet-review-<rid>-`) holding its prompt and its checkout, and runs
with `PYTEST_ADDOPTS=-p no:cacheprovider` and `PYTHONDONTWRITEBYTECODE=1`, so concurrent reviews share no working
tree and no pytest cache; all threads use the pinned CLI copy and the CLI-start retry applies per call. **The review job (PLAN-v4 section 7.3,
frozen before T1; `[reviewer] job = "checkout"`):** each call gets a fresh temporary checkout of the
exact submitted head as its working directory (`cwd = "{checkout}"`: an export of that commit's tree,
no `.git`, no other refs, with `strip_paths`, i.e. `.claude/` and `CLAUDE.md`, removed so a change
cannot alter the reviewer's settings or instructions), and a packet with the task text, acceptance
criteria, the files changed and the diff against the merge base, rendered through
`prompts/reviewer.md` and fed on stdin. The reviewer may read and search files and run the visible
suite in the checkout; the command's `{allowed_tools}` placeholder carries the allow-list
(`Read`, `Grep`, `Glob`, `Bash({python} -m pytest:*)`). The reviewer never sees the queue, other
changes, or hidden tests, and the prompt is the same for every review. The call's duration includes
exporting the checkout. `job = "diff"` is the superseded diff-only job (packet with the harness's
visible-test result, an empty temp dir, `prompts/reviewer-diff.md`); `run` refuses it. Live windows
and `calibrate` build the packet with the same function (`review.make_packet`).
- Output parsing: the first non-empty line must start with APPROVE or REQUEST_CHANGES. Case,
  markdown, and a `Verdict:` prefix are tolerated. If the first line has no verdict, the first line
  that starts with one is used. Anything else is a `review_error`. Unclear output is never read as
  approval.
- Errors: a failed call is retried once, straight away, by the same reviewer. If the retry also fails, the
  change goes back to the front of the queue (dropped instead if a newer head of its task is already queued),
  where any reviewer may take it, and the failing reviewer backs off for `retry_backoff_s`. Downtime is per
  reviewer: from its failure to its next successful review. The window is VOID when the reviewers' combined
  capacity was down more than `max_downtime_min` (10), i.e. summed downtime / K: a `note` starting `VOID:` and
  the same reason in `run.json` `notes`. At grace end each reviewer with downtime gets a
  `reviewer_downtime reviewer=… s=…` note.
- Each reviewer's `reviewer_busy`/`reviewer_idle` mark its stretches of back-to-back reviews, so V is reviews
  divided by busy hours (per reviewer: `derive.py`). All K reviewers continue through grace; a review still open
  at grace end is noted per reviewer (`review_open_at_grace_end task=… head=… reviewer=…`) and the analysis clips
  that reviewer's busy time only.

**Merge queue** (serial) for each approved head:
1. `hidden_pre`: that task's hidden tests on the exact approved head. A failure bounces
   `escaped_defect`.
2. `rebase`: a three-way merge (`git merge-tree`) of the change's net diff onto the current `main`,
   committed as one squash commit. A conflict bounces
   `rebase_conflict`.
3. `tests_post`: visible tests, plus the hidden tests of this task and of every task already merged
   in this window (`post_hidden_scope = "merged"`). A visible failure bounces `visible_fail`;
   otherwise a hidden failure bounces `integration_failure`.
4. `merge`: `main` is fast-forwarded to the squash commit.

Hidden tests are copied into a throwaway `git archive` export of the commit under test, run there,
and deleted. They are never staged, committed or pushed. A test checks every blob and path
reachable from every ref of the remote and of the orchestrator's clone. The rework message states
the cause in plain words. For the hidden-test causes it says only "Acceptance check failed", with no
test names and no output. For `visible_fail` it includes the tail of the visible test output,
since those tests are public; for `review`, the reviewer's reason.

**Window control** (`run_window`):
- The window starts; the log notes `window_start`, `task_supply n=<N>` (tasks in the window's list) and
  `slots n=… task_timeout_min=… task_budget_min=…`, then `warmup_end` after `warmup_min`.
- At `window_end`, no more work is handed out, busy slots are freed and the sessions are stopped.
  Branches and submissions seen after this point are logged only as `note` lines and are not queued.
- During grace, reviews of already-submitted changes (`grace_reviews = true`) and merges carry on.
  Real windows run the full grace period. Dry runs stop early once nothing is left.
- If a review is still running then, the log notes `review_open_at_grace_end task=… head=…`. That
  review finishes and is logged after `grace_end` (the validator warns); the analysis counts neither it
  nor its busy time (analysis README decision 20).
- Then `grace_end`, the threads stop, a `.claude/` diff check against the sandbox commit runs, and
  `run.json` is written.
- Nothing is marked censored; the analysis derives that.

**Reset** (`python -m harness reset`):
- Force-sets `main` on the remote to `base_ref` and deletes every `claude/*` branch.
- Commits the window's seeded task order as `TASKS.json` to `main` (with `[run] first_task` moved to the
  front). The file holds id, title, text and acceptance criteria, never tests or solutions. Sessions do
  not read it; it records the order the harness hands tasks out in.
- Writes `runs/<id>/reset.json` with the sandbox commit, the TASKS commit and the order. `run`
  reads it.

## Files

| Path | What |
|---|---|
| `harness/config.py` | config dataclasses; TOML loader; `PHASES` (the pre-registered shape of each phase, incl. slots, session timeout, T0's first task) and the phase check |
| `harness/clock.py` | virtual clock (time scale for dry runs); ISO-ms timestamps |
| `harness/schema.py` | SCHEMA.md as code; structural + semantic log validator |
| `harness/events.py` | JSONL event writer (refuses events that do not match the schema) |
| `harness/gitops.py` | git wrappers |
| `harness/reset.py` | reset |
| `harness/tasks.py` | task catalogue, seeded order, test execution in temp checkouts |
| `harness/review.py` | review packet (`make_packet`), verdict parser, command reviewer: checkout job, cwd, allow-list, stdin (UNVERIFIED template) |
| `harness/calibrate.py` | `calibrate`: reference solutions and mechanical mutants through the same `CommandReviewer`; writes the calibration-review log |
| `harness/throttle.py` | `throttle`: abort rule 1 (PLAN-v6): start-up and coding ratios, one slot vs twelve, Welch 90% interval, tolerance 1.25; the old activity measure reported beside it |
| `harness/orchestrator.py` | slots and sessions (dispatcher, timeouts, rework queue), watcher, prep, review queue, merge queue, bounces, window control, run.json |
| `harness/launchers.py` | per-task prompt and rework message rendering; routine, manual and command session launchers (launch, follow-up, session id lookup, disabling routines); the verified guards; the private prompt dir; the routine state file |
| `harness/routines.py` | RemoteTrigger calls through a `claude -p` runner: instruction, output parsing, `rearm` / `create` / `get` / `list_runs` / `disable` with validation |
| `harness/sim.py` | SimSession (one per task), SimCloudLauncher, SimReviewer, oracle |
| `harness/toygen.py` | toy sandbox + 10 toy task templates with hidden tests and reference patches |
| `harness/validate.py` | task validation: hidden fails on base, hidden and visible pass with reference |
| `harness/dryrun.py`, `harness/report.py`, `harness/cli.py` | dry-run wiring, event counts, CLI |
| `config.t0.toml`, `config.t1.toml`, `config.t2.toml`, `config.t1b.toml`, `config.sweep-n{1,12}-k{1,3}.toml` | one real config per phase (PLAN-v4 section 7.4, T0, PLAN-v6 section 5.1); they differ only in `[run]` and `[reviewer] parallel` |
| `config.dryrun.toml` | dry-run config |
| `prompts/reviewer.md` | the frozen review job's prompt (checkout job) |
| `prompts/reviewer-diff.md` | SUPERSEDED diff-only review prompt (`job = "diff"`) |
| `prompts/worker.md` | per-task session prompt (template; draft until T0, then pre-registered) |
| `prompts/rework.md` | follow-up message after a bounce (template; draft until T0) |
| `tests/` | pytest suite |

CLI: `python -m harness {reset,run,routines-setup,calibrate,throttle,dry-run,status,log,validate-tasks,validate-log,make-toy} --config FILE`.
`routines-setup` creates (or looks up and checks) the phase's N slot routines, disabled, and writes their ids to
the private state file; `--dry-run` prints the plan without any call.
`log` appends operator events (`meter`, `note`, `worker_down`, `worker_restart`; worker = slot id) to a
run's log and checks them against the schema.

## Task files the real sandbox must provide

- `task_file`: `{"tasks": [{"id": "001", "title": "...", "text": "...", "acceptance": ["...", ...]}]}`
- `hidden_tests_dir/<id>/test_*.py`: pytest files, run from the checkout root with
  `--import-mode=importlib`. Keep them outside the sandbox repo.
- `reference_dir/<id>.patch`: `git apply`-able patch against `base_ref`. `validate-tasks`, `calibrate`
  and SimSession use it; it is never shared.
- Test commands are `[tests] visible_cmd` and `hidden_cmd`. Point `[tests] python` at the sandbox's
  venv if it has dependencies.
- If the hidden tests use the visible suite's `tests/conftest.py` fixtures or import its helper
  module, set `hidden_subdir = "tests/_hidden_acceptance"` (the copy then sits under `tests/`, so
  pytest applies that conftest) and `hidden_pythonpath = ["tests", "."]` (so both
  `from helpers import` and `from tests.helpers import` resolve under `--import-mode=importlib`).
  The real sandbox needs both; without them 99 of 120 tasks fail validation.
- The tasks repo's `export_for_harness.py` writes this layout from its `tasks/T###/` directories.

## Dry run on the real sandbox

With `[sim] use_toy = false`, `dry-run` uses `[repo]` and `[tasks]` as configured instead of a toy
sandbox. The remote must be a local path (a bare clone of the sandbox); anything else is refused.
Because the work dirs then hold task texts and hidden tests, keep that config and its `output_dir`
in a private location (the tasks repo's gitignored `dryrun/`), never under this repo.
SimSession applies the reference patch; if it no longer applies to the current `main` it tries a
three-way apply, then (with `union_resolve = true`) keeps both sides of each conflict, as a worker
would for two additions at the same place. If it still cannot, or after `max_reworks` reworks, the session
goes silent and the harness's session timeout abandons the task.

Every merge-queue pass logs a `note` starting `mq_timing` with the real (wall-clock) seconds of
each step: `hidden_pre_s`, `rebase_s`, `post_export_s`, `visible_post_s`, `hidden_post_s` (with
`n_hidden_post`, the number of tasks whose hidden tests ran), `merge_s` and `total_s`. These are
real seconds even when the clock is accelerated, so they measure the merge-queue precondition.

## What is simulated in a dry run

- **SimCloudLauncher / SimSession** stand in for `claude --cloud` and `claude -p ... --cloud <id>`, one
  SimSession per task, against a local bare repo (one clone per slot, reused by whichever session runs there).
  - Launch returns at once with a session id. After a start-up delay (exp., mean `startup_mean_s`) the
    session pushes `claude/task-<id>` at `origin/main`, then works (exp. with rate λ, `rate_per_hour`) and
    pushes one `READY: <id>` commit, then waits.
  - A change is the task's reference patch, or, with `p_wrong`, a note file only (hidden tests fail).
  - Optional extras: `p_conflict` (a shared `SIM_HOTSPOT.txt`, textual conflicts), `p_semantic` (a shared
    constant: integration failures), `p_visible_break`, `p_stall` (never pushes READY: exercises the timeout).
  - A rework message wakes the session: after `rework_factor` of a fresh task's time it merges `origin/main`
    into its branch and rebuilds the change there (a merge commit, `READY:`). A probe makes it push `PROBE:`.
- **SimReviewer**: lognormal service time (`review_mean_s`, `review_cv`), false-reject rate,
  catch rate for wrong changes, and a crash rate that exercises retries.
  - It reads a ground-truth oracle, and the catch rate falls with queue depth
    (`catch_drop_per_depth`). This gives the escaped-defect analysis something to find. The real
    reviewer never sees the queue.
- **Toy sandbox**: `toylib`, which has a visible suite and 10 task templates, each adding a module
  that a hidden test imports.
  - With `n_toy_tasks > 10` the templates repeat under new module names. The dry-run config uses
    30, so a 90-minute window does not run out of tasks.
  - `validate-tasks` confirms every toy task fails on base and passes with its reference.
- **Time**: every logged time is virtual, `start + real elapsed × time_scale`. At scale 90 a
  90-minute window plays in about a minute. Test and git commands take real time and are scaled
  too, so at scale 90 a 0.4 s pytest run shows up as 36 s. That is realistic for CI, but it means
  dry-run timings are not a benchmark.

## UNVERIFIED settings (fill in after the product checks: PLAN-v4 section 7.3 and T0)

`python -m harness run` refuses to start while any template it needs has `verified = false` (reviewer:
`[reviewer] verified`; launcher in `mode = "command"`: `[launcher] verified` and `followup_verified`; in
`mode = "routine"`: `followup_verified`, and `verified` in every phase except t0, whose job is to check it; it
also needs a routine id for every slot, from `routines-setup`).

| Setting | Default | What to check |
|---|---|---|
| `[reviewer] command`, `allowed_tools` | `claude -p --model {model} --output-format json --allowedTools {allowed_tools} --no-session-persistence`, prompt on stdin, cwd the checkout; allow-list `Read,Grep,Glob,Bash({python} -m pytest:*)` | the Opus 5.5 model id; billed to the plan, not the credits; the allow-list syntax and that nothing outside it runs (no edits, web, MCP servers, user-level settings or hooks); no session persistence; exit code on a rate limit |
| `[reviewer] output_format` | `json` | the JSON field names (the parser expects `result` and `usage.input_tokens`/`output_tokens`) |
| `[run] worker_model`, `reviewer_model` | `claude-haiku-4-5`, `claude-opus-5-5` | the exact model id strings; Haiku 4.5 available for cloud sessions (`--cloud --model`) on the paying account, and the model the session actually ran (T0) |
| `[launcher] verified` (routine mode) | false | the whole routine path under the harness (T0 repeat): the re-arm through the runner and its validation, that Haiku reliably passes the JSON verbatim and echoes the raw output (a long prompt, ~10 KB of output), the firing delay, the session id lookup via `list_runs`, the branch pushed, the follow-up to a `cse_` id, disabling at window end |
| `[launcher.routine] runner` | the verified `claude -p ... --tools RemoteTrigger ... --output-format json` | verified 2026-09-28 by hand (~9 s). `--output-format stream-json --verbose` would let the parser read the real tool result instead of the model's copy (the parser already prefers one if present); unchecked |
| routine create (`routines-setup`) | `enabled: false`, `run_once_at` now + 1 h, `mcp_connections: []`, then `clear_mcp_connections: true` | that a routine can be created disabled (never fires), and the `get` / `list` input shapes (`{"action": "get", "trigger_id": ...}`, `{"action": "list"}`); `update`, `create` and `list_runs` shapes are verified |
| `[launcher] launch_command` | `claude --cloud {prompt} --model {model} --name {name}`, cwd the launch clone | that it works non-interactively from a script; that a long multi-line prompt as one argument is accepted; which branch/commit the session starts from (T0) |
| `[launcher] session_id_regex` | `(?P<session_id>session_[A-Za-z0-9]{8,})` | whether the launch prints a session id or URL at all, where, and its exact form (T0) |
| `[launcher] detach`, `detach_after_s`, `detach_signal` | `on_id`, 120 s, `none` | when the command can be left, and whether leaving it (or sending SIGINT/SIGTERM to it) stops the cloud session (T0) |
| `[launcher] followup_command`, `followup_stdin` | `claude -p {message} --cloud {session_id}` | that the message reaches the running (or idle, stopped-after-READY) session, how long it takes, the exit code on failure (T0 probe) |
| `[launcher] stop_command` | empty | whether any command stops or archives a session; until then the operator stops sessions by hand |
| `[repo] accept_other_claude_branches` | `true` | whether sessions use `claude/task-<id>` as told, or a name of their own (T0) |
| `[repo] remote_url`, `[tasks] *`, `[launcher] launch_dir`, `prompt_dir` | placeholders | the private sandbox repo, the local task, hidden-test and reference paths, the launch clone, the private prompt dir |
| `prompts/worker.md`, `prompts/rework.md` | draft | the final wording after T0, to be pre-registered |
| undocumented limits | - | idle timeout of a cloud session (a session waiting for rework may expire), concurrent-session limit (12 needed at T1), shared rate limits (throttling, abort rule 1) |

## Phase configs (PLAN-v4 section 7.4; PLAN-v6 sections 5.1 and 6.1)

| File | phase | kind | N (slots) | window (warm-up) | K reviewers | start schedule |
|---|---|---|---|---|---|---|
| `config.t0.toml` | t0 | trial | 1 | 45 min (0) | 1 | all at 0; `first_task = "T145"`, `probe_followup = true` |
| `config.t1.toml` | t1 | trial | 12 | 60 min (10) | 1 | `[[0, 1], [30, 12]]`: s1 at minute 0, s2-s12 at minute 30 |
| `config.t2.toml` | t2 | pilot | 1 | 60 min (10) | 1 | all at 0 (PLAN-v4/v5; v6 has no pilot) |
| `config.t1b.toml` | t1b | trial | 12 | 120 min (0) | 3 | `[[0, 1], [90, 12]]`: s1 alone for 90 min, then s1-s12 for 30 min |
| `config.sweep-n1-k1.toml` | sweep-n1-k1 | sweep | 1 | 45 min (5) | 1 | all at 0 |
| `config.sweep-n1-k3.toml` | sweep-n1-k3 | sweep | 1 | 45 min (5) | 3 | all at 0 |
| `config.sweep-n12-k1.toml` | sweep-n12-k1 | sweep | 12 | 45 min (5) | 1 | all at 0 |
| `config.sweep-n12-k3.toml` | sweep-n12-k3 | sweep | 12 | 45 min (5) | 3 | all at 0 |

The PLAN-v4/v5 sweep configs (`config.sweep-n1.toml`, `config.sweep-n12.toml`: 90 min, one reviewer) are
retired; the sweep's cell order is `v6.v6_order` (PLAN-v6 section 5.1). `run` refuses a config whose
`[reviewer] parallel` differs from its phase's K, and any sweep config with K other than 1 or 3. The K variants
of one fleet size share their slot routines (`config.routine_group`: `sweep-n12-k1` and `sweep-n12-k3` both use
`carnot-study2-sweep-n12-s<k>`), so `routines-setup` is needed once per N, not per cell.

All of them: grace 10, session timeout 25 min, time budget 20 min, Haiku 4.5 sessions, Opus 5.5 reviewer,
`base_ref = "sandbox-v1"`, the checkout review job, `launcher.mode = "routine"` (`verified = false` until the
T0 repeat; `[launcher.routine]` environment `env_01REHbnKNqfeaJdQr9VjHaX5`, source the sandbox's GitHub URL);
warm-up as in the table. They differ only in `[run]` and `[reviewer] parallel` (a test checks this). `run`
refuses a config whose kind, N, window, warm-up, grace, start schedule, session timeout / budget, first task,
probe, models, review job or K differ from its named phase (`config.PHASES`), prints them, and starts only when the operator types the
phase name (or passes `--yes`). With a `start_schedule`, later slots open from a thread at their minute, so
the window's own timing is never held up by the operator. T0 is a product check, not part of the pilot:
`derive.py --pilot` refuses a run whose `run.json` notes say `phase=t0`.

## Reviewer calibration (PLAN-v4 section 7.3; abort rule 4)

```sh
python -m harness calibrate --config config.t2.toml --tasks @ids.txt --variants reference,mutant \
    --out ../analysis/calibration.jsonl --job v2-checkout [--parallel 4] \
    [--mutant-dir <private dir>] [--detail-out <private file>]
```

- Each task's reference patch ("reference", expected APPROVE) and a mechanically broken copy ("mutant",
  expected REQUEST_CHANGES) are committed on `base_ref` in the orchestrator's clone (never pushed) and sent
  through the same `CommandReviewer`, prompt, packet builder and checkout job as a live window.
- Mutants: one comparison or boolean inverted, one `return` made `return None`, one hunk dropped, or one new
  file dropped, tried in a seeded order; kept only if it applies and the task's hidden tests fail on it.
  They are written to `--mutant-dir` (default: `mutants/` beside `[tasks] reference_dir`, i.e. the private
  tasks repo's `dryrun/export/mutants`) and reused on reruns. The command refuses a mutant or detail path
  inside this repo.
- One row per review is appended to the calibration-review log (analysis/README.md), which
  `predict.py --calibration` reads for abort rule 4; rows already in the file are skipped, so a run can be
  resumed. Reasons go only to `--detail-out` (private), never to the public log.
- `duration_s` covers the reviewer call(s) including an immediate retry. V from calibration uses call
  durations and verdicts, never queueing. Every row records `parallel`; the summary gives `mean_duration_s`.
- **Contention check (PLAN-v6 section 6.10, free):** the same reference and mutant items at `--parallel 1` and
  `--parallel 3`, under two job labels (`--job v6-par1`, `--job v6-par3`; the mutants are reused from
  `--mutant-dir`), then compare the two jobs' mean `duration_s`. The v6 simulation assumed no contention
  (20% per extra reviewer is harmless to SCALE but raises rule 4's flag rate).
- Refuses to run while `[reviewer] verified = false`.

## Throttling on T1b (PLAN-v6 section 5.4; abort rule 1, revised)

`python -m harness throttle runs/<T1b> [--exclude s2,s3 | --exclude none] [--split-min 90] [--json out.json]`.
For every task launched in the one-slot phase (before the second `start_schedule` minute) and in the
twelve-slot phase: **start-up** = branch first pushed (`claim`) minus the routine's `run_once_at` (from the task's
`launch_detail` note; its `session_launch` if missing), and **coding** = first READY minus `claim`. The statistic
is the ratio of geometric means (twelve slots over one) with a Welch 90% interval on the log scale; tolerance
1.25. **STOP** if the start-up interval lies wholly above 1.25, **CLEAR** if wholly below, else **INCONCLUSIVE**
(proceed; report the ratio beside SCALE). A coding interval wholly above 1.25 is a flag, not a stop. Slots lost to
operator-logged failures unrelated to the service are excluded first: `--exclude s2,s3`, or by default every slot
with an operator `worker_down` (reason starting `operator`); `--exclude none` keeps all. Pure Python (the same
statistic as `analysis/v6.py` `throttle_v6`). On T1 (`runs/T1-2026-09-28`, s2 and s3 excluded) it reads start-up
1.13 (90% 0.98-1.31), coding 1.16 (0.87-1.54): INCONCLUSIVE.

Reported beside it (no longer the rule): the PLAN-v4 activity measure (session launches + READY per slot-minute,
one-slot phase against twelve-slot phase, the old 0.8 threshold's reading, an approximate interval, the median
launch-to-first-READY minutes, `usage` tokens if ever logged) and the operator's `meter` and `plan_usage`
readings.

## T0 operator checklist (1 slot, 20 min, task T145)

T0 settles the routine launch path before T1 (the first T0, 2026-09-28, settled the follow-up and showed that
`claude --cloud` sessions cannot push). It runs with `launcher.mode = "routine"` and `verified = false`, which
`run` accepts for phase t0 only. Nothing in T0 enters the pilot.

Before: fill in `[repo]`, `[tasks]`, `[tests]` and `[launcher] prompt_dir` (private) in `config.t0.toml` as in
the other phase files; the reviewer must be verified (PLAN-v4 section 7.3). Read the credit meter and the
plan-usage page (M0).

1. `python -m harness routines-setup --config config.t0.toml --dry-run`, then without `--dry-run`: creates
   `carnot-study2-t0-s1`, disabled, and prints the state file. Check at claude.ai/code/routines that it exists
   and is off (nothing fires until `run` re-arms it).
2. `python -m harness reset --config config.t0.toml --run-id <T0> --seed <s>`: check `task_order[0]` is
   `T145` and `branches_deleted`.
3. `python -m harness run --config config.t0.toml --run-id <T0>`; type `t0`. The harness re-arms s1's routine
   with T145's prompt and `run_once_at` 30 s ahead. Record (one `python -m harness log --config config.t0.toml
   --run-id <T0> --type note --field text="t0 <item>=<value>"` per item):
   - **rearm_s**: how long the re-arm took (the raw runner outputs are in the private prompt dir,
     `routine-*-rearm.log`), and whether Haiku passed the JSON verbatim (a failure is a `session_launch_failed`
     note naming the mismatch);
   - **fire_delay_s**: `run_once_at` (first `launch_detail` note) to the run's `created_at` (routine page);
   - **session_id**: the `launch_detail ... session_id_found=true session_id=cse_...` note, and that it is the
     run shown on the routine page;
   - **start_ref / branch**: the session cloned `main` at the TASKS commit and pushed `claude/task-T145` (the
     harness logs `claim`); no push was refused;
   - **model**: the model the session ran (Haiku 4.5?); **questions**: whether it asked anything or stopped;
   - **ready**: the `READY: T145` push and the harness's `submit` within one poll.
4. After the first READY the harness sends the probe follow-up to the `cse_` id; it notes `probe_ack ...
   delay_s=` when the `PROBE: T145` commit arrives (or `probe_timeout` after 10 min).
5. If the change bounces, check the rework message arrives and the session pushes a new `READY:`.
6. With the slot free the harness re-arms the routine for the next task; let it run to the window end. At
   `WINDOW END` the harness disables the routine (`routine_disabled` note; anything listed under
   `ROUTINES NOT DISABLED` must be disabled by hand). Stop / archive the open sessions by hand.
7. After: `validate-log`, `status`; meter and plan-usage readings (M1); the per-session cost if visible. Then
   set `[launcher] verified = true` in all five phase files, settle the prompts' final wording and
   pre-register it. Before T1: `routines-setup` with `config.t1.toml` (and later each sweep config).

## Operator checklist for a real window

Before T1:
1. Set `[repo]`, `[tasks]`, `[tests]` and `[launcher] launch_dir` / `prompt_dir` in all phase configs
   (the same values). Run `python -m harness validate-tasks --config config.sweep-n12-k3.toml` against the GitHub
   remote; 220/220.
2. The CLI check (PLAN-v4 section 7.3): fill in the UNVERIFIED reviewer template and set
   `verified = true`, or the harness refuses.
3. T0 (above): settles the routine launcher (`verified = true` afterwards). Then `routines-setup` once per
   phase config (the routines are per phase: `carnot-study2-<phase>-s<k>`; the sweep's K variants of one N
   share them, so once for `sweep-n1-*` and once for `sweep-n12-*`).
4. Calibrate the frozen review job (`calibrate`, above) against the design λ (target V 13.6-16 per busy hour).
5. Commit the harness and push it with the pre-registration. `run.json` records `harness_commit`,
   with `-dirty` if the harness has uncommitted changes.

T1 (`config.t1.toml`): `reset`, then `run ... --meter-start <M0>`; slot s1 opens at minute 0 and s2-s12 at
minute 30. Log `plan_usage` notes before, at minutes 25, 35 and 55,
and after (`python -m harness log --config config.t1.toml --run-id <T1> --type note --field text="plan_usage pct=37 src=..."`).
After: meter M1, then `python -m harness throttle runs/<T1>`.

T1b (`config.t1b.toml`, K = 3): as T1, with s1 alone for 90 min and s2-s12 from minute 90 to 120; then
`python -m harness throttle runs/<T1b>` (with `--exclude` for any slot lost to an operator-logged failure).

For each sweep window (`config.sweep-n{1,12}-k{1,3}.toml`, in the order of PLAN-v6 section 5.1, `v6.v6_order`;
T2 windows under PLAN-v4/v5 used `config.t2.toml`):
1. Choose `run_id` (e.g. `2026-10-02-N12-r1`) and a seed; the config fixes N.
2. **Meter before:** read the credits meter.
3. `python -m harness reset --config <phase config> --run-id <id> --seed <seed>`. Check that the
   printed `sandbox_commit` is the frozen tag and `branches_deleted` looks right.
4. `python -m harness run --config <phase config> --run-id <id> --meter-start <credits>`. Check the
   printed phase, kind, N, window, session timeout and models, and type the phase name to start. The harness
   launches one session per task (re-arming the slot routines) and sends rework as follow-ups. Keep the terminal open; it logs every event.
5. During the window:
   - The harness marks a slot down itself after 3 failed launches in a row (`worker_down`, printed as
     `SLOT sK DOWN`); it stays down for the window. Otherwise, a slot whose sessions keep failing to launch for
     more than 10 minutes: log `worker_down` / `worker_restart` for it, e.g. `python -m harness log --config <phase config> --run-id <id> --type worker_down --field worker=s3 --field reason="..."`.
     Session timeouts are handled by the harness and are not restarts.
   - More than 2 restarts of a slot voids the window. Record that as a `note` starting `VOID:`.
6. At "WINDOW END" the harness disables the slot routines (disable any it lists as failed by hand); stop
   every open session. The harness hands out no more work and
   finishes reviews and merges through the grace period.
7. **Meter after:** `python -m harness log --config <phase config> --run-id <id> --type meter --field credits_left_usd=<x> --field source=operator`.
8. `python -m harness validate-log runs/<id>`, then `python -m harness status --config <phase config> --run-id <id>`.
   Check `notes` in `run.json` for `VOID:`. A voided window is rerun under a new id, never spliced.
9. Before the next window, check that the predicted balance after the rest of the chosen design stays above
   $50 (PLAN-v4 section 7.2).

## Decisions made where PLAN-v3 / SCHEMA.md were silent

These are listed in the build reports and should be pre-registered or overruled:

1. *Retired (one session per task, decision 22).* Claims carried an empty `CLAIM:` commit with a `Worker:`
   trailer.
2. *Retired (decision 22).* Race markers `claude/race-<id>-<worker>`.
3. **Event times**: observation time at the watcher's poll, not git commit time.
4. **k excludes the change itself.** A bounced change stays in flight until it is merged.
5. **Several `READY:` commits in one push** make one submission, at the newest.
6. **Rebase is a squash**: the change's net diff is three-way merged onto `main`, so worker merge
   commits do not re-conflict. `main` gets one commit per task.
7. **`tests_post` hidden scope**: this task plus every task merged earlier in the window. Without
   the earlier tasks, an integration failure could only come from this task's own tests.
8. **Grace**: reviews continue during grace, so changes submitted before the end can still finish.
   Submissions after the window end are not queued; they appear as `note` lines only.
9. **Reviewer failures**: an immediate retry by the same reviewer. After two failures the change is re-queued
   at the front (for any reviewer) and that reviewer backs off. Downtime, per reviewer, runs from its first
   failure to its next success; VOID uses the summed downtime / K.
10. **run.json**: only the SCHEMA.md keys (with `n_reviewers` since PLAN-v6). The reset TASKS commit and any VOID reasons go into
    `notes`.
11. **Superseded submissions**: a newer `READY:` for a task whose older head is still waiting for
    review replaces it in place, with a `note`. (With one session per task this happens only if a session
    pushes a second READY unasked.)
12. **Extra `note` lines**: `visible_pre` results, window phase marks, harness errors,
    `mq_timing` (real seconds per merge-queue step) and `start_schedule` (when a worker group is started).
    Operator notes for abort rule 1 start `plan_usage`.
13. **Task-supply notes** (PLAN-v4 prep): `task_supply n=<N>` at window start and `tasks_exhausted n=<N>`
    when the last task in the list is handed to a slot (sessions; was: claimed). The analysis stops lambda and
    attempt counts at that minute, so running out of tasks cannot look like coordination drag.
14. **Review open at grace end**: noted as `review_open_at_grace_end`; the review is still allowed to
    finish (and is logged after `grace_end`), but the analysis drops it and its busy time.
15. **Calibration reviews are not windows.** The offline calibration of the reviewer (PLAN-v4 section 5 step 2)
    is logged in the analysis's calibration-review log format (analysis/README.md), which only `predict.py`
    reads, for abort rule 4. It is never written as a run directory, so it cannot enter the pilot's V, b or
    review-time CV (PLAN-v4.1 section 6.3).
16. **Worker prompt, conflicts**: the prompt and every rework message say to merge `origin/main` into the
    task branch before (re-)submitting, never to rebase or force-push (DRYRUN-REPORT §6.5).
17. **Review job = checkout (PLAN-v4 section 7.3).** The reviewer's working directory is an export of the
    exact head (not a git clone, so no other refs or objects are reachable), with `.claude/` and `CLAUDE.md`
    removed: the sandbox's `.claude/settings.json` allows `Bash(git *)`, and a change could otherwise edit
    the reviewer's own permissions or instructions. The harness's visible-test result is not in the packet;
    the reviewer runs the tests itself. `prep` still runs them and logs `visible_pre` for the record.
18. **One packet builder** (`review.make_packet`) for live windows and calibration; calibration's diff base
    is `base_ref`, live's the merge base with `origin/main`.
19. **Phase check at `run`** (PLAN-v4 section 7.4): no default real config; `run` refuses a mismatch with
    `config.PHASES` and asks for the phase name as confirmation.
20. **Start schedule**: `[run] start_schedule = [[minute, slots open from then], ...]`; T1 uses
    `[[0, 1], [30, 12]]`. Each group of slots opens from a thread and is noted as
    `start_schedule minute=<m> slots=<ids>`.
21. **Throttling measure** (PLAN-v4 section 7.5): activity = session launches + READY per slot-minute; the
    split between phases is the opening of a second slot; 5 minutes after each slot opens are skipped.
22. **One cloud session per task** (PLAN-v4 section 7.8). N slots; the harness hands tasks out in the seeded
    order, launches a session per task with the task in its prompt, frees the slot on READY, and never pushes
    to a session's branch: rework is a follow-up message to the task's own session when a slot frees, and the
    slot is then that session's until its next READY. At most N sessions are active. Replaces claims, races,
    TASKS.json scanning and FEEDBACK.md. What assumed long-running workers and changed: the claim protocol
    and race markers (gone; `claim` now means "branch first pushed", `claim_race` retired), the
    worker prompt (per task), FEEDBACK.md commits (now follow-up messages), `worker_start` (one per slot at
    its opening, session_id null), the start schedule (opens slots), the watcher's worker attribution (the
    slot, not a `Worker:` trailer), the throttle measure (every task is a launch), SimWorker/SimLauncher
    (SimSession/SimCloudLauncher), reset (deletes every `claude/*` branch), and the analysis's start-up time
    and supply-exhaustion fallback.
23. **Rework before new work.** A free slot takes queued rework (FIFO) before the next new task; a rework
    whose session is still busy elsewhere waits in the queue. Only the newest feedback for a task is sent.
24. **Session timeout** 25 min from the launch or the last rework message (the prompt's budget is 20):
    `session_timeout`, the task abandoned for the window, its slot freed; a later READY from it is ignored
    (accepting it would run more than N sessions). The timeout does not run while a change waits for review
    (the session is not in a slot then). A launch that fails outright is retried once, then abandoned.
25. **Branch naming.** A `READY: <id>` commit on any `claude/*` branch counts for task `<id>`
    (`accept_other_claude_branches`), in case sessions choose their own branch names; noted when it happens.
26. **T0 probe.** One follow-up after T0's first READY asks for an empty `PROBE:` commit, so follow-up
    delivery is checked even when nothing bounces; the slot is held until it arrives or 10 min pass.
27. **Private prompt files.** Rendered prompts, messages and launch output hold task text and go only to
    `[launcher] prompt_dir` outside this repo; the event log holds no task text beyond what it held before
    (review reasons).
28. **run.json notes** carry `phase=<phase>` and `worker_model_design=session-per-task`; `derive.py --pilot`
    uses the phase to refuse T0.
