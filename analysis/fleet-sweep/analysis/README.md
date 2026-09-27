# Fleet sweep (study 2): pre-registered analysis

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
| `derive.py` | Per-window quantities (PLAN-v3 section 6) and per-PR / per-approval tables; `--pilot` pools T1 + T2 into the pilot parameters. |
| `predict.py` | Point predictions for the four rivals at any N, the q-gate (N_low, N_high, redesign / re-cut conditions), the section-4 budget rule; writes the pre-registration table. |
| `score.py` | Primary analysis: rival likelihoods, pre-registered outcomes coded PASS / FAIL, V band, attempts, escaped-defect and collision logistics; writes `RESULTS-draft.md` and `results.json`. |
| `synth.py` | Discrete-event simulation of the harness (workers, FIFO reviewer, serial merge queue, re-reviews, censoring) writing SCHEMA.md logs under a chosen truth. |
| `selftest.py` | Unit checks plus the simulation study (family recovery, planted escape and collision effects, censoring check, surprises). Writes `selftest-output/SELFTEST.md` and `selftest.json`. |

## How to run each step

```sh
# 0. Before anything is spent: the self-test (about 10 min on 10 cores; --quick for about 1 min)
$PY selftest.py                      # -> selftest-output/SELFTEST.md, selftest.json

# 1. Every run directory, as soon as it is written (and on the dry run's logs)
$PY validate_schema.py ../runs/<run_id>

# 2. After T1 and T2: the pilot parameters
$PY derive.py --pilot ../runs/<T1> ../runs/<T2> --out pilot.json

# 3. Gate, budget rule and point predictions (the pre-registration table)
$PY predict.py --pilot pilot.json --burn <$ per worker session-hour> --pilot-spend <$ T1+T2> \
    --md prediction.md --json prediction.json
#    (by hand: --lambda-pilot --n-pilot --V --b-review --b-hidden [--b-other] --r0 --ci-time-min)
#    --sizes N1 N2 gives the table at other sizes; --rival-rework picks the reading (see decisions)

# 4. After the four sweep windows
$PY validate_schema.py ../runs/<w1> ../runs/<w2> ../runs/<w3> ../runs/<w4>
$PY derive.py ../runs/<w1> ../runs/<w2> ../runs/<w3> ../runs/<w4> --csv-dir tables/   # per-window, per-PR, per-approval CSVs
$PY score.py --pilot pilot.json ../runs/<w1> ../runs/<w2> ../runs/<w3> ../runs/<w4> --out-dir results/
#    -> results/RESULTS-draft.md, results/results.json

# Synthetic logs for a dry run of the whole chain
$PY synth.py --truth carnot --sizes 1 5 --seed 1 --out /tmp/synth   # T1, T2, 4 windows (low, high, high, low)
$PY synth.py --truth usl --set V0=14 escape_depth=0.1 p=0.02 --out /tmp/synth-usl
```

`score.py` recomputes the predictions from `pilot.json` with the same code as `predict.py`, so the
pre-registered `pilot.json` plus the code hash fixes every number it compares against.

## What goes into the pre-registration (PLAN-v3 section 9)

Before T2:

1. Code hashes: `shasum -a 256 analysis/*.py` (and the harness's).
2. The gate rule and definitions: this README's "Definitions" and "Decisions" sections, and the
   docstrings of `derive.py` and `predict.py`.
3. The outcome codings from `score.py` (`code_outcomes`): S1-S5, O1-O5 with the thresholds in the
   RESULTS table's "rule" column, and which variants are primary (see decisions 9-11 below).
4. `selftest-output/SELFTEST.md` and `selftest.json` (the analysis run on synthetic logs, with the
   recovery, detection and false-positive rates), and one synthetic `RESULTS-draft.md` from step 0's
   dry run, showing every outcome coded.
5. The choice of `--rival-rework` reading and `--escape-model` (decisions 5 and 10).

After T2, before the first sweep window:

6. `pilot.json` (lambda, V with interval, b by cause, r0, completion share, CI time, review time and
   tokens, worker tokens per attempt, start-up time).
7. `prediction.md` / `prediction.json`: the gate decision, N_low, N_high, the budget rule, and the
   point-prediction table (finished and attempts per window for each rival at N_low and N_high, with
   95% predictive intervals, predicted V, b and escape rate).

## Definitions as implemented (PLAN-v3 section 6)

- Counting window for attempts and finished work: [window_start + warm-up, window_end]; events are
  read up to window_end + grace. Hours per window = window - warm-up (80 min).
- **Attempt**: a task's first `submit` (attempt_no = 1) inside the counting window.
- **Finished**: a counted attempt whose task is merged by window_end + grace, with `tests_post`
  hidden tests green on the merged head.
- **Censored**: a counted attempt that is not finished and whose latest head is still waiting for or in
  review or in the merge queue at the end. **Rework open**: not finished, last event a bounce (the
  worker has not re-submitted). Censored changes are neither finished nor rework.
- **Review**: a `review_end`. Queue depth at review = `queue_depth` of the head's first `review_start`.
- **V** = reviews / reviewer-busy hours (reviewer_busy/idle intervals, clipped to window + grace).
- **b_review** = review bounces / reviews; **b_hidden** = (escaped + integration) / approvals with a
  merge-queue outcome; **b_other** = (rebase conflicts + visible fails) / the same; **b** = all bounces
  / reviews.
- **lambda** = counted attempts / worker-hours after warm-up (worker down-time excluded).
- **k, m**: as logged at the first submit (derive does not recompute them).
- Per-PR "bounced at least once" and "resolved" (= bounced or finished); the collision logistic uses
  resolved first attempts only.

## Decisions where PLAN-v3 or SCHEMA.md was ambiguous

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
   measured on the same window length.
6. **Ties.** When two rivals predict identical counts (Carnot = USL when review never binds), the tie
   is reported, not broken by list order.
7. **Gate details.** N searched 1-20; N_low is the largest N <= 8 with demand <= 0.7 V. Added a
   condition PLAN-v3 does not have: if even N = 1 has demand > 0.7 V the decision is
   REDESIGN_SATURATED_AT_1. With alpha = 0.1, beta = 0.01, X(8)/X(2) = 1.98 < 1.5/0.7 = 2.14, so the gate
   can only pass with N_low = 1, and only if V / lambda1 lies in a band about 1.65x wide (printed by
   `predict.py`).
8. **pilot.** lambda and the completion share come from the T2 (kind = pilot) window only; V, b, r0 and
   CI time pool T1 and T2 (T1's PRs are reviewed too). lambda uses T2's warm-up exclusion.
9. **S1, S2 (finished ratio surprises).** Coded literally (ratio >= 1.3 / <= 0.7 with the queue
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
11. **V band (S4, O1).** Coded literally on pooled V per size (and pooled half-windows for drift) against
    [0.75, 1.25] x pilot V. With the pilot's ~10-20 reviews that band fails by sampling noise alone most
    of the time, so noise-aware versions `S4n`/`O1n` fail only if the ratio is outside the band AND its
    exact 95% interval excludes 1. Recommended as primary; per-window and per-half-window values are
    reported, not coded.
12. **S3 (b rising)**: one-sided Fisher exact test on bounces per review, pooled per size, p < 0.05.
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

## Self-test results (300 replicates per cell; full tables in `selftest-output/SELFTEST.md`)

`selftest-output/example-synthetic/` has one synthetic pilot, prediction table and RESULTS-draft for the
pre-registration. All 24 unit and end-to-end checks pass.

What the simulation says about the design, before any money is spent:

- **The gate usually does not pass with a pilot this small.** Under the Carnot truth (V placed in the
  middle of the feasible band), T1 + T2 give a median of 11 reviews and about 5 counted attempts; pilot
  V / true V has a 10-90% range of 0.70-1.63. The gate says RUN in only 37% of simulated pilots (27%
  "saturated at N = 1", 19% "unreachable", 17% "N_high > 8"). When it runs, N_low is always 1.
- **Family recovery through the whole pipeline is weak.** Carnot truth: Carnot picked 55% (`plan`),
  91% (`recovered`), 44% (`completion`, with a further 20% tied with USL). Uncapped truths are
  misread as Carnot 36-80% of the time depending on reading. With the pilot replaced by its expected
  value, Carnot truth is recovered 91-98% under `recovered`/`completion` but only 31% under `plan`:
  pilot noise and the rework reading, not the sweep, are the binding problems.
- **Review-comparable table (N = 3, 8, oracle parameters).** Carnot-vs-Amdahl-vs-linear matches
  REVIEW-fable-v2 closely for q <= 3 (0.88-1.00 vs 0.95-1.00). At q = 4 we get 0.47 vs 0.75-0.82: PLAN-v3's
  Carnot formula counts re-reviews in demand (lam X / (1 - b)), so at q = 4 it predicts a cap the
  review's (1 - r) min(lam X, V) formula did not, and its uncapped branch assumes every attempt
  finishes. Amdahl recovery with cv = 0.3 is 0.65-0.83 vs the review's 0.72-0.82; adding USL as a fourth
  rival (which the review did not score) takes 20-40 points from Amdahl because USL and Amdahl are close
  at N <= 8.
- **Escaped defects.** Only 9-32% of sweeps reach 8 events. The literal task-stratified model never
  detects anything (0-1%); `logit_cluster_task` detects a planted effect in 14-22% of sweeps that
  reach 8 events, with a false-positive rate of 11% conditional (1% overall). Underpowered at 4 windows.
- **Collisions.** p = 0 gives k-slope false positives of 4-6% and p-hat CI excluding 0 in 1-2%. Planted
  p = 0.05 is recovered on average (mean p-hat 0.044-0.051, coverage 0.96) but the k-slope is detected
  only 16% of the time, and p = 0.005-0.02 cannot be told from 0. Window fixed effects absorb the
  between-size variation in k.
- **Censoring (review v2, C) is handled.** Under p = 0, the pre-registered outcome (bounced at least
  once, censored excluded) gives a k-slope in 4-6% of sweeps; the rejected "not finished" outcome gives
  one in 77-92%.
- **Codings under the model's own truth** (false-alarm rates): literal S1 74-75%, S1r 33%; literal S4
  69-70%, S4n 4%; literal O1 95%, O1n 12-13%; S2 0-1%, S3 3%, O2 13-15%. A skimming or slowing
  reviewer of the size simulated moves V(high)/V(low) only to 1.11 / 0.89, which no coding detects
  reliably.
