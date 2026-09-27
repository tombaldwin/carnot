# Study 2 plan, version 3: does review set the limit?

Supersedes PLAN-v2.md. Answers REVIEW-fable-v2.md must-fix A–G and its should-fix list. Nothing is
run and no credit is spent until the harness passes a dry run on simulated agents and the
pre-registration (section 9) is committed and pushed.

## 1. What is being tested

Under a review limit, the Carnot model says finished work ≈ (1 − b) × V, where V is what the
reviewer can check per hour (re-reviews included) and b the share of reviews that send a change
back. A serial reviewer makes "finished stops rising" partly true by construction (review v2, B), so
the headline is the part that is **not** an identity:

1. **V is a property of the reviewer, not of the load.** V at the high fleet size equals V at the low
   size and in the pilot, within ±25%, and does not drift between the first and second half of a
   window. A reviewer that speeds up under load (skims) or slows down falsifies the fixed-capacity
   assumption behind the rule of thumb.
2. **Finished ≈ (1 − b_hidden) × V × (1 − b_review) × hours when saturated,** and b does not change
   with fleet size.
3. **Attempts per agent-hour keep rising with fleet size** even when finished work does not; if they
   fall, that is a real measurement of coordination drag in this setting.
4. **Escaped defects rise with queue depth.** A change the reviewer approved that then fails its
   hidden tests is an escaped defect. The model's "unverified change is entropy" claim predicts more
   of them when the queue is long. No rival predicts this.
5. **Collisions (study 1 replicated).** Bounce probability against in-flight k and file-overlapping
   in-flight m, per PR.

**Outcomes that would surprise the model, pre-registered:** finished(high) ≥ 1.3 × finished(low)
while the review queue is non-empty for most of the window; finished(high) ≤ 0.7 × finished(low)
with the queue non-empty; b rising with fleet size; V outside ±25% of the pilot; escape rate flat or
falling with queue depth while V rises.

## 2. Rivals and scoring (review v2, G)

Four curves, each turned into point predictions per window from the pilot's λ, V, b and CI time,
with α = 0.1, β = 0.01 and p = 0.005 (study 1) where needed:

| Rival | Finished per window |
|---|---|
| Carnot, review-capped | (1 − b_hidden)(1 − b_review) × min(λ·X(N)/(1 − b), V) × hours |
| USL, no review limit | (1 − r₀) × λ·X(N) × hours |
| Amdahl, α only, no review limit | (1 − r₀) × λ·N/(1 + α(N − 1)) × hours |
| Linear | (1 − r₀) × λ·N × hours |

The criterion is the Poisson log-likelihood of the four windows' finished counts under each rival,
with a fixed over-dispersion allowance (coefficient of variation 0.3, not estimated from two
replicates). The pooled ratio finished(high) ÷ finished(low) is reported as a descriptive only.

## 3. The pilot gate chooses the sizes (review v2, A and E)

- **T1, throttling and concurrency (≈ $4):** 1 worker alone for 15 min, then 9 at once for 15 min
  (the sweep's peak). Measures per-worker tokens per hour, tool latency, launch success. If
  per-worker tokens per hour fall by more than 20% at 9, concurrent sessions are throttled
  together, and the sweep is not run at 9.
- **T2, pilot (≈ $6–10):** 2 workers for 60 min. The reviewer reviews every PR from T1 and T2,
  aiming for 20–25 reviews. Outputs, all pre-registered: λ, V with an interval, b by cause, review
  time, worker tokens per attempt, session start-up time, CI time, and the gate decision.
- **The gate**, written before T2 runs. Review demand at size N is λ·X(N)/(1 − b) reviews per hour.
  - N_low = the largest N with demand ≤ 0.7 V (the reviewer has slack).
  - N_high = the smallest N with demand ≥ 1.5 V (the reviewer is clearly overloaded).
  - If demand at N = 8 is below V, the review limit cannot be reached within budget. Then, in order:
    shrink tasks to raise λ; or give the reviewer a heavier, pre-registered job (run the visible
    tests, check each acceptance criterion); or do not run the sweep.
  - If N_high > 8 the budget is re-cut before running, or the sweep is not run.

## 4. Budget: $250 of cloud session credits, floor $50

Only workers are cloud sessions. The reviewer runs locally on the Max plan (section 5), so it does
not draw on the credits. Each paid step is started only if the predicted balance after it, from the
pilot's measured burn, stays above $50.

| Step | Worker session-hours | At $4.20 / session-hour |
|---|---|---|
| T1 | 0.25 + 2.25 = 2.5 | ≈ $11 |
| T2 | 2.0 | ≈ $8 |
| Sweep: N_low and N_high, 2 replicates each, 90 min | 3 × (N_low + N_high), e.g. 33 for 3 and 8 | ≈ $139 |
| **Total** | ≈ 37.5 | **≈ $158** |

Decision rule from T2's burn (b = measured $ per worker session-hour, P = pilot spend):
full sweep if b × 3 × (N_low + N_high) ≤ $200 − P; otherwise 75-min windows if that fits; otherwise
one replicate of each size, reported as a pilot. Windows are spread across separate 5-hour plan
windows if the plan's rate limits turn out to apply to cloud sessions while credits remain.

## 5. Harness

**Sandbox project.** A purpose-built Python project, 5–8k lines, with a visible pytest suite that
runs in under 60 s. Frozen at a tagged commit and reset before every window, including `.claude/`
(diff-checked after each run). It is hosted as a private GitHub repo attached to the worker
sessions only.

**Tasks.** ~120 small tasks (20–150 lines each), each with a text description, acceptance criteria
in plain language, a reference solution (never shared) and **hidden acceptance tests that stay on
the local machine**. Every task is validated before the study: the hidden tests fail on the base
commit and pass with the reference solution. Tasks are sized in one difficulty band. Each window
gets a fresh seeded random order, and its task file is committed to main at reset.

**Workers (cloud sessions, Sonnet 5).** Identical prompt and settings. Coordination is git only:

- *Claim:* push a new branch `claude/task-<id>` from main. If the push is rejected because the branch
  exists, take the next task. The orchestrator logs claim races.
- *Submit:* push a commit whose message starts `READY:`. Agents do not wait; they claim the next task.
- *Rework:* the orchestrator pushes `FEEDBACK.md` to the task branch when a change is sent back. The
  worker checks its open branches between tasks (`git fetch`), fixes, and re-submits with `READY:`.
  Every re-submission is reviewed again (review v2, C).

**Reviewer (local, Opus 5.5, a fresh headless `claude -p` per review).** The orchestrator hands it
exactly one change: the diff, the task text and acceptance criteria, and the visible test result. It
never sees the queue or other PRs, and its context cannot drift over a window (review v2, D). It
returns approve or request-changes with a reason. A different model from the workers, to reduce
shared blind spots. V is its natural pace for this defined job. Reviews run one at a time. A review
that crashes is retried once; more than 10 min of reviewer downtime in a window voids the window.

**Merge queue (local orchestrator, serial).** For each approved change: run the hidden tests on
the exact approved head (fail = *escaped defect*); rebase on main; run visible and hidden tests
again (fail only here = *integration failure*, the collision term); merge if green. Any bounce
writes `FEEDBACK.md` with the cause and nothing about hidden test names ("acceptance check failed").
The queue's own serial time per change is measured and reported as the harness's α.

**Windows.** 90 min, first 10 min excluded as warm-up, changes merged within a 10-min grace period
count. Order low, high, high, low.

## 6. Definitions (review v2, C)

- **Attempt:** the first `READY:` push for a task. **Review:** one reviewer call. **Bounce:** a
  change sent back, by cause: review / rebase conflict / visible fail / escaped defect /
  integration failure. **b_review** = review bounces ÷ reviews; **b_hidden** = escaped + integration
  ÷ approvals; **b** = all bounces ÷ reviews.
- **Finished:** merged and green on hidden tests by window end plus grace. **Censored:** submitted
  but still open at the end; neither finished nor rework.
- **V:** reviews completed per reviewer-busy hour, all reviews including re-reviews.
- **k, m:** changes in flight, and those sharing a file, at a change's first `READY:` push.

## 7. Analysis (script written and run on synthetic logs before any real run)

- Primary: rival log-likelihoods (section 2) and the surprise outcomes (section 1), each coded
  pass/fail.
- V per window and per half-window against the pilot's ±25% band.
- Attempts per agent-hour, low vs high.
- Escaped defects: logistic on queue depth at review + time in window + window fixed effect, task as
  a stratum. Fewer than 8 escaped-defect events → reported as descriptive only.
- Bounce against k and m: logistic with window fixed effects; censored changes excluded.
- Fitted α and β from `carnot.py fit` reported only as descriptive, never as a test.

## 8. Exclusions

Throttling: per-worker tokens per hour at N_high more than 20% below T1's single-worker rate flags
the window. Worker death: a worker down for more than 10 min is restarted; more than 2 restarts
void the window. Reviewer downtime over 10 min voids the window. Voided windows are rerun if credit
allows, never spliced. Everything is published whichever way it comes out, and the paper gets a
revision-log entry.

## 9. Pre-registration (commit and push before T2)

Sandbox commit hash; task list and validation report; worker and reviewer prompts; orchestrator,
queue and analysis code hashes; the gate rule; definitions; the surprise outcomes; exclusion rules;
and, after T2, the pilot outputs and the point-prediction table for each rival at N_low and N_high,
committed before the first sweep window.

## 10. Still to verify (no spend unless stated)

| Question | How |
|---|---|
| CLI launch of cloud sessions from a script, and the exact syntax | docs + one launch in T1 |
| Cloud sessions draw on the credits; meter update frequency; $ per token vs list price | user reads meter before/after T1 and T2 |
| Sessions keep working unattended for 90 min; idle timeout | T2 |
| Concurrent sessions throttled together, at 9 | T1 |
| Plan rate limits apply to cloud sessions while credits remain | T1/T2 + docs |
| Pushing `claude/` branches works for claim, submit and feedback | T1 |
| Local reviewer calls are fast enough and not throttled by the workers' use of the same plan | T2 |
