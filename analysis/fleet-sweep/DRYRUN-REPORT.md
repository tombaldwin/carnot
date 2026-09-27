# Study 2 dry run on the real sandbox and tasks

Date: 2026-09-27. PLAN-v4 §5 step 1. The harness was run on the real sandbox (`sandbox-v1`, behind a
local bare remote) with the 120 real tasks. Workers and reviewer were simulated. Nothing was pushed
to GitHub, no cloud session was started, and no model was called. All working data (exported tasks,
hidden tests, run directories, pair analyses) is in the private tasks repo's gitignored `dryrun/`
directory. This report names tasks only by id and gives no task content.

## Summary

| PLAN-v4 §4 precondition | Harness side | Verdict |
|---|---|---|
| 1. T1: 12 concurrent workers | 12 simulated workers ran one 120-min window through the git protocol: 0 git errors, 0 harness errors, 5 claim races detected and logged. Real-session throttling can only be measured in T1. | **GO** (harness); T1 itself still to run |
| 2. Merge queue ≤ 0.75 min per change | Mean 1.9 s at N = 1, max 2.9 s at N = 12. The worst case with 53 tasks merged is about 3.2 s. | **GO**, about 15× headroom; no fix needed |
| 3. Pilot λ ≥ 4 per agent-hour | The sim was set to 7/agent-hour and `derive.py` recovered it: 7.0 before tasks ran out, 8.2 (CI 4.6–13.5) at N = 1. | **GO** (measurement works); the real λ comes from T2 |
| 4. V within ±30% of 2 × pilot λ | The sim was set to V = 14 and `derive.py` gave 12.5 and 13.2 (CIs include 14). There is a small downward bias, described below. | **GO** (measurement works) |
| 5. Credits | Not exercised. | n/a |
| Task supply at N = 12 (implied by §2) | **All 120 tasks were claimed by minute 84 of the 120-min window.** | **NO-GO until fixed**, see §3 |
| Hidden-test runner on the real tasks | Only 21/120 tasks validated before the fix; 120/120 after it. | **GO after the fix** in §1 |
| Analysis end to end on real harness output | `validate_schema`, `derive`, `derive --pilot`, `predict` and `score` all ran without errors. | **GO**, with two plan-version mismatches, see §5 |

## 1. Exporter and hidden-test runner

`export_for_harness.py` (committed to the tasks repo) turns `tasks/T###/` into the harness layout:
- `tasks.json`, with id, title, text (the task body) and acceptance;
- `hidden/<id>/test_hidden_<id>.py`;
- `reference/<id>.patch`;
- a private `meta.json` (kind, expected files and lines) for overlap analysis.

**What broke.** Through the harness's `validate-tasks`, only 21 of the 120 tasks validated. All 99
failures were "hidden tests fail with the reference solution". There were two causes:
- 76 tasks use fixtures from the sandbox's `tests/conftest.py`. The harness copied hidden tests to
  `<checkout>/_hidden_acceptance/`, outside `tests/`, so that conftest never applied.
- 66 tasks import the visible suite's helper module as `from helpers import …`. That conftest does
  the same. Under `--import-mode=importlib`, `tests/` is not on `sys.path`.

**Fix** (harness, as options; the toy defaults are unchanged):
- `[tests] hidden_subdir = "tests/_hidden_acceptance"`: the hidden copy sits under `tests/`, so the
  conftest applies.
- `[tests] hidden_pythonpath = ["tests", "."]`: these are prepended to `PYTHONPATH` for hidden runs
  only, so both `from helpers import` and `from tests.helpers import` resolve. None of the current
  tasks uses the second form, but both are tested.

`--import-mode=importlib` is kept. Hidden files are still copied into a throwaway export and deleted
afterwards. After the fix, 120 of 120 tasks validate through the harness: each fails on base, and
passes hidden and visible tests with its reference. The same options are now set in `config.toml`.

## 2. Merge-queue serial time

**Setup.**
- Config: `dryrun/config.real-dry.toml`.
- Time scale 10: a 120-min window with 10 min warm-up and 10 min grace ran in about 13.5 real minutes.
- SimWorker: λ = 7 attempts per agent-hour, `p_wrong` = 0.15, `p_conflict` = 0.10 (hotspot file),
  rework factor 0.5.
- SimReviewer: mean 257 s (V = 14 per busy hour), CV 0.4, crash rate 0.02.
- One window at N = 1 (seed 1001) and one at N = 12 (seed 1002).

**Instrumentation added.** Each merge-queue pass logs a `note` starting `mq_timing`. It holds the
real wall-clock seconds for each step, so the numbers are not inflated by the virtual clock.

Real seconds per merge-queue pass:

| Step | N = 1 (11 passes) mean / max | N = 12 (23 passes) mean / max |
|---|---|---|
| hidden_pre (this task's hidden tests on the approved head) | 0.28 / 0.30 | 0.38 / 0.73 |
| rebase (fetch, `merge-tree`, squash commit) | 0.11 / 0.16 | 0.09 / 0.23 |
| export of the rebased tree | 0.03 / 0.03 | 0.02 / 0.07 |
| visible tests after rebase | 1.12 / 1.16 | 0.68 / 1.59 † |
| hidden tests after rebase (this + all merged tasks) | 0.31 / 0.42 | 0.22 / 0.56 † |
| merge (push to main) | 0.07 / 0.10 | 0.04 / 0.14 |
| **total serial time per change** | **1.92 / 2.04** | **1.56 / 2.89** |

† At N = 12, 11 of the 23 passes stopped at hidden_pre (9 escaped defects) or at the rebase (2
conflicts). So the post-rebase means include zeros. Passes that reached the tests took 2.0–2.9 s.

**Growth of the post-rebase hidden set.** The "merged" scope runs the hidden tests of every task
merged earlier in the window, so the set grows through the window. The cost is about 0.16 s for
one task, plus about 0.035 s per additional merged task:
- N = 1: 0.42 s at 11 tasks.
- N = 12: 0.56 s at 12 tasks.

A window only merges as many tasks as the reviewer passes: about 11–15 at this design point, 12 in
the N = 12 window.

**Worst case.** A random-order cumulative merge of every reference patch that applies cleanly gives
53 tasks on main. On that tree:
- visible suite: 1.28 s;
- hidden tests of all 53 tasks: 1.39 s;
- total per change: about 3.2 s, including hidden_pre and the rebase.

That is 0.05 min against the 0.75 min limit. Even a visible suite several times larger (agents add
tests) stays far inside the limit.

**Fix: none needed.** "Merged" scope, serial, is kept as pre-registered, so the integration-failure
measurement keeps full coverage. Two cheaper options were considered and not adopted:
- Running hidden_pre off the serial path. It is only about 0.3 s, and it would add a second place
  that runs hidden tests.
- Limiting post-rebase hidden tests to tasks whose files overlap the change. This would miss
  integration failures across files. The pair analysis in §4 found none, but agents' own
  implementations may differ from the reference patches.

An `overlap` scope was drafted and then removed, because nothing needs it.

**Caveats.**
- The dry run shares one machine with 12 simulated worker clones, the visible-test prep thread and
  the merge queue. In a real window the workers run in the cloud, so local contention will be
  lower.
- The real reviewer (`claude -p`) runs locally but is I/O-bound.

## 3. Task supply

| | N = 1 | N = 12 |
|---|---|---|
| Claims in the window | 16 | **120 (all)** |
| Claims by minute 30 / 60 / 90 | 5 / 8 / 9 | 41 / 87 / 120 |
| Last claim | min 115 | **min 84** |
| First attempts counted (post warm-up) | 15 | 114 |
| Claim races detected | 0 | 5 |
| In-flight changes k at first submit, mean / max | 2.3 / 6 | 55 / 112 |

**Supply runs out at N = 12.** Twelve agents at λ ≈ 7 claim about 84 tasks per hour, so 120 tasks
last about 85 minutes. For the last 35 minutes of every N = 12 window, the workers have nothing to
claim. As a result:
- The measured per-agent λ at N = 12 falls from 7.0 (minutes 10–84) to 5.18 (the whole window),
  purely from supply.
- O3 (attempts rise with N) still passes. But the per-agent attempt ratio, and anything read as USL
  drag, is contaminated.
- The uncapped rivals' predictions exceed the supply. Linear at N = 12 predicts 180 attempts and
  132 finished. A supply ceiling would look like a cap.
- The primary claim (finished capped at about V × hours ≈ 15) is far below 120, so the primary
  likelihood ratio is not itself threatened. The attempt-based outcomes and λ(12) are.

At the plan's target of 8 per agent-hour, one N = 12 window needs about 12 × 8 × 2 = 192 claims.

**Options, in order of preference:**
- (a) Write about 100 more tasks, to about 220 in total, for a margin over 192.
- (b) Let tasks that no worker has touched carry over without re-use. This does not help, because
  every task is claimed.
- (c) Shorten the N = 12 counting window to the part before exhaustion. This changes the design.
- (d) Pre-register λ(12) and O3 as measured only up to the first minute at which no unclaimed task
  remains, and log that minute. This is the cheapest option and needs a small `derive.py` change.

**Randomisation works.**
- Each seed gives a different order. The first five tasks differ completely between seeds 1001 and
  1002.
- Order is reproducible from the seed through `random.Random(seed)`.
- N = 1 claimed strictly in TASKS.json order. N = 12 claimed in order apart from local reordering
  from concurrency and races.
- With N = 12 exhausting the list, the order only affects which tasks land early. At N = 1 it
  decides which 15–16 tasks are attempted at all.

**Reference patches that cannot be merged together.**

This was checked for all 7,140 pairs of reference patches, plus 20 random-order cumulative merges.
The checker is `pair_conflicts.py`, committed to the tasks repo.

- **Textual conflicts: 297 pairs (4.2%).**
  - 95 of the 120 tasks conflict with at least one other task.
  - The degree distribution is heavy-tailed: 21 tasks have 10–17 conflict partners each (for
    example T102, T108, T052, T087, T088, T105, T110).
  - Almost all conflicts are in two app modules, each a place where many tasks add an entry at the
    same spot: 126 and 118 of the 297 pairs.
  - None of the conflicts is in the test files.
  - In a random order, only 50–56 of the 120 reference patches can be merged unchanged (mean 53).
    The rest need a conflict resolution.
- **Semantic conflicts: none.**
  - All 6,843 textually clean pairs pass the visible suite plus both tasks' hidden tests.
  - All 20 cumulative merges (50–56 tasks each) pass the visible suite and every merged task's
    hidden tests.
- **Can conflicts be resolved?** Keeping both sides of each conflict turns 177 of the 297 pairs
  green. The other 120 need a real resolution, which an agent can do.

**What this means for the study:**
- `rebase_conflict` bounces are built into the task set and grow with the number of changes merged
  since a branch point. Merges are review-capped at about 15 per window, so in the N = 12 window only
  2 rebase conflicts occurred. The rate will still be higher at N = 12 than at N = 1.
- So b_other, and b if it includes all causes, will tend to rise with N for reasons unrelated to the
  reviewer. S3 must be pre-registered on **b_review** (review bounces / reviews), with b_other
  reported separately. PLAN-v4 §1 says only "the share of reviews that bounce, b". As implemented,
  `score.py`'s S3 uses all bounces per review.
- In the dry run, S3 passes only because p = 0.09 for 0.31 → 0.57, driven by hidden-pre and conflict
  bounces.
- Integration failures (hidden tests of merged tasks breaking after a rebase) will be rare. The
  reference patches produce none. Any that occur will come from agents' own implementations.
- The SimWorker had to learn to resolve conflicts (§6). Real workers get the harness's
  `rebase_conflict` FEEDBACK. The worker prompt should say plainly that "merge main into your branch
  and resolve" is expected and common.

## 4. Analysis package on real-harness logs

Run with `/Users/tom/git/carnot/analysis/aidev/.venv/bin/python`, in order:

1. `validate_schema.py` on both runs: OK, 0 errors. There are 3 warnings (details under "Warnings"
   below).
2. `derive.py --pilot real-N1-a` gave `pilot.json`:
   - λ = 8.18 (15 attempts in 1.83 worker-hours);
   - V = 12.5, b = b_review = 0.31, r0 = 0.17;
   - completion 0.73;
   - CI time 0.32 min (virtual; see the caveat below).
3. `predict.py --window-min 120 --sizes 1 12 --rival-rework completion`: ran. q = V / λ1 = 1.52, and
   demand / V at N = 1 is 0.95, which matches the design's "92% loaded at N = 1".
4. `derive.py` on both windows, with CSV tables: ran.
5. `score.py --rival-rework completion --escape-model logit_cluster_task`: ran and wrote
   `RESULTS-draft.md` and `results.json`.
   - Carnot had the highest likelihood (LR 118 over USL) under all three rework readings.
   - The escaped-defect depth effect the sim plants (catch rate falls with queue depth) was detected:
     OR 1.19 per waiting change, p = 0.006, 9 events.

**Derived quantities against the simulator's settings:**

| Quantity | Sim setting | Derived | Check |
|---|---|---|---|
| λ per agent-hour | 7.0 | N = 1: 8.18 (CI 4.6–13.5); N = 12, before supply ran out: 7.00 | ✓ |
| V per busy hour | 14.0 (257 s) | 12.5 (7.1–20.3), 13.2 (8.7–19.0); mean review duration 268 s at both sizes (V_dur 13.4) | ✓ within noise; small bias below |
| b_review | about 0.23 expected (0.15 false reject on correct, 0.7 catch on 15% wrong) | 0.31 (5/16), 0.18 (5/28) | ✓ within noise |
| Finished | = merges of first attempts submitted after warm-up | N = 1: 11 = 11 merges; N = 12: 8 = 12 merges − 4 first submitted in warm-up | ✓ exact |
| Censored + rework_open + finished = attempts | | N = 1: 3 + 1 + 11 = 15; N = 12: 105 + 1 + 8 = 114 | ✓ exact |
| Escaped defects | planted through depth-dependent catch rate | 9, all at queue depth ≥ 6 | ✓ |

**Warnings and small biases:**
- A review in progress at grace end completes after `grace_end` and is logged then. The validator
  warns and the analysis ignores it. Its busy time up to grace end still counts, so V is biased low
  by up to one review per window: about 3–6% at 16–29 reviews.
- Reviewer crash-and-retry time also counts as busy time.
- Fix, either one:
  - in the harness, log `reviewer_idle` at `grace_end` and discard the unfinished review;
  - in `derive.py`, end busy time at the last counted `review_end`.
- Both are small but systematic, and bear on the V(12)/V(1) stability claim.

**Plan-version mismatches in the analysis code (to fix before pre-registration):**
- `predict.py` still applies PLAN-v3's pilot gate. It prints `REDESIGN_SATURATED_AT_1` for this
  design, because the reviewer is 95% loaded at N = 1. PLAN-v4 has no gate, so the output is
  harmless but misleading. Its default `--window-min` is 90; v4 windows are 120.
- `score.py` codes PLAN-v3's outcomes (S1–S5, O1–O5, V band [0.75, 1.25]). PLAN-v4's list differs:
  - the primary claim is the likelihood ratio;
  - O2, S3, O3;
  - the V-stability band is [0.8, 1.25], plus a Welch test on log review durations that `score.py`
    does not compute.
- The code must be aligned with the v4 claim list before its hash is pre-registered.

**Time-scale caveat.** Virtual-time quantities include real test and git time multiplied by the
time scale: CI time per change, review duration overheads, and prep delays before review. At scale
10, a 2 s merge-queue pass appears as 20 s of CI time (0.32 min in `pilot.json`). Merge-queue timing
must be read from the `mq_timing` notes (real seconds), not from derived CI time, in any accelerated
run.

## 5. Harness changes

All changes are in `analysis/fleet-sweep/harness/`. They contain no task content and are not
committed.

- `config.py`:
  - `[tests] hidden_pythonpath`;
  - comment on `hidden_subdir` under `tests/`;
  - `[sim] use_toy`, `union_resolve`, `max_reworks`.
- `tasks.py`:
  - hidden runs get the `PYTHONPATH` prefix;
  - `hidden_subdir` may be nested;
  - `run(..., timing=)` records export, visible and hidden seconds.
- `orchestrator.py`: `merge_one` logs an `mq_timing` note (real seconds per step and outcome). The
  logic is unchanged; it is split into `_merge_steps` and `_post_scope`.
- `dryrun.py`:
  - `use_toy = false` runs against the configured `[repo]` and `[tasks]`, and refuses any remote that
    is not local;
  - writes `sim_unappliable.json` when a SimWorker had to abandon a task.
- `sim.py`: SimWorker applies a reference patch in stages:
  - plain apply;
  - then a three-way apply;
  - then, optionally, a keep-both-sides conflict resolution;
  - otherwise it abandons the task and records it.

  It can also cap reworks per task. Without this, the first real conflict put a worker into an
  endless error loop, and tasks were left claimed but never submitted.
- `config.toml`: the two hidden-test options for the real sandbox.
- `README.md`: documents the options, the real-sandbox dry run and `mq_timing`.
- `tests/test_hidden_runner.py`: 2 new tests on a synthetic repo, covering conftest fixtures and
  both helper-import styles.

The whole suite passes: 39 tests.

In the tasks repo, the new helper scripts are `export_for_harness.py` and `pair_conflicts.py`, and
`dryrun/` is gitignored.

## 6. Must change before the paid trials

1. **Task supply at N = 12.** Add about 100 tasks (to about 220), or pre-register option (d) of §3.
   Without one of these, λ(12), O3's per-agent reading and the uncapped rivals' attempt predictions
   are distorted by supply exhaustion at about minute 85.
2. **S3 definition.** Pre-register it on b_review. Report b_other (rebase conflicts), which the task
   set makes rise with N, separately. Align `score.py`.
3. **Align `score.py` and `predict.py` with PLAN-v4** before hashing:
   - the v4 claim list;
   - the V band [0.8, 1.25];
   - the Welch test on log review durations;
   - drop the v3 gate;
   - default windows of 120 min.
4. **V busy-time bias at grace end** (§4). Fix it in the harness or in `derive.py`.
5. **Worker prompt.** State that conflicts with main are common and must be resolved by merging main
   into the branch. 95 of 120 tasks have at least one conflict partner.
6. **Real config.** Set `hidden_subdir = "tests/_hidden_acceptance"` and
   `hidden_pythonpath = ["tests", "."]` (already in `config.toml`), then run `validate-tasks` against
   the private GitHub remote before T1.

Not blocking: merge-queue time (§2) and the hidden-test runner (§1, fixed).

## 7. 220-task rerun (PLAN-v4.1)

Date: 2026-09-27. The task set now has 220 validated tasks. Same setup as sections 1-3 (local bare
remote, simulated workers and reviewer, time scale 10), with SimWorker λ = 8 per agent-hour (was 7) and
SimReviewer V = 14 (mean 257 s, CV 0.4). Configs `dryrun/config.rerun-N12.toml` and `config.rerun-N1.toml`
(separate local remotes); analysis output in `dryrun/analysis-v41/`. Tasks are named by id only.

**Export and validation.** `export_for_harness.py` wrote all 220 tasks; the harness's `validate-tasks`
passed **220 of 220** (each fails on base, passes hidden and visible tests with its reference; 6 min 20 s).

**N = 12, one 120-min window (seed 2002):**

| | Value |
|---|---|
| Tasks claimed in the window | **182 of 220** (38 left); no `tasks_exhausted` note |
| Claims by minute 30 / 60 / 90 / 110 / 120 | 53 / 99 / 144 / 169 / 182 |
| Last claim | **minute 119.0** |
| First attempts counted (post warm-up); λ | 164; 7.45 per agent-hour over the whole window, not truncated, not flagged |
| Claim races | 6 (logged, 0 git or harness errors) |
| Reviews, V | 28, V = 13.2 (8.8-19.1); one review open at grace end, clipped (decision 20) |
| Merge-queue passes | 24: mean **1.34 s**, max **2.21 s** real time; the 12 passes that merged: mean 2.02 s, max 2.21 s |
| Post-rebase hidden set | up to 12 tasks |

Merge-queue time stays at about 0.04 min per change against the 0.75 min limit (about 20x headroom),
as in section 2. The supply lasts the window: at the observed claim rate (about 91 per hour) the 220 tasks
would last about 145 min. It would run out inside a 120-min window only if the claim rate rose above
220 / 2 h = 110 per hour, i.e. about 9.2 claims per agent-hour at N = 12 (15% above the λ target of 8).
The minute-110 flag (PLAN-v4.1 section 6.6) and the truncation of attempt-based measures cover that case.

**N = 1, one 120-min window (seed 2001):** 15 claims, last at minute 109.1; 14 counted first attempts
(λ = 7.64); 22 reviews, V = 13.4; 12 merges; merge queue mean 1.87 s, max 2.19 s. Three bounces were
`integration_failure`, all of task T108 after the SimWorker resolved its textual conflict with T019 by
keeping both sides. That pair is one of the 120 conflicting pairs that need a real resolution (section 3:
keep-both-sides leaves the hidden tests failing), so this is a simulated-worker artefact; it also shows
the post-rebase hidden check catching a bad resolution as intended.

**Analysis chain on the new logs (code as amended for v4.1):**
- `validate_schema`: both OK, 0 errors; 1 warning (the N = 12 review that ended after grace, ignored).
- `derive.py --pilot` now refuses these runs (kind `dry-run` is not a live T1/T2 run); with
  `--allow-nonlive`: λ 7.64 (4.2-12.8), V 13.4 (8.4-20.3), review-time CV 0.44, `review_cv_ok` true,
  `review_source` records the non-live runs.
- `predict.py`: q = 1.75, loads 1.05 / 3.88 at N = 1 / 12, no rival uses up 220 tasks within a window.
  Without a calibration log, abort rule 4 prints NOT EVALUATED. With a proxy calibration log (the 51
  simulated review durations in the calibration-log format, to exercise the reader only) calibrated
  V = 13.6, 0.89 of target -> OK.
- `score.py`: P1 PASS (log LR +6.18 over USL); O2 PASS (7 finished, interval 4-24); P1-nf / O2-nf N/A
  (no window flagged); S3 PASS; O3 PASS; Vdur PASS (p = 0.85); Vratio 0.99 (0.54-1.81), reported only.

**Status of section 6's list:** 1 (supply) fixed by the 220 tasks plus the flag; 2 (S3 on b_review), 3
(v4 alignment, now v4.1: Welch test as the reviewer-pace test, no combined V, no equivalence claim) and 4
(grace-end bias) are in the analysis code; 5 (worker prompt) is in `prompts/worker.md`; 6 (real config)
remains for the operator before T1.
