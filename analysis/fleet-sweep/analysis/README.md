# Fleet sweep (study 2): pre-registered analysis

Coded to **PLAN-v4** as amended by its section 6 (**v4.1**: fixed sizes N = 1 and 12, three 120-min windows
each, no pilot gate, claims graded confirmatory / conditional / descriptive in advance; the reviewer-pace test is
the Welch test on log review durations; live-only pilot; calibration log for abort rule 4 only; supply flag at
minute 110). PLAN-v3's gate and outcome codings are kept only
behind explicit flags (`predict.py --v3-gate`, `score.py --plan v3`) so the design search can be
reproduced; they are superseded.

Python 3.11, numpy, scipy (no pandas needed). Nothing here uses the network or any API. Use the
existing venv:

```sh
PY=/Users/tom/git/carnot/analysis/aidev/.venv/bin/python
cd /Users/tom/git/carnot/analysis/fleet-sweep/analysis
```

| File | What it does |
|---|---|
| `common.py` | Model curves (USL, Amdahl, linear), timestamps, the negative-binomial likelihood (Poisson with fixed CV 0.3), logistic / conditional-logistic fitters, the model-form collision fit. |
| `validate_schema.py` | Checks `events.jsonl` (+ `run.json`) against SCHEMA.md: fields, types, enums, timestamps, ordering, serial reviews, attempt numbering, merges only of approved + green heads. Exit 1 on errors. |
| `derive.py` | Per-window quantities (PLAN-v3 section 6 accounting) and per-PR / per-approval tables, with the task-supply truncation and flag (minute 110) and the grace-end V correction; `--pilot` pools the live T1 + T2 runs (and nothing else) into the pilot parameters, including `review_time_cv` and the flag `review_cv_ok`. |
| `predict.py` | PLAN-v4.1 pre-registration table: point predictions for the four rivals at N = 1 and 12 (per window and per three windows), the O2 interval, design-point loads, abort rules 3-5 (rule 4 from the calibration log, `--calibration`), the task-supply check, and the operating-characteristics statement. `--v3-gate` adds the superseded PLAN-v3 gate for reference. |
| `score.py` | PLAN-v4.1: P1 (capped vs best uncapped likelihood ratio) and O2, each also without the windows flagged for running out of tasks (P1-nf, O2-nf), S3 (b_review), O3, the reviewer-pace test Vdur (Welch on log review durations; conditional on `review_cv_ok`), and the descriptive Vratio, S1r/S2r, four-way ranking, escapes, collisions and bounce causes, each graded; writes `RESULTS-draft.md` and `results.json`. `--plan v3` gives the superseded codings. |
| `synth.py` | Discrete-event simulation of the harness (workers, FIFO reviewer, serial merge queue, re-reviews, censoring) writing SCHEMA.md logs under a chosen truth, with the harness's `task_supply` / `tasks_exhausted` notes. `--v4` gives PLAN-v4's pilot and sweep layout. |
| `selftest.py` | Unit checks plus the simulation study at the PLAN-v4 design point (every v4 coding under four truths and a load-dependent reviewer), the task-supply check, and planted escape and collision effects. Writes `selftest-output/SELFTEST.md` and `selftest.json`. |

## How to run each step

```sh
# 0. Before anything is spent: the self-test (about 1-2 min on 10 cores; --quick for about 20 s)
$PY selftest.py                      # -> selftest-output/SELFTEST.md, selftest.json, example-synthetic/

# 1. Every run directory, as soon as it is written (and on the dry run's logs)
$PY validate_schema.py ../runs/<run_id>

# 2. After T1 and the eight T2 windows: the pilot parameters (fixes review_cv_ok), live runs only
$PY derive.py --pilot ../runs/<T1> ../runs/<T2-1> ... ../runs/<T2-8> --out pilot.json
#    (refuses runs whose kind is not trial / pilot; --allow-nonlive only for a dry run of the chain)

# 3. Point predictions and the pre-registration table (PLAN-v4 defaults: N = 1 and 12, 3 x 120 min, `completion`)
$PY predict.py --pilot pilot.json --calibration calibration.jsonl --task-supply <tasks in TASKS.json> \
    --burn <$ per session-hour> --balance <credits left, $> --md prediction.md --json prediction.json
#    --calibration: the calibration-review log (below), read for abort rule 4 only
#    (by hand: --lambda-pilot --n-pilot --V --b-review --b-hidden [--b-other] --r0 --completion --ci-time-min)
#    --v3-gate appends the superseded PLAN-v3 gate for reference; the v4 path never computes it

# 4. After the six sweep windows (ABBAAB)
$PY validate_schema.py ../runs/<w1> ... ../runs/<w6>
$PY derive.py ../runs/<w1> ... ../runs/<w6> --csv-dir tables/     # per-window, per-PR, per-approval CSVs
$PY score.py --pilot pilot.json ../runs/<w1> ... ../runs/<w6> --out-dir results/
#    -> results/RESULTS-draft.md, results/results.json (defaults: --rival-rework completion,
#       --escape-model logit_cluster_task)

# Synthetic logs for a dry run of the whole chain
$PY synth.py --v4 --truth carnot --set lam1=6.8 V0=13.6 n_tasks=220 --seed 1 --out /tmp/synth   # T1, 8 x T2, 6 windows
$PY synth.py --truth usl --set V0=14 escape_depth=0.1 p=0.02 --out /tmp/synth-usl               # PLAN-v3 layout
```

`derive.py` reads the task supply from the harness's `task_supply` note or, for older logs, from the run
directory's `reset.json`. `score.py` recomputes the predictions from `pilot.json` with the same code as
`predict.py`, so the pre-registered `pilot.json` plus the code hash fixes every number it compares
against.

## What goes into the pre-registration (PLAN-v4 section 5, step 6)

Before T2:

1. Code hashes: `shasum -a 256 analysis/*.py` (and the harness's).
2. The definitions and decisions: this README's "Definitions" and "Decisions" sections, and the
   docstrings of `derive.py`, `predict.py` and `score.py`.
3. The result codings and grades from `score.py` (`code_outcomes_v4`): P1, O2 (each also without flagged
   windows: P1-nf, O2-nf), S3, O3 confirmatory; Vdur conditional on `review_cv_ok`; Vratio, S1r, S2r, RANK,
   ESC, COLL, BOUNCE descriptive, with the rules in the RESULTS table's "Detail" column.
4. `selftest-output/SELFTEST.md` and `selftest.json`, and the synthetic example in
   `selftest-output/example-synthetic/` (pilot, prediction table and RESULTS-draft with every result
   graded).
5. The operating characteristics (`common.V4_OC`, printed by `predict.py`), recomputed for v4.1 by
   `design-search/oc_v41.py` and tabulated in `../OPERATING-CHARACTERISTICS.md` (PLAN-v4.1 section 6.8); they
   replace the DESIGN-SEARCH figures.

After T1 and T2, before the first sweep window:

6. `pilot.json` (lambda, V with interval, b by cause, r0, completion share, CI time, review time, its CV
   and **`review_cv_ok`**, tokens, start-up time; all from live T1 + T2 reviews, `review_source`).
6a. The calibration-review log and predict.py's abort-rule-4 line (calibrated V against 2 x pilot lambda).
7. `prediction.md` / `prediction.json`: the point-prediction table (finished and attempts per window and
   per three windows for each rival at N = 1 and 12, with 95% predictive intervals), the O2 interval,
   the design-point loads and abort rules 3-5.

## Calibration-review log (PLAN-v4.1 section 6.3)

The offline reviews that set the reviewer's job (PLAN-v4 section 5 step 2: pilot PRs re-reviewed, reference
and deliberately broken solutions) are logged in a file of their own, not in a harness run directory. One
review per line, **JSONL** (or **CSV** with a header row and the same column names):

```json
{"review_id": "cal-0001", "duration_s": 251.3, "verdict": "approve", "source": "reference", "task": "T042",
 "expected": "approve", "job": "v1", "reviewer_model": "opus-5.5", "t": "2026-10-01T09:12:00.000Z"}
```

| Field | Required | Meaning |
|---|---|---|
| `review_id` | yes | unique id |
| `duration_s` | yes | wall-clock seconds of the reviewer call, from hand-over to parsed verdict, including an immediate retry |
| `verdict` | yes | `approve`, `request_changes` or `error` (a call that produced no verdict); case-insensitive |
| `source` | no | `pilot_pr`, `reference`, `broken` or `other` |
| `task` | no | task id |
| `expected` | no | `approve` (reference) or `request_changes` (broken); for the descriptive catch / false-reject rates |
| `job` | no | version of the review-job definition being calibrated; default: the last row's job |
| `reviewer_model`, `t` | no | for the record |

`predict.py --calibration <file> [--calibration-job <job>]` reads it **for abort rule 4 only**: calibrated
V = rows with a verdict / (sum of every row's `duration_s`, errors included) per hour, for one job version,
against 2 x pilot lambda (+/-30%). If the rule fails, the job is redefined and recalibrated under a new `job`
value in the same file. The log is never pooled into the pilot's V, b or review-time CV (derive.py refuses a
path that is not a run directory), so `review_cv_ok` depends on live reviews only. Catch rate on broken
solutions, false-reject rate on references, review-time CV and error count are printed as descriptive.

## Definitions as implemented (PLAN-v3 section 6, carried over by PLAN-v4)

- Counting window for attempts and finished work: [window_start + warm-up, window_end]; events are
  read up to window_end + grace. Hours per window = window - warm-up (110 min in PLAN-v4).
- **Attempt**: a task's first `submit` (attempt_no = 1) inside the counting window.
- **Finished**: a counted attempt whose task is merged by window_end + grace, with `tests_post`
  hidden tests green on the merged head.
- **Censored**: a counted attempt that is not finished and whose latest head is still waiting for or in
  review or in the merge queue at the end. **Rework open**: not finished, last event a bounce (the
  worker has not re-submitted). Censored changes are neither finished nor rework.
- **Review**: a `review_end`. Queue depth at review = `queue_depth` of the head's first `review_start`.
- **V** = reviews / reviewer-busy hours (reviewer_busy/idle intervals, clipped to window + grace, and
  clipped at the start of a review still running at window_end + grace; decision 20).
- **Review-time CV** = sample sd / mean of `duration_s` over counted reviews; the pilot's value, from the
  live T1 + T2 reviews only, sets `review_cv_ok` = (CV <= 0.5).
- **b_review** = review bounces / reviews; **b_hidden** = (escaped + integration) / approvals with a
  merge-queue outcome; **b_other** = (rebase conflicts + visible fails) / the same; **b** = all bounces
  / reviews.
- **lambda** = counted attempts / worker-hours after warm-up (worker down-time excluded). In a window
  flagged `supply_truncated`, attempts and worker-hours stop at the task-supply exhaustion minute
  (decision 19); `attempts_full`, `lam_full`, `worker_hours_full` keep the whole counting window.
- **Task supply**: tasks in the window's TASKS.json (`task_supply` note, else reset.json); exhausted at
  the `tasks_exhausted` note, else at the claim that makes every task claimed. **Flagged**
  (`supply_flagged`) if exhausted more than 10 min before window_end, i.e. before minute 110 of a 120-min
  window (decision 30).
- **k, m**: as logged at the first submit (derive does not recompute them).
- Per-PR "bounced at least once" and "resolved" (= bounced or finished); the collision logistic uses
  resolved first attempts only.

## Decisions where PLAN-v3, PLAN-v4 or SCHEMA.md was ambiguous

Decisions 1-18 were made for PLAN-v3; those marked *superseded* no longer apply to the pre-registered
(PLAN-v4) path. Decisions 19-29 were made for PLAN-v4, 30-34 for its section 6 amendments (v4.1).

1. **Warm-up and finished.** Finished counts only changes first submitted after the warm-up (as the
   review's simulation did); a warm-up change merged later is not counted.
2. **Rival anchoring.** Each rival's single-agent rate is anchored at the pilot:
   lam1_R = lambda_pilot x n_pilot / X_R(n_pilot), so every rival reproduces the pilot's attempt rate
   (otherwise linear would be penalised for USL's drag at N = 2).
3. **b in the Carnot formula.** 1 - b(N) = (1 - b_review)(1 - b_hidden(N))(1 - b_other). PLAN-v3's
   formula has no term for rebase conflicts and visible fails; with b_other = 0 the implementation is
   exactly PLAN-v3's. The measured b is reported next to the implied one.
4. **Where p enters.** b_hidden(N) = 1 - (1 - b_hidden)(1 - p)^(N - n_pilot): the collision term grows
   with N from the pilot's level. Merge-queue capacity 60 / CI-time also caps Carnot (never binding at
   CI times of 2-3 min).
5. **The rivals' rework term (important).** PLAN-v3 writes the uncapped rivals as (1 - r0) lambda X
   hours, while Carnot's uncapped branch (1 - b_hidden)(1 - b_review) lambda X / (1 - b) = lambda X assumes
   every bounced change is reworked and finishes. The two are inconsistent, and the self-test shows the
   consequence: at the gate's N_high (demand >= 1.5 V) Carnot's capped prediction and USL's
   (1 - r0) prediction differ by only a few per cent, so Carnot vs USL is decided by the rework
   accounting at N_low, not by the review cap. Three readings are implemented (`--rival-rework`):
   `plan` (as written, default), `recovered` (rivals x 1, as Carnot), `completion` (all rivals and
   Carnot's uncapped branch use the pilot's measured completion share c = finished / attempts, so
   Carnot = USL below the knee and they differ only by the cap). `score.py` always reports all three.
   Recommendation: pre-register `completion`, and make T2's window as long as the sweep's so c is
   measured on the same window length. *PLAN-v4 adopts `completion` (now the default of predict.py and
   score.py); T2 is eight 60-min windows, so c is measured on 60-min windows (see "ambiguities" in 28).*
6. **Ties.** When two rivals predict identical counts (Carnot = USL when review never binds), the tie
   is reported, not broken by list order.
7. *Superseded (PLAN-v4 has no gate; `predict.py --v3-gate` only).* **Gate details.** N searched 1-20; N_low is the largest N <= 8 with demand <= 0.7 V. Added a
   condition PLAN-v3 does not have: if even N = 1 has demand > 0.7 V the decision is
   REDESIGN_SATURATED_AT_1. With alpha = 0.1, beta = 0.01, X(8)/X(2) = 1.98 < 1.5/0.7 = 2.14, so the gate
   can only pass with N_low = 1, and only if V / lambda1 lies in a band about 1.65x wide (printed by
   `predict.py`).
8. **pilot.** lambda and the completion share come from the T2 (kind = pilot) windows only; V, b, r0 and
   CI time pool T1 and T2 (T1's PRs are reviewed too). lambda uses T2's warm-up exclusion.
9. *S1, S2 superseded; S1r, S2r kept as descriptive (decision 25).* **S1, S2 (finished ratio surprises).** Coded literally (ratio >= 1.3 / <= 0.7 with the queue
   non-empty >= 50% of the post-warm-up N_high windows). Because the gate puts N_low below the knee,
   Carnot itself predicts a ratio near 2, so the literal S1 fires under the model's own truth most of
   the time. `S1r`/`S2r` apply the same 1.3 / 0.7 thresholds to observed ratio / Carnot-predicted ratio;
   recommended as the primary coding.
10. **Escaped-defect model.** "Task as a stratum" is implemented literally as conditional logistic
    regression stratified by task (`clogit_task`). Almost every task has one approval, and a task with
    two approvals has them because the first escaped (escape -> rework -> re-approval), so every
    informative stratum has the event first: the model is degenerate (separation on time) and never
    detects anything in the self-test. Also implemented: `clogit_task_first` (first approval per task
    and window only) and `logit_cluster_task` (window FE, all approvals, task-clustered SEs, LR test).
    Recommendation: pre-register `logit_cluster_task`. Events < 8 -> descriptive only, all variants.
    *PLAN-v4 adopts `logit_cluster_task` (the default) and grades the whole analysis descriptive.*
11. *Superseded by decision 22.* **V band (S4, O1).** Coded literally on pooled V per size (and pooled half-windows for drift) against
    [0.75, 1.25] x pilot V. With the pilot's ~10-20 reviews that band fails by sampling noise alone most
    of the time, so noise-aware versions `S4n`/`O1n` fail only if the ratio is outside the band AND its
    exact 95% interval excludes 1. Recommended as primary; per-window and per-half-window values are
    reported, not coded.
12. *S3 superseded by decision 21; S5, O1, O4 dropped; O2 and O3 unchanged.* **S3 (b rising)**: one-sided
    Fisher exact test on bounces per review, pooled per size, p < 0.05.
    **S5**: V rising = V(high)/V(low) > 1.25; "flat or falling" = depth coefficient not positive at
    one-sided p < 0.05; N/A below 8 events. **O2**: the N_high windows' total finished count inside
    Carnot's 95% predictive interval (Poisson-gamma, CV 0.3 per window). **O3**: fleet attempt rate
    high/low > 1 at one-sided exact p < 0.05 (the per-agent-hour ratio is reported against USL's
    X(N)/N ratio; a per-agent fall is expected under USL). **O5**: descriptive.
13. **Over-dispersion**: negative binomial with size 1/CV^2 = 11.1 (Var = mu + 0.09 mu^2).
14. **k excludes the change itself** in the synthetic logs (SCHEMA.md: "changes in flight at this
    moment"); derive uses k as logged. A bounced change awaiting rework counts as in flight.
15. **Collision estimate.** Besides the logistic, p is estimated in the model's own form
    P(bounced) = 1 - s_w (1 - p)^k (1 - p_m)^m with a profile-likelihood interval, bounded to [0, 0.5].
16. **Schema strictness.** No fields beyond SCHEMA.md; session_id, new_head, tokens_*, cost_usd_est
    may be null (as the harness's own validator allows). Submits after window_end are ignored.
17. **Half-windows** split at the window midpoint; grace belongs to the second half; a review belongs
    to the half it ended in.
18. **Descriptive alpha, beta**: `carnot.py fit` on (n_pilot, pilot attempts/h), (N_low, ...),
    (N_high, ...) — three points, not identifiable, reported only.
19. **Task-supply truncation (DRYRUN-REPORT section 3, option d).** At N = 12 the dry run claimed all 120
    tasks by minute 84, so per-agent lambda fell from 7.0 to 5.2 from supply alone. `derive.py` finds the
    exhaustion minute (harness note `tasks_exhausted`, else the claim that makes every task in the supply
    claimed; supply from the `task_supply` note, else reset.json) and, if it is before window_end, flags
    the window `supply_truncated` and computes attempts, attempts per hour, worker-hours and lambda over
    [warm-up end, exhaustion] only. It stays in force with the larger task set. Finished, censored and
    rework_open are still over the whole counting window, and the finished-count likelihood (P1, O2) is
    not truncated: a flagged N = 12 window gets a supply warning next to P1 instead. If the supply is
    unknown, nothing is truncated and the window is listed as "supply unknown".
20. **V grace-end bias.** A review still running at window_end + grace is not counted, and its busy time
    was counted, biasing V low by up to one review per window (3-6% in the dry run). Fix (derive.py):
    busy time is clipped at that review's first `review_start`. Reviews are serial, so everything after
    that point belongs to the uncounted review; for a reviewer busy without a break this is the same as
    ending busy time at the last counted `review_end`, and it is exact when the reviewer was idle in
    between. Chosen over the pro-rata alternative because it needs no expected review duration and
    excludes the length-biased in-progress review cleanly. The harness logs are unchanged (the review
    still completes and is logged after grace_end, which the validator warns about); the harness now
    adds a `note` `review_open_at_grace_end task=... head=...` for the record. Reviewer crash-and-retry
    time still counts as busy time: failed calls are part of the reviewer's real capacity.
21. **S3 on review bounces only.** PLAN-v4 says "the share of reviews that bounce, b". The task set
    makes rebase conflicts rise with the number of merges, so S3 is coded on b_review = review bounces /
    reviews (one-sided Fisher exact, FAIL at p < 0.05). Rebase conflicts, visible fails, escaped defects and
    integration failures are reported per approval with a merge-queue result, by size (BOUNCE,
    descriptive), with the all-causes b beside them.
22. *Superseded by decision 32.* **V constancy coding.** Vratio: the exact (conditional binomial) 95% interval of V(12)/V(1); PASS
    ("stable") if it lies inside [0.8, 1.25], FAIL if it lies wholly outside, else INCONCLUSIVE. Vdur:
    Welch t-test on log `duration_s` of the counted reviews, N = 12 vs N = 1, FAIL at two-sided p < 0.05.
    Combined V: PASS if both PASS, FAIL if either FAILs, else INCONCLUSIVE.
23. **`review_cv_ok`.** Set by `derive.py --pilot` from the pooled live T1 + T2 review durations (CV <= 0.5; decision 31) and
    recorded in pilot.json before the first sweep window. `score.py` reads it from pilot.json only (no
    command-line override, so it cannot be changed after the sweep); if absent it is treated as false and
    V constancy is descriptive.
24. **No gate on the v4 path.** `predict.py` never computes the PLAN-v3 gate unless `--v3-gate` is given,
    and then labels it SUPERSEDED. Defaults: sizes 1 and 12, 3 windows, 120 min, 10 min warm-up (grace 10
    min is the harness's), `completion` reading. It adds abort rule 3 (lambda >= 4), abort rule 4 (V within
    +/-30% of 2 x pilot lambda), abort rule 5 with `--burn --balance` (78 session-h, $50 floor), and a
    supply check with `--task-supply` (the minute each rival's predicted claim rate would use up the list).
25. **Grades.** Every result in `results.json` has `grade` (confirmatory / conditional / descriptive)
    and `counts_as` (for conditional results: confirmatory iff review_cv_ok). Descriptive results that
    have a rule (S1r, S2r) are still coded PASS/FAIL; the others are coded REPORTED or N/A. RESULTS-draft.md
    labels every section and row.
26. **P1.** Carnot's log-likelihood minus the best of USL, Amdahl and linear, under `completion`; PASS iff
    positive. A tie (identical predictions, e.g. no window where review binds) is not "higher" and fails,
    as ties counted as failures in the design search. The other two readings are reported as sensitivity.
27. *Superseded by decision 33.* **Operating characteristics** are constants in `common.V4_OC`, quoted from PLAN-v4 section 1 and
    DESIGN-SEARCH.md (stage C, recommended design; the confusion matrix is from `stageC_final.json`). They
    are printed next to the results, never recomputed by score.py.
28. **The design search keeps PLAN-v3 codings.** `design-search/dsim.py` now calls `score(..., plan="v3")`,
    the only change there, so its tables stay reproducible. Its figures were computed before decisions 19
    and 20 (derive.py changes V by a few per cent and truncates supply-limited windows; the synth pool had
    120 tasks, so linear-truth N = 12 windows ran out of tasks there too).
29. **synth.py** logs the harness's `task_supply` / `tasks_exhausted` notes (no change to its random
    streams) and has a `pilot_design="v4"` / `--v4` layout: T1 of 1 worker for 30 min plus 11 more for the
    last 15 min, eight separate 60-min T2 windows of one worker, sweep 1/12 x 3 x 120 min ABBAAB.
30. **Supply flag at minute 110 (PLAN-v4.1 section 6.6).** With 220 tasks a window should not run out, but if
    one does before minute 110 (more than 10 min before window_end) derive.py sets `supply_flagged` (truncation,
    decision 19, still applies to any exhaustion before window_end). score.py codes P1 and O2 on all windows and
    reports them again without the flagged windows as P1-nf and O2-nf (same grade, shown beside them; N/A when
    nothing is flagged or when a size has no unflagged window left). Attempt-based measures stop at the
    exhaustion minute as before.
31. **Live-only pilot; calibration log (PLAN-v4.1 sections 6.2-6.3).** `derive.py --pilot` takes run
    directories of kind `trial` or `pilot` only (T1, T2) and records `review_source`; `--allow-nonlive` exists
    only to push a dry run through the chain. The calibration reviews go in the calibration-review log above,
    which only `predict.py --calibration` reads, for abort rule 4. Without it predict.py prints rule 4 as NOT
    EVALUATED (the live V is shown as a preview). Near the 0.5 threshold `review_cv_ok` is close to a coin toss
    (OPERATING-CHARACTERISTICS.md), and the pre-registration says so.
32. **Reviewer pace (PLAN-v4.1 section 6.1).** The conditional-confirmatory test is `Vdur`: Welch t-test on log
    `duration_s` of the counted reviews, N = 12 against N = 1, FAIL iff two-sided p < 0.05, else PASS;
    confirmatory iff `review_cv_ok`. `Vratio` (V(12)/V(1) with its exact conditional-binomial 95% interval) is
    descriptive and coded REPORTED: no band and no equivalence claim, since with about 40 and 75 reviews the
    interval is roughly 0.7-1.45. The combined `V` result is removed.
33. **Operating characteristics recomputed (PLAN-v4.1 section 6.8).** `design-search/oc_v41.py` simulates the
    final setup (220 tasks, grace-end fix, live-only pilot of T1 + 8 x 60-min T2, Haiku assumptions of
    DESIGN-SEARCH, the v4.1 codings) and `../OPERATING-CHARACTERISTICS.md` tabulates the result; `common.V4_OC`
    holds those figures and predict.py / score.py print them.
34. **Conditional-logit likelihood in log space.** `common._clogit_ll` computed the elementary symmetric
    polynomial on a linear scale, which underflowed to 0 (math domain error, crashing score.py) when one row
    dominated a stratum with two or more events. oc_v41.py hit it in one of 1000 simulated sweeps through the
    `clogit_task` escape sensitivity model. The recursion now runs in log space (`logaddexp`); values are
    unchanged where the old code worked (checked against brute-force enumeration).

## Self-test results (300 replicates per cell; full tables in `selftest-output/SELFTEST.md`)

All 57 unit and end-to-end checks pass, including the grace-end V correction (a hand-built log with a
review open at grace end), task-supply truncation from the note and from reset.json, the v4.1 supply flag
(out at minute 105: flagged; at 112: truncated only), P1-nf / O2-nf on flagged windows, the calibration-log
reader (JSONL and CSV, job versions, invalid rows refused) and abort rule 4 using the calibrated V (NOT
EVALUATED without a log), derive --pilot refusing a non-live run, the clogit likelihood no longer failing when
one row dominates a stratum (decision 34), predict.py's v4 output (no REDESIGN_*, all four rivals at N = 1 and
12, the OC statement), `--v3-gate` kept as SUPERSEDED, a grade on every result, Vdur the only conditional result
and its grade following `review_cv_ok`, and no combined V result. `selftest-output/example-synthetic/` has one
synthetic v4 pilot, prediction table and RESULTS-draft.

Part V (the design point, 300 studies per cell) agrees with the pre-registered operating characteristics
within simulation error. Those are computed with 2000 studies per cell by `design-search/oc_v41.py` and
tabulated in `../OPERATING-CHARACTERISTICS.md`: P1 correct 0.965 / 0.80 / 1.00 / 1.00 under Carnot / USL /
Amdahl / linear truth; O2 false alarms 0.10 (review-time CV 1) and 0.04 (CV 0.5); S3 0.06; Vdur false
positives 0.04-0.05 and power against a +/-25% reviewer 0.18 (CV 1) and 0.73 (CV 0.5); `review_cv_ok` set in
1.00 / 0.53 / 0.00 of pilots at true CV 0.3 / 0.5 / 0.7. Part S: with 120 tasks, 93% of linear-truth studies
have N = 12 windows that run out; with 220, 3%.
