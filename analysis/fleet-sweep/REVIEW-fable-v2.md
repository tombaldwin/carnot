# Review of PLAN-v2.md (fleet sweep, study 2) — second round, before any credit is spent

Reviewer: Claude (Fable 5.1), 2026-09-27. Read in order: REVIEW-fable.md (round 1), analysis/aidev/RESULTS.md
(study 1), plugins/carnot/skills/carnot/reference/model.md (model and the new rule of thumb), then
PLAN-v2.md. Numbers below come from a discrete-event re-run of the round-1 simulation adapted to the v2
design (N = 3, 8; ABBA; 90-min windows with 10-min warm-up and 10-min grace; one FIFO reviewer whose
service time is exponential with mean 1/V; bounced PRs return after 10 min and are re-reviewed; λ = 4
attempts per agent-hour; α = 0.1, β = 0.01, p = 0.005, r₀ = 0.4). Scripts are in the appendix.

## Verdict: go with changes — but the pilot must be allowed to say "don't run the sweep as sized"

v2 is a real improvement: it drops the α/β fit, the token bucket and the same-order confound, logs k and m
per PR, pins rework definitions, uses study 1's p, and lists the product facts to verify with a budgeted
trial for the two that matter most. The new core design is sound in one important respect: with a serial
reviewer at a natural pace, the sweep's finished-count contrast is highly discriminating (≥ 0.9
probability of picking the right family at Poisson noise when the reviewer is saturated at both sizes,
robust to ±30% error in V and to 30% common-mode over-dispersion). But that power is bought with
circularity: a single serial reviewer *cannot* produce more than V approvals per hour, so "finished is
flat between N = 3 and N = 8" is a property of the harness whenever review demand at N = 3 already
exceeds V, and the Amdahl and linear rivals are ruled out by construction rather than by evidence. The
plan does not yet say what q the pilot must find for the design to be informative, and at plausible
values (a Sonnet session reviewing a 20–150-line PR in 3–6 min gives V ≈ 10–20/h against demand of
~10–16 first reviews/h at N = 3–8, or 16–24/h counting re-reviews) q can land anywhere from "saturated
at N = 2" to "never binding at N = 8", where the Carnot prediction collapses onto the uncapped USL and
the sweep tests nothing about review. The pre-registered falsification criterion (pooled finished(8) >
1.5 × finished(3)) fires falsely about one time in five under the model's own truth, because each window
yields only ~4–8 finished changes. And the accounting still lets queue delay masquerade as rework. The
fix is not more budget; it is (i) a pre-registered gate on the pilot's q that chooses N_low and N_high
(or aborts), (ii) re-aiming the headline at what a saturated reviewer *can* falsify — that V is a stable
property of the reviewer, that (1 − r₀) × V predicts finished work, and that escaped defects rise with
queue depth — and (iii) using the likelihood comparison, not the pooled ratio, as the criterion.

## Status of round-1 must-fix items

| # | Round-1 item | Status | Note |
|---|---|---|---|
| 1 | Stop fitting α, β; pre-register point predictions | **Fixed, one inconsistency** | Test 3 is descriptive and test 1 is a likelihood comparison of point predictions. But the Amdahl rival is written as (1 − r₀) × λ·X(N), and X(N) in model.md includes β, giving a ratio of 1.49 at N = 8/3, not the "about 1.8" the plan states (1.88 is the α-only form). The two forms differ by as much as Carnot-capped differs from either; state which one is the rival and add the uncapped USL as a fourth curve, since that is what "Carnot" becomes when review does not bind. |
| 2 | Drop the review-cap plateau or reframe it as a test of r(N)/V under load | **Partly** | The imposed bucket is gone and V is measured, which is the right move. But the primary test is still the plateau, and finished ≤ (approvals per hour) × hours is an identity for a serial reviewer. The non-trivial content — is V constant across N and across the window; do re-reviews consume capacity; does escape rise with depth — is only partly promoted: test 5 covers escape, nothing tests V-stability. See new must-fix B. |
| 3 | Decide in-flight behaviour; make k measured | **Fixed** | Option (b) chosen and k, m logged at creation. Note k now includes PRs waiting on the reviewer, so k at N = 8 will be dominated by queue depth; the k-slope in test 4 therefore mixes collision exposure with queue position (see must-fix C). |
| 4 | Merge queue is the harness's α; measure it, keep CI < 90 s | **Fixed** | Serial queue time measured and reported. There are now two serial stages (reviewer, then queue); say which one's time is included in "review completed" and in V. |
| 5 | Pin rework accounting | **Partly** | Definitions of attempt, rework event, finished, escaped defect are there. But r is defined as "share of attempts not finished", which at a saturated reviewer is mostly *queue truncation at window end*, not rework; and V is "reviews completed per hour" (including re-reviews) while the prediction multiplies min(λX, V) by (1 − r) as if V counted first reviews. Whether a merge-queue bounce (rebase conflict, visible fail) requires re-review is unstated. See must-fix C. |
| 6 | Randomise task order per window; calibrate difficulty | **Fixed for order, partly for calibration** | Fresh seeded order per window. "Drawn from one difficulty band calibrated in the pilot" is not achievable from a 45-min, 2-agent pilot (≈ 6 tasks); either calibrate difficulty by construction (author-estimated lines, files touched, tests count) and pre-register the rule, or accept a task random effect and say so. |
| 7 | Reconcile with study 1 | **Fixed** | p = 0.005 from study 1, headline moved to review. Study 1's estimates are 0.0017–0.004; say the prediction is insensitive across 0–0.005 (it is: r(8) − r(3) = 0.015). |
| 8 | Verify product facts before building | **Partly** | The table and T1 exist and are budgeted. Gaps: the "free" 2-session launch check draws credit if the sessions run; peak concurrency of 9 sessions (8 workers + reviewer) is never tried before the first paid N = 8 window (T1 tries 4); whether 5-hour/weekly plan rate limits apply to cloud sessions while credits remain is not on the list; the reviewer's idle behaviour (polling cost vs inactivity timeout) is not on the list. |

Round-1 should-fix items: throttling detector (in T1, with the 20% threshold — done); bounce message
("acceptance check failed" — done); warm-up/grace (done); freezing prompts/repo and diffing `.claude/`
(done); N = 1 dropped (done); over-dispersion "estimated from the replicate spread" (not estimable from
two replicates per size — see should-fix); worker utilisation logging (not mentioned; only reviewer queue
occupancy is).

## New must-fix items

**A. Gate the sweep on the pilot's q, and let q choose the sizes.** The design is only informative if
the pilot places review demand relative to V in a known regime. Pre-register, before T2:
- how V is estimated (see must-fix E on the pilot), how review demand is computed (λ·X(N) × expected
  reviews per attempt, i.e. 1/(1 − b) where b is the per-review bounce rate, not 1/(1 − r₀) as an
  assumption), and
- a rule that maps (λ, V, b) to sizes: N_high = smallest N with demand ≥ 1.5 V (cheaper than 8 if the
  reviewer is slow; larger than 8 if it is fast, in which case the budget must be re-cut or the study
  aborted), N_low = largest N with demand ≤ 0.7 V (so one point sits below the knee and the rule of
  thumb's *location* is tested, not only the plateau), and
- an abort/redesign rule: if demand at N = 8 is below V (q(1 − b) > X(8) ≈ 3.5), the review limit
  cannot be reached within budget; either shrink tasks to raise λ, or make the reviewer's job heavier by
  pre-registered prompt (e.g. it must run the visible tests and read the issue's acceptance text; this
  is still a natural pace, just for a defined job), or do not run.

Simulation of the discriminating power as a function of q (V exact unless stated; 4 windows; 3000
draws; Poisson log-likelihood scoring as the plan specifies; "Amdahl truth" means the reviewer keeps
up):

| q = V/λ | Carnot mean finished/window N=3 → N=8 | P(Carnot picked \| Carnot), cv 0 / 0.3 | with V̂ = 0.7 V | with V̂ = 1.3 V | P(Amdahl picked \| Amdahl), cv 0 / 0.3 | with V̂ = 1.3 V, cv 0.3 |
|---|---|---|---|---|---|---|
| 1.5 | 3.9 → 3.7 | 1.00 / 1.00 | 0.99–1.00 | 1.00 | 0.96 / 0.82 | 0.80 |
| 2.0 | 5.4 → 5.2 | 1.00 / 1.00 | 0.97–0.98 | 1.00 | 0.93 / 0.78 | 0.70 |
| 2.5 | 6.6 → 6.7 | 0.98 / 0.99 | 0.89–0.91 | 1.00 | 0.89 / 0.72 | 0.62 |
| 3.0 | 7.7 → 8.3 | 0.95 / 0.96 | 0.80–0.85 | 0.98–0.99 | 0.85 / 0.64 | 0.56 |
| 4.0 | 9.5 → 10.9 | 0.75 / 0.82 | 0.59–0.66 | 0.75–0.82 | 0.75 / 0.55 | 0.55 |

Reading: when the reviewer is saturated at both sizes (q ≤ 2.5) the family is picked correctly ≥ 0.9
of the time even with V mis-estimated by 30% and with 30% common-mode noise — but in that regime the
result is guaranteed by the harness (see B). From q ≈ 3 upwards, where the result would be informative,
power falls quickly and becomes sensitive to V̂; under-estimating V by 30% at q = 4 gives a 41% chance of
misreading the model's own truth as Amdahl. Note also the count level: 4–8 finished per window, so a
single window has a 35–50% coefficient of variation.

**B. Break the circularity by re-aiming the headline, and pre-register the surprising outcomes.** Once
demand > V at both sizes, Carnot's "flat" prediction is a queueing identity, and Amdahl/linear are
falsified by the existence of a serial reviewer, not by the data. What is *not* an identity, and should
be the pre-registered content of test 1 and test 2:
1. **V is a property of the reviewer, not of the load.** Predict V(N = 8) = V(N = 3) = V(pilot) within a
   stated band (say ±25%), measured as reviews completed per reviewer-busy-hour, and also first-half vs
   second-half of each window. Reviewer speeding up under load (shorter reviews when many are waiting)
   would falsify the fixed-capacity assumption behind the rule of thumb; slowing down (queue anxiety,
   context growth, compaction) would too, in the other direction.
2. **Finished ≈ (1 − b) × V × hours** when saturated, where b is the per-review bounce rate — this is
   the model's (1 − r₀) × cap claim, and it is falsifiable if b rises with N (reviewer stricter under
   load), if re-reviews are not the only extra demand (e.g. rebase churn), or if the reviewer approves
   more freely under load (b falls, finished rises above the band while escape rises).
3. **Attempts per agent-hour keep rising with N** (workers are not slowed by an unreviewed backlog).
   Under option (b) this is expected; a *fall* (agents wait, rebase repeatedly, or exhaust the task list)
   is a real finding about USL α, β in this setting.
4. **Escaped defects rise with queue depth** (test 5), which is the "unverified change is entropy" claim
   and the only test in the plan that no rival predicts.

State explicitly which outcomes would surprise the model: finished(8) ≥ 1.3 × finished(3) with the
queue non-empty (reviewer scales with load); finished(8) ≤ 0.7 × finished(3) with the queue non-empty
(review capacity consumed super-linearly by re-review churn or the reviewer slowing); bounce rate b
rising with N; escape rate flat or falling with depth while V rises. These are the informative results;
a flat finished curve on its own is not.

**C. Fix the accounting so queue delay is not scored as rework, and the prediction uses the same
quantities as the measurement.**
- Define b = bounces per review (by cause: review requested changes / rebase conflict / visible fail /
  hidden fail), and report it per window and against k, m, queue depth. This is the quantity the
  collision test (test 4) should model, with a PR's outcome being "bounced at least once" — not "not
  finished by window end".
- Treat attempts still open at window end + grace as *censored*, not as rework. r as "share of attempts
  not finished" will otherwise rise mechanically with N at a saturated reviewer, and test 4's logistic on
  k will report a strong positive slope for a reason that has nothing to do with collisions; the plan's
  "counts against study 1's conclusion: p > 0.03" could then trigger spuriously.
- Define V as *reviews completed per reviewer-busy-hour* (all reviews, including re-reviews), and write
  the capped prediction as finished = (1 − b_hidden) × (approvals per hour) × hours, where approvals per
  hour = V × (1 − b_review). Or define V as first-reviews per hour and say so; either is fine, mixed is
  not.
- State whether a merge-queue bounce (rebase conflict, visible fail) goes back through the reviewer.
  Recommend: yes, always (so every re-submission is one more review; simple and consistent with the
  model's "reworked changes consume review time"), and log it as such.
- Separate *escaped defect* (hidden tests fail on the exact head the reviewer approved) from
  *integration failure* (hidden tests pass on the approved head but fail after rebase on a moved main).
  Run the hidden suite on both. Only the first is a reviewer miss; the second is the collision term.
  Without this split, test 5 attributes collisions to review load and vice versa.

**D. The reviewer must not see the queue, must not be a single 90-minute context, and must have a
failure rule.**
- "Reviewing open PRs oldest first" means the session lists open PRs and therefore sees the queue
  length every cycle, whatever the prompt says. Either the orchestrator hands the reviewer exactly one
  PR at a time (a labelled PR or a message naming it; the reviewer never lists), or accept that queue
  visibility is part of what is being tested and say so. Recommend the former for the primary run.
- A single session reviewing 15–30 PRs over 90 min accumulates context, hits compaction, and changes
  pace and quality as it goes; at N = 8 it sees more PRs, so N is confounded with reviewer context
  length. Pre-register one of: (i) a fresh cloud session per review (V then includes session start-up,
  which is fine — it is the natural pace of the job as defined — and it removes drift; cost per review
  rises by the start-up tokens, to be measured in the pilot), or (ii) one long session with per-review
  duration, tokens and approval logged as a time series and a pre-registered drift test (first vs last
  third). (i) is cleaner; verify that per-review session start-up is fast enough not to dominate V.
- The reviewer is a single point of failure: if it dies at minute 40 the window is void. Pre-register a
  heartbeat, a restart rule (restart within X min; log the gap as reviewer-down time), and a validity
  rule (reviewer down > 10 min or > 2 restarts → window void; rerun if credit allows, never splice).
  With a fresh session per review (i), this largely disappears.

**E. Make the pilot estimate V, not just λ.** T2 as budgeted (2 workers + reviewer, 45 min) yields ≈ 6
attempts and ≈ 6–10 reviews: V estimated from that has a standard error of 30–40%, which the table above
shows is tolerable at low q and damaging at the q where the result would be informative. Cheap fix: keep
the PRs produced in T1 (the throttling trial's 5 sessions will open perhaps 10–15 PRs) and have the
reviewer session review all of them plus T2's, giving 20–25 reviews for V, b and per-review tokens. Also
pre-register the pilot's outputs as: λ, V (with interval), b by cause, mean review tokens, mean worker
tokens per attempt, session start-up time, CI time, and the q-gate decision from must-fix A.

**F. Budget and decision rule.** The arithmetic is 42.9 session-hours (39 sweep + 1.7 T1 + 2.3 T2);
at $4.20 that is $180 and the $69 headroom holds. At $5/session-hour, which the plan's own rule accepts
as "full sweep", the total is $215 and the floor is breached ($35 left); at $6.50 with 75-min windows it
is ≈ $236. Re-derive the thresholds from the floor: full sweep only if burn ≤ ($200 − pilot spend)/39 ≈
$4.60; 75-min windows up to ≈ $5.50; one replicate above that. Two further points:
- the reviewer's burn is not a worker's burn. A saturated reviewer at N = 8 reads diffs continuously;
  an under-loaded one at N = 3 either polls (tokens for nothing) or waits for orchestrator messages and
  risks the inactivity timeout. Measure reviewer $/hour separately in T2 and budget it separately; with
  a fresh session per review, budget per review instead.
- peak concurrency is 9 sessions; T1 tests 4. Either run T1's burst at 9 (2 more short sessions,
  ≈ $3) or pre-register that the first N = 8 window's first 10 minutes are the concurrency test, with an
  abort rule if fewer than 8 workers are running by minute 10. Also add to the verify table whether the
  plan's 5-hour/weekly rate limits apply to cloud sessions while credits remain; if they do, 13.5
  session-hours in a 90-min window is the exposure, and the ABBA sequence should be spread across
  5-hour windows.

**G. Replace the pooled-ratio criterion with the likelihood comparison, and give test 5 a stated
minimum.** Under the model's own truth the pooled f(8)/f(3) exceeds 1.5 in 17–23% of simulated sweeps
(counts are too small), and under Amdahl truth it exceeds 1.5 only 70–77% of the time. The Poisson
log-likelihood comparison the plan already specifies does far better (table above) and should be the
pre-registered criterion, with the ratio reported as a descriptive. For test 5, pre-register the model
(logistic: escaped defect ~ queue depth at review + time-in-window + window fixed effect, with task as a
random effect or stratum), and a minimum number of escaped-defect events (say 8) below which it is
reported as descriptive only; at ~30–50 approvals across the sweep and an escape rate of 10–30% that is
not assured.

## Should-fix

- **Same model reviewing same model.** Correlated blind spots inflate escape and deflate b relative to a
  human or different-model reviewer. This does not threaten the within-study contrasts (both sizes share
  it) but does limit what the paper can say about "review capacity" in general. State it as a limit. If
  the org allow-list permits and the pilot burn allows, a different model for the reviewer would be a
  cheap way to weaken the correlation; do not switch models between windows.
- **Over-dispersion cannot be estimated from two replicates per size.** Pre-register a fixed
  quasi-Poisson dispersion (e.g. φ = 1.5) or a negative-binomial with a fixed CV of 0.3, and report the
  replicate spread as a check; simulations above show conclusions at low q survive cv = 0.3.
- **Escaped defects vs time-in-window.** Queue depth grows monotonically over the window at a saturated
  reviewer, so depth is nearly collinear with time. Time-in-window must be in the model (must-fix G),
  and the N = 3 windows, where depth fluctuates, provide most of the identifying variation; say so.
- **Worker utilisation.** Log busy vs waiting for every worker (claim wait, rebase, idle); the
  practical claim is output per credit, and at N = 8 most agent-hours are spent producing PRs that sit
  in a queue.
- **Idle reviewer and the inactivity timeout.** At N = 3 the reviewer may be idle for minutes at a
  time; confirm in T2 that the session survives, or use per-review sessions.
- **Task list exhaustion.** 8 agents × 1.5 h × ~1.8 attempts per agent-hour ≈ 22 tasks per N = 8
  window plus re-submissions; four windows draw ~60 distinct tasks from 120. Fine, but confirm no task
  repeats across windows within a replicate pair and log the draw.
- **Prediction table format.** Publish the point predictions as finished and attempted counts per
  window for each rival, plus the predicted V, b and escape rate, with parameter sources; the current
  text describes them but the pre-registration must contain the numbers.
- **Paper wording.** model.md's "if αq + βq² ≥ 1, review never binds" and "finished ≈ (1 − r₀) × cap"
  should be checked against the b-vs-r₀ distinction in must-fix C: cap in reviews per day supports
  (1 − b) × cap approvals, of which a further share fail integration.

## Pre-registration non-negotiables (commit before the first paid window)

1. The q-gate and size-selection rule (must-fix A), with the abort condition, written before T2 runs.
2. Definitions: attempt, review, bounce by cause, censored, finished, escaped defect vs integration
   failure, V (busy-hour basis), b; and the rule that every re-submission is re-reviewed.
3. Point predictions per window (finished, attempts, V, b, escape) for Carnot-capped, USL-uncapped,
   Amdahl α-only and linear, with parameter values and sources, and the analysis script run on a
   synthetic log with the likelihood criterion and the "surprise" outcomes (must-fix B) coded pass/fail.
4. Reviewer protocol: one-PR-at-a-time hand-off, session lifetime (per review or long-lived with drift
   test), heartbeat/restart/void rules.
5. Budget rule re-derived from the $50 floor with reviewer burn measured separately; concurrency test at
   9 sessions; rate-limit applicability verified.
6. Exclusions: throttling detector (T1-calibrated, 20%), reviewer-down rule, worker-death rule, never
   splice; what gets published whichever way it comes out.

## Appendix: simulation

Discrete-event model per window: attempts arrive as a Poisson process at rate λ·X(N) (USL for Carnot,
α-only for Amdahl, N for linear), optionally multiplied by a gamma common-mode factor with CV `cv`;
attempts arriving in the first 10 min are excluded from counts; one FIFO reviewer with Exp(1/V) service;
approval with probability 1 − r(N); bounced PRs return after 10 min and are re-reviewed; reviews finishing
after 100 min are dropped. Amdahl/linear truths use an infinitely fast reviewer. Scoring: Poisson
log-likelihood of the four finished counts under each rival's point prediction, predictions using
V̂ = V(1 + err). Scripts: `sim_v2.py` and `sim_v2b.py` (this session's scratchpad); core functions
reproduced below.

```python
ALPHA, BETA, P, R0, LAM = 0.1, 0.01, 0.005, 0.4, 4.0
WARM, WIN, GRACE, REWORK_DELAY = 10/60, 90/60, 10/60, 10/60
def X_usl(n): return n / (1 + ALPHA*(n-1) + BETA*n*(n-1))
def X_amd(n): return n / (1 + ALPHA*(n-1))
def r_of(n): return 1 - (1-R0)*(1-P)**(n-1)
def des_window(n, V, cv, model="carnot", finite_reviewer=True):
    mult = 1.0 if cv <= 0 else random.gammavariate(1/cv**2, cv**2)
    rate = LAM * (X_usl(n) if model=="carnot" else X_amd(n) if model=="amdahl" else n) * mult
    r = r_of(n) if model=="carnot" else R0
    events=[]; t=0.0
    while True:
        t += random.expovariate(rate)
        if t > WIN: break
        events.append(t)
    attempts = sum(1 for tt in events if tt >= WARM)
    if not finite_reviewer:
        return attempts, sum(1 for tt in events if tt >= WARM and random.random() < 1-r)
    q=[(tt,i) for i,tt in enumerate(events)]; heapq.heapify(q); free=0.0; fin=0
    while q:
        arr,i = heapq.heappop(q); done = max(arr,free) + random.expovariate(V)
        if done > WIN+GRACE: break
        free = done
        if random.random() < 1-r: fin += events[i] >= WARM
        else: heapq.heappush(q,(done+REWORK_DELAY,i))
    return attempts, fin
def predictions(n, V_hat, hours=WIN-WARM):
    return {"carnot": (1-r_of(n))*min(LAM*X_usl(n), V_hat)*hours,
            "amdahl": (1-R0)*LAM*X_amd(n)*hours, "linear": (1-R0)*LAM*n*hours}
```

Additional outputs used above: review demand including re-reviews at b = 0.4 is 11.9 / 15.9 / 23.6
reviews per hour at N = 2 / 3 / 8 against first-review demand of 7.1 / 9.5 / 14.2; P(pooled f8/f3 > 1.5)
under Carnot truth is 0.16–0.23 across q = 1.5–4 and cv = 0–0.3; under Amdahl truth 0.70–0.77; under
linear truth 0.93–0.99.
