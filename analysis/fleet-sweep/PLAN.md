# Study 2 plan: a controlled fleet sweep (DRAFT, for review before any money is spent)

## Question

Does finished output against fleet size follow the Carnot model?

    U(N) = (1 − r(N)) × min(λ·X(N), V/h)
    X(N) = N / (1 + α(N−1) + β·N(N−1))
    r(N) = 1 − (1 − r₀)(1 − p)^(N−1)

Specifically: (a) does output peak and then flatten or fall; (b) is the peak below the USL-only peak
√((1−α)/β) because of rework; (c) when the review cap is set to bind, does output flatten at
(1 − r)·V/h as predicted.

## Budget and hard stops

- Paid for by the user's **$250 of cloud session credits** (Claude Code cloud sessions; expire
  5 November 2026). The credits apply automatically to cloud sessions; once they are used up the
  plan's regular usage applies, and the plan has extra usage **turned off**, so no money can be
  spent beyond the credits. The residual risk is eating into the Max plan's weekly limits, not cost.
- Stop rule: the harness stops launching work when the credit meter (checked by the user in
  claude.ai settings between windows, since sessions may not report cost to the harness) shows
  **$50 left**, and every window has a hard wall-clock end. A partial sweep is reported as partial.
- Open question for review: whether cloud sessions under credits are rate-limited like the plan.
  Throttling would look exactly like coordination drag, so any 429 or slowdown is logged and the
  affected time excluded.

## Design

- **Fleet sizes:** N = 1, 2, 4, 8. One replicate each (budget). Run order randomised with a fixed seed.
- **Window:** 2 hours of wall clock per size, same start state each time. 30 agent-hours in total.
- **Workers:** N Claude Code **cloud sessions**, launched from a local orchestrator, each cloning
  the sandbox repo from GitHub. Same prompt, tools, model and settings for every agent and every N.
  Model: Sonnet 5 if cloud sessions allow choosing it (cheaper, so the credit stretches further);
  otherwise the default, with the windows shortened to fit.
- **Estimated cost:** ≈ $4.20 per agent-hour on Sonnet 5 (≈ $7 on Opus 5.5) → ≈ $126 (≈ $210) for
  30 agent-hours. Uncertain by ×0.5–×2. If the pilot shows a higher burn rate, the windows shrink
  before the sweep starts, not during it.

### The repository and tasks

- A purpose-built sandbox project, ~5–10k lines, with a real test suite, in a language agents handle
  well (TypeScript or Python). Built once, frozen, and restored to the same commit before each run.
- **Backlog:** ~120 small, comparable tasks (features and bug fixes of 20–150 lines), more than any
  size can finish in 2 hours, so every run is throughput-limited rather than backlog-limited. Same
  backlog, same randomised order, every run.
- **Each task has hidden acceptance tests** the agents cannot see. They decide whether a merged
  change actually counts, so "finished" is objective, and "passed visible tests but wrong" shows up
  as rework when the hidden tests run after merge.
- Task overlap is left to arise naturally from the codebase (tasks touching shared modules), not
  engineered. The task-to-file overlap will be measured before the runs and reported, because it
  sets p.

### The loop each agent runs

1. Claim the next task: tasks are GitHub issues in a fixed order; an agent claims one by applying a
   label with its id, retrying on a race. Claim races are logged (they are a small contention term).
2. Work on a branch from the current main in its own cloud sandbox. Run the visible tests.
3. Open a pull request, which enters the merge queue.

### The merge queue and the review cap

- **Merge queue (the α term):** a GitHub Actions workflow (or the local orchestrator) processes one
  pull request at a time: rebase onto main, run visible + hidden tests, merge if green. The hidden
  tests live in a separate private repository that only the merge queue can read, so no agent
  sandbox ever has them. A rebase conflict or failing test sends the change back to the agent that wrote it
  (rework, logged by cause).
- **Review cap (the V/h term):** a token bucket that lets through **R lines of change per hour**,
  standing in for a human reviewer. Changes wait in the queue until the bucket allows them. No LLM
  reviewer (cost), so the cap is exact and known. R is chosen so the model predicts the cap binds
  at N = 4–8 but not at N = 1–2, so both regimes are visible in one sweep.

## Measurements

Per run: finished changes (merged and passing hidden tests at the end of the window); rework
events by cause (rebase conflict, visible-test failure, hidden-test failure after merge); queue wait
times; agent busy vs waiting time; tokens and cost per finished change; any API errors or 429s with
timestamps (throttled time is excluded from the window and reported).

Derived: X(N), r(N) and U(N) per size; α and β fitted from the four points with p measured
directly (so conflicts are not counted twice); predicted vs observed U(N).

## Pre-registration (to be written before the runs)

Committed and pushed before the first paid run: the chosen R; the task-overlap measurement;
predicted α, β, p (p from study 1 and the task overlap); the predicted best N; which limit binds at
each N; and what would count against the model (e.g. output still rising at N = 8 with no rework
increase; or the review-capped plateau more than 30% off (1 − r)·V/h).

## Threats and mitigations

- **One replicate.** Single-run noise may swamp differences between adjacent sizes. Reported
  plainly; the pre-registration says the study can detect the shape (rise, peak, flatten), not
  small differences.
- **Task heterogeneity.** Fixed order means each size meets the same early tasks; later tasks differ
  by how far each run got. Mitigation: tasks drawn from a narrow difficulty band.
- **Rate limits and API errors.** Logged; throttled time excluded; if more than 10% of a window is
  lost the run is flagged.
- **Agents are not people.** The sweep tests the model for agent fleets with a merge queue, not
  human teams.
- **The harness itself.** A short pilot at N = 2 (≈ $10–15 of credit) before the sweep, which also
  measures the real burn rate per agent-hour.

## Deliverables

`analysis/fleet-sweep/` in the Carnot repo: the sandbox project and tasks, the harness, the
pre-registration, raw logs, the analysis script and a RESULTS.md. The paper gets a revision-log
entry with the result, whichever way it comes out.
