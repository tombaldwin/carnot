# Study 2 operating characteristics (PLAN-v6)

The simulated operating characteristics of the PLAN-v6 design (PLAN-v6 section 5), its degrade design and the revised
abort rule 1. **These figures, not the design-search screening figures, go into the pre-registration.** They are the
values in `analysis/v6.py` `V6_OC`, which `predict.py` and `score.py` print beside the results.

Simulation only: `analysis/synth.py` (the v6 process, calibrated on T1 and T0c) -> `derive.py` -> `v6.py` (the codings
as pre-registered), driven by `analysis/design-search/oc_v6.py` through `dsim6.py`; raw output `oc_v6.json` and
`oc_v6_t1b.json`. No network, API, harness or cloud calls. **1000 freshly simulated studies per cell** (every window
simulated anew, not drawn from a bank), so a rate near 0.05 has a 95% half-width of about ±0.014, near 0.5 about
±0.031, near 0.9 about ±0.019. The same seed block is used for each truth in both designs (common random numbers).

## Setup

| Item | Value |
|---|---|
| Design | 2 x 2 of fleet size N in {1, 12} and parallel reviewers K in {1, 3}: **N = 1: six windows at K = 1 and six at K = 3; N = 12: three at K = 3 and four at K = 1; 45 min each** (5 min warm-up, 10 min grace), order `v6.v6_order` (two N = 1 windows first; N = 12 windows spread evenly, alternating K) |
| Degrade design (1.5x burn under budget (a)) | N = 1: five at each K; N = 12: two at K = 3, three at K = 1 |
| Cost, budget (a) | T1b 7.5 + sweep 72 = 79.5 worker session-hours: **$167 at $2.10**, fits the $199 above the floor up to **$2.50**; degrade 7.5 + 52.5 = 60: $126 at $2.10, **$189 at $3.15**, fits up to **$3.32** |
| Worker (T1 + T0c) | one Haiku session per task in N slots; per task: re-arm 20 s, start-up lognormal median 85 s (log-sd 0.28), coding lognormal median 83 s (0.45), rework lognormal median 70 s (0.33) sent 1.5 s after a slot frees; 25-min timeout; lambda at N = 1 about 14 first submissions per slot-hour |
| Worker truths | linear; mild (Amdahl alpha 0.03, a sensitivity); Amdahl (alpha 0.1); USL (alpha 0.1, beta 0.01); Carnot uncapped (USL + collisions at p = 0.0075); "T1-like" = linear workers with T1's collision rate p = 0.0075. Drag stretches the work legs (coding, rework) enough that the whole per-task cycle scales as N / X(N); start-up is not dragged |
| Noise | Gamma(mean 1, CV 0.3) multiplier on worker speed per window; sensitivity CV 0.15 and 0.5 |
| Reviewer | K reviewers on one FIFO queue; review time drawn from the 134 T1 + T0c durations (median 20.1 s, p90 29.3 s, max 38.9 s), never load-dependent; 1.5% call errors retried. Sensitivities: 1.5x longer ("slow reviews"); 3x shorter ("fast reviews", the CAP null: one reviewer never binds); contention: each review x (1 + 0.2 (K - 1)) |
| Quality | 39% of reviews ask for changes on first and rework attempts alike (T1: 46 of 117); about 3% of approvals fail hidden tests (T1: 2 of 71); planted "escapes double" at N = 12 |
| Merge queue | serial, about 5.8 s per change (T1) |
| Collisions | at a change's first rebase, each other change merged since its base collides with probability p (90% rebase conflicts), plus 0.5% background integration failures |
| lambda -30% | every worker leg 1/0.7 longer: lambda at N = 1 about 10 |
| Tasks | 220 per window |

## Headline table (recommended design; degrade design in the last column)

| Result | Grade | Rate | Degrade |
|---|---|---|---|
| **SCALE false alarm**, linear workers, window CV 0.3 | **confirmatory, primary** | **0.020** | 0.030 |
| SCALE false alarm, T1-like workers (linear, p = 0.0075) | | 0.026 | 0.032 |
| SCALE false alarm at window CV 0.15 / 0.5 | | 0.000 / 0.12 | 0.000 / 0.13 |
| SCALE false alarm with review contention 0.2 (K = 3 reviews 40% slower) / slow reviews | | 0.020 / 0.020 | 0.028 / 0.027 |
| **SCALE power: Amdahl / USL / Carnot uncapped** | **confirmatory, primary** | **0.97 / 1.00 / 1.00** | 0.885 / 1.00 / 1.00 |
| SCALE power against a mild bend (alpha 0.03, per-agent ratio 0.76) | | 0.32 | 0.22 |
| SCALE power, Amdahl, at window CV 0.5 / at lambda -30% / with contention 0.2 | | 0.87 / 0.93 / 0.96 | 0.79 / 0.82 / 0.91 |
| SCALE reads BEND under a coding-only service slowdown of 1.3x at N > 1 (not drag) | | 0.10 | 0.09 |
| **CAP false alarm**: one reviewer never binds (reviews 3x faster), linear workers | **confirmatory, secondary** | **0.025** | 0.036 |
| **CAP power: T1-like workers / linear workers** (K = 1 : 3 finished ratio 0.61 / 0.68) | **confirmatory, secondary** | **0.74 / 0.51** | 0.54 / 0.38 |
| CAP power with slow reviews (ratio 0.44) / window CV 0.5 / T1-like with p = 0.015 | | 0.995 / 0.59 / 0.85 | 0.95 / 0.46 / 0.69 |
| CAP rate where the model predicts no ceiling at K = 1: USL / Amdahl / mild workers / lambda -30% | | 0.048 / 0.030 / 0.089 / 0.08 | 0.045 / 0.037 / 0.074 / 0.05 |
| **COLL false alarm** (p = 0), USL-like / linear workers | **confirmatory, secondary** | **0.015 / 0.035** | 0.008 / 0.022 |
| **COLL power, USL-like workers**, p = 0.0035 / 0.0075 / 0.015 | **confirmatory, secondary** | **0.48 / 0.78 / 0.98** | 0.38 / 0.69 / 0.93 |
| COLL power, linear or T1-like workers, p = 0.0035 / 0.0075 | | 0.99 / 1.00 | 0.95 / 1.00 |
| p-hat 95% interval covers the true p | | 0.94-0.96 | 0.95-0.97 |
| ESC-N false alarm / power for a doubling (0.03 -> 0.06 at N = 12) | descriptive | 0.06 / 0.44 | 0.06 / 0.40 |
| ESC: the escape rate, median 95% half-width (median approvals) | descriptive | ±0.012 on 0.031 (858) | ±0.014 (622) |
| K1: the K contrast at N = 1 flagged at two-sided p < 0.05 (the model predicts none) | descriptive | 0.010 (0.07 at CV 0.5) | 0.006 |

"Power" is the share of simulated studies coding BEND (SCALE), CAPPED (CAP), DETECTED (COLL) or RISES (ESC-N) at
one-sided p < 0.05 under a truth with the effect; "false alarm" the same share under a truth without it.

**Reading SCALE.** With K = 3 the reviewers are busy 57% of an N = 12 window under linear workers (the busiest window's
95th percentile 0.94), so review does not cap the workers; the false alarm stays at 0.02-0.03 even with 40% slower
reviews. SCALE uses the N = 1 windows at both K; the K contrast at N = 1 is null in the simulation (K1, 1% of studies
flag it). The one threat SCALE cannot separate from drag is a service-side slowdown of coding at N > 1: a 1.3x
coding-only slowdown reads as BEND 10% of the time. Rule 1 (T1b) catches start-up slowdowns, and its coding ratio is
reported beside SCALE for this reason.

**Reading CAP.** CAP tests the paper's `U = (1 - r) min(lambda X(N), V/h)` directly: at N = 12 one reviewer finishes
fewer changes than three only if lambda X(12) exceeds one reviewer's capacity. Under near-linear workers (what T1 saw)
the model predicts it, and CAP's power is 0.74 under T1-like workers and 0.51 under collision-free linear workers
(which make fewer reviews). Under drag (USL, Amdahl) one reviewer keeps up and CAP should read NOT-DETECTED, as it does
95-97% of the time. CAP's power is limited by the pre-registered window CV of 0.3 on only 3 + 4 windows, not by the
effect, which is large (ratio 0.6-0.7); if the true window noise in review-bound windows is smaller (the reviewer, not
the workers, sets their output), the test is conservative. A NOT-DETECTED CAP with SCALE LINEAR-NOT-REJECTED and the
K = 1 reviewer busy >= 80% would be surprising under the model and is reported as such.

## Four-way family pick (descriptive)

| truth | linear | Amdahl | USL | Carnot uncapped | linear vs bending correct |
|---|---|---|---|---|---|
| linear | 0.98 | 0.02 | 0.00 | 0.00 | 0.98 |
| Amdahl | 0.02 | 0.86 | 0.11 | 0.01 | 0.98 |
| USL | 0.00 | 0.11 | 0.48 | 0.41 | 1.00 |
| Carnot uncapped | 0.00 | 0.09 | 0.42 | 0.50 | 1.00 |

USL and Carnot uncapped differ by (1 - p)^11, 8% at p = 0.0075: not separable by counts; COLL measures p directly.

## Where the limit moves (UTIL, descriptive)

Reviewer busy share per reviewer (mean over windows) and reviews per window:

| workers | N = 12, K = 1 | N = 12, K = 3 (busiest window, p95) | reviews per window N = 12 K = 1 / K = 3 / N = 1 | finished per window N = 12 K = 1 / K = 3 / N = 1 |
|---|---|---|---|---|
| linear | 0.99 | 0.57 (0.94) | 145 / 208 / 17 | 70 / 105 / 8.7 |
| T1-like | 0.99 | 0.59 (0.95) | 146 / 215 / 17 | 61 / 103 / 8.7 |
| Amdahl | 0.77 | 0.27 (0.45) | 94 / 95 / 17 | 49 / 50 / 8.8 |
| USL | 0.48 | 0.16 (0.29) | 56 / 56 / 17 | 31 / 31 / 8.7 |
| linear, contention 0.2 | 0.99 | 0.77 (1.00) | 145 / 208 / 17 | 70 / 105 / 8.7 |

Rule 4 flags an N = 12, K = 3 window with the reviewers busy >= 80%: in 23% of studies under linear workers (some
window), 84% with contention 0.2; SCALE is then also reported without it. Task supply ran out more than 10 min before
a window's end in about 1% of studies (20% at window CV 0.5, from the fastest N = 12 windows); SCALE-nf covers it.

## Revised abort rule 1 (T1b; `oc_v6_t1b.json`, 1000 simulated T1b logs per cell)

T1b: one slot for L1 minutes, then twelve for 30 min, K = 3. Decision on the start-up ratio (twelve : one, geometric
means, Welch 90% interval on logs), tolerance 1.25: STOP if the interval is wholly above 1.25, CLEAR if wholly below,
else INCONCLUSIVE.

| one-slot phase L1 | start-up tasks at one slot (median) | 90% CI factor (median) | no throttle: CLEAR / STOP | 1.25x: STOP | 1.5x: STOP | 2x: STOP | drag only (Amdahl): STOP / coding flag |
|---|---|---|---|---|---|---|---|
| 30 min (T1 as run) | 7 | x/1.21 | 0.57 / 0.001 | 0.06 | 0.44 | 0.95 | 0.001 / 0.97 |
| 60 min | 14 | x/1.15 | 0.84 / 0.000 | 0.04 | 0.65 | 1.00 | 0.000 / 1.00 |
| **90 min (recommended)** | **21** | **x/1.12** | **0.94 / 0.000** | **0.04** | **0.77** | **1.00** | **0.000 / 1.00** |
| 120 min | 27 | x/1.11 | 0.96 / 0.000 | 0.04 | 0.81 | 1.00 | 0.000 / 1.00 |

The start-up rule never stops under coordination drag alone (drag stretches the work, not the provisioning), while the
coding ratio flags drag almost always, which is why coding is a flag and not a stop. A coding-only service slowdown of
1.5x is invisible to the start-up rule (STOP 0.000) and flagged by the coding ratio 40% of the time. Applied to the real
T1 (30-min one-slot phase, s2 and s3 excluded): start-up ratio 1.13 (90% 0.98-1.31) -> INCONCLUSIVE; coding 1.16
(0.87-1.54), no flag.

## Calibration check (T1 reproduced; `calib_t1.json`, 1000 simulated T1 logs)

The simulator runs T1's exact schedule (one slot 30 min, then twelve, s3 and s2 lost at minutes 34.9 and 40.7, K = 1).
Observed T1 against the simulated median and 5-95% range, and the observed value's percentile among the simulations:

| measure | T1 | simulated median (5-95%) | percentile |
|---|---|---|---|
| lambda, one slot / twelve (without s2, s3) | 16.0 / 13.6 | 14.0 (8-22) / 14.0 (7.6-22.8) | 0.72 / 0.46 |
| reviewer busy share, twelve-slot phase / its last 20 min | 0.88 / 0.95 | 0.93 (0.78-0.96) / 1.00 (0.88-1.00) | 0.13 / 0.09 |
| waiting for review at minute 35 / 40 / 45 / 50 / 55 / 60 | 4 / 10 / 18 / 24 / 25 / 29 | 5 / 11 / 15 / 19 / 25 / 29 | 0.34-0.60 |
| waiting at 65 / 70 min (grace) | 17 / 2 | 15 / 1 | 0.53 / 0.52 |
| reviews / submissions in the twelve-slot phase | 76 / 106 | 79 (67-84) / 109 (69-158) | 0.25 / 0.45 |
| share of reviews asking for changes; merges; mean review s | 0.39; 63; 21.0 | 0.40; 59; 21.1 | 0.45; 0.72; 0.44 |

Every observed measure lies inside the simulated 5-95% range; the reviewer was a little less busy in T1 than simulated
(13th percentile), consistent with T1's two review errors and its CLI incident.

## Reproducing

```sh
PY=/Users/tom/git/carnot/analysis/aidev/.venv/bin/python
cd /Users/tom/git/carnot/analysis/fleet-sweep/analysis/design-search
$PY calib_t1.py --reps 1000          # calibration check              -> calib_t1.json   (about 15 s)
$PY oc_v6.py --part t1b --reps 1000  # rule 1 on simulated T1b logs    -> oc_v6_t1b.json  (about 1 min on 10 cores)
$PY oc_v6.py --part oc --reps 1000   # the design and degrade design   -> oc_v6.json      (about 17 min on 10 cores)
```
