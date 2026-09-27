# Study 2 plan, version 4: is review the binding limit?

Supersedes PLAN-v3.md. The design comes from a simulation search over worker model, task size,
pilot, reviewer pace, fleet sizes and windows (analysis/design-search/DESIGN-SEARCH.md). The search
found that **no design within the budget reaches every target**: with realistic run-to-run noise,
the rivals USL and Amdahl cannot be told apart at affordable fleet sizes. What *can* be answered
reliably is whether output is capped by review, which is the paper's headline claim. This version is
built around that, and says in advance which results are confirmatory and which are descriptive.

Everything in PLAN-v3 carries over unless changed here: the git-only worker protocol, the local
single-change reviewer, hidden tests kept local, the accounting definitions (§6), exclusions (§8)
and the event-log schema (SCHEMA.md).

## 1. Claims, graded in advance

**Confirmatory, primary.** *Review is the binding limit.* Carnot's review-capped prediction has a
higher likelihood than the best of the uncapped rivals (USL, Amdahl, linear), with the likelihood
ratio reported. Simulated operating characteristics at the design point: correct 0.97 under Carnot
truth; 0.87 / 1.00 / 1.00 under USL / Amdahl / linear truth; 0.70 if agents are 30% slower than
assumed. These numbers go into the pre-registration.

**Confirmatory, secondary** (point predictions that are not identities of the harness):
- O2: finished work at N = 12 lies inside Carnot's 95% predictive interval.
- S3: the share of reviews that bounce, b, does not rise with fleet size (Fisher exact).
- O3: attempts rise with fleet size (exact rate-ratio test).

**Conditionally confirmatory.** *The reviewer's pace does not change with load.* V(12)/V(1) with its
interval, called stable if the interval lies inside [0.8, 1.25], and a Welch test on log review
durations. Confirmatory only if the pilot shows review-time CV ≤ 0.5, decided and recorded before
the first sweep window; otherwise descriptive.

**Descriptive** (reported whatever they show, with their simulated power stated in advance so a null
result is not read as evidence):
- the four-way ranking of rivals with the simulated confusion matrix (USL vs Amdahl is not claimed);
- escaped defects against reviewer queue depth (power ≈ 0.10);
- collisions against in-flight changes (power 0.09 at p = 0.01, 0.34 at p = 0.05);
- S1r/S2r surprise ratios (S2r fires about half the time under the model's own truth at N = 12, so it
  is not a criterion).

**Circularity, stated.** At the design point the reviewer is 92% loaded with one agent and about 3.2×
overloaded with twelve. A flat finished count at N = 12 is therefore expected by construction and is
not itself evidence; the claims that are not identities are listed above.

## 2. Design

| Item | Value |
|---|---|
| Workers | Claude Code cloud sessions, **Haiku 4.5**, identical prompt |
| Task size target | ≈ 8 first attempts per agent-hour (≈ 7.5 min of agent work). The 120 validated tasks (3–147 app lines, median ≈ 45) are used as they are; the pilot measures λ |
| Reviewer | one serial local reviewer (Opus 5.5, fresh call per change, never sees the queue), with the job defined so that V ≈ 2 × pilot λ (q = 2). Calibrated offline before the pilot, adjusted only before the sweep |
| Fleet sizes | **N = 1 and N = 12**, fixed in advance. No pilot gate |
| Windows | 3 per size, 120 min each (10 min warm-up, 10 min grace), order ABBAAB |
| T1 | 1 worker × 30 min, then 11 more × 15 min (12 at once): concurrency and throttling |
| T2 pilot | 8 separate 60-min windows of 1 worker, on different days / plan windows |
| Calibration | 120 local reviews (pilot PRs re-reviewed, reference and deliberately broken solutions), no credits |
| Merge queue | serial time ≤ 0.75 min per change |
| Analysis | as adopted: `completion` reading, S1r/S2r, V ratio + Vdur, `logit_cluster_task`; see analysis/README.md |

## 3. Budget

| Step | Session-hours | At $2.10 (Haiku, assumed) |
|---|---|---|
| T1 | 3.25 | $7 |
| T2 | 8 | $17 |
| Sweep | 3 × (1 + 12) × 2 = 78 | $164 |
| **Total** | 89.25 | **$187** |

The credit meter is read by the user before and after T1 and after T2. The real burn per
session-hour replaces the assumption; each sweep window starts only if the predicted balance after it
stays above $50. At 1.5× burn the pre-set degrade rule shortens the sweep windows (simulated: $199,
primary test 0.85).

## 4. Abort rules (checked in order; any failure stops the study before sweep spend)

1. T1: 12 concurrent sessions run, and per-worker tokens/hour at 12 is no more than 20% below 1.
2. T1/T2: merge-queue serial time ≤ 0.75 min per change.
3. T2: pilot λ ≥ 0.5 × target (4 per agent-hour).
4. Calibration: V within ±30% of 2 × pilot λ, else redefine the review job and recalibrate.
5. Credits: the predicted balance after the sweep ≥ $50.

If rule 3 fails, the study is reported as a pilot with its measurements and no sweep. If λ is low but
above the floor, the design's own simulation says the primary test degrades to about 0.70; that
figure is stated in the pre-registration before the sweep.

## 5. Order of work

1. Free: full dry run on the real sandbox and tasks with simulated workers and reviewer; measure
   merge-queue time; run derive/predict/score on the dry-run log.
2. Free: reviewer calibration on reference and broken solutions.
3. Final review of this plan (Fable), then the user's go-ahead.
4. Needs the user: a private GitHub repo for the sandbox, and meter readings.
5. Paid: T1, then T2 (abort rules 1–3).
6. Pre-registration committed and pushed: pilot measurements, calibrated V, point predictions, the
   confirmatory/descriptive split with operating characteristics.
7. Paid: the sweep.
