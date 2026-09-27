# Fleet sweep (study 2): pre-registered analysis

Coded to **PLAN-v4** (fixed sizes N = 1 and 12, three 120-min windows each, no pilot gate, claims graded
confirmatory / conditional / descriptive in advance). PLAN-v3's gate and outcome codings are kept only
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
| `derive.py` | Per-window quantities (PLAN-v3 section 6 accounting) and per-PR / per-approval tables, with the task-supply truncation and the grace-end V correction; `--pilot` pools T1 + T2 into the pilot parameters, including `review_time_cv` and the flag `review_cv_ok`. |
| `predict.py` | PLAN-v4 pre-registration table: point predictions for the four rivals at N = 1 and 12 (per window and per three windows), the O2 interval, design-point loads, abort rules 3-5, the task-supply check, and the operating-characteristics statement. `--v3-gate` adds the superseded PLAN-v3 gate for reference. |
| `score.py` | PLAN-v4 section 1: P1 (capped vs best uncapped likelihood ratio), O2, S3 (b_review), O3, V constancy (Vratio, Vdur; conditional on `review_cv_ok`), and the descriptive S1r/S2r, four-way ranking, escapes, collisions and bounce causes, each graded; writes `RESULTS-draft.md` and `results.json`. `--plan v3` gives the superseded codings. |
| `synth.py` | Discrete-event simulation of the harness (workers, FIFO reviewer, serial merge queue, re-reviews, censoring) writing SCHEMA.md logs under a chosen truth, with the harness's `task_supply` / `tasks_exhausted` notes. `--v4` gives PLAN-v4's pilot and sweep layout. |
| `selftest.py` | Unit checks plus the simulation study at the PLAN-v4 design point (every v4 coding under four truths and a load-dependent reviewer), the task-supply check, and planted escape and collision effects. Writes `selftest-output/SELFTEST.md` and `selftest.json`. |

## How to run each step

```sh
# 0. Before anything is spent: the self-test (about 1-2 min on 10 cores; --quick for about 20 s)
$PY selftest.py                      # -> selftest-output/SELFTEST.md, selftest.json, example-synthetic/

# 1. Every run directory, as soon as it is written (and on the dry run's logs)
$PY validate_schema.py ../runs/<run_id>

# 2. After T1 and the eight T2 windows: the pilot parameters (fixes review_cv_ok)
$PY derive.py --pilot ../runs/<T1> ../runs/<T2-1> ... ../runs/<T2-8> --out pilot.json

# 3. Point predictions and the pre-registration table (PLAN-v4 defaults: N = 1 and 12, 3 x 120 min, `completion`)
$PY predict.py --pilot pilot.json --task-supply <tasks in TASKS.json> --burn <$ per session-hour> \
    --balance <credits left, $> --md prediction.md --json prediction.json
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
3. The result codings and grades from `score.py` (`code_outcomes_v4`): P1, O2, S3, O3 confirmatory;
   Vratio, Vdur, V conditional on `review_cv_ok`; S1r, S2r, RANK, ESC, COLL, BOUNCE descriptive, with the
   rules in the RESULTS table's "Detail" column.
4. `selftest-output/SELFTEST.md` and `selftest.json`, and the synthetic example in
   `selftest-output/example-synthetic/` (pilot, prediction table and RESULTS-draft with every result
   graded).
5. The operating characteristics (`common.V4_OC`, printed by `predict.py`), quoted from PLAN-v4
   section 1 and DESIGN-SEARCH.md.

After T1 and T2, before the first sweep window:

6. `pilot.json` (lambda, V with interval, b by cause, r0, completion share, CI time, review time, its CV
   and **`review_cv_ok`**, tokens, start-up time).
7. `prediction.md` / `prediction.json`: the point-prediction table (finished and attempts per window and
   per three windows for each rival at N = 1 and 12, with 95% predictive intervals), the O2 interval,
   the design-point loads and abort rules 3-5.

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
- **Review-time CV** = sample sd / mean of `duration_s` over counted reviews; the pilot's value sets
  `review_cv_ok` = (CV <= 0.5).
- **b_review** = review bounces / reviews; **b_hidden** = (escaped + integration) / approvals with a
  merge-queue outcome; **b_other** = (rebase conflicts + visible fails) / the same; **b** = all bounces
  / reviews.
- **lambda** = counted attempts / worker-hours after warm-up (worker down-time excluded). In a window
  flagged `supply_truncated`, attempts and worker-hours stop at the task-supply exhaustion minute
  (decision 19); `attempts_full`, `lam_full`, `worker_hours_full` keep the whole counting window.
- **Task supply**: tasks in the window's TASKS.json (`task_supply` note, else reset.json); exhausted at
  the `tasks_exhausted` note, else at the claim that makes every task claimed.
- **k, m**: as logged at the first submit (derive does not recompute them).
- Per-PR "bounced at least once" and "resolved" (= bounced or finished); the collision logistic uses
  resolved first attempts only.

## Decisions where PLAN-v3, PLAN-v4 or SCHEMA.md was ambiguous

Decisions 1-18 were made for PLAN-v3; those marked *superseded* no longer apply to the pre-registered
(PLAN-v4) path. Decisions 19-29 were made for PLAN-v4.

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
22. **V constancy coding.** Vratio: the exact (conditional binomial) 95% interval of V(12)/V(1); PASS
    ("stable") if it lies inside [0.8, 1.25], FAIL if it lies wholly outside, else INCONCLUSIVE. Vdur:
    Welch t-test on log `duration_s` of the counted reviews, N = 12 vs N = 1, FAIL at two-sided p < 0.05.
    Combined V: PASS if both PASS, FAIL if either FAILs, else INCONCLUSIVE.
23. **`review_cv_ok`.** Set by `derive.py --pilot` from the pooled T1 + T2 review durations (CV <= 0.5) and
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
27. **Operating characteristics** are constants in `common.V4_OC`, quoted from PLAN-v4 section 1 and
    DESIGN-SEARCH.md (stage C, recommended design; the confusion matrix is from `stageC_final.json`). They
    are printed next to the results, never recomputed by score.py.
28. **The design search keeps PLAN-v3 codings.** `design-search/dsim.py` now calls `score(..., plan="v3")`,
    the only change there, so its tables stay reproducible. Its figures were computed before decisions 19
    and 20 (derive.py changes V by a few per cent and truncates supply-limited windows; the synth pool had
    120 tasks, so linear-truth N = 12 windows ran out of tasks there too).
29. **synth.py** logs the harness's `task_supply` / `tasks_exhausted` notes (no change to its random
    streams) and has a `pilot_design="v4"` / `--v4` layout: T1 of 1 worker for 30 min plus 11 more for the
    last 15 min, eight separate 60-min T2 windows of one worker, sweep 1/12 x 3 x 120 min ABBAAB.

## Self-test results (300 replicates per cell; full tables in `selftest-output/SELFTEST.md`)

All 42 unit and end-to-end checks pass, including the grace-end V correction (a hand-built log with a
review open at grace end), task-supply truncation from the note and from reset.json, predict.py's v4
output (no REDESIGN_*, all four rivals at N = 1 and 12, the OC statement), `--v3-gate` kept as
SUPERSEDED, and a grade on every result, with the V-constancy grade following `review_cv_ok`.
`selftest-output/example-synthetic/` has one synthetic v4 pilot, prediction table and RESULTS-draft.

At the PLAN-v4 design point (synth: lambda 6.8, V 13.6, 220 tasks, pilot without the free calibration
reviews), share of 300 simulated studies:

- **P1** correct: 0.97 under Carnot (DESIGN-SEARCH 0.97), wrong 0.20 under USL (0.13 with calibration),
  0.00 under Amdahl and linear. A reviewer 25% faster at N = 12 (skimN) is still read as capped 0.86 of
  the time.
- **O2** fails 0.13 under Carnot truth at review-time CV 1 (0.04 at CV 0.5), so its false-alarm rate is
  above 5%; it fails 0.64 / 0.98 / 1.00 under USL / Amdahl / linear.
- **S3** (b_review) fails 0.08 under Carnot, 0.01-0.04 under the uncapped truths.
- **O3** passes in every truth (1.00).
- **Vratio** is INCONCLUSIVE in essentially every study, under every truth: with about 40 reviews at
  N = 1 and 75 at N = 12 the exact interval of V(12)/V(1) is roughly 0.7-1.45, never inside [0.8, 1.25]
  (that needs about 150 reviews per size). It can FAIL (uncapped truths: 0.63-1.00) but not PASS, so the
  combined V claim can only fail or be inconclusive.
- **Vdur** false alarms 0.05 (CV 1) / 0.04 (CV 0.5); power against the +/-25% reviewer 0.18-0.20 at CV 1
  and 0.66-0.73 at CV 0.5, matching DESIGN-SEARCH (0.19-0.21, 0.68-0.78).
- **review_cv_ok** is never true at review-time CV 1 and true in 0.52-0.62 of pilots when the true CV is
  exactly 0.5: near the threshold the flag is a coin toss.
- **S1r / S2r** false alarms under Carnot 0.05 / 0.53, as DESIGN-SEARCH.
- **Task supply** (linear truth, the fastest supply use): with 120 tasks, 93% of studies have N = 12
  windows that run out; the truncated lambda(12) is 4.92 against 4.39 uncut (lambda(1) 5.36; per-agent
  ratio 0.94 instead of about 0.82). With 220 tasks 3% still run out.
- **Escaped defects** (sizes 1/5, PLAN-v3 layout): `logit_cluster_task` detects a planted g = 0.1 / 0.2 in
  6% / 4% of sweeps overall (22% / 14% given >= 8 events); null 1% overall (11% given >= 8 events).
  **Collisions**: k-slope power 0.10-0.16 at p = 0.02-0.05; with censored first attempts excluded the
  p = 0 false-positive rate is 4-6%, against 77-92% for the rejected "not finished" outcome.
