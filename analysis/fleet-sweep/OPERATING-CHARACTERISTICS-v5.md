# Study 2 operating characteristics (PLAN-v5)

The simulated operating characteristics of the PLAN-v5 design (PLAN-v5 section 5) and its degrade design. **These
figures, not the design-search screening figures, go into the pre-registration.** They are the values in
`analysis/v5.py` `V5_OC`, which `predict.py` and `score.py` print beside the results.

Simulation only: `analysis/synth.py` (the v5 process) -> `derive.py` -> `v5.py` (the codings as pre-registered),
driven by `analysis/design-search/oc_v5.py` through `dsim5.py`; raw output `oc_v5.json`. No network, API, harness or
cloud calls. 2000 simulated studies per cell, so a rate near 0.05 has a 95% half-width of about ±0.010, near 0.5
about ±0.022, near 0.9 about ±0.013. The same seed block is used for each truth in both designs (common random
numbers).

## Setup

| Item | Value |
|---|---|
| Design | N = 1 and 12; **twelve 90-min windows at N = 1 and three at N = 12** (10 min warm-up, 10 min grace), order 1, 1, 12, 1, 1, 1, 1, 12, 1, 1, 1, 1, 12, 1, 1; no T2 pilot |
| Degrade design (1.5x burn) | the same without one N = 12 window (twelve at N = 1, two at N = 12) |
| Cost | 72 sweep session-hours + T1 6.5 + T0 about $1: **$166 at $2.10**, fits $200 up to $2.43 per session-hour; degrade 54 sweep session-hours: $128 at $2.10, **$192 at $3.15**, fits up to $3.28 |
| Worker | one Haiku session per task in N slots; 1.3-min start-up; READY frees the slot; rework to the next free slot as a follow-up to the same session; 25-min timeout; lambda at N = 1 = 6.0-6.4 first submissions per slot-hour |
| Worker truths | linear; mild (Amdahl alpha 0.03; a sensitivity, not a rival); Amdahl (alpha 0.1); USL (alpha 0.1, beta 0.01); Carnot uncapped (USL + collisions at p = 0.005) |
| Noise | Gamma(mean 1, CV 0.3) multiplier on worker speed per window; sensitivity CV 0.15 and 0.5 |
| Reviewer | serial, one call per change, uniform 10-30 s; sensitivity 20-40 s ("slow reviews") |
| Quality | escape share about 0.10 of approvals at every size unless planted; planted rises to about 0.15 and 0.20 at N = 12 |
| Merge queue | serial, about 2 s per change |
| Collisions | at a change's first rebase, each other change merged since its base collides with probability p (80% rebase conflicts, 20% integration failures), plus 1% background integration failures; sensitivity: a census-like fixed pair-conflict graph (1.2% of pairs, all among the 15% that share a file) |
| lambda -30% | lambda at N = 1 about 4.4 per slot-hour |
| Tasks | 220 per window (no window ran out in any cell) |

## Headline table (recommended design)

| Result | Grade | Rate | At 1.5x burn (degrade) |
|---|---|---|---|
| **SCALE false alarm**, linear workers, window CV 0.3 | **confirmatory, primary** | **0.030** | 0.028 |
| SCALE false alarm at window CV 0.15 / 0.5 | | 0.001 / 0.11 | 0.001 / 0.12 |
| SCALE false alarm, linear workers with 20-40 s reviews (reviewer saturated in the busiest windows) | | 0.033 | 0.036 |
| **SCALE power: Amdahl / USL / Carnot uncapped** | **confirmatory, primary** | **0.91 / 1.00 / 1.00** | 0.82 / 1.00 / 1.00 |
| SCALE power against a mild bend (alpha 0.03, per-agent ratio about 0.8) | | 0.26 | 0.20 |
| SCALE power, Amdahl, at window CV 0.15 / 0.5 | | 0.97 / 0.81 | 0.90 / 0.72 |
| SCALE at lambda -30%: false alarm / power Amdahl / power USL | | 0.027 / 0.89 / 1.00 | 0.032 / 0.78 / 0.995 |
| **COLL false alarm** (p = 0; background integration failures only), USL-like / linear workers | **confirmatory, secondary** | **0.020 / 0.034** | 0.013 / 0.028 |
| **COLL power, USL-like workers**, p = 0.005 / 0.01 / 0.02 / 0.05 | **confirmatory, secondary** | **0.28 / 0.52 / 0.83 / 0.98** | 0.21 / 0.42 / 0.72 / 0.95 |
| COLL power, linear workers, p = 0.005 / 0.01 / 0.02 | | 0.83 / 0.98 / 1.00 | 0.72 / 0.94 / 1.00 |
| COLL power on the census-like graph, manifest 1 / 0.5 (USL-like workers) | | 0.64 / 0.37 | 0.50 / 0.28 |
| p-hat 95% interval covers the true p (p per merge truths) | | 0.95-0.97 | 0.94-0.97 |
| ESC-N false alarm, USL-like / linear workers | descriptive | 0.060 / 0.051 | 0.058 / 0.051 |
| ESC-N power, 0.10 -> 0.15 / 0.20 at N = 12, USL-like workers | descriptive | 0.37 / 0.78 | 0.34 / 0.69 |
| ESC-N power, 0.10 -> 0.15 / 0.20, linear workers | descriptive | 0.42 / 0.86 | 0.40 / 0.83 |
| ESC: median 95% half-width of the escape rate, USL-like / linear workers (median approvals) | descriptive | 0.038 (242) / 0.026 (498) | 0.041 (202) / 0.030 (373) |

"Power" is the share of simulated studies coding BEND (SCALE), DETECTED (COLL) or RISES (ESC-N) at one-sided p < 0.05
under a truth with the effect; "false alarm" the same share under a truth without it.

**Reading COLL.** It is a test of p = 0 whose false-alarm rate is controlled (0.02-0.03), with the power curve above
as part of the claim. A DETECTED result says collisions force rework and estimates p per merged change; a
NOT-DETECTED result rules out p of about 0.02 or more with USL-like workers (0.005 or more if the workers scale
linearly, since more merges then happen per change), and says nothing about p = 0.005.

**Reading SCALE.** SCALE is about the workers' own scaling. Linear workers with p = 0.02 collisions are no longer
linear in finished output (their per-agent ratio is 0.92), and SCALE calls BEND in 7.5% of those studies; that is the
Carnot rework term, not a false alarm.

## Four-way family pick (descriptive)

Rows: truth; columns: the rival with the highest likelihood (each with its own free level), recommended design.

| truth | linear | Amdahl | USL | Carnot uncapped | three-way correct (USL and Carnot merged) | linear vs bending correct |
|---|---|---|---|---|---|---|
| linear | 0.97 | 0.03 | 0.00 | 0.00 | 0.97 | 0.97 |
| Amdahl | 0.08 | 0.84 | 0.08 | 0.005 | 0.84 | 0.92 |
| USL | 0.00 | 0.23 | 0.43 | 0.35 | 0.77 | 1.00 |
| Carnot uncapped | 0.00 | 0.22 | 0.43 | 0.35 | 0.78 | 1.00 |

USL and Carnot uncapped predict within 5% of each other at N = 12 (p = 0.005), so they cannot be separated by
finished counts; the collision term is measured directly by COLL instead. Degrade design: linear 0.95, Amdahl 0.77,
three-way USL 0.74 / Carnot 0.77.

## Where the limit moves (UTIL, descriptive)

Reviewer busy share in the busiest N = 12 window (median over studies; 95th percentile), and the mean over windows:

| workers | reviews 10-30 s: busiest window, median (p95) | mean | reviews 20-40 s: busiest window, median | merge queue, busiest window |
|---|---|---|---|---|
| linear | 0.79 (1.00) | 0.64 | 1.00 | 0.054 |
| mild | 0.62 (0.83) | 0.50 | – | 0.042 |
| Amdahl | 0.41 (0.58) | 0.33 | 0.62 | 0.028 |
| USL / Carnot | 0.26 (0.37) | 0.20 | 0.39 | 0.018 |

The merge queue never binds. The reviewer does not bind under drag, but under linear workers at N = 12 it is busy
most of the time and saturates in the busiest windows if reviews take 20-40 s. The simulated SCALE false alarm stays
at 0.03 even then, because output is capped only when the queue grows without bound; PLAN-v5 section 5 rule 4 flags
any N = 12 window with the reviewer busy >= 80% and SCALE is reported with and without it.

## Other simulated quantities (recommended design, medians)

| workers | lambda N = 1 / 12 (per slot-hour) | finished per window N = 1 / 12 | per-agent ratio | timeouts per N = 12 window | mean j at N = 12 |
|---|---|---|---|---|---|
| linear | 6.4 / 6.3 | 7.8 / 96 | 1.01 | 2.5 | 7.7 |
| mild | 6.4 / 4.9 | 7.9 / 74 | 0.78 | 4.8 | 6.9 |
| Amdahl | 6.4 / 3.4 | 7.9 / 48 | 0.51 | 10.3 | 5.5 |
| USL | 6.4 / 2.2 | 7.9 / 30 | 0.31 | 17.4 | 4.0 |

## Reproducing

```sh
PY=/Users/tom/git/carnot/analysis/aidev/.venv/bin/python
cd /Users/tom/git/carnot/analysis/fleet-sweep/analysis/design-search
$PY oc_v5.py --reps 2000        # about 18 min on 10 cores -> oc_v5.json
$PY tables5.py                  # stage tables -> tables_v5.md
```
