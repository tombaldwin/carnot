# Review of PLAN.md (fleet sweep, study 2) — before any credit is spent

Reviewer: Claude (Fable 5.1), 2026-09-27. Scope: the draft plan at `analysis/fleet-sweep/PLAN.md`,
read against the model (`plugins/carnot/skills/carnot/reference/model.md`, `scripts/carnot.py`),
the paper (`web-heat-death/.../heat-death-of-the-codebase/index.njk`) and the study 1
pre-registration (`analysis/aidev/PREREG.md`). Numbers below come from a small simulation
(Poisson attempts, binomial rework, λ = 4 attempts per agent-hour, model defaults α = 0.1,
β = 0.01, p = 0.05, r₀ = 0.4); the script is reproduced in the appendix so the assumptions can be
checked.

## Verdict: go with changes

The experiment is worth running, but not as drafted. The design as written spends most of its 30
agent-hours on the two questions it cannot answer (fitting α and β, and the review-cap plateau) and
under-spends on the one it can (does finished output stop rising while attempted output still
rises, i.e. does rework climb with N). With four single-replicate points, a least-squares fit of
α and β returns an 80% interval that spans the whole published range (α 0–0.48, β 0–0.06 against
truth 0.1, 0.01), so "peak below the USL peak" cannot be read off a fit. The review-cap plateau at
(1 − r)·V/h is an accounting identity once the token bucket is exact and r is measured, so it
tests nothing. Meanwhile the qualitative contrast — Carnot predicts finished output flat or falling
between N = 4 and N ≈ 10–16 while Amdahl-only predicts it nearly doubling and linear scaling
predicts it quadrupling — is detectable even at Poisson noise with one replicate, and comfortably
with two. The plan should be cut to that contrast, replicated, and pre-registered as three rival
point predictions rather than a curve fit. Two further things need settling before any credit is
spent: the harness must decide whether agents block on their PR (the model assumes N changes in
flight, the draft loop lets the in-flight count grow without bound), and the product facts on
cloud sessions (launch, autonomy, model choice, metering, rate limits) are all currently guesses.

## Must-fix

1. **Stop trying to fit α and β; test three pre-registered point predictions instead.**
   Simulation (appendix): with the plan's 1/2/4/8 × 1 × 2 h design under Carnot truth, the grid
   fit in `carnot.py fit_usl` recovers α in [0, 0.48] and β in [0, 0.06] (10th–90th percentile)
   when fitted to finished changes, and [0, 0.25] / [0, 0.034] when fitted to attempts. Those
   intervals include "no coordination cost at all" and "worse than Cursor's lock fleet". The
   derived quantity √((1−α)/β) is therefore meaningless from this data, and question (b) as posed
   is unanswerable. Replace it with: at the largest N, is finished output *below* what the
   attempts curve alone would give, by the pre-registered factor? Concretely, pre-register the
   three predicted finished counts per window for each rival (Carnot / Amdahl with constant r₀ /
   linear with constant r₀) and score them by Poisson log-likelihood. With parameters fixed in
   advance from the pilot, the plan's own design already picks the true family ~90% of the time
   at Poisson noise; the revised design below gets to ~98%. The paper's "Sweep the fleet"
   section (index.njk line 198: "Four points are enough to fit α and β") should be softened
   whatever this study finds; four points at ~10–30 events each are not enough.

2. **Drop the review-cap plateau test, or reframe it as a test of r(N) under load.** The plan
   says the cap is "exact and known" and r is "measured directly". Then finished = (1 − r) ×
   (cap throughput) is bookkeeping, not a prediction; a 30%-off criterion can only fail through a
   harness bug. The non-trivial content of the model's review term is (i) whether reworked changes
   *consume* review capacity (the paper says (1−r) multiplies the ceiling because reviewers spend
   time on changes that come back) and (ii) whether r keeps rising with N once the cap binds
   (in-flight count and queue wait both grow). Neither needs a separate cap regime with its own
   budget: (ii) is the same r(N) test as the main sweep, and (i) is a harness design decision the
   experiment should state, not discover. Recommendation: no token bucket in the main runs. If
   there is credit left at the end, run one N = 8 window with the cap on as an exploratory check
   of whether agents' behaviour changes when they are blocked (they may start larger tasks, or idle).

3. **Decide what an agent does while its PR sits in the queue, and make the in-flight count
   equal N.** The model's r(N) has exponent N − 1 because each agent has one change in flight.
   The draft loop (claim → branch → PR → claim the next) lets each agent pile up PRs, so in-flight
   = N + queue length, and at N = 8 with a serial queue that could be 20+. That would inflate
   rework relative to the model for a reason the model does not describe, and the fitted p from
   overlap would then not match. Two acceptable choices, both pre-registered: (a) an agent blocks
   until its PR is merged or bounced (in-flight = N exactly; the queue's serial time then appears
   as agent idle time, which is the α term); or (b) agents continue, and the analysis uses the
   *measured* in-flight count at each PR's creation as k, exactly as study 1 does, and tests
   r(k) not r(N). (b) yields many more data points per credit (every PR is an observation) and is
   the same test as study 1's H1/H3 in a controlled setting. I recommend (b) for the r term and
   (a)-style accounting for utilisation, i.e. log both.

4. **The merge queue is the α term by construction; measure it, do not "fit" it.** A one-at-a-time
   rebase + full test run of c minutes is a serial section of exactly c/(task time) per change,
   which is Amdahl's α, imposed by the harness. At N = 8 the model expects ~14 attempts/hour, so
   with a 5-minute CI the queue alone caps throughput at 12/hour before any agent-side
   coordination cost exists. Pre-register the CI time, predict the queue-imposed α from it, and
   report it as a harness parameter. Keep CI under 60–90 s (small project, fast test suite, no
   dependency install per run) so that the queue is not the binding constraint at the largest N
   you run. Otherwise the sweep measures your GitHub Actions latency.

5. **Rework accounting must be pinned down so p is not counted twice and reworked tasks are not
   counted as extra attempts.** The plan says "conflicts are not counted twice", but the loop
   sends a bounced change back to its author, who re-submits. Define, before the runs: an
   *attempt* = first PR for a task; a *rework event* = any bounce (rebase conflict, visible fail,
   hidden fail after merge) of that task, by cause; *finished* = task merged and green on hidden
   tests at window end; *agent time lost to rework* = time between bounce and re-submission. Then
   the throughput curve is attempts per agent-hour (this is λX), rework is events per attempt (r),
   and finished = attempts × (1 − share never finished). Note the model's (1−r) × λX treats a
   reworked change as lost; in the harness it is delayed, so also report the model's prediction
   under both readings. This is a pre-registration decision, not an analysis decision.

6. **Same task order for every N confounds task difficulty with N.** With ~120 tasks and 8–28
   attempts per run, N = 1 sees tasks 1–8 and N = 8 sees tasks 1–28; if difficulty drifts with
   position, the size effect is contaminated. Use a different random order per run (seeded, logged)
   and treat task as a random effect, or block: draw each run's tasks from the same difficulty
   stratum. Also state how tasks were sized (a single-agent dry run on a sample would calibrate
   λ and difficulty at once; it is the pilot's job).

7. **Reconcile with the study 1 results that are already in the repo.** `analysis/aidev/results.json`
   (and `run.log`) exist and report θ ≈ +0.0004 (p̂ ≈ −0.0004, CI [−0.0014, 0.0006]) for H1,
   p_m ≈ 0.0017 for H3, and an AIC gap of +4.4 against the geometric form. If that is a real run
   and not a smoke test, p is ~0.002, not 0.05, and at that p the Carnot curve is almost
   indistinguishable from Amdahl-with-constant-rework at any N ≤ 16. The plan says p comes from
   study 1; if p is ~0 the sweep's distinctive prediction vanishes and the pre-registration must
   say so up front (the sweep then tests whether rework rises *at all* under controlled
   concurrency, which is still worth knowing, but it is a different headline). Do not pick p after
   seeing the sweep.

8. **Verify the remaining product facts before building the harness** (see the open-questions
   list). Launch from a script and per-session model choice are documented; unattended 2-hour
   operation, the idle timeout, how the credits are metered, and behaviour under concurrent
   rate-limit pressure are not. A throttle that scales with concurrent sessions is
   indistinguishable from β, so item 5 of the open questions (a one-vs-four throughput trial) is
   a precondition for the sweep, not a nice-to-have.

## Should-fix

- **Pre-register a throttling detector, not just "log 429s".** Compare per-agent model latency
  and tokens per hour at the smallest and largest N; if per-agent tokens/hour falls with N by more
  than a pre-set amount (say 20%), the run is flagged as throttled regardless of visible 429s.
  This is the only way to separate rate limiting from coordination drag.
- **Hidden tests: define what feedback the agent gets.** If the queue reports which hidden test
  failed and how, agents will fit to the message and later attempts on the same task are not
  independent of the hidden suite. Pre-register the bounce message (e.g. "acceptance check failed"
  only). Also confirm the agent's GitHub credential cannot read the private test repo or the
  queue's Actions logs (a workflow that prints test names into a public log leaks the suite).
- **Warm-up and cooldown.** Cloning, reading the repo and the first task take several minutes per
  session; at a 2 h window that is ~5–10% per run and similar across N, but at shorter windows it
  is not. Exclude a fixed warm-up (say the first 10 min) from throughput, identically for every N,
  and pre-register it. Also decide whether in-flight PRs at the wall-clock end count if they merge
  within a grace period (recommend: 10 min grace, pre-registered).
- **Learning across runs.** The model has no memory between sessions unless the product provides
  one (to verify), but the *harness author* does. Freeze prompts, repo, task text and orchestrator
  after the pilot; hash them into the pre-registration commit.
- **Over-dispersion.** Poisson is the floor. Task heterogeneity, one agent going down a rabbit
  hole, and a queue stall are all common-mode shocks that make run-to-run variance larger than the
  mean. Two replicates are the minimum needed to estimate that at all; report the replicate
  spread alongside the Poisson SE.
- **Utilisation.** Log agent busy vs waiting so that if the queue or the cap binds, the cost of
  idle agents is visible; the model's "best N" claim is about output, but the practical claim is
  output per credit.
- **N = 1 is the least informative point per credit.** It yields ~8 attempts and ~5 finished in
  2 h (SD ≈ 2.2, 45% CV) and does not separate any rival, since all three agree at N = 1. λ and
  r₀ are better estimated from per-agent rates pooled across every run plus the pilot.

## Open questions to verify before spending (and how)

Each of these was a guess in the plan. A documentation check (by a helper agent against
code.claude.com and support.claude.com, 2026-09-27) settled some; the rest need a trial or a
support ticket. Record every answer in the pre-registration. Items marked *documented* still
deserve a two-minute confirmation on the account that will pay, since settings can be
org-specific.

1. **Launch and coordination.** *Documented:* a cloud session can be started from the CLI
   (`claude --cloud`) or by a routine (scheduled or API-triggered), so N sessions can be launched
   from a script. *Not documented:* any concurrency limit on parallel cloud sessions. Trial: start
   four in a burst, confirm all four run the loop and log their start times.
2. **Autonomy and duration.** *Documented:* cloud sessions stop after "a period of inactivity"
   and the VM is reclaimed; the period is not stated, and no max-turns or max-duration is
   published. *Not documented:* whether a session keeps claiming tasks for 2 h unprompted. Trial in
   the pilot: one session on a 3-task loop; does it claim the third task without a message?
   Fallback: the orchestrator sends "next task" messages, with that latency logged as harness
   time, not agent time. The plan's loop should be robust to a session dying mid-window (rerun
   rule, see pre-registration).
3. **Model choice.** *Documented:* the model can be chosen per cloud session (web UI dropdown,
   `/model` in-session, routine form), subject to an org allow-list. So Sonnet 5 is available
   unless the account restricts it. Confirm the allow-list on the paying account.
4. **Metering.** *Not documented:* a "$250 of cloud session credits" product. Public docs describe
   Claude Code as billed per token at API rates, with plan "usage credits" as an optional
   overage pool, and `/usage` showing session tokens; per-session dollar cost is not exposed in
   cloud sessions. So: (a) confirm in claude.ai settings what the meter actually is and how often
   it updates; (b) confirm CLI- and routine-launched sessions draw from it; (c) if the meter lags,
   replace the "$50 left" stop rule with a per-window estimate from the pilot. Cross-check the
   pilot burn against `/usage` token counts × list price so the credit-to-token rate is known.
5. **Rate limits.** *Documented:* cloud sessions share the account's 5-hour and weekly limits.
   *Not documented:* what happens at the limit in a cloud session (429, pause, queue) and whether
   concurrent sessions are collectively throttled. Trial: tokens/hour and tool-call latency for one
   session alone vs the same prompt with four running. This is the throttling detector's
   calibration and must be done before the sweep, not inferred from it.
6. **Network and credentials.** *Documented:* git operations are proxied through Anthropic's
   servers and the credential never enters the VM; a session can only reach repositories attached
   to it; pushes go only to `claude/`-prefixed branches, and protected branches are rejected.
   This is good for the hidden-test design: attach only the sandbox repo, keep the tests repo
   unattached and readable only by the queue's own token. *Confirm:* that the Actions logs of the
   queue are not readable by the session's GitHub identity, and that the `claude/` branch prefix
   is compatible with the queue's branch naming.
7. **Memory.** *Documented:* CLAUDE.md and auto-memory are per repository, stored in the repo's
   `.claude/` directory, and loaded at session start; cloud sessions are not documented as
   differing. So earlier runs *can* leak into later ones through the repo. Reset the repo to the
   frozen commit (including `.claude/`) before every run, and add `.claude/` state to the list of
   things the harness diff-checks after each run.
8. **GitHub merge queue.** GitHub's native merge queue batches and requires branch protection
   rules, and the push restriction to `claude/` branches above may interact with it. A home-grown
   serial queue in the orchestrator is simpler, gives exact timestamps and runs the hidden tests
   from a private checkout. Decide before building.

## Budget realism

Public API list prices (cached 2026-06): Sonnet 5 $2 / $10 per MTok in/out; Opus 5 $5 / $25;
cache reads are a fraction of the input price. A Claude Code agent doing 15-minute tasks on a
5–10k-line repo typically holds 40–100k of mostly-cached context across a few dozen turns per
task; at four tasks an hour that is on the order of several million cached-read tokens, under a
million fresh input tokens and tens of thousands of output (plus thinking) tokens per agent-hour.
At Sonnet list prices that lands at roughly $2–5 per agent-hour; at Opus 2–3× that. So $4.20 is
plausible for Sonnet if caching works and effort is not at maximum; $7 for Opus is optimistic.
None of this says how *credits* are metered, which is the real unknown.

**Pilot burn check (must be produced before the sweep).** From an N = 2, 45–60 min pilot: credit
consumed per agent-hour (from the meter delta, not from tokens), attempts per agent-hour (λ),
rework share (r₀ proxy), CI time per PR, and warm-up time. Decision rule, fixed in advance:

- burn ≤ $5/agent-hour → run the revised design in full (≈ 39 agent-hours);
- $5–8 → drop the second replicate at the larger size first, then shorten windows to 75 min;
- > $8 → run sizes 3 and 10 once each at 90 min (≈ 20 agent-hours) and report as a pilot;
- λ < 2 per agent-hour → tasks are too big for the design; halve task size before the sweep,
  since every power figure above scales with the event count.

Keep the $50 floor, but enforce it by per-window estimate (pilot burn × agent-hours scheduled)
rather than by reading the meter mid-sequence.

## Concrete revised design (fits $250 with margin)

Purpose: test one qualitative prediction that separates the model from its rivals — finished
output stops rising with N while attempts keep rising, because rework rises — and measure r as a
function of measured in-flight concurrency k on every PR.

- **Sizes: N = 3 and N = 10** (or 12 if concurrency and rate limits allow). These bracket the
  model's predicted best size (~6 at defaults) and give the largest predicted contrast per
  agent-hour: Carnot predicts the finished ratio U(10)/U(3) ≈ 1.05, Amdahl-only ≈ 2.1, linear
  ≈ 3.3. Simulation: with two replicates each at 90 min, the correct family is picked ~98% of the
  time at Poisson noise (vs ~92% for the plan's design at 30 agent-hours).
- **Replicates: 2 each**, different task-order seed per replicate, run order ABBA.
- **Windows: 90 min** including a pre-registered 10-min warm-up excluded from counts and a
  10-min merge grace period after the wall-clock end.
- **Agent-hours: 2 × (3 + 10) × 1.5 = 39**, ≈ $165 at $4.20, ≈ $195 at $5; plus pilot ≈ $10.
  Leaves the $50 floor at a $5 burn. If the pilot burn allows, add one N = 16 × 90 min window
  (24 agent-hours is too much; use 60 min = 16 agent-hours) as a single exploratory point, since
  a *fall* at N = 16 (Carnot predicts U(16) ≈ 0.6 × U(4)) is the only monotone-vs-non-monotone
  contrast available and no rival predicts it.
- **No token-bucket cap in the main runs** (must-fix 2). Keep CI under ~90 s so the serial queue
  is not binding at N = 10 (model expects ~14 attempts/hour there).
- **Per-PR measurements**: creation time, in-flight count k and file-overlapping in-flight count
  m at creation, outcome by cause, time in queue, time to re-submission. This gives ~80–100 PR
  observations across the sweep for a within-study replication of study 1's H1/H3, which is more
  informative per credit than the four run-level points.
- **Primary analysis (pre-registered script):** Poisson log-likelihood of finished counts under
  the three rivals with parameters fixed from the pilot (λ, r₀) and the model defaults (α, β, p),
  reported as a likelihood ratio; plus attempts per agent-hour vs N (is X(N)/N falling?), and
  r vs k and vs m across all PRs (logistic, as in study 1). Report the fitted α, β from
  `carnot.py fit` only as a descriptive with its grid-bootstrap interval, never as a test.
- **Falsification criteria (fixed in advance):** the model is counted against if (i) finished
  output at N = 10 exceeds 1.6 × finished at N = 3 (pooled replicates) with no rise in rework —
  that is the Amdahl/linear region; or (ii) rework share does not rise with in-flight k
  (logistic slope ≤ 0 with its CI excluding the pre-registered p); or (iii) attempts per
  agent-hour do not fall with N (no coordination drag at all, in which case α, β ≈ 0 and the
  paper's defaults are wrong for this setting). The model is counted *for* only if all three go
  its way; anything mixed is reported as mixed.

## Pre-registration checklist (commit and push before the first paid window)

- Sandbox repo commit hash; task list with text, order seeds per run, and the difficulty stratum
  rule; measured task-to-file overlap and the p it implies; agent prompt and settings; orchestrator
  and queue code hash; CI time measured on the pilot; warm-up and grace rules.
- Pilot outputs: burn per agent-hour, λ, r₀ proxy; the decision-rule outcome.
- Point predictions per window for each rival (a table of expected finished and attempted
  counts for N = 3 and N = 10, and N = 16 if run), with the parameter values used and their source
  (pilot, study 1, or model default), and the in-flight accounting choice (must-fix 3).
- The analysis script, runnable on a synthetic log, with the three falsification criteria coded
  as pass/fail, before any real log exists.
- Exclusion rules: throttling detector threshold, the >10% lost-time flag, and what happens to a
  run where a session dies (recommend: rerun the window if credit allows, else report as partial;
  never splice).
- What gets published whichever way it comes out, and the paper's revision-log entry template.

## Appendix: simulation

Assumptions: attempts per window ~ Poisson(λ·X(N)·hours), finished ~ Binomial(attempts, 1 − r(N)),
λ = 4/agent-hour. Rivals: Carnot (α 0.1, β 0.01, p 0.05, r₀ 0.4); Amdahl-only (α 0.1, r = r₀);
linear (r = r₀). Expected counts per 2 h window:

| N | Carnot attempts | Carnot finished | r(N) | Amdahl finished | Linear finished |
|---|---|---|---|---|---|
| 1 | 8.0 | 4.8 | 0.40 | 4.8 | 4.8 |
| 2 | 14.3 | 8.1 | 0.43 | 8.7 | 9.6 |
| 3 | 19.0 | 10.3 | 0.46 | 12.0 | 14.4 |
| 4 | 22.5 | 11.6 | 0.49 | 14.8 | 19.2 |
| 8 | 28.3 | 11.9 | 0.58 | 22.6 | 38.4 |
| 10 | 28.6 | 10.8 | 0.62 | 25.3 | 48.0 |
| 16 | 26.1 | 7.3 | 0.72 | 30.7 | 76.8 |

Note the Carnot finished curve is flat from N = 4 to N = 12 (11.6 → 9.6, within one Poisson SD),
so the *position* of the peak is not measurable at this budget; only "flat vs still rising" is.

Probability of picking the true family by Poisson likelihood with parameters known (best case,
2000 draws):

| Design | agent-h | truth Carnot → Carnot | truth Amdahl → Amdahl |
|---|---|---|---|
| Plan: 1,2,4,8 × 1, 2 h | 30 | 0.92 | 0.86 |
| 2,4,8 × 1, 2 h | 28 | 0.93 | 0.87 |
| 4,8 × 2, 2 h | 48 | 0.97 | 0.95 |
| **3,10 × 2, 1.5 h** | **39** | **0.98** | **0.98** |
| 4,12 × 2, 1.5 h | 48 | 0.99 | 0.99 |
| 2,16 × 1, 2 h | 36 | 1.00 | 1.00 |
| 8 × 1, 4 h | 32 | 0.98 | 0.94 |

Identifiability of α, β by the `carnot.py` grid fit on the plan's design, Carnot truth, 300 draws
(10th / 50th / 90th percentile): fitted to finished, α = 0.00 / 0.08 / 0.48, β = 0.000 / 0.019 /
0.060; fitted to attempts, α = 0.00 / 0.07 / 0.25, β = 0.000 / 0.009 / 0.034. Truth α = 0.1,
β = 0.01.

```python
import math, random
random.seed(1)
lam = 4.0
def carnot(n, a=0.1, b=0.01, p=0.05, r0=0.4):
    X = n / (1 + a*(n-1) + b*n*(n-1)); r = 1 - (1-r0)*(1-p)**(n-1)
    return lam*X, (1-r)*lam*X, r
def amdahl(n, a=0.1, r0=0.4):
    X = n / (1 + a*(n-1)); return lam*X, (1-r0)*lam*X, r0
def linear(n, r0=0.4): return lam*n, (1-r0)*lam*n, r0
def poisson(m):
    L = math.exp(-m); k = 0; p = 1
    while True:
        p *= random.random()
        if p < L: return k
        k += 1
def draw(model, n, hours):
    raw, u, r = model(n); att = poisson(raw*hours)
    return att, sum(1 for _ in range(att) if random.random() < 1-r)
def loglik(obs, model, sizes, hours):
    return sum(f*math.log(model(n)[1]*h) - model(n)[1]*h - math.lgamma(f+1)
               for (a, f), n, h in zip(obs, sizes, hours))
def classify(sizes, hours, truth, reps=2000):
    wins = {"carnot": 0, "amdahl": 0, "linear": 0}
    for _ in range(reps):
        obs = [draw(truth, n, h) for n, h in zip(sizes, hours)]
        lls = {k: loglik(obs, m, sizes, hours) for k, m in
               [("carnot", carnot), ("amdahl", amdahl), ("linear", linear)]}
        wins[max(lls, key=lls.get)] += 1
    return {k: v/reps for k, v in wins.items()}
```
