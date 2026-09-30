# Study 2 operating characteristics (PLAN-v7)

The simulated operating characteristics of the PLAN-v7 designs (PLAN-v7 section 5: the 400-task design, recommended,
and the 300- and 500-task designs that rule 2 may choose instead) and of abort rule 1 in slot mode. **These figures,
not the design-search screening figures, go into the pre-registration.** They are the values in `analysis/v7.py`
`V7_OC`, which `predict.py` and `score.py` print beside the results.

**Re-simulated on 2026-09-30 after the review (REVIEW-fable-v7.md)**, with: every N = 1 window at K = 5 in the 300- and
400-task designs (the 500-task design keeps the 2 x 2); T1b 30 + 15 min; sessions retired after 4 tasks or 6 messages
of any kind; rule 1's coding legs counted only for hand-outs at least 5 min before window end; new sensitivities (10% of
hand-outs lost; the old 8-task rule without a message limit). The pre-review figures (N = 1 split 6 + 6 by K, T1b 30 +
10) are superseded; they differed by simulation noise except where noted.

Simulation only: `analysis/synth.py` (the v7 process, calibrated on T0d and T0e) -> `derive.py` -> the codings
(`v6.py`, re-graded by `v7.py`), driven by `analysis/design-search/oc_v7.py` through `dsim7.py`; raw output
`oc_v7.json`, `oc_v7_t1b.json`, `oc_v7_reuse.json`. No network, API, harness or cloud calls. **1000 freshly simulated
studies per cell**, every window simulated anew, **one task pool per study reused by every window** (each window with
its own task order, as the real `reset`). A rate near 0.05 has a 95% half-width of about +-0.014, near 0.5 about
+-0.031, near 0.9 about +-0.019. The same seed block is used for each truth in every design.

## Setup

| Item | Value |
|---|---|
| 400-task design (recommended) | **N = 1: twelve windows at K = 5; N = 12: three at K = 5; 15 min each** (3 min warm-up, 10 min grace), order `v6_order` (two N = 1 windows first; N = 12 windows fifth, ninth and thirteenth) |
| 300-task design | N = 1: ten at K = 5; N = 12: two at K = 5 |
| 500-task design | N = 1: six at K = 1 and six at K = 5; N = 12: three at K = 5 and one at K = 1 (CAP) |
| T1b | one slot for 30 min, then twelve for 15 min, K = 5, one session per slot |
| Worker (T0d + T0e) | one session per slot, retired after 4 tasks or 6 messages (launch prompt, next tasks, rework); launch 4.1 s + start-up lognormal median 28.7 s, follow-up 1.6 s + 15.0 s (log-sd 0.05; 0.25 as the pre-registered rule-1 sensitivity: the measured 0.02-0.04 were the 15-s poll's quantisation); coding median 82 s (0.44); rework median 48 s (0.37), to the task's own session; session timeout 10 min; lambda at N = 1 about 24 first submissions per slot-hour |
| Worker truths | as v6: linear; mild (Amdahl alpha 0.03); Amdahl (0.1); USL (0.1, 0.01); Carnot uncapped (USL + p = 0.0075); "T1-like" = linear with T1's collision rate p = 0.0075 |
| Noise | Gamma(mean 1, CV 0.3) on worker speed per window; sensitivities CV 0.15 and 0.5 |
| Reviewer | K reviewers, one FIFO queue, durations drawn from the 168 pooled T1-T0e reviews (median 20.5 s), never load-dependent; sensitivities: 1.5x slower, 3x faster (the CAP null), contention x (1 + c (K - 1)) with c = 0.1, 0.2 |
| Quality | 41% of reviews ask for changes; about 3% of approvals escape; planted "escapes double" at N = 12 |
| Collisions | p per other change merged since the change's base (its hand-out or last rework message), 90% rebase conflicts, 0.5% background integration failures |
| Budget unit | a task = one hand-out (session launch or follow-up `kind=task`), a first attempt with its rework: about $0.45 (T0d / T0e) |
| Losses | "stall=x": a share x of hand-outs never reach READY (compaction, a mis-named branch the watcher ignores): the slot is held until the 10-min timeout. T0e: 2 of 7 slot-mode hand-outs lost (2 of 23 over T0d + T0e); 3% and **10%** simulated |
| Shared-context and reuse sensitivities | follow-up tasks code 25% faster ("posfx 0.75"); 8 tasks per session (with the message limit; and the old rule without it); per-task log-sd 0.3 / 0.5 of coding time, the same task in every window (task reuse), with fresh or fixed task orders |

## Headline table (400-task design; 300 and 500 in the last columns)

| Result | Grade | 400 | 300 | 500 |
|---|---|---|---|---|
| **Tasks: T1b + sweep (simulated mean; p90 of the sweep)** | | **87 + 317 = 404** (367) | 87 + 225 = 312 (266) | 87 + 412 = 499 (474) |
| $ at $0.45 per task (range $0.30-0.60) | | **$182** ($121-242) | $140 ($94-187) | $225 ($150-299) |
| fits the $186 above the floor up to (rule 2's threshold) | | **$0.46 per task** | $0.59 | $0.37 |
| Opus reviews: T1b + sweep | | **135 + 458 = 593** | 135 + 325 = 460 | 135 + 522 = 657 |
| **SCALE false alarm**, linear workers, window CV 0.3 | **confirmatory, primary** | **0.029** | 0.026 | 0.028 |
| SCALE false alarm, T1-like workers / CV 0.15 / **CV 0.5 (the honest bound)** | | 0.038 / 0.001 / **0.10** | 0.038 / - / 0.12 | 0.041 / - / 0.11 |
| SCALE false alarm with review contention 0.1 / 0.2 / slow reviews | | 0.023 / 0.035 / 0.033 | 0.031 / - / - | 0.024 / - / - |
| SCALE false alarm: lambda 20 / 3% lost / **10% lost** / task reuse (log-sd 0.3) / 8 tasks per session / old rule (8 tasks, no message limit) / shared context (follow-ups 25% faster) / start-up log-sd 0.25 | | 0.024 / 0.029 / **0.023** / 0.027 / 0.026 / 0.037 / 0.017 / 0.028 | 0.025 / - / 0.028 / - / - / - / - / - | 0.023 / - / 0.024 / - / - / - / - / - |
| **SCALE power: Amdahl / USL / Carnot uncapped** | **confirmatory, primary** | **0.89 / 1.00 / 1.00** | 0.78 / 1.00 / 1.00 | 0.89 / 1.00 / 1.00 |
| SCALE power against a mild bend (alpha 0.03, per-agent ratio 0.77) | | 0.26 | 0.19 | 0.23 |
| **SCALE minimum detectable effect** (80% power; log-SE about 0.25 from CV 0.3 over three N = 12 windows) | | **per-agent ratio about 0.55** | about 0.5 | about 0.55 |
| SCALE power, Amdahl, at CV 0.5 / lambda 20 / contention 0.2 / shared context / **10% lost** | | 0.82 / 0.89 / 0.91 / 0.89 / **0.60** | - | - |
| SCALE reads BEND under a coding-only service slowdown of 1.3x at N > 1 (not drag) | | 0.19 | - | - |
| **CAP false alarm**: one reviewer never binds (reviews 3x faster), linear workers | **confirmatory, secondary (500 only)** | N/A | N/A | **0.036** |
| **CAP power: T1-like / linear workers** (K = 1 : 5 finished ratio 0.48 / 0.51) | **confirmatory, secondary (500 only)** | N/A | N/A | **0.58 / 0.52** |
| CAP power at CV 0.5 / contention 0.1 / lambda 20 (ratio 0.62) | | | | 0.56 / 0.50 / 0.22 |
| CAP rate where the model predicts no ceiling at K = 1: USL / Carnot / Amdahl | | | | 0.12 / 0.12 / 0.06 |
| CAP rate under mild workers (one reviewer does bind partly: ratio 0.70) | | | | 0.124 |
| **COLL false alarm** (p = 0): USL-like / linear workers | **confirmatory, secondary** | **0.001 / 0.003** | 0.000 / 0.002 | 0.002 / 0.007 |
| **COLL power, T1-like workers**, p = 0.0035 / 0.0075 / 0.015 | **confirmatory, secondary** | **0.45 / 0.76 / 0.96** | 0.32 / 0.64 / - | 0.54 / 0.85 / - |
| **COLL power, USL-like workers**, p = 0.0035 / 0.0075 / 0.015 | **confirmatory, secondary** | **0.05 / 0.20 / 0.46** | 0.03 / 0.09 / 0.28 | 0.08 / 0.25 / 0.53 |
| p-hat 95% interval covers the true p | | 0.93-0.97 | 0.91-0.98 | 0.93-0.98 |
| ESC-N false alarm / power for a doubling (0.03 -> 0.06 at N = 12) | descriptive | 0.03 / 0.30 | 0.02 / 0.20 | 0.06 / 0.31 |
| ESC: the escape rate, median 95% half-width (median approvals) | descriptive | +-0.022 on 0.032 (265) | +-0.027 (188) | +-0.020 (302) |
| K1: the K contrast at N = 1 flagged at two-sided p < 0.05 (the model predicts none) | descriptive | N/A (all K = 5) | N/A | 0.011 |
| SCALE sensitivity versions: false alarm of the Welch / CV-estimated tests | reported beside SCALE, never the verdict | 0.083 / 0.085 | 0.091 / 0.091 | 0.083 / 0.078 |

"Power" is the share of simulated studies coding BEND (SCALE), CAPPED (CAP), DETECTED (COLL) or RISES (ESC-N) at
one-sided p < 0.05 under a truth with the effect; "false alarm" the same share under a truth without it.

**Reading SCALE.** The verdict is one p-value: the fixed-CV one-sided NB LR test on all N = 1 windows and all N = 12,
K = 5 windows; the versions above are sensitivities. Five reviewers at N = 12 are busy 0.61 of a window under linear
workers (0.80 with 10% contention per extra reviewer, 0.90 with 20%); rule 4 flags an N = 12 window with the reviewers
busy >= 80% in 41% of studies (89% with contention 0.1), and SCALE's false alarm stays 0.023-0.035 throughout. The three
N = 12 windows carry SCALE's power: with the pre-registered window CV of 0.3, the per-agent ratio's uncertainty is
dominated by the few N = 12 windows, which is why the 300-task design (two of them) drops to 0.78 against Amdahl and
why the minimum detectable per-agent ratio is about 0.55: the study tests the paper's default Amdahl (0.48) and USL
(0.29) curves, not any bend. The window CV of 0.3 is assumed: at 0.5 the false alarm is 0.10, the honest bound; the
observed CV of the N = 1 windows cannot check it (twelve windows of about four finished tasks have a Poisson CV near
0.5). **Losses:** with 10% of hand-outs lost the false alarm stays 0.023 but the power against Amdahl falls to 0.60: a
lost hand-out costs an N = 1 slot most of a 15-min window, which lowers N = 1 output relatively more and flattens the
per-agent ratio (0.60 instead of 0.49). A coding-only service slowdown of 1.3x at N > 1 reads as BEND 19% of the time:
rule 1's coding flag, THROTTLE (rule 1b) and DRAG (`merged_main`) are reported beside SCALE for this reason. Under the
linear null the simulated per-agent ratio is 1.02-1.04 (the safe side for a one-sided test).

**Reading CAP (500-task design only).** One N = 12 window with one reviewer finishes about half what a K = 5 window
does under near-linear workers (ratio 0.48-0.51), but with one such window and the pre-registered CV of 0.3 the test
finds it only 52-58% of the time. Under heavy drag (USL, Carnot uncapped) the model predicts no ceiling, and CAP reads
CAPPED in 12% of studies: 15-min windows under USL drag finish only one or two tasks per slot, and their output varies
more than the CV of 0.3 allows. A CAPPED reading together with SCALE's BEND is therefore to be read with the UTIL
figures. At 300 and 400 tasks CAP is not run: T1's descriptive result (one reviewer busy 88% at twelve slots under the
routine launcher, lambda 14, a queue growing to 29) stands for the ceiling.

**Reading COLL.** Collisions need merges. Under near-linear workers an N = 12, K = 5 window of 15 min has about 66
first merge-queue passes, and COLL detects T1's p in 76% of studies; under USL-like workers the slowed fleet makes
about 16 passes per window, and the power falls to 0.20. The false alarm of 0.001-0.003 at nominal 0.05 means the test
is badly under-sized (about one background event per study under p = 0), not that it is usefully conservative; and its
power is coupled to SCALE's answer: if SCALE reads a strong bend, COLL is close to uninformative.

## Four-way family pick (descriptive, 400-task design)

| truth | linear | Amdahl | USL | Carnot uncapped | linear vs bending correct |
|---|---|---|---|---|---|
| linear | 0.96 | 0.04 | 0.00 | 0.00 | 0.96 |
| Amdahl | 0.06 | 0.79 | 0.13 | 0.02 | 0.94 |
| USL | 0.00 | 0.09 | 0.32 | 0.59 | 1.00 |
| Carnot uncapped | 0.00 | 0.09 | 0.27 | 0.64 | 1.00 |

## Where the limit moves (UTIL, descriptive; 400-task design, 500 for K = 1)

| workers | reviewers busy, N = 12, K = 5 (mean; busiest window mean) | N = 12, K = 1 (500) | finished per window: N = 12 K = 5 / K = 1 / N = 1 | reviews per window: N = 12 K = 5 / K = 1 / N = 1 |
|---|---|---|---|---|
| linear | 0.61 (0.76) | 1.00 | 49.6 / 24.5 / 4.1 | 114 / 64 / 9.5 |
| T1-like | 0.62 (0.77) | 1.00 | 47.2 / 21.9 / 4.1 | 117 / 64 / 9.5 |
| Amdahl | 0.28 (0.36) | 0.92 | 23.8 / 21.6 / 4.1 | 50 / 46 / 9.5 |
| USL | 0.14 (0.20) | 0.66 | 13.0 / 13.2 / 4.1 | 25 / 25 / 9.5 |
| linear, contention 0.1 / 0.2 | 0.80 / 0.90 | 1.00 | 48.5 / 24.3 / 4.1 | 112 / 64 / 9.5 |

Task supply never ran out (220 tasks; an N = 12 window of 15 min hands out about 80).

## Abort rule 1 in slot mode (T1b; `oc_v7_t1b.json`, 1000 simulated T1b logs per cell, K = 5)

T1b: one slot for L1 minutes, then twelve for L2. The rule's statistic is the follow-up start-up ratio (twelve : one,
geometric means, Welch 90% interval on logs), tolerance 1.25: STOP if the interval is wholly above 1.25, CLEAR if wholly
below, else INCONCLUSIVE. The launch start-up ratio gives a flag, the coding ratio a flag (coding legs only for
hand-outs at least 5 min before window end); the mixed ratio (v6's measure on every hand-out) is shown for comparison
only.

**Pre-registered: the start-up log-sd 0.25 rows.** Every `claim` in T0d / T0e was timestamped at the watcher's 15-s
poll, so the measured start-up spreads (log-sd 0.02-0.04) were the poll's quantisation, and the rule as first simulated
(at 0.05) had no real counterpart; the harness now polls every 2 s. Until T1b's own one-slot phase shows the real
spread, the rule's operating characteristics are those at log-sd 0.25 (and T0e's one post-retirement launch took 6x
longer than the others).

| T1b L1 + L2, start-up log-sd | tasks median (p90) | reviews | follow-ups at one / twelve slots | CI factor | no throttle: CLEAR / STOP | 1.5x: STOP | 2x: STOP | launch-only 2x: STOP / launch flag | drag (Amdahl): STOP / CLEAR | coding flag: coding-only 1.5x / Amdahl |
|---|---|---|---|---|---|---|---|---|---|---|
| **30 + 15, log-sd 0.25 (pre-registered)** | **86 (119)** | **134** | **8 / 49** | **x/1.17** | **0.75 / 0.000** | **0.54** | **0.985** | **0.000 / 0.78** | **0.000 / 0.71** | **0.31 / 0.94** |
| 30 + 10, log-sd 0.25 (before the review) | 65 (89) | 93 | 9 / 34 | x/1.17 | 0.70 / 0.000 | 0.52 | 0.965 | 0.000 / 0.80 | 0.000 / 0.66 | 0.29 / 0.92 |
| 30 + 15, log-sd 0.05 (comparison only) | 87 (120) | 135 | 8 / 50 | x/1.03 | 1.00 / 0.000 | 1.00 (1.25x: 0.04) | 1.00 | 0.000 / 1.00 | 0.000 / 1.00 | 0.34 / 0.95 |
| 30 + 15, log-sd 0.05, 10% of hand-outs lost | 71 (92) | 97 | 6 / 34 | x/1.04 | 0.95 / 0.000 | | | | | |

30 + 15 includes each twelve-slot slot's first retirement and relaunch (the fifth task, at minute 10-11 of the
twelve-slot phase), which 30 + 10 missed, and makes the twelve-slot phase the length of a sweep N = 12 window; it costs
about 21 more tasks. The coding flag, now counted without the end-censoring bias, is raised by drag almost always
(Amdahl 0.95, USL 0.99) and is not a stop, as in v6.

**Why follow-ups and not the mixed start-up.** At 30 + 15 the mixed measure CLEARs only 0.78 of the time with no
throttling (0.51 at log-sd 0.25), 0.36 under USL drag (drag leaves fewer tasks per slot, so the twelve-slot phase has
more launches), and a launch-only slowdown pushes it to INCONCLUSIVE, never to a decision: it measures the mix of
launches and follow-ups as much as the service.

## Task reuse across windows (`oc_v7_reuse.json`, 400-task design, 1000 studies per cell)

Per-task coding time varying with log-sd 0.3 or 0.5, the same for a task in every window that uses it:

| task order | SCALE false alarm, log-sd 0.3 / 0.5 | per-agent ratio under linear, log-sd 0.5 | SCALE power, Amdahl (log-sd 0.3) |
|---|---|---|---|
| a fresh pool per window (no reuse; reference) | 0.027 / 0.036 | 1.036 | 0.89 |
| **one pool reused by every window, fresh order per window (the design, rule 7)** | **0.027 / 0.029** | 1.042 | 0.89 |
| one pool, the same order in every window (one reset seed) | 0.035 / **0.063** | **1.10** | 0.86 |

Reuse with a fresh order per window is unbiased. With one seed for every window the N = 1 windows would all take the
same first few tasks while the N = 12 windows take the first 80, so the two sizes would compare different task sets: the
false alarm rises to 0.06 and the per-agent ratio drifts by 10%. Rule 7 requires a fresh seed per reset.

## Calibration check (`calib_t0de.json`, 1000 simulated T0d and T0e logs)

Every observed T0d / T0e measure lies inside the simulated 5-95% range (percentiles 0.21-0.86), except T0d's median
review time (23.5 s against a simulated 20.6 s, 5-95% 18.5-23.2; percentile 0.97). Table in DESIGN-SEARCH-v7.md
section 2. (Run before the review; the message limit does not bind in a T0e-length one-slot log of 7 hand-outs, and
the start-up comparisons there are against poll-quantised measurements.)

## Reproducing

```sh
PY=/Users/tom/git/carnot/analysis/aidev/.venv/bin/python
cd /Users/tom/git/carnot/analysis/fleet-sweep/analysis
$PY t0de_params.py <runs>/T0d-2026-09-29 <runs>/T0e-2026-09-29 --public design-search/t0de_params_public.json
cd design-search
$PY calib_t0de.py --reps 1000           # calibration check                         -> calib_t0de.json   (about 1 min)
$PY search7.py --stage A                # 776 designs, R = 150                      -> v7_stageA.json    (about 20 min on 10 cores)
$PY search7.py --stage B                # 27 designs, 15 truths, R = 600            -> v7_stageB.json    (about 20 min)
$PY oc_v7.py --part t1b --reps 1000     # rule 1 on simulated slot-mode T1b logs    -> oc_v7_t1b.json    (about 3 min)
$PY oc_v7.py --part oc --reps 1000      # the three designs                          -> oc_v7.json        (about 10 min)
$PY oc_v7.py --part reuse --reps 1000   # task reuse across windows                  -> oc_v7_reuse.json  (about 2 min)
```

The design search (stage A / B) ran before the review, with the N = 1 windows split by K and T1b at 30 + 10; its
screening figures are superseded by the tables above. Timings vary with machine load; the search banks go to
`$V7_SCRATCH` (default `/tmp/v7-banks`).
