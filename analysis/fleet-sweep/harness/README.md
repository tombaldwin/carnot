# Fleet-sweep harness (study 2 orchestrator)

This runs one window of the study in PLAN-v4.md (protocol from PLAN-v3.md): it resets the sandbox
repo, watches the workers' git branches, feeds the reviewer one change at a time, runs the serial
merge queue, and writes `runs/<run_id>/events.jsonl` and `run.json` in the format SCHEMA.md sets
out. It needs only the Python 3.11 standard library and git 2.40 or later. pytest is used for its
own tests and for the sandbox's test suites.

Dry runs, and every test, use simulated workers and a simulated reviewer against a local bare
repo. **Nothing here calls `claude`, an Anthropic API, GitHub, or a cloud session unless you run
`reset` or `run` with a real config.**

```sh
cd analysis/fleet-sweep/harness
python3.11 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m pytest -q                      # about 1.5 min
.venv/bin/python -m harness dry-run --run-id dry-1 # a 90-min window in about 1 min
.venv/bin/python -m harness status --config config.dryrun.toml --run-id dry-1
```

## How it works

```
workers (cloud or SimWorker) --git push--> remote <--git fetch-- watcher --> review queue (FIFO)
                                              ^                                   | one at a time
                                              |                               reviewer
                                FEEDBACK.md --+-- bounce <--- merge queue <------+ approve
                                              main  <-------- (serial) merge
```

**Worker protocol (git only; `prompts/worker.md` is the draft worker prompt).**

- *Claim:* push a new branch `claude/task-<id>` holding one empty commit, `CLAIM: task-<id>`, with
  a `Worker: wN` trailer. The claim commit is unique, so a second claimant's push is rejected
  (non-fast-forward). Without it, two workers pushing an unchanged `main` would both "succeed" and
  the race could not be seen. A worker whose claim is rejected pushes a race marker
  `claude/race-<id>-<worker>`. The watcher logs it as `claim_race` and deletes it.
- *Submit:* a commit whose message starts `READY:`, with the `Worker:` trailer. The worker does not
  wait.
- *Rework:* the orchestrator bounces a change by committing `FEEDBACK.md` to the branch in a
  commit whose message starts `FEEDBACK:`. Between tasks, the worker looks for its branches whose
  tip is such a commit, fixes the change, deletes `FEEDBACK.md`, and pushes a new `READY:` commit.
  Every re-submission is reviewed again.
- *Conflicts with main:* the prompt tells workers plainly that these are common (95 of the 120 real
  tasks conflict textually with at least one other) and that a `rebase_conflict` bounce is resolved by
  merging `origin/main` into the task branch, keeping both sides, and re-submitting; never by rebasing,
  force-pushing or dropping what is on `main`.

**Watcher** (`orchestrator.poll`, every `poll_interval_s`). It fetches `main` and `claude/*`.
- A new `claude/task-*` branch is logged as `claim`. The worker comes from the `Worker:` trailer,
  or failing that the commit author.
- New `READY:` commits on a branch, not reachable from `main`, are logged as a `submit`. If one push
  holds several, the newest is the submitted head. `attempt_no` counts submissions per task.
  `lines_changed` and `files` come from the diff against the merge base with `main`, excluding
  `FEEDBACK.md`.
- When the last task in the window's list is claimed, the watcher logs a `note`
  `tasks_exhausted n=<N>` straight after that `claim`. The list is `reset.json`'s `task_order` (the
  TASKS.json on `main`), else the task catalogue.
- `k` is the number of *other* tasks in flight (submitted at least once, not merged). `m` is how
  many of those share a file with this change.
- Event times are the time the watcher saw the push, not git commit dates.

**Prep.** This thread runs the visible tests on each submitted head, so the review packet can
include the result. It logs a `note` of the form `visible_pre task=… head=… passed=…`.

**Review queue.** A FIFO with one reviewer call at a time. The packet holds the diff against the
merge base, the task text and acceptance criteria, and the visible test result on that head. It
is rendered through `prompts/reviewer.md`. The reviewer never sees the queue, other changes, or
hidden tests.
- Output parsing: the first non-empty line must start with APPROVE or REQUEST_CHANGES. Case,
  markdown, and a `Verdict:` prefix are tolerated. If the first line has no verdict, the first line
  that starts with one is used. Anything else is a `review_error`. Unclear output is never read as
  approval.
- Errors: a failed call is retried once, straight away. If the retry also fails, the change goes
  back to the front of the queue and the reviewer backs off for `retry_backoff_s`. Downtime runs
  from a failure to the next successful review. More than `max_downtime_min` (10) in a window
  writes a `note` starting `VOID:` and adds the same reason to `run.json` `notes`.
- `reviewer_busy`/`reviewer_idle` mark stretches of back-to-back reviews, so V is reviews divided
  by busy hours.

**Merge queue** (serial) for each approved head:
1. `hidden_pre`: that task's hidden tests on the exact approved head. A failure bounces
   `escaped_defect`.
2. `rebase`: a three-way merge (`git merge-tree`) of the change's net diff onto the current `main`,
   committed as one squash commit with `FEEDBACK.md` removed. A conflict bounces
   `rebase_conflict`.
3. `tests_post`: visible tests, plus the hidden tests of this task and of every task already merged
   in this window (`post_hidden_scope = "merged"`). A visible failure bounces `visible_fail`;
   otherwise a hidden failure bounces `integration_failure`.
4. `merge`: `main` is fast-forwarded to the squash commit.

Hidden tests are copied into a throwaway `git archive` export of the commit under test, run there,
and deleted. They are never staged, committed or pushed. A test checks every blob and path
reachable from every ref of the remote and of the orchestrator's clone. `FEEDBACK.md` states the
cause in plain words. For the hidden-test causes it says only "Acceptance check failed", with no
test names and no output. For `visible_fail` it includes the tail of the visible test output,
since those tests are public.

**Window control** (`run_window`):
- The window starts; the log notes `window_start` and `task_supply n=<N>` (tasks in the window's list),
  then `warmup_end` after `warmup_min`.
- At `window_end`, the workers are stopped. Claims and submissions seen after this point are logged
  only as `note` lines and are not queued.
- During grace, reviews of already-submitted changes (`grace_reviews = true`) and merges carry on.
  Real windows run the full grace period. Dry runs stop early once nothing is left.
- If a review is still running then, the log notes `review_open_at_grace_end task=… head=…`. That
  review finishes and is logged after `grace_end` (the validator warns); the analysis counts neither it
  nor its busy time (analysis README decision 20).
- Then `grace_end`, the threads stop, a `.claude/` diff check against the sandbox commit runs, and
  `run.json` is written.
- Nothing is marked censored; the analysis derives that.

**Reset** (`python -m harness reset`):
- Force-sets `main` on the remote to `base_ref` and deletes every `claude/task-*` and
  `claude/race-*` branch.
- Commits the window's seeded task order as `TASKS.json` to `main`. The file holds id, title, text
  and acceptance criteria, never tests or solutions.
- Writes `runs/<id>/reset.json` with the sandbox commit, the TASKS commit and the order. `run`
  reads it.

## Files

| Path | What |
|---|---|
| `harness/config.py` | config dataclasses; TOML loader |
| `harness/clock.py` | virtual clock (time scale for dry runs); ISO-ms timestamps |
| `harness/schema.py` | SCHEMA.md as code; structural + semantic log validator |
| `harness/events.py` | JSONL event writer (refuses events that do not match the schema) |
| `harness/gitops.py` | git wrappers |
| `harness/reset.py` | reset |
| `harness/tasks.py` | task catalogue, seeded order, test execution in temp checkouts |
| `harness/review.py` | review packet, verdict parser, command reviewer (UNVERIFIED template) |
| `harness/orchestrator.py` | watcher, prep, review queue, merge queue, bounces, window control, run.json |
| `harness/launchers.py` | manual and command (UNVERIFIED) worker launchers; the verified-template guard |
| `harness/sim.py` | SimWorker, SimReviewer, SimLauncher, oracle |
| `harness/toygen.py` | toy sandbox + 10 toy task templates with hidden tests and reference patches |
| `harness/validate.py` | task validation: hidden fails on base, hidden and visible pass with reference |
| `harness/dryrun.py`, `harness/report.py`, `harness/cli.py` | dry-run wiring, event counts, CLI |
| `config.toml` | real-window config (placeholders + UNVERIFIED templates) |
| `config.dryrun.toml` | dry-run config |
| `prompts/reviewer.md`, `prompts/worker.md` | reviewer packet template; draft worker prompt |
| `tests/` | pytest suite |

CLI: `python -m harness {reset,run,dry-run,status,log,validate-tasks,validate-log,make-toy} --config FILE`.
`log` appends operator events (`meter`, `note`, `worker_down`, `worker_restart`) to a run's log and
checks them against the schema.

## Task files the real sandbox must provide

- `task_file`: `{"tasks": [{"id": "001", "title": "...", "text": "...", "acceptance": ["...", ...]}]}`
- `hidden_tests_dir/<id>/test_*.py`: pytest files, run from the checkout root with
  `--import-mode=importlib`. Keep them outside the sandbox repo.
- `reference_dir/<id>.patch`: `git apply`-able patch against `base_ref`. `validate-tasks` and
  SimWorker use it; it is never shared.
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
SimWorker applies the reference patch; if it no longer applies to the current `main` it tries a
three-way apply, then (with `union_resolve = true`) keeps both sides of each conflict, as a worker
would for two additions at the same place. `max_reworks` caps how often it reworks one task.

Every merge-queue pass logs a `note` starting `mq_timing` with the real (wall-clock) seconds of
each step: `hidden_pre_s`, `rebase_s`, `post_export_s`, `visible_post_s`, `hidden_post_s` (with
`n_hidden_post`, the number of tasks whose hidden tests ran), `merge_s` and `total_s`. These are
real seconds even when the clock is accelerated, so they measure the merge-queue precondition.

## What is simulated in a dry run

- **SimWorker** follows the protocol above against a local bare repo.
  - Work time is exponential with rate λ (`rate_per_hour`); rework takes `rework_factor` of that.
  - A change is the task's reference patch, or, with `p_wrong`, a note file only (hidden tests
    fail).
  - Optional extras:
    - `p_conflict`: write a shared `SIM_HOTSPOT.txt`, which textually conflicts with other such
      changes.
    - `p_semantic`: edit a shared constant, which breaks other tasks after rebase and gives an
      integration failure.
    - `p_visible_break`: break a visible test.
  - Rework rebuilds the change on the current `main` as a merge commit.
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

## UNVERIFIED settings (fill in after the product checks in PLAN-v3 §10)

`python -m harness run` refuses to start while any template it needs has `verified = false`.

| Setting | Default | What to check |
|---|---|---|
| `[reviewer] command` | `sh -c "claude -p --model {model} < {prompt_file}"` | exact `claude -p` flags: model selection, no tools or permissions needed, no session persistence, headless auth on the Max plan |
| `[reviewer] output_format` | `text` | whether to use a JSON output mode, and its field names (the parser expects `result` and `usage.input_tokens`/`output_tokens`) |
| `[launcher] start_command`, `stop_command`, `session_id_regex` | empty | whether and how a cloud session can be started and stopped from a script, and what it prints. Until then use `mode = "manual"` |
| `[repo] remote_url`, `[tasks] *` | placeholders | the private sandbox repo and the local task, hidden-test and reference paths |
| worker prompt `prompts/worker.md` | draft | that cloud sessions can push `claude/task-*` and `claude/race-*` branches (T1), and that they resolve `rebase_conflict` bounces by merging `origin/main` as the prompt says; the final wording, to be pre-registered |

## Operator checklist for a real window

Before the day:
1. Set `[repo]`, `[tasks]` and `[tests]` in `config.toml`. Run
   `python -m harness validate-tasks --config config.toml`; all tasks must be valid.
2. Confirm the PLAN-v3 §10 facts. Fill in the UNVERIFIED templates and set `verified = true`, or
   keep `launcher.mode = "manual"`.
3. Commit the harness and push it with the pre-registration. `run.json` records `harness_commit`,
   with `-dirty` if the harness has uncommitted changes.

For each window (PLAN-v4: N = 1 and N = 12, three windows each, order 1, 12, 12, 1, 1, 12):
1. Choose `run_id` (e.g. `2026-10-02-N12-r1`), `n_workers` (1 or 12, fixed in advance; there is no
   pilot gate) and a seed.
2. **Meter before:** read the credits meter.
3. `python -m harness reset --config config.toml --run-id <id> --seed <seed>`. Check that the
   printed `sandbox_commit` is the frozen tag and `branches_deleted` looks right.
4. `python -m harness run --config config.toml --run-id <id> --meter-start <credits>`. With the
   manual launcher, start each session with its file from `runs/<id>/worker-prompts/wN.md` and
   paste its session id when asked. Keep the terminal open; it logs every event.
5. During the window:
   - A worker down for more than 10 minutes: restart it and log both events, e.g.
     `python -m harness log --run-id <id> --type worker_down --field worker=w3 --field reason="..."`,
     then the same with `worker_restart`.
   - More than 2 restarts voids the window. Record that as a `note` starting `VOID:`.
6. At the "WINDOW END" prompt, stop every worker session. The harness stops accepting claims and
   finishes reviews and merges through the grace period.
7. **Meter after:** `python -m harness log --run-id <id> --type meter --field credits_left_usd=<x> --field source=operator`.
8. `python -m harness validate-log runs/<id>`, then `python -m harness status --run-id <id>`. Check
   `notes` in `run.json` for `VOID:`. A voided window is rerun under a new id, never spliced.
9. Decide from the burn whether the next window keeps the predicted balance above $50 (PLAN-v4 §3-4).

## Decisions made where PLAN-v3 / SCHEMA.md were silent

These are listed in the build report and should be pre-registered or overruled:

1. **Claims carry a commit**: an empty `CLAIM:` commit with a `Worker:` trailer. This is what
   makes races detectable and claims attributable.
2. **Race markers**: races are seen only when the loser pushes `claude/race-<id>-<worker>`.
3. **Event times**: observation time at the watcher's poll, not git commit time.
4. **k excludes the change itself.** A bounced change stays in flight until it is merged.
5. **Several `READY:` commits in one push** make one submission, at the newest.
6. **Rebase is a squash**: the change's net diff is three-way merged onto `main`, so worker merge
   commits do not re-conflict. `main` gets one commit per task.
7. **`tests_post` hidden scope**: this task plus every task merged earlier in the window. Without
   the earlier tasks, an integration failure could only come from this task's own tests.
8. **Grace**: reviews continue during grace, so changes submitted before the end can still finish.
   Submissions after the window end are not queued; they appear as `note` lines only.
9. **Reviewer failures**: an immediate retry. After two failures the change is re-queued at the
   front with a backoff. Downtime runs from the first failure to the next success.
10. **run.json**: only the SCHEMA.md keys. The reset TASKS commit and any VOID reasons go into
    `notes`.
11. **Superseded submissions**: a newer `READY:` for a task whose older head is still waiting for
    review replaces it in place, with a `note`.
12. **Extra `note` lines**: `visible_pre` results, window phase marks, harness errors, and
    `mq_timing` (real seconds per merge-queue step).
13. **Task-supply notes** (PLAN-v4 prep): `task_supply n=<N>` at window start and `tasks_exhausted n=<N>`
    when the last task in the list is claimed. The analysis stops lambda and attempt counts at that
    minute, so running out of tasks cannot look like coordination drag. Only claims seen during the
    window count; claims of ids not in the list do not.
14. **Review open at grace end**: noted as `review_open_at_grace_end`; the review is still allowed to
    finish (and is logged after `grace_end`), but the analysis drops it and its busy time.
15. **Calibration reviews are not windows.** The offline calibration of the reviewer (PLAN-v4 section 5 step 2)
    is logged in the analysis's calibration-review log format (analysis/README.md), which only `predict.py`
    reads, for abort rule 4. It is never written as a run directory, so it cannot enter the pilot's V, b or
    review-time CV (PLAN-v4.1 section 6.3).
16. **Worker prompt, conflicts**: the prompt states that conflicts with `main` are common and are
    resolved by merging `origin/main` into the task branch before re-submitting (DRYRUN-REPORT §6.5).
