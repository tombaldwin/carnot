# Study 2 design search: is there a design within the budget that reaches the targets?

Simulation only. No network, API, GitHub or cloud calls were made. The work extends `synth.py`, `derive.py`,
`predict.py` and `score.py` without changing them. It is driven by `dsim.py` (one simulated study),
`runner.py` (parallel runs with common random numbers across designs), `design_search.py` (stages A–C),
`final.py` (the shortlist, V-test power and robustness), `ceiling.py` (an upper bound that holds for any
design) and `tables.py` (the tables below). The raw results are in `stageA.json`, `stageB.json`,
`stageC_final.json`, `robustness.json` and `ceiling.json`. The exploratory scripts `explore*.py` and
`diag*.py` produced the diagnostics quoted in section 1.

## Answer in one paragraph

**No design within $200 reaches the targets.** The shortfall is structural. With CV 0.3 window-to-window
over-dispersion, the four-rival likelihood cannot tell USL from Amdahl: their shapes differ by only about
33% between N = 1 and N = 12. The pilot's level estimate carries the same 30% window noise. Even with a
perfect pilot, no in-budget sweep with N_max ≤ 8 exceeds min-recovery 0.72 (Sonnet) or 0.76 (Haiku). Reaching
0.84–0.90 needs N_max = 12 and 3–4 replicates, and that ceiling assumes no pilot error. With a realistic pilot,
the best design found recovers the correct family 0.97 / 0.67 / 0.69 / 0.91 of the time (Carnot / USL / Amdahl /
linear; **min 0.67**). The binary question the study actually asks, *review-capped or not*, is answered correctly
**≥ 0.87** of the time under every truth. No design gives the pre-registered V-constancy coding (O1n) 0.8 power
against a ±25% reviewer. Its false-positive rate is 0.14–0.21 when review times are exponential, and its power
is capped near 0.5–0.6 because the ±25% band sits exactly at the effect size. A duration-based test reaches
0.68–0.94 power at FPR ≈ 0.05, but only if review times have CV ≤ 0.5. The escaped-defect and collision tests
stay underpowered: escape 0.10; collision slope 0.09 at p = 0.01 and 0.34 at p = 0.05. PLAN-v3 as written
recovers Carnot 17% of the time and USL 2% under the same assumptions. The pre-registration should make
"review-capped vs uncapped" and the V/finished point predictions confirmatory. The four-way family ranking,
escape-vs-depth and collisions should be descriptive (section 5).

## 1. Method and assumptions

**One simulated study** is the whole pipeline: T1 (1 worker for 15 min, then N_max at once for 15 min; its PRs
reviewed), then T2 pilot windows, then `derive.pilot_params`, then `predict.Params(rival_rework="completion")`,
then (for gated designs) `predict.gate`, then the sweep windows (`synth.simulate`, sizes in ABBA order), then
`derive_window`. Scoring uses `score.rival_scores` (NB likelihood, CV 0.3, ties counted as failures),
`score.score` for S1r/S2r/S4n/O1n (two extreme sizes), `escape_analysis(..., "logit_cluster_task")` and
`collision_analysis`. The analysis decisions are as instructed: `completion` reading, S1r/S2r, S4n/O1n,
`logit_cluster_task`.

**Truths** (synth.py families): Carnot, meaning USL workers plus a fixed-capacity FIFO reviewer; USL, Amdahl
and linear workers with a reviewer that speeds up to meet any demand. Every window, the pilot windows
included, gets a Gamma(mean 1, **CV 0.3**) multiplier on worker speed. For the V test there are two extra truths:
*skimN* and *slowN*, in which the reviewer's pace at the high fleet size is ×1.25 or ×0.75 of its pace at the
low size and in the pilot. That is the only load channel open to a reviewer that is handed one PR and never
sees the queue (for example, rate limits shared with the workers). I also ran a queue-depth step version (×1.25 or
×0.75 whenever someone is waiting). It realises only V_hi/V_lo ≈ 1.12 / 0.86, because the N = 1 arm queues too,
so it understates a 25% effect; it is kept in `stageC_final.json` as `skim`/`slow`. The other planted truths:
escape (catch probability × exp(−0.2 · depth)), collisions p = 0.01 and p = 0.05, and a null with p = 0.

**Levers and their mapping into the simulation:**

| Lever | Values searched | How it enters |
|---|---|---|
| Worker | Sonnet 5 at $4.20/session-h; Haiku 4.5 at $2.10 | Haiku: defect rate 0.49 (r0 ×1.3: 0.42 to 0.54); λ = 0.85 × task-size target assumed when V is set, truth scenarios 0.7× and 1.0× |
| Task size | λ1 = 4, 6, 8 attempts per agent-hour | synth `lam1`; rework time scales as 6 min × 4/λ |
| Reviewer capacity | q = V/λ1 = 1.5, 2, 2.5, 3 (V fixed at design time) | synth `V0`, service time exponential (CV 1); CV 0.5 as a sensitivity |
| Sizes | fixed (2,6), (2,8), (3,9), (1,6), (1,8), (1,10), (1,12), (1,3,8), (1,4,10), (1,4,12); the PLAN-v3 gate | three-size designs are scored on all windows; V and S codings use the extreme sizes |
| Windows | 60 / 90 / 120 min, 1–4 replicates per size | 10-min warm-up, 10-min grace |
| Pilot | T2 1, 2 or 4 workers; 60 or 120 min; 1, 2, 4 or 8 separate windows; T1 reviewed or not | T2 windows are separate runs, so each gets its own CV-0.3 draw |
| Free reviewer calibration (added lever) | 0 or 120 reviews | The local reviewer, which costs no credits, reviews 120 diffs (for example, the pilot's PRs plus reference and mutant solutions) at no load. Their pace has a lognormal shift of sd 0.10 (0.20 as a sensitivity) relative to live PRs. The reviews are pooled into pilot V. |
| Merge queue | serial time 0.75 min per change (harness requirement); synth default 2.6 min as a sensitivity | see below |

**Budget.** Cost = $/session-hour × (T1 + T2 + sweep worker-hours). T1 is 0.5 + 0.25 × (N_max − 1) hours. The
reviewer, the calibration and the orchestrator are local and free. Designs must total ≤ $200, which is $250
less the $50 floor. If the realised burn is higher, a pre-registered degrade rule applies: keep the pilot, then
take the largest sweep that fits, with fewer replicates and/or windows shortened in 15-min steps down to
60 min.

**Search.** The search ran in three stages:
- **Stage A:** all 2,136 in-budget sweep shapes with a fixed good pilot, the 4 family truths, 100 replicates each (854k studies).
- **Stage B:** 33 shapes carried forward (the best by four-way and by binary recovery at each q) × 18 pilot options, 100 replicates.
- **Stage C:** 25 designs plus 6 PLAN-v3 references, 12 truths, **800 replicates**, which gives a 95% half-width of ±2.8 points at 0.8 and ±3.5 at 0.5.

Robustness used 800 replicates per cell.

**Two findings that shaped the search (diagnostics, `diag1.py`, `diag2.py`):**
1. *Merge queue.* At synth's default 2.6 min of serial merge-queue time per change, the queue caps finished work
   at about 23 per hour. At N ≥ 8 and λ ≥ 6 that cap binds under the linear and Amdahl truths, so they look
   capped. Linear-truth recovery falls from 0.91 to **0.01** for the recommended design (robustness table). The
   harness must keep the serial part of the merge queue at ≤ ~0.75 min per change: a ≤ 30 s post-rebase suite,
   with hidden-pre tests run off the serial path or kept very short. All results below assume this.
2. *The rivals' completion share is not constant in N, even under the uncapped truths.* It drifts from about 0.71
   to 0.64 between N = 2 and N = 8 under USL (drag slows rework, so more work is censored, and collisions grow
   with k). This is a misspecification of the `completion` reading, and it pushes USL truths toward "capped"
   (Carnot) picks.

## 2. Results

### Where the ceiling is (any design, perfect pilot)

The table below uses oracle parameters, window counts drawn NB(CV 0.3), and the pre-registered likelihood.
There is no rework, censoring, merge-queue or pilot error. "Level profiled out" means the pilot supplies no
level, so only shape is scored.

| Worker | N_max allowed | best sizes, reps x min, lambda, q | cost $ | C / U / A / L | min (oracle level) | min (level profiled out) |
|---|---|---|---|---|---|---|
| sonnet | 6 | 1/6, 4 x 60, 6, 2 | 143 | 0.95 / 0.61 / 0.54 / 0.84 | 0.54 | 0.35 |
| sonnet | 8 | 2/8, 4 x 60, 8, 2.5 | 193 | 0.91 / 0.72 / 0.72 / 0.94 | 0.72 | 0.46 |
| sonnet | 10 | 1/10, 3 x 60, 8, 1.5 | 164 | 0.99 / 0.83 / 0.76 / 0.94 | 0.76 | 0.50 |
| sonnet | 12 | 1/12, 3 x 60, 8, 1.5 | 189 | 0.99 / 0.86 / 0.84 / 0.96 | 0.84 | 0.58 |
| haiku | 6 | 1/6, 4 x 120, 8, 1.5 | 130 | 1.00 / 0.72 / 0.59 / 0.90 | 0.59 | 0.43 |
| haiku | 8 | 1/8, 4 x 120, 8, 1.5 | 164 | 1.00 / 0.84 / 0.76 / 0.93 | 0.76 | 0.58 |
| haiku | 10 | 1/10, 4 x 120, 8, 2 | 197 | 0.99 / 0.87 / 0.86 / 0.97 | 0.86 | 0.64 |
| haiku | 12 | 1/12, 4 x 60, 8, 1.5 | 122 | 0.99 / 0.91 / 0.90 / 0.98 | 0.90 | 0.69 |

With N_max ≤ 8, which is the plan's range and its concurrency test, **no design can reach 0.8 even with a
perfect pilot**. With CV 0.3 per window, the USL-vs-Amdahl contrast needs very wide size ranges and many
windows.

### Stage A levers (best and mean four-way min-recovery over the 2,136 shapes)

- **worker**: haiku: best 0.63, mean 0.26, sonnet: best 0.59, mean 0.27
- **lam**: 4: best 0.40, mean 0.20, 6: best 0.56, mean 0.28, 8: best 0.63, mean 0.31
- **q**: 1.5: best 0.63, mean 0.31, 2: best 0.56, mean 0.29, 2.5: best 0.47, mean 0.25, 3: best 0.42, mean 0.21
- **L**: 60: best 0.63, mean 0.26, 90: best 0.60, mean 0.27, 120: best 0.56, mean 0.27
- **reps**: 1: best 0.50, mean 0.24, 2: best 0.59, mean 0.28, 3: best 0.58, mean 0.28, 4: best 0.63, mean 0.28
- **sizes**: 1/12: best 0.63, 1/10: best 0.56, 1/4/12: best 0.55, 3/9: best 0.49, 1/4/10: best 0.48, 1/8: best 0.43, 2/8: best 0.41, 1/6: best 0.39, 1/3/8: best 0.39, 2/6: best 0.38

Task size matters: λ = 8 beats 6, which beats 4, because more counts per window reduce Poisson noise. Wide size
ranges beat everything else. The PLAN's (2,8), (2,6) and (3,9) pairs top out at 0.38–0.49, and three sizes do
not beat the widest pair. Worker model and window length matter little once cost is equalised; Haiku's lower
price buys replicates.

### Pilot levers (stage B)

| T2 workers | window min | windows | free calibration reviews | T1 reviewed | T2 worker-h | mean min recovery | mean capped-vs-uncapped min | pilot V / true V, 10-50-90% (Carnot) |
|---|---|---|---|---|---|---|---|---|
| 1 | 60 | 8 | 120 | yes | 8.0 | 0.522 | 0.800 | 0.89 / 1.00 / 1.13 |
| 1 | 60 | 8 | 0 | yes | 8.0 | 0.486 | 0.743 | 0.85 / 0.99 / 1.15 |
| 2 | 60 | 8 | 120 | yes | 16.0 | 0.476 | 0.733 | 0.90 / 1.01 / 1.11 |
| 1 | 60 | 4 | 120 | yes | 4.0 | 0.452 | 0.802 | 0.88 / 1.00 / 1.14 |
| 2 | 60 | 4 | 120 | yes | 8.0 | 0.446 | 0.749 | 0.89 / 1.01 / 1.13 |
| 1 | 60 | 4 | 0 | yes | 4.0 | 0.423 | 0.731 | 0.83 / 1.01 / 1.21 |
| 2 | 60 | 8 | 0 | yes | 16.0 | 0.375 | 0.620 | 0.88 / 1.01 / 1.14 |
| 2 | 120 | 1 | 120 | yes | 4.0 | 0.364 | 0.678 | 0.87 / 1.01 / 1.14 |
| 1 | 120 | 1 | 120 | yes | 2.0 | 0.348 | 0.710 | 0.87 / 1.01 / 1.15 |
| 2 | 60 | 4 | 0 | yes | 8.0 | 0.332 | 0.626 | 0.85 / 1.01 / 1.20 |
| 1 | 120 | 1 | 0 | yes | 2.0 | 0.318 | 0.613 | 0.78 / 1.02 / 1.34 |
| 2 | 60 | 1 | 120 | yes | 2.0 | 0.314 | 0.638 | 0.87 / 1.01 / 1.15 |
| 1 | 60 | 1 | 120 | yes | 1.0 | 0.271 | 0.665 | 0.86 / 1.00 / 1.16 |
| 2 | 120 | 1 | 0 | yes | 4.0 | 0.259 | 0.514 | 0.81 / 1.00 / 1.29 |
| 1 | 60 | 1 | 0 | yes | 1.0 | 0.240 | 0.596 | 0.76 / 1.02 / 1.44 |
| 2 | 60 | 1 | 0 | yes | 2.0 | 0.239 | 0.575 | 0.78 / 1.03 / 1.40 |
| 2 | 60 | 1 | 0 | no | 2.0 | 0.224 | 0.565 | 0.73 / 1.05 / 1.51 |
| 4 | 60 | 1 | 0 | yes | 4.0 | 0.156 | 0.408 | 0.77 / 1.02 / 1.32 |

The pilot should run **one worker, in several separate windows**. The anchor (λ × completion share) takes one
CV-0.3 draw per pilot window, so eight 60-min windows beat one 120-min window by a wide margin. Pilots at N = 2
to 4 are worse: at a slow reviewer they are partly review-limited, which depresses the completion share that
every rival is scaled by. n_p = 4 is the worst option (0.16). The free calibration adds about 0.03–0.10 and
narrows the pilot's V error from roughly 0.76–1.44 to 0.89–1.13 (10–90%). Reviewing T1's PRs helps slightly
(0.24 vs 0.22).

### Ranked table (stage C, 800 replicates per truth; search designs by four-way min, then PLAN-v3 references)

Column notes:
- **Load** is the design-point review demand ÷ V: λ1·X(N)/(1 − b)/V, where 1/(1 − b) is 1.64 for Sonnet and 1.84 for Haiku (measured in synth). Review v2's non-circularity rule needs ≤ 0.7 at N_low and ≥ 1.5 at N_high.
- **Capped-vs-uncapped** counts Carnot truth as correct when Carnot is picked, and an uncapped truth as correct when Carnot is not picked.
- **V test** is O1n FAIL rate against the ±25% fleet-size reviewer; FPR is O1n FAIL under Carnot. "Vdur" is a Welch t-test on log review durations, high vs low size.
- **Escape** is the planted depth effect g = 0.2 detected with ≥ 8 events. **Collision** is the planted p, detected as a one-sided k-slope.
- **Gate RUN** applies only to the gated references: 0.33 for PLAN-v3 and 0.54–0.55 with the better pilot.

| # | Sizes, windows | Worker, lambda, V/h (q) | Pilot (T2) | Cost $ | Agent-h | Load at N_low / N_high (x V) | C / U / A / L recovered | **min** | Capped-vs-uncapped: C / U / A / L | **bin min** | V test +/-25% at N_high: O1n power, FPR (svc CV 1 / 0.5) | Vdur power (svc CV 1 / 0.5), FPR | S1r / S2r false alarm | Escape power a .05 / .01 | Collision k-slope, p .01 / .05 (a .05); p .05 at a .01 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | 1 & 12; 4 x 90 min | haiku, 8, 10 (1.5) | 2 w x 8 x 60 min + cal 120 | 177 | 97.2 | 1.23 / 4.30 | 0.91 / 0.71 / 0.69 / 0.91 | **0.69** | 0.91 / 0.87 / 1.00 / 1.00 | **0.87** | 0.43, 0.19 / 0.20, 0.00 | 0.13 / 0.51, 0.05 | 0.03 / 0.73 | 0.14 / 0.05 | 0.07 / 0.17; 0.05 |
| 2 | 1 & 12; 3 x 120 min | haiku, 8, 14 (2) | 1 w x 8 x 60 min + cal 120 | 187 | 89.2 | 0.92 / 3.23 | 0.97 / 0.67 / 0.69 / 0.91 | **0.67** | 0.97 / 0.87 / 1.00 / 1.00 | **0.87** | 0.54, 0.18 / 0.37, 0.01 | 0.19 / 0.68, 0.06 | 0.05 / 0.53 | 0.10 / 0.03 | 0.09 / 0.34; 0.12 |
| 3 | 1 & 12; 3 x 120 min | haiku, 8, 10 (1.5) | 1 w x 8 x 60 min + cal 120 | 187 | 89.2 | 1.23 / 4.30 | 0.97 / 0.71 / 0.66 / 0.94 | **0.66** | 0.97 / 0.95 / 1.00 / 1.00 | **0.95** | 0.47, 0.17 / 0.29, 0.00 | 0.15 / 0.59, 0.04 | 0.05 / 0.51 | 0.09 / 0.03 | 0.09 / 0.27; 0.12 |
| 4 | 1 & 12; 2 x 90 min | sonnet, 8, 12 (1.5) | 1 w x 8 x 60 min + cal 120 | 184 | 50.2 | 1.09 / 3.84 | 0.96 / 0.66 / 0.64 / 0.76 | **0.64** | 0.96 / 0.84 / 1.00 / 1.00 | **0.84** | 0.32, 0.14 / 0.07, 0.00 | 0.10 / 0.31, 0.06 | 0.17 / 0.44 | 0.08 / 0.03 | 0.08 / 0.19; 0.06 |
| 5 | 1 & 12; 2 x 90 min | sonnet, 8, 16 (2) | 1 w x 8 x 60 min + cal 120 | 184 | 50.2 | 0.82 / 2.88 | 0.95 / 0.61 / 0.63 / 0.72 | **0.61** | 0.95 / 0.77 / 0.99 / 1.00 | **0.77** | 0.35, 0.14 / 0.12, 0.00 | 0.10 / 0.36, 0.04 | 0.18 / 0.36 | 0.10 / 0.03 | 0.07 / 0.23; 0.09 |
| 6 | 1 & 12; 3 x 120 min | haiku, 8, 14 (2) | 1 w x 8 x 60 min | 187 | 89.2 | 0.92 / 3.23 | 0.96 / 0.60 / 0.69 / 0.91 | **0.60** | 0.96 / 0.80 / 1.00 / 1.00 | **0.80** | 0.45, 0.17 / 0.18, 0.00 | 0.19 / 0.68, 0.06 | 0.05 / 0.52 | 0.10 / 0.03 | 0.09 / 0.34; 0.12 |
| 7 | 1 & 4 & 10; 3 x 90 min | haiku, 8, 14 (2) | 1 w x 8 x 60 min + cal 120 | 164 | 78.2 | 0.92 / 3.29 | 0.97 / 0.60 / 0.59 / 0.89 | **0.59** | 0.97 / 0.86 / 0.99 / 1.00 | **0.86** | 0.45, 0.18 / 0.23, 0.00 | 0.15 / 0.53, 0.05 | 0.07 / 0.49 | 0.12 / 0.04 | 0.07 / 0.29; 0.10 |
| 8 | 1 & 12; 3 x 120 min | haiku, 8, 17 (2.5) | 1 w x 8 x 60 min + cal 120 | 187 | 89.2 | 0.74 / 2.58 | 0.93 / 0.56 / 0.70 / 0.90 | **0.56** | 0.93 / 0.77 / 1.00 / 1.00 | **0.77** | 0.53, 0.19 / 0.45, 0.01 | 0.20 / 0.71, 0.04 | 0.07 / 0.47 | 0.15 / 0.05 | 0.08 / 0.40; 0.17 |
| 9 | 1 & 12; 3 x 120 min | haiku, 8, 14 (2) | 1 w x 4 x 60 min + cal 120 | 179 | 85.2 | 0.92 / 3.23 | 0.92 / 0.61 / 0.56 / 0.84 | **0.56** | 0.92 / 0.87 / 1.00 / 1.00 | **0.87** | 0.52, 0.20 / 0.34, 0.02 | 0.19 / 0.68, 0.06 | 0.08 / 0.51 | 0.10 / 0.03 | 0.09 / 0.34; 0.12 |
| 10 | 1 & 12; 3 x 120 min | haiku, 6, 13 (2.5) | 1 w x 8 x 60 min + cal 120 | 187 | 89.2 | 0.74 / 2.58 | 0.89 / 0.52 / 0.63 / 0.94 | **0.52** | 0.89 / 0.75 / 1.00 / 1.00 | **0.75** | 0.46, 0.20 / 0.30, 0.01 | 0.14 / 0.56, 0.04 | 0.07 / 0.50 | 0.17 / 0.06 | 0.07 / 0.28; 0.10 |
| 11 | 1 & 12; 2 x 120 min | haiku, 8, 17 (2.5) | 1 w x 8 x 60 min + cal 120 | 133 | 63.2 | 0.74 / 2.58 | 0.93 / 0.51 / 0.65 / 0.89 | **0.51** | 0.93 / 0.72 / 0.99 / 1.00 | **0.72** | 0.42, 0.17 / 0.27, 0.00 | 0.13 / 0.56, 0.06 | 0.10 / 0.45 | 0.14 / 0.04 | 0.08 / 0.30; 0.13 |
| 12 | 1 & 8; 4 x 120 min | haiku, 8, 10 (1.5) | 1 w x 8 x 60 min + cal 120 | 173 | 82.2 | 1.23 / 4.34 | 0.98 / 0.56 / 0.47 / 0.95 | **0.47** | 0.98 / 0.98 / 1.00 / 1.00 | **0.98** | 0.58, 0.18 / 0.36, 0.00 | 0.20 / 0.69, 0.05 | 0.03 / 0.56 | 0.11 / 0.02 | 0.07 / 0.31; 0.11 |
| 13 | 1 & 3 & 8; 3 x 120 min | haiku, 8, 17 (2.5) | 1 w x 8 x 60 min + cal 120 | 173 | 82.2 | 0.74 / 2.61 | 0.94 / 0.46 / 0.49 / 0.90 | **0.46** | 0.94 / 0.84 / 0.98 / 1.00 | **0.84** | 0.60, 0.21 / 0.46, 0.00 | 0.20 / 0.70, 0.06 | 0.06 / 0.49 | 0.15 / 0.05 | 0.09 / 0.44; 0.20 |
| 14 | 1 & 4 & 10; 3 x 90 min | haiku, 8, 14 (2) | 1 w x 4 x 60 min + cal 120 | 156 | 74.2 | 0.92 / 3.29 | 0.90 / 0.53 / 0.45 / 0.83 | **0.45** | 0.90 / 0.85 / 0.99 / 1.00 | **0.85** | 0.46, 0.20 / 0.22, 0.01 | 0.15 / 0.53, 0.05 | 0.08 / 0.51 | 0.12 / 0.04 | 0.07 / 0.29; 0.10 |
| 15 | 1 & 10; 4 x 120 min | haiku, 8, 20 (3) | 1 w x 8 x 60 min + cal 120 | 184 | 98.8 | 0.61 / 2.19 | 0.89 / 0.43 / 0.64 / 0.91 | **0.43** | 0.89 / 0.67 / 1.00 / 1.00 | **0.67** | 0.62, 0.20 / 0.51, 0.01 | 0.20 / 0.78, 0.06 | 0.06 / 0.43 | 0.19 / 0.06 | 0.07 / 0.41; 0.17 |
| 16 | 1 & 4 & 12; 1 x 90 min | sonnet, 8, 20 (2.5) | 1 w x 8 x 60 min + cal 120 | 154 | 36.8 | 0.66 / 2.30 | 0.91 / 0.43 / 0.54 / 0.75 | **0.43** | 0.91 / 0.64 / 0.96 / 1.00 | **0.64** | 0.29, 0.17 / 0.08, 0.00 | 0.08 / 0.27, 0.06 | 0.20 / 0.34 | 0.10 / 0.03 | 0.07 / 0.31; 0.13 |
| 17 | 1 & 10; 1 x 120 min | sonnet, 8, 20 (2.5) | 1 w x 8 x 60 min + cal 120 | 138 | 32.8 | 0.66 / 2.34 | 0.91 / 0.41 / 0.47 / 0.76 | **0.41** | 0.91 / 0.71 / 0.92 / 1.00 | **0.71** | 0.36, 0.18 / 0.12, 0.00 | 0.09 / 0.35, 0.05 | 0.16 / 0.39 | 0.10 / 0.03 | 0.10 / 0.30; 0.11 |
| 18 | 1 & 10; 4 x 90 min | haiku, 8, 20 (3) | 1 w x 8 x 60 min + cal 120 | 161 | 76.8 | 0.61 / 2.19 | 0.89 / 0.41 / 0.61 / 0.89 | **0.41** | 0.89 / 0.62 / 0.99 / 1.00 | **0.62** | 0.58, 0.19 / 0.46, 0.01 | 0.17 / 0.71, 0.06 | 0.08 / 0.46 | 0.18 / 0.06 | 0.06 / 0.35; 0.12 |
| 19 | 2 & 8; 4 x 120 min | haiku, 8, 20 (3) | 1 w x 8 x 60 min + cal 120 | 190 | 90.2 | 1.10 / 2.17 | 0.87 / 0.39 / 0.52 / 0.89 | **0.39** | 0.87 / 0.75 / 0.99 / 1.00 | **0.75** | 0.66, 0.17 / 0.65, 0.01 | 0.30 / 0.94, 0.05 | 0.06 / 0.30 | 0.19 / 0.06 | 0.10 / 0.50; 0.25 |
| 20 | 1 & 3 & 8; 3 x 120 min | haiku, 8, 17 (2.5) | 1 w x 4 x 60 min + cal 120 | 164 | 78.2 | 0.74 / 2.61 | 0.85 / 0.40 / 0.38 / 0.86 | **0.38** | 0.85 / 0.82 / 0.98 / 1.00 | **0.82** | 0.59, 0.23 / 0.42, 0.00 | 0.20 / 0.70, 0.06 | 0.07 / 0.48 | 0.15 / 0.05 | 0.09 / 0.44; 0.20 |
| 21 | 1 & 8; 4 x 120 min | haiku, 8, 10 (1.5) | 1 w x 4 x 60 min + cal 120 | 164 | 78.2 | 1.23 / 4.34 | 0.95 / 0.53 / 0.37 / 0.92 | **0.37** | 0.95 / 0.97 / 1.00 / 1.00 | **0.95** | 0.56, 0.19 / 0.34, 0.01 | 0.20 / 0.69, 0.05 | 0.05 / 0.53 | 0.11 / 0.02 | 0.07 / 0.31; 0.11 |
| 22 | 1 & 3 & 8; 3 x 120 min | haiku, 8, 17 (2.5) | 2 w x 8 x 60 min + cal 120 | 190 | 90.2 | 0.74 / 2.61 | 0.89 / 0.37 / 0.48 / 0.90 | **0.37** | 0.89 / 0.79 / 0.98 / 1.00 | **0.79** | 0.61, 0.22 / 0.48, 0.00 | 0.20 / 0.70, 0.06 | 0.02 / 0.68 | 0.15 / 0.05 | 0.09 / 0.44; 0.20 |
| 23 | 1 & 10; 4 x 120 min | haiku, 8, 20 (3) | 1 w x 8 x 60 min | 184 | 98.8 | 0.61 / 2.19 | 0.88 / 0.37 / 0.64 / 0.91 | **0.37** | 0.88 / 0.60 / 0.99 / 1.00 | **0.60** | 0.52, 0.17 / 0.24, 0.00 | 0.20 / 0.78, 0.06 | 0.06 / 0.44 | 0.19 / 0.06 | 0.07 / 0.41; 0.17 |
| 24 | 1 & 8; 4 x 120 min | haiku, 8, 20 (3) | 1 w x 4 x 60 min + cal 120 | 164 | 78.2 | 0.61 / 2.17 | 0.78 / 0.35 / 0.42 / 0.85 | **0.35** | 0.78 / 0.76 / 0.98 / 1.00 | **0.76** | 0.63, 0.22 / 0.56, 0.01 | 0.25 / 0.82, 0.06 | 0.07 / 0.46 | 0.14 / 0.04 | 0.10 / 0.47; 0.24 |
| 25 | 2 & 8; 4 x 120 min | haiku, 8, 20 (3) | 1 w x 4 x 60 min + cal 120 | 181 | 86.2 | 1.10 / 2.17 | 0.78 / 0.34 / 0.39 / 0.84 | **0.34** | 0.78 / 0.76 / 0.98 / 1.00 | **0.76** | 0.66, 0.19 / 0.66, 0.02 | 0.30 / 0.94, 0.05 | 0.08 / 0.33 | 0.19 / 0.06 | 0.10 / 0.50; 0.25 |
| 26 | **Gate + better pilot**: gate; 3 x 60 min | sonnet, 8, 26 (3.2) | 1 w x 4 x 60 min + cal 120 | 105 | 33.2 | - | 0.47 / 0.10 / 0.13 / 0.26 | **0.10** | 0.47 / 0.20 / 0.32 / 0.43 | **0.20** | 0.25, 0.09 / 0.17, 0.00 | 0.05 / 0.28, 0.02 | 0.08 / 0.11 | 0.09 / 0.03 | 0.04 / 0.11; 0.04 |
| 27 | **Fixed (2, 8), plan pilot**: 2 & 8; 2 x 90 min | sonnet, 4, 13 (3.2) | 2 w x 1 x 60 min | 144 | 34.2 | 0.92 / 1.81 | 0.41 / 0.08 / 0.19 / 0.65 | **0.08** | 0.41 / 0.50 / 0.71 / 0.92 | **0.41** | 0.21, 0.14 / 0.01, 0.00 | 0.10 / 0.34, 0.04 | 0.17 / 0.23 | 0.10 / 0.02 | 0.05 / 0.12; 0.04 |
| 28 | **Fixed (3, 9), plan pilot**: 3 & 9; 1 x 90 min | sonnet, 4, 13 (3.2) | 2 w x 1 x 60 min | 95 | 22.5 | 1.22 / 1.83 | 0.42 / 0.07 / 0.17 / 0.64 | **0.07** | 0.42 / 0.47 / 0.66 / 0.88 | **0.42** | 0.17, 0.16 / 0.00, 0.00 | 0.06 / 0.18, 0.06 | 0.23 / 0.19 | 0.03 / 0.01 | 0.05 / 0.11; 0.04 |
| 29 | **Gate + better pilot, lambda 4**: gate; 2 x 90 min | sonnet, 4, 13 (3.2) | 1 w x 4 x 60 min + cal 120 | 100 | 33.2 | - | 0.34 / 0.06 / 0.09 / 0.33 | **0.06** | 0.34 / 0.25 / 0.33 / 0.45 | **0.25** | 0.17, 0.09 / 0.05, 0.00 | 0.03 / 0.13, 0.02 | 0.07 / 0.13 | 0.03 / 0.01 | 0.02 / 0.05; 0.02 |
| 30 | **PLAN-v3 sizes fixed (1, 5)**: 1 & 5; 2 x 90 min | sonnet, 4, 13 (3.2) | 2 w x 1 x 60 min | 90 | 21.5 | 0.51 / 1.60 | 0.33 / 0.04 / 0.12 / 0.61 | **0.04** | 0.33 / 0.50 / 0.60 / 0.79 | **0.33** | 0.20, 0.15 / 0.01, 0.00 | 0.07 / 0.27, 0.04 | 0.13 / 0.25 | 0.06 / 0.01 | 0.04 / 0.11; 0.04 |
| 31 | **PLAN-v3 (gate)**: gate; 2 x 90 min | sonnet, 4, 13 (3.2) | 2 w x 1 x 60 min | 91 | 31.2 | - | 0.17 / 0.02 / 0.04 / 0.15 | **0.02** | 0.17 / 0.14 / 0.15 / 0.23 | **0.14** | 0.05, 0.03 / 0.00, 0.00 | 0.02 / 0.09, 0.01 | 0.04 / 0.08 | 0.02 / 0.00 | 0.02 / 0.04; 0.01 |

### V-constancy tests in detail (power against the ±25% reviewer, FPR under Carnot)

| Design | truth | O1n | S4n | Vratio (exact CI of V_hi/V_lo excl. 1) | Vdur (Welch, log review durations, hi vs lo) | Vcal (hi vs free calibration) | median V_hi/V_lo |
|---|---|---|---|---|---|---|---|
| H lam8 q1.5 N1/12 120m x3 | T2 1w 8x60m T1r cal120 (service CV 1.0) | carnot | 0.17 | 0.10 | 0.04 | 0.04 | 0.08 | 1.01 |
| H lam8 q1.5 N1/12 120m x3 | T2 1w 8x60m T1r cal120 (service CV 1.0) | skimN | 0.47 | 0.39 | 0.19 | 0.17 | 0.27 | 1.25 |
| H lam8 q1.5 N1/12 120m x3 | T2 1w 8x60m T1r cal120 (service CV 1.0) | slowN | 0.54 | 0.46 | 0.24 | 0.15 | 0.23 | 0.75 |
| H lam8 q1.5 N1/12 120m x3 | T2 1w 8x60m T1r cal120 (service CV 0.5) | carnot | 0.00 | 0.00 | 0.00 | 0.06 | 0.20 | 1.00 |
| H lam8 q1.5 N1/12 120m x3 | T2 1w 8x60m T1r cal120 (service CV 0.5) | skimN | 0.29 | 0.27 | 0.04 | 0.59 | 0.73 | 1.25 |
| H lam8 q1.5 N1/12 120m x3 | T2 1w 8x60m T1r cal120 (service CV 0.5) | slowN | 0.42 | 0.40 | 0.08 | 0.69 | 0.77 | 0.74 |
| S lam8 q1.5 N1/12 90m x2 | T2 1w 8x60m T1r cal120 (service CV 1.0) | carnot | 0.14 | 0.08 | 0.03 | 0.06 | 0.08 | 0.99 |
| S lam8 q1.5 N1/12 90m x2 | T2 1w 8x60m T1r cal120 (service CV 1.0) | skimN | 0.32 | 0.24 | 0.10 | 0.10 | 0.18 | 1.22 |
| S lam8 q1.5 N1/12 90m x2 | T2 1w 8x60m T1r cal120 (service CV 1.0) | slowN | 0.41 | 0.33 | 0.14 | 0.10 | 0.16 | 0.73 |
| S lam8 q1.5 N1/12 90m x2 | T2 1w 8x60m T1r cal120 (service CV 0.5) | carnot | 0.00 | 0.00 | 0.00 | 0.05 | 0.15 | 1.00 |
| S lam8 q1.5 N1/12 90m x2 | T2 1w 8x60m T1r cal120 (service CV 0.5) | skimN | 0.07 | 0.07 | 0.00 | 0.31 | 0.59 | 1.24 |
| S lam8 q1.5 N1/12 90m x2 | T2 1w 8x60m T1r cal120 (service CV 0.5) | slowN | 0.15 | 0.15 | 0.02 | 0.37 | 0.58 | 0.73 |
| H lam8 q1.5 N1/12 90m x4 | T2 2w 8x60m T1r cal120 (service CV 1.0) | carnot | 0.19 | 0.10 | 0.04 | 0.05 | 0.07 | 1.00 |
| H lam8 q1.5 N1/12 90m x4 | T2 2w 8x60m T1r cal120 (service CV 1.0) | skimN | 0.43 | 0.35 | 0.16 | 0.14 | 0.27 | 1.26 |
| H lam8 q1.5 N1/12 90m x4 | T2 2w 8x60m T1r cal120 (service CV 1.0) | slowN | 0.49 | 0.39 | 0.22 | 0.13 | 0.18 | 0.75 |
| H lam8 q1.5 N1/12 90m x4 | T2 2w 8x60m T1r cal120 (service CV 0.5) | carnot | 0.00 | 0.00 | 0.00 | 0.04 | 0.20 | 1.00 |
| H lam8 q1.5 N1/12 90m x4 | T2 2w 8x60m T1r cal120 (service CV 0.5) | skimN | 0.20 | 0.19 | 0.02 | 0.51 | 0.72 | 1.25 |
| H lam8 q1.5 N1/12 90m x4 | T2 2w 8x60m T1r cal120 (service CV 0.5) | slowN | 0.39 | 0.37 | 0.07 | 0.58 | 0.70 | 0.74 |
| H lam8 q2 N1/12 120m x3 | T2 1w 8x60m T1r cal120 (service CV 1.0) | carnot | 0.18 | 0.09 | 0.04 | 0.06 | 0.08 | 0.99 |
| H lam8 q2 N1/12 120m x3 | T2 1w 8x60m T1r cal120 (service CV 1.0) | skimN | 0.54 | 0.44 | 0.23 | 0.21 | 0.28 | 1.24 |
| H lam8 q2 N1/12 120m x3 | T2 1w 8x60m T1r cal120 (service CV 1.0) | slowN | 0.62 | 0.55 | 0.30 | 0.19 | 0.30 | 0.74 |
| H lam8 q2 N1/12 120m x3 | T2 1w 8x60m T1r cal120 (service CV 0.5) | carnot | 0.01 | 0.01 | 0.00 | 0.05 | 0.23 | 0.99 |
| H lam8 q2 N1/12 120m x3 | T2 1w 8x60m T1r cal120 (service CV 0.5) | skimN | 0.37 | 0.34 | 0.07 | 0.68 | 0.76 | 1.25 |
| H lam8 q2 N1/12 120m x3 | T2 1w 8x60m T1r cal120 (service CV 0.5) | slowN | 0.60 | 0.55 | 0.18 | 0.78 | 0.82 | 0.74 |
| S lam8 q2 N1/12 90m x2 | T2 1w 8x60m T1r cal120 (service CV 1.0) | carnot | 0.14 | 0.09 | 0.03 | 0.04 | 0.08 | 0.97 |
| S lam8 q2 N1/12 90m x2 | T2 1w 8x60m T1r cal120 (service CV 1.0) | skimN | 0.35 | 0.29 | 0.11 | 0.13 | 0.20 | 1.22 |
| S lam8 q2 N1/12 90m x2 | T2 1w 8x60m T1r cal120 (service CV 1.0) | slowN | 0.43 | 0.36 | 0.14 | 0.10 | 0.19 | 0.73 |
| S lam8 q2 N1/12 90m x2 | T2 1w 8x60m T1r cal120 (service CV 0.5) | carnot | 0.00 | 0.00 | 0.00 | 0.06 | 0.14 | 0.98 |
| S lam8 q2 N1/12 90m x2 | T2 1w 8x60m T1r cal120 (service CV 0.5) | skimN | 0.12 | 0.12 | 0.01 | 0.36 | 0.64 | 1.24 |
| S lam8 q2 N1/12 90m x2 | T2 1w 8x60m T1r cal120 (service CV 0.5) | slowN | 0.24 | 0.23 | 0.02 | 0.42 | 0.69 | 0.73 |
| S lam8 q2.5 N1/10 120m x1 | T2 1w 8x60m T1r cal120 (service CV 1.0) | carnot | 0.18 | 0.09 | 0.04 | 0.05 | 0.07 | 1.00 |
| S lam8 q2.5 N1/10 120m x1 | T2 1w 8x60m T1r cal120 (service CV 1.0) | skimN | 0.36 | 0.28 | 0.08 | 0.13 | 0.18 | 1.21 |
| S lam8 q2.5 N1/10 120m x1 | T2 1w 8x60m T1r cal120 (service CV 1.0) | slowN | 0.42 | 0.34 | 0.15 | 0.09 | 0.23 | 0.72 |
| S lam8 q2.5 N1/10 120m x1 | T2 1w 8x60m T1r cal120 (service CV 0.5) | carnot | 0.00 | 0.00 | 0.00 | 0.05 | 0.16 | 0.99 |
| S lam8 q2.5 N1/10 120m x1 | T2 1w 8x60m T1r cal120 (service CV 0.5) | skimN | 0.12 | 0.11 | 0.01 | 0.35 | 0.63 | 1.23 |
| S lam8 q2.5 N1/10 120m x1 | T2 1w 8x60m T1r cal120 (service CV 0.5) | slowN | 0.22 | 0.21 | 0.01 | 0.40 | 0.67 | 0.74 |
| S lam8 q2.5 N1/4/12 90m x1 | T2 1w 8x60m T1r cal120 (service CV 1.0) | carnot | 0.17 | 0.10 | 0.02 | 0.06 | 0.09 | 1.00 |
| S lam8 q2.5 N1/4/12 90m x1 | T2 1w 8x60m T1r cal120 (service CV 1.0) | skimN | 0.29 | 0.21 | 0.08 | 0.10 | 0.12 | 1.18 |
| S lam8 q2.5 N1/4/12 90m x1 | T2 1w 8x60m T1r cal120 (service CV 1.0) | slowN | 0.36 | 0.28 | 0.12 | 0.08 | 0.17 | 0.72 |
| S lam8 q2.5 N1/4/12 90m x1 | T2 1w 8x60m T1r cal120 (service CV 0.5) | carnot | 0.00 | 0.00 | 0.00 | 0.04 | 0.13 | 0.98 |
| S lam8 q2.5 N1/4/12 90m x1 | T2 1w 8x60m T1r cal120 (service CV 0.5) | skimN | 0.08 | 0.08 | 0.00 | 0.27 | 0.56 | 1.24 |
| S lam8 q2.5 N1/4/12 90m x1 | T2 1w 8x60m T1r cal120 (service CV 0.5) | slowN | 0.12 | 0.12 | 0.01 | 0.33 | 0.64 | 0.72 |

### Robustness of the three lead designs (800 replicates per truth)

| Scenario | Design | realised cost $ | C / U / A / L recovered | min | capped-vs-uncapped C / U / A / L | bin min |
|---|---|---|---|---|---|---|
| base | q 2.5 (strictly non-circular alternative) | 187 | 0.94 / 0.58 / 0.69 / 0.90 | 0.58 | 0.94 / 0.80 / 1.00 / 1.00 | 0.80 |
| base | **recommended (q 2)** | 187 | 0.97 / 0.65 / 0.67 / 0.91 | 0.65 | 0.97 / 0.89 / 1.00 / 1.00 | 0.89 |
| base | Sonnet, q 2.5, 1 & 10, 1 x 120 | 138 | 0.92 / 0.41 / 0.47 / 0.75 | 0.41 | 0.92 / 0.68 / 0.93 / 1.00 | 0.68 |
| burn x1.5 | q 2.5 (strictly non-circular alternative) | 199 | 0.94 / 0.51 / 0.62 / 0.88 | 0.51 | 0.94 / 0.76 / 0.99 / 1.00 | 0.76 |
| burn x1.5 | **recommended (q 2)** | 199 | 0.97 / 0.60 / 0.60 / 0.90 | 0.60 | 0.97 / 0.85 / 0.99 / 1.00 | 0.85 |
| burn x1.5 | Sonnet, q 2.5, 1 & 10, 1 x 120 | 189 | 0.91 / 0.40 / 0.46 / 0.77 | 0.40 | 0.91 / 0.67 / 0.93 / 1.00 | 0.67 |
| lambda x0.7 | q 2.5 (strictly non-circular alternative) | 187 | 0.66 / 0.25 / 0.62 / 0.94 | 0.25 | 0.66 / 0.50 / 0.96 / 1.00 | 0.50 |
| lambda x0.7 | **recommended (q 2)** | 187 | 0.82 / 0.47 / 0.61 / 0.94 | 0.47 | 0.82 / 0.70 / 0.98 / 1.00 | 0.70 |
| lambda x0.7 | Sonnet, q 2.5, 1 & 10, 1 x 120 | 138 | 0.64 / 0.20 / 0.43 / 0.82 | 0.20 | 0.64 / 0.51 / 0.84 / 1.00 | 0.51 |
| burn x1.5 and lambda x0.7 | q 2.5 (strictly non-circular alternative) | 199 | 0.62 / 0.25 / 0.56 / 0.92 | 0.25 | 0.62 / 0.52 / 0.93 / 1.00 | 0.52 |
| burn x1.5 and lambda x0.7 | **recommended (q 2)** | 199 | 0.81 / 0.40 / 0.56 / 0.93 | 0.40 | 0.81 / 0.64 / 0.97 / 1.00 | 0.64 |
| burn x1.5 and lambda x0.7 | Sonnet, q 2.5, 1 & 10, 1 x 120 | 189 | 0.64 / 0.19 / 0.44 / 0.81 | 0.19 | 0.64 / 0.48 / 0.83 / 1.00 | 0.48 |
| slow merge queue (2.6 min/change) | q 2.5 (strictly non-circular alternative) | 187 | 0.94 / 0.60 / 0.35 / 0.01 | 0.01 | 0.94 / 0.74 / 0.99 / 0.98 | 0.74 |
| slow merge queue (2.6 min/change) | **recommended (q 2)** | 187 | 0.96 / 0.71 / 0.36 / 0.01 | 0.01 | 0.96 / 0.86 / 0.99 / 0.99 | 0.86 |
| slow merge queue (2.6 min/change) | Sonnet, q 2.5, 1 & 10, 1 x 120 | 138 | 0.92 / 0.48 / 0.12 / 0.00 | 0.00 | 0.92 / 0.58 / 0.80 / 0.63 | 0.58 |
| calibration shift sd 0.2 | q 2.5 (strictly non-circular alternative) | 187 | 0.93 / 0.58 / 0.69 / 0.90 | 0.58 | 0.93 / 0.81 / 1.00 / 1.00 | 0.81 |
| calibration shift sd 0.2 | **recommended (q 2)** | 187 | 0.96 / 0.65 / 0.67 / 0.91 | 0.65 | 0.96 / 0.89 / 1.00 / 1.00 | 0.89 |
| calibration shift sd 0.2 | Sonnet, q 2.5, 1 & 10, 1 x 120 | 138 | 0.91 / 0.41 / 0.47 / 0.75 | 0.41 | 0.91 / 0.69 / 0.93 / 1.00 | 0.69 |
| no window over-dispersion | q 2.5 (strictly non-circular alternative) | 187 | 0.95 / 0.76 / 0.84 / 0.99 | 0.76 | 0.95 / 0.90 / 1.00 / 1.00 | 0.90 |
| no window over-dispersion | **recommended (q 2)** | 187 | 0.99 / 0.81 / 0.83 / 0.99 | 0.81 | 0.99 / 0.98 / 1.00 / 1.00 | 0.98 |
| no window over-dispersion | Sonnet, q 2.5, 1 & 10, 1 x 120 | 138 | 0.91 / 0.65 / 0.77 / 0.97 | 0.65 | 0.91 / 0.86 / 1.00 / 1.00 | 0.86 |
| haiku lambda 0.7 x target | q 2.5 (strictly non-circular alternative) | 187 | 0.84 / 0.43 / 0.65 / 0.93 | 0.43 | 0.84 / 0.65 / 0.98 / 1.00 | 0.65 |
| haiku lambda 0.7 x target | **recommended (q 2)** | 187 | 0.93 / 0.59 / 0.65 / 0.95 | 0.59 | 0.93 / 0.82 / 0.99 / 1.00 | 0.82 |
| haiku lambda 1.0 x target | q 2.5 (strictly non-circular alternative) | 187 | 0.97 / 0.67 / 0.71 / 0.78 | 0.67 | 0.97 / 0.88 / 1.00 / 1.00 | 0.88 |
| haiku lambda 1.0 x target | **recommended (q 2)** | 187 | 0.99 / 0.73 / 0.68 / 0.79 | 0.68 | 0.99 / 0.95 / 1.00 / 1.00 | 0.95 |
| anchor on low arm (analysis variant) | q 2.5 (strictly non-circular alternative) | 187 | 0.99 / 0.62 / 0.74 / 0.88 | 0.62 | 0.99 / 0.73 / 1.00 / 1.00 | 0.73 |
| anchor on low arm (analysis variant) | **recommended (q 2)** | 187 | 0.99 / 0.70 / 0.71 / 0.89 | 0.70 | 0.99 / 0.83 / 1.00 / 1.00 | 0.83 |
| anchor on low arm (analysis variant) | Sonnet, q 2.5, 1 & 10, 1 x 120 | 138 | 0.94 / 0.41 / 0.46 / 0.74 | 0.41 | 0.94 / 0.68 / 0.93 / 1.00 | 0.68 |
| uncapped truths: rework lost | q 2.5 (strictly non-circular alternative) | 187 | - / 0.50 / 0.64 / 0.53 | - | - / 0.73 / 0.99 / 1.00 | - |
| uncapped truths: rework lost | **recommended (q 2)** | 187 | - / 0.58 / 0.65 / 0.53 | - | - / 0.82 / 1.00 / 1.00 | - |
| uncapped truths: rework lost | Sonnet, q 2.5, 1 & 10, 1 x 120 | 138 | - / 0.33 / 0.48 / 0.44 | - | - / 0.62 / 0.89 / 1.00 | - |

## 3. Recommended design

**Haiku 4.5 workers, λ target 8 attempts per agent-hour, V ≈ 14 reviews per busy hour (q = 2), fleet sizes
1 and 12, three 120-min windows at each size (ABBAAB order). Pilot: T1 (1 worker, then 12 at once), then T2 as
eight separate 60-min windows of one worker, plus 120 free calibration reviews by the local reviewer.**

| Item | Value |
|---|---|
| Worker model | Haiku 4.5 (Sonnet 5 fallback: sizes 1 & 12, 2 × 90 min, q = 2, same pilot; $184, min 0.61, binary 0.77) |
| Task size target | λ1 = 8 first attempts per agent-hour (≈ 7.5 min of agent work; roughly 10–75 changed lines) |
| Reviewer | one serial local reviewer, one PR per fresh call, job defined so that V ≈ 2 × λ1 at the design point: 14/h with Haiku's λ ≈ 6.8 (about 4.3 min per review). Calibrate V offline before T2 and adjust the job definition, not after. |
| Merge queue | serial time ≤ 0.75 min per change (required; see section 1) |
| T1 | 1 worker × 30 min + 11 workers × 15 min = 3.25 session-h: throttling and 12-way concurrency test |
| T2 pilot | 8 windows × 1 worker × 60 min = 8 session-h (spread across days/5-h plan windows) |
| Calibration | 120 local reviews (pilot PRs re-reviewed plus reference and mutant solutions), no credits |
| Sweep | N = 1 and N = 12, 3 windows each, 120 min (10 warm-up, 10 grace): 3 × 13 × 2 = 78 session-h |
| Analysis | as adopted (`completion`, S1r/S2r, S4n/O1n, `logit_cluster_task`), plus Vdur (section 5) |
| **Credits** | 89.25 session-h × $2.10 = **$187**, leaving $13 of contingency above the floor; **$237 of the $250 including the $50 floor** |
| At 1.5× burn | degrade rule gives $199 (sweep shortened); min 0.60, binary 0.85 |

**Rationale.** It has the highest four-way min-recovery of any design whose reviewer is not already saturated at
N_low: 0.97 / 0.67 / 0.69 / 0.91. It has the best capped-vs-uncapped recovery among those designs (0.87), and it
is the most robust of the three leads in every scenario. Under λ 30% below target it drops only to min 0.47 /
binary 0.70, against 0.25 / 0.50 for q = 2.5. The reason is that a lower λ raises the effective q, so a design
that starts near the knee stays informative, while one that starts with slack loses its cap at N = 12.

**Caveat on circularity (review v2, must-fix B).** At the design point the reviewer is 92% loaded at N = 1. It is
not saturated, but it misses review v2's "≤ 0.7 V" slack rule. The strictly compliant alternative is q = 2.5
(load 0.74 / 2.58): V ≈ 17/h, same everything else, $187. It scores min 0.56–0.58 and binary 0.77–0.80, with
V-test power slightly higher, and it degrades badly if λ is lower than assumed. If λ comes out at target, the
q = 2 design tests the plateau and the V-constancy claims cleanly, but tests the *location* of the knee only
weakly. If λ is lower, which is the likelier direction for λ = 8, N = 1 moves to load 0.64 and the rule is met.
**Write the choice into the pre-registration.** I recommend q = 2, with the rule stated as "load at N_low ≤ 1.0
at the design point, ≤ 0.7 expected under the pilot's λ".

**Do not use the pilot gate.** Its N ≤ 8 limit and the 0.7/1.5 thresholds make it pass 33–55% of the time
(PLAN-v3: 0.33), and with N_max = 8 it cannot reach the recovery that N = 12 gives. Fixed sizes chosen in
advance, with V set by calibration, do better on every metric. That rules out the target
P(gate RUN | informative) ≥ 0.8, so the design has no gate. Keep only abort rules: throttling at 12 in T1;
pilot λ below 0.5 × target; calibrated V outside ±30% of 2λ.

**New requirements this design creates:**
1. **12 concurrent cloud sessions.** T1 tests this before any sweep spend.
2. **Tiny tasks.** Can λ reach 8, and with how many validated tasks? Each N = 12 window uses about 60 claims, and task repetition across windows needs checking.
3. **A fast merge queue.**
4. **T2 as eight short windows** on different days.

## 4. Honest statement: the targets are not met

| Target | Best achievable (recommended design unless stated) | Met? |
|---|---|---|
| P(correct family) ≥ 0.8 under each of Carnot, USL, Amdahl, linear | 0.97 / 0.67 / 0.69 / 0.91 (min 0.67); best found anywhere 0.69, from the circular q = 1.5 design; ceiling with a perfect pilot 0.84–0.90 (N_max = 12) | **No** (USL and Amdahl) |
| Same, binary question: review-capped vs uncapped | 0.97 / 0.87 / 1.00 / 1.00 (min 0.87; 0.85 at 1.5× burn; 0.70 at λ × 0.7) | Yes at the design point; not robust to λ −30% |
| P(gate RUN \| informative) ≥ 0.8 | best gate 0.55 (better pilot), PLAN-v3 0.33 | **No**, so no gate is used |
| V-constancy test (S4n/O1n) power ≥ 0.8 vs ±25%, FPR ≤ 0.1 | O1n power 0.54 / FPR 0.18 (service CV 1); 0.37 / 0.01 (CV 0.5). Best of any design: 0.66 / 0.17. | **No.** The band equals the effect, so power is capped near 0.5–0.6. |
| V constancy with a duration test (Vdur, Welch on log review times, high vs low) | 0.19 at FPR 0.06 (CV 1); **0.68** at FPR 0.05 (CV 0.5). Best: 0.94 / 0.05 with sizes 2 & 8, 4 × 120, q = 3, CV 0.5 | Only if review times have CV ≤ 0.5, and only in designs with many reviews at both sizes |
| FPR of O1n ≤ 0.1 | 0.14–0.21 at service CV 1 in every candidate; ≤ 0.01 at CV 0.5 | No at CV 1 |
| Escaped defects vs depth (report) | power 0.10 at α 0.05, 0.03 at α 0.01, with ≥ 8 events in 100% of runs; the null rate is 0.05 | underpowered |
| Collision k-slope (report) | planted p = 0.01: 0.09 (α .05) / 0.03 (α .01); p = 0.05: 0.34 / 0.12 | underpowered below p ≈ 0.05 |
| Robustness: burn 1.5× | min 0.60, binary 0.85 | degrades gracefully |
| Robustness: λ 30% low | min 0.47, binary 0.70 (Carnot 0.82, USL 0.47) | material loss |

**Why no design gets there.** The limit is not the budget as such. It comes from three things together:
- **Over-dispersion.** A fixed CV of 0.3 per window means any single window's level is uncertain by ±30%, so
  shapes that differ by about 33% (USL vs Amdahl) need about 8 or more windows per size to separate.
- **Pilot anchoring.** Every rival's level comes from pilot windows that carry the same noise, and the
  pre-registered likelihood scores levels, not just shapes. Profiling the level out loses information instead
  (ceiling 0.58–0.69). Pooling the sweep's own N = 1 windows into the anchor (tested as an analysis variant)
  adds 0.00–0.05.
- **The completion reading's misspecification.** Completion falls with N under drag even without a review cap.

Setting the over-dispersion to 0 lifts the recommended design to min 0.81 and binary 0.98, so a real window CV
well below 0.3 would change the verdict. T1/T2 cannot estimate it, so it cannot be relied on.

## 5. What this implies for the pre-registration wording

1. **Confirmatory, primary: "Is review the binding limit?"** State it as the capped-vs-uncapped comparison
   within the same likelihood. The hypothesis is that Carnot-capped beats the best uncapped rival, with the
   likelihood ratio reported. The simulated operating characteristics go in the pre-registration: 0.97 correct
   under Carnot; 0.87 / 1.00 / 1.00 under USL / Amdahl / linear; 0.70 if λ is 30% below target.
2. **Confirmatory, secondary: the point predictions that are not identities.** Finished at N = 12 lies within
   Carnot's predictive interval (O2). b does not rise with N (S3). Attempts rise with N (O3).
   S1r/S2r false-alarm rates under the model's own truth are 0.05 and 0.53. **S2r is not usable as a surprise
   criterion at N = 12**: it fires about half the time under Carnot because the predicted ratio is small and
   noisy. Report it descriptively, or re-derive its threshold from simulation (for example 0.5 instead of 0.7).
3. **Descriptive: the four-way ranking.** Report the ordering and the likelihood ratios with the simulated
   confusion matrix. Under USL truth, USL is picked 67% of the time and Carnot 13%. Under Amdahl truth, Amdahl
   is picked 69% of the time and USL 16%. Do not claim USL-vs-Amdahl discrimination.
4. **V constancy: change the test or call it descriptive.** O1n/S4n as coded cannot reach 0.8 power at a
   ±25% effect with the ±25% band, and at exponential review times the FPR is 0.14–0.21. Pre-register instead:
   - the ratio V(12)/V(1) with its interval, plus an equivalence statement (the interval inside [0.8, 1.25] =
     "V stable");
   - a Welch test on log review durations (Vdur), confirmatory only if T1 + T2 show review-time CV ≤ 0.5
     (decided before the sweep);
   - otherwise descriptive.
   Do not make the calibration comparison (Vcal) confirmatory: its FPR is 0.15–0.26 when calibration diffs
   differ from live PRs.
5. **Escaped defects vs queue depth and collisions: descriptive/exploratory.** They have 10% and 9–34% power.
   Keep the ≥ 8-event rule. Every candidate design reaches 8 events, so the model will run, but a null result is
   uninformative. Say so in advance.
6. **Circularity.** State the design-point loads (0.92 and 3.2 × V) and the fallback wording above. State that a
   flat finished count at N = 12 is expected by construction, and that the non-identity claims are items 1, 2
   and 4.
7. **Harness preconditions, as abort rules:**
   - merge-queue serial time ≤ 0.75 min per change (measured in T1/T2);
   - 12-way concurrency without throttling (T1, the 20% rule);
   - pilot λ ≥ 0.5 × target;
   - calibrated V within ±30% of 2 × pilot λ, otherwise redefine the review job and re-calibrate before any sweep window.

## Reproducing

```sh
PY=/Users/tom/git/carnot/analysis/aidev/.venv/bin/python
cd /Users/tom/git/carnot/analysis/fleet-sweep/analysis/design-search
$PY ceiling.py                       # 2 min
$PY design_search.py --stage A       # ~40 min on 10 cores
$PY design_search.py --stage B       # ~15 min
$PY design_search.py --stage C       # ~20 min
$PY final.py                         # ~15 min (shortlist extras, V power, robustness)
$PY tables.py                        # tables.md; DESIGN-SEARCH.md = this prose + tables
```
