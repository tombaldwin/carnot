# Study 2 plan, version 2: does review set the limit? (DRAFT, for review before any credit is spent)

Supersedes PLAN.md. Incorporates every must-fix in REVIEW-fable.md and the study 1 result
(analysis/aidev/RESULTS.md): concurrency barely raised rejection in 48,000 agent PRs (p ≈ 0–0.004),
so collisions no longer separate the Carnot model from Amdahl's law. The paper's rule of thumb now
leads with review capacity. This study tests that.

## Question

With one reviewer gating merges, does finished output stop rising with fleet size at the point the
rule of thumb predicts, while attempted output keeps rising? And when the reviewer falls behind, do
more wrong changes get through?

Rivals, each turned into point predictions before the sweep:

| Model | Finished changes per window |
|---|---|
| **Carnot v2** | (1 − r) × min(λ·X(N), V), with V the reviewer's measured capacity |
| **Amdahl, no review limit** | (1 − r₀) × λ·X(N) |
| **Linear** | (1 − r₀) × λ·N |

## Budget: $250 of cloud session credits, floor $50

Credits pay for Claude Code cloud sessions. When they run out the plan's usage applies and extra
usage is off, so no money can be spent; the residual risk is plan limits. Spend is controlled by
estimate, not by reading the meter mid-run: each window's cost is predicted from the pilot's
measured burn per agent-hour, and a window is only started if the predicted balance after it stays
above $50.

| Step | Sessions × time | Estimate at $4.20/session-hour |
|---|---|---|
| T1 throttling trial: 1 agent alone, then 4 at once, 20 min each | 5 × 0.33 h | ≈ $7 |
| T2 pilot: 2 agents + reviewer, 45 min | 3 × 0.75 h | ≈ $10 |
| Sweep: N = 3 and N = 8, two replicates each, 90 min, each with 1 reviewer | (3+1+8+1) × 2 × 1.5 h = 39 h | ≈ $164 |
| **Total** | | **≈ $181**, leaving ≈ $69 |

Pilot decision rule (fixed now): burn ≤ $5/session-hour → full sweep. $5–6.5 → windows cut to
75 min. > $6.5 → one replicate of each size only, reported as a pilot. λ < 2 attempts per
agent-hour → halve task size before the sweep.

## Harness

- **Sandbox repo:** a purpose-built ~5–10k-line project (TypeScript or Python) with a fast visible
  test suite (CI under 90 s), frozen at a tagged commit, `.claude/` included. Reset to that commit
  before every window, and the harness diffs `.claude/` after each run.
- **Tasks:** ~120 small tasks (20–150 lines each) as GitHub issues, drawn from one difficulty band
  calibrated in the pilot. **A fresh seeded random order per window**, logged.
- **Hidden acceptance tests** per task live in a private repo attached to no agent session. Only
  the orchestrator reads them. Bounce messages say only "acceptance check failed".
- **Workers:** N Claude Code cloud sessions, Sonnet 5 (model choice per session is documented;
  confirm the org allow-list). Identical prompt and settings. Each loops: claim an issue (label,
  retry on race), work on a `claude/` branch, open a PR, claim the next. **Agents do not block on
  their PR** (review must-fix 3, option b); every PR's in-flight count k and file-overlapping
  in-flight count m are logged at creation.
- **Reviewer:** one cloud session, Sonnet 5, reviewing open PRs oldest first, one at a time. It
  sees the diff, the issue and the visible tests, and either approves or requests changes with a
  reason. **Its capacity is whatever its natural pace is**, with no token bucket, so the review
  limit is measured, not imposed (answers review must-fix 2). The reviewer prompt is identical in
  every window and does not mention the queue length.
- **Merge queue:** the local orchestrator, serial. For each approved PR: rebase on main, run
  visible and hidden tests, merge if green, bounce otherwise. The queue's serial time per PR is
  measured and reported as the harness's own α (must-fix 4).
- **Windows:** 90 min, first 10 min excluded as warm-up, PRs merged within a 10-min grace period
  after the end count. Run order ABBA: 3, 8, 8, 3.

## Accounting (fixed in advance, must-fix 5)

- *Attempt* = the first PR for a task. *Rework event* = any bounce of that task (review requested
  changes, rebase conflict, visible fail, hidden fail), by cause. *Finished* = merged and green on
  hidden tests at window end plus grace. *Escaped defect* = a PR the reviewer approved that then
  failed hidden tests.
- λ = attempts per agent-hour. V = reviews completed per hour. r = share of attempts not finished.
- The model treats a reworked change as lost; the harness only delays it. Predictions are reported
  under both readings.

## Pre-registered predictions and tests

Parameters come from the pilot (λ, V, r₀, CI time) and model defaults (α = 0.1, β = 0.01), with
p = 0.005 from study 1. Point predictions for each rival are committed before the sweep.

1. **Review limit (primary).** Carnot v2 predicts finished(8) ÷ finished(3) close to 1 whenever the
   pilot's q = V ÷ λ is below about 3, where Amdahl predicts about 1.8 and linear about 2.7.
   Scored by Poisson log-likelihood of the four windows' finished counts under each rival, with
   over-dispersion estimated from the replicate spread. **Counts against the model:** pooled
   finished(8) > 1.5 × finished(3) while the reviewer's queue is non-empty for most of the window.
2. **The rule of thumb.** The rule's predicted fleet size q ÷ (1 − αq − βq²) and predicted finished
   work (1 − r₀) × V are stated in advance. Reported: whether each window's finished count lies
   within its 90% prediction band.
3. **Coordination drag.** Attempts per agent-hour at N = 8 vs N = 3. Descriptive, with the harness's
   own queue α reported beside it; no α/β fit is treated as a test (must-fix 1). If attempts per
   agent-hour do not fall at all, the paper's default α and β are too high for this setting, and it
   says so.
4. **Collisions (replication of study 1).** Rework probability against in-flight k and m across all
   PRs, logistic with window fixed effects. Predicted: slope per concurrent PR under 0.01, and
   larger for file-overlapping PRs than others. **Counts against study 1's conclusion:** p > 0.03.
5. **Review load and escaped defects (the "unverified change is entropy" test).** Escaped-defect
   rate against the reviewer's queue depth when it reviewed each PR, and against N. Predicted:
   rises with queue depth. This is the most novel measurement and the least powered; it is
   pre-registered as a test but the paper will call a positive result preliminary.

The model is counted *for* only if tests 1 and 2 go its way. Anything else is reported as mixed or
against. All results are published whichever way they come out, and the paper gets a revision-log
entry.

## Before any paid window: facts to verify

| Question | How | Status |
|---|---|---|
| Can N cloud sessions be launched from a script? | Docs say `claude --cloud` or routines; confirm with a 2-session launch | to confirm (free) |
| Do CLI-launched sessions draw on the cloud credits? | Launch one, check the meter after | T1 |
| Do sessions keep looping unattended for 90 min? | Pilot T2; fallback: orchestrator sends "next task" | T2 |
| Is the idle timeout long enough? | T2 | T2 |
| Are concurrent sessions throttled together? | T1: tokens/hour and tool latency, 1 alone vs 4 together; flag the sweep if per-agent tokens/hour falls > 20% | T1 |
| How often does the credit meter update, and is it $-for-$ with API prices? | User reads it before and after T1 and T2; compare with `/usage` × list price | T1, T2 |
| Can the reviewer read PRs and comment through the proxied git access? | T2 | T2 |
| Can agent sessions read the private tests repo or the orchestrator's logs? | Check attached repos and permissions before T2 | before T2 |

## Deliverables

`analysis/fleet-sweep/`: the sandbox repo and tasks (hidden tests published after the study), the
orchestrator, the pre-registration with the pilot numbers and point predictions, the analysis
script (runnable on a synthetic log before any real one exists), raw logs, RESULTS.md.
