# Study 2 operating characteristics (PLAN-v4.1)

PLAN-v4 section 6.8: the operating characteristics are recomputed with the final setup, and **these figures,
not the DESIGN-SEARCH ones or PLAN-v4 section 1's, go into the pre-registration**. Grades as reframed in PLAN-v4
section 7 (v4.2); see the footnote under the headline table. They are also the values in `common.V4_OC`, which
`predict.py` and `score.py` print beside the results.

Simulation only (synth -> derive -> predict -> score, the analysis code as pre-registered). No network, API
or harness calls. Script: `analysis/design-search/oc_v41.py`; raw output `oc_v41.json`. 2000 simulated
studies per cell (1000 for the escape and collision cells), so a rate near 0.5 has a 95% half-width of
about ±0.022, near 0.8 about ±0.018, and near 0.95 about ±0.010 (±0.031 at worst for the 1000-study cells).
Every headline rate is within ±3%.

## Setup

| Item | Value |
|---|---|
| Worker | Haiku 4.5 assumptions as in DESIGN-SEARCH: λ1 = 0.85 × the 8/h target = 6.8 first attempts per agent-hour, defect rate 0.49, rework time 3.5 min |
| Reviewer | V0 = 2 × 6.8 = 13.6 reviews per busy hour (q = 2); review-time CV 1 (exponential) unless stated |
| Noise | Gamma(mean 1, CV 0.3) multiplier on worker speed per window, pilot windows included |
| Merge queue | 0.75 min serial time per change |
| Task supply | **220 tasks** per window |
| Pilot | T1 (1 worker for 30 min, 12 for the last 15 min, PRs reviewed) plus **eight separate 60-min T2 windows** of one worker. **Live reviews only**: V, b and the review-time CV come from these runs; no calibration reviews pooled (about 66 live reviews per pilot)  *(v4.2 runs the twelve-worker part of T1 for 30 min, not 15; not re-simulated: it only adds live T1 reviews to the pilot's V, b and CV, PLAN-v4 section 7.5)* |
| Sweep | N = 1 and 12, three 120-min windows each (10 min warm-up, 10 min grace), ABBAAB |
| Analysis | `derive.py` with the grace-end V fix and supply truncation / flag; `score.py` v4.1 codings: P1 (`completion` reading, NB CV 0.3, ties count as not higher), O2, S3 (b_review), O3, Vdur (Welch, conditional on `review_cv_ok`), Vratio descriptive |
| Truths | Carnot (USL workers, fixed-capacity FIFO reviewer); USL, Amdahl, linear workers with a reviewer that speeds up with queue depth; skimN / slowN: the reviewer's pace at N = 12 is 1.25× / 0.75× its pace at N = 1 and in the pilot |

## Headline table

| Result | Grade | Rate | Pre-registered value | DESIGN-SEARCH |
|---|---|---|---|---|
| **P1 correct, Carnot truth** | confirmatory (manipulation check, v4.2) | 0.965 | 0.97 (0.965 rounded) | 0.97 |
| **P1 correct, USL truth**\* | confirmatory (manipulation check) | 0.80 (P1 wrongly passes 0.198) | 0.80 | 0.87 |
| **P1 correct, Amdahl truth**\* | confirmatory (manipulation check) | 0.998 | 1.00 | 1.00 |
| **P1 correct, linear truth**\* | confirmatory (manipulation check) | 1.000 | 1.00 | 1.00 |
| P1 at 1.5× burn (degrade rule: 2 × 120-min windows per size), Carnot / USL\* / Amdahl\* / linear\* | confirmatory (manipulation check) | 0.95 / 0.75 / 0.99 / 1.00 | 0.95; USL 0.75 | 0.97 / 0.85 / 0.99 / 1.00 |
| P1 at λ −30% (true λ1 = 4.76, V unchanged), Carnot / USL\* / Amdahl\* / linear\* | confirmatory (manipulation check) | 0.76 / 0.66 / 0.985 / 1.00 | 0.76; USL 0.66 | 0.82 / 0.70 / 0.98 / 1.00 |
| **O2 false alarm, Carnot truth**, review-time CV 1 / 0.5 | **confirmatory, primary (v4.2)** | 0.099 / 0.041 | 0.10 / 0.04 | not reported (v4.1 draft: about 0.13) |
| O2 fails under USL / Amdahl / linear (power)\* | confirmatory, primary | 0.63 / 0.99 / 1.00 | | |
| **S3 false alarm, Carnot truth**, CV 1 / 0.5 | descriptive (v4.2; was confirmatory) | 0.058 / 0.077 | 0.06 | not reported |
| S3 under USL / Amdahl / linear\* | descriptive | 0.04 / 0.03 / 0.02 | | |
| **O3 pass (attempts rise), every truth** | descriptive (v4.2; was confirmatory) | 0.996-1.000 | 1.00 | not reported |
| O3 false alarm | descriptive | not meaningful: every truth has attempts rising with N | | |
| **Vdur false positive** (Carnot truth), CV 1 / 0.5 | conditional | 0.042 / 0.052 | 0.04 / 0.05 | 0.06 / 0.05 |
| **Vdur power vs a ±25% reviewer**, CV 1 (skimN / slowN) | conditional | 0.18 / 0.18 | 0.18 | 0.21 / 0.19 |
| **Vdur power vs a ±25% reviewer**, CV 0.5 (skimN / slowN) | conditional | 0.66 / 0.80 (mean 0.73) | 0.73 | 0.68 / 0.78 |
| **P(`review_cv_ok` set)** at true review-time CV 0.3 / 0.5 / 0.7 / 1 | | 1.000 / 0.532 / 0.002 / 0.000 | 1.00 / 0.53 / 0.00 | not reported |

"P1 correct" means P1 PASS under Carnot truth and P1 FAIL under an uncapped truth.

\* **Footnote (PLAN-v4 section 7.1).** Every row marked \* is computed under an uncapped truth, whose data come
from a synthetic reviewer that speeds up with queue depth (`synth.py`, `reviewer_load = 2`: rate = V0 × (1 + 2 ×
depth)). The real harness cannot produce such a reviewer (a fresh call per change that never sees the queue), so
these rows describe a counterfactual, not a result the study can observe. In the real harness P1 is a
manipulation check: it can fail only through a harness or calibration fault. O2 is the primary quantitative
test; its false-alarm rate under Carnot truth (the first O2 row) is the figure that applies to the real harness.

**Grades (PLAN-v4 section 7, v4.2):** O2 confirmatory, primary quantitative test; P1 confirmatory manipulation
check; Vdur conditional; S3, O3 and the rest descriptive.

Vdur's power is unconditional on the flag. Near the 0.5 threshold `review_cv_ok` is a coin toss (0.53 at true
CV 0.5), so at that CV the reviewer-pace claim is confirmatory in about half of studies. At the truths the
design search treats as likely (CV about 1 for LLM review times), it will be descriptive.

## Descriptive results

| Result | Rate | DESIGN-SEARCH |
|---|---|---|
| Vratio: median 95% interval of V(12)/V(1) under Carnot truth | 0.69-1.47 (median ratio 1.00; about 48 and 81 reviews per size) | "roughly 0.7-1.45" |
| S1r false alarm, Carnot truth (CV 1 / 0.5) | 0.049 / 0.046 | 0.05 |
| S2r fires, Carnot truth (CV 1 / 0.5) | 0.53 / 0.47 (not a criterion) | 0.53 |
| Escaped defects vs queue depth (`logit_cluster_task`), planted g = 0.2: detected at α 0.05 / 0.01 | 0.12 / 0.04 (≥ 8 events in 100% of sweeps, median 36) | 0.10 / 0.03 |
| Escaped defects, null (g = 0): false positive at α 0.05 / 0.01 | 0.064 / 0.017 (≥ 8 events in 99.5%) | 0.05 |
| Collisions, k-slope, planted p = 0.01: detected at α 0.05 / 0.01 | 0.08 / 0.02 | 0.09 / 0.03 |
| Collisions, planted p = 0.05: at α 0.05 / 0.01 | 0.36 / 0.15 | 0.34 / 0.12 |
| Collisions, null (p = 0): false positive at α 0.05 | 0.05 | not reported |

**Four-way ranking** (rows: truth; columns: family with the highest likelihood; base scenario, CV 1):

| truth | Carnot | USL | Amdahl | linear | tie |
|---|---|---|---|---|---|
| Carnot | 0.965 | 0.023 | 0.001 | 0.000 | 0.012 |
| USL | 0.198 | 0.570 | 0.221 | 0.008 | 0.005 |
| Amdahl | 0.002 | 0.158 | 0.675 | 0.166 | 0.000 |
| linear | 0.000 | 0.001 | 0.069 | 0.931 | 0.000 |

USL against Amdahl is still not separable (DESIGN-SEARCH: 0.67 / 0.69 on the diagonal; now 0.57 / 0.68).

## Task supply

With 220 tasks no window runs out under Carnot, USL or Amdahl truth in any of the 2000 studies per cell
(median share of tasks claimed in an N = 12 window: 0.24 / 0.22 / 0.31). Under linear truth, the fastest
supply use (median 0.58 claimed), 6.0% of studies have at least one window that runs out before 120 min and
2.8% have a window flagged (out before minute 110); in those, P1-nf is still correct (FAIL) in every case.
At 1.5× burn the figures are 4.2% and 1.8%.

## Changes from DESIGN-SEARCH.md and PLAN-v4 section 1

1. **P1 under USL truth: 0.80, not 0.87.** The main cause is the live-only pilot. DESIGN-SEARCH pooled 120
   free calibration reviews into the pilot's V, which narrowed its error; without them the pilot's V carries
   more noise (about 66 live reviews), and under USL truth that noise more often lets the capped prediction
   fit. The self-test measured the same drop at 300 replicates (0.20 wrong vs 0.13 with calibration).
2. **At λ −30%: Carnot 0.76, USL 0.66** (DESIGN-SEARCH 0.82 / 0.70; PLAN-v4 quoted the binary minimum 0.70).
   The capped-vs-uncapped minimum is now 0.66. Most of the Carnot shortfall is ties (0.15 of studies): with
   the slower pilot's parameters Carnot's and USL's predictions coincide, and a tie counts as not higher.
3. **At 1.5× burn: Carnot 0.95, USL 0.75** (DESIGN-SEARCH 0.97 / 0.85; PLAN-v4 quoted 0.85). The degrade rule
   gives two 120-min windows per size ($199 with v4's 3.25-h T1; with v4.2's 6.5-h T1 the degrade design costs $209
   at 1.5× burn and fits only up to 1.43× burn, PLAN-v4 section 7.2).
4. **O2 false alarm 0.10 at review-time CV 1** (PLAN-v4.1 section 6.4 said about 0.13, from 300 replicates);
   0.04 at CV 0.5. Still above the nominal 0.05 at CV 1, and the pre-registration says so.
5. **Vdur** is now the reviewer-pace test itself (PLAN-v4.1 section 6.1). Its false-positive rate and power
   match DESIGN-SEARCH within noise (power 0.73 against 0.68-0.78 at CV 0.5; 0.18 against 0.19-0.21 at CV 1).
   Vratio is reported only; its interval never fits inside [0.8, 1.25] at this design, which is why the
   equivalence claim was dropped.
6. **New figures** not in DESIGN-SEARCH: the probability that `review_cv_ok` is set, S3 and O3 rates, the
   collision null rate, and the supply-flag rates.
7. Escape and collision power are unchanged within noise (0.12 vs 0.10; 0.08 / 0.36 vs 0.09 / 0.34).

## Reproducing

```sh
PY=/Users/tom/git/carnot/analysis/aidev/.venv/bin/python
cd /Users/tom/git/carnot/analysis/fleet-sweep/analysis/design-search
$PY oc_v41.py --reps 2000 --reps-extra 1000     # about 5 min on 10 cores -> oc_v41.json
```

Seeds are fixed. The same seed block is used for each truth across the base, λ −30% and 1.5× burn
scenarios (common random numbers).
