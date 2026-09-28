# Study 2 design search for PLAN-v6: what T1 changed, and a 2 x 2 of fleet size and reviewers

Simulation only. No network, API, GitHub, harness or cloud calls. The only private inputs were the T1 and T0c event
logs (2026-09-28), read by `analysis/t1_params.py`, which writes numbers only: aggregates to
`t1_params_public.json` (tracked) and per-task timings to a private file beside the runs (never in this repo). No task
text, reviewer reason, error text or note body was copied.

Code: `analysis/t1_params.py` (T1 -> parameters), `analysis/synth.py` (the v6 process: `V6_TRUTH`, `make_truth_v6`,
`simulate_study_v6`; K parallel reviewers), `analysis/derive.py` (per-reviewer busy time), `analysis/v6.py` (the
codings), `calib_t1.py` (the simulator against T1), `dsim6.py` (window banks, one study), `search6.py` (stages A and
B), `oc_v6.py` (the final operating characteristics and rule 1). Raw results: `v6_stageA.json`, `v6_stageB.json`,
`oc_v6.json`, `oc_v6_t1b.json`, `calib_t1.json`.

## Answer in one paragraph

**T1 showed that with one serial reviewer, review binds at N = 12.** Haiku sessions made about 14 first submissions per
slot-hour (v5 assumed 6), 39% of reviews asked for changes, and each review took a median 20 s: twelve slots demanded
about 212 reviews an hour against one reviewer's 171, the reviewer was busy 88% of the twelve-slot phase and its queue
grew to 29 by the window's end. Under v5's design SCALE would then have compared the workers at N = 1 with the
reviewer's ceiling at N = 12: simulated with T1's process, a one-reviewer SCALE reads BEND in **53% of studies with
perfectly linear workers** (88% with T1's collision rate). **Recommended: vary the number of parallel reviewers K as a
factor, in a 2 x 2 of N in {1, 12} and K in {1, 3}**: six 45-min windows at each (N = 1, K) cell, three at (12, 3) and
four at (12, 1), 5-min warm-up, preceded by T1b (one slot for 90 min, then twelve for 30 min). 79.5 worker
session-hours: **$167 at the assumed $2.10**, fitting the $199 above the floor up to $2.50; degrade design (one N = 12
window of each K and two N = 1 windows fewer) $189 at 1.5x. **SCALE** (agent-side bend, on the K = 3 windows and every
N = 1 window) stays **confirmatory, primary**: false alarm 0.02, power 0.97 / 1.00 / 1.00 (Amdahl / USL / Carnot).
**CAP** (at N = 12, one reviewer finishes less than three: the paper's min(lambda X, V/h)) is **confirmatory,
secondary**: false alarm 0.025 when one reviewer never binds, power 0.74 under T1-like workers and 0.51 under
collision-free linear workers, and it correctly stays quiet under drag (USL / Amdahl: 0.03-0.05), where the model
predicts no ceiling. **COLL** stays **confirmatory, secondary** (false alarm 0.015-0.035; power 0.78 at T1's p = 0.0075
with USL-like workers, 1.00 with linear ones). Escapes are now rare (3% of approvals), so ESC is precise (±0.012) but
ESC-N weak (0.44 for a doubling): descriptive. **Abort rule 1 is rebuilt on start-up time** (a service-side quantity
that coordination drag does not touch), with a 90% interval against a 1.25 tolerance; 90 min at one slot gives CLEAR
0.94 with no throttling, STOP 0.77 at a 1.5x slowdown and 1.00 at 2x, never STOP under drag alone.

## 1. What T1 and T0c measured (numbers only; `t1_params_public.json`)

T1: one slot (s1) minutes 0-30, twelve slots 30-60, 10 min grace, Haiku 4.5 workers via per-slot routines, one serial
reviewer (local `claude -p`, Opus 5.5, default effort). Slots s3 and s2 were lost at minutes 34.9 and 40.7 to a CLI
auto-update (their `worker_down` events were logged late); phase B below excludes them. T0c: one slot, 45 min.

| quantity | T1 one slot (A) | T1 twelve slots (B, 10 working) | T0c (one slot) | v5 assumed |
|---|---|---|---|---|
| first submissions per slot-hour (lambda) | 16.0 (8 in 30 min) | 13.6 (68 in 5 slot-h); 13.9 after its first 5 min | 13.3 (10 in 45 min) | 6 |
| re-arm (slot freed -> launch) | 20.5 s | 20.1 s | 21.1 s | 0 |
| start-up (launch -> branch pushed), mean / median | 79 / 78 s | 90 / 93 s | 111 / 96 s | 78 s |
| of which routine fire -> push, mean | 69 s | 79 s | 102 s | |
| coding (pushed -> first READY), median / geometric mean | 82 / 76 s | 84 / 88 s | 83 / 81 s | ~5 min |
| rework (message -> READY), median | 61 s | 74 s | 62 s | 5 min |
| rework message after a slot frees | 1.8 s | 1.4 s | | |

| review and merge queue | value |
|---|---|
| review duration (T1, 117 reviews) | mean 21.0 s, median 20.2, p90 29.2, max 38.9, CV 0.28; first attempts 21.0 s, rework 20.9 s |
| review duration vs queue depth | correlation 0.01 (the reviewer never sees the queue); phase A 20.9 s, phase B 21.0 s |
| pooled T1 + T0c (134 reviews) | median 20.1 s, p90 29.3 s (the simulator draws from these quantiles) |
| requests for changes | 46 / 117 = 0.39 (first attempts 31 / 80, rework 15 / 37); T0c 8 / 17 |
| gap between reviews while changes wait | median 0.07 s; visible-test prep before a review 1.5 s |
| reviewer busy share | phase A 0.15; **phase B 0.88** (last 20 min 0.95); grace 1.00 |
| waiting for review at minute 35 / 40 / 45 / 50 / 55 / 60 / 65 / 70 | 4 / 10 / 18 / 24 / 25 / **29** / 17 / 2 (max 31) |
| demand vs service in phase B | 106 submissions (212 / h) against 76 reviews (152 / h) |
| merge queue per change | mean 5.8 s (p90 8.1); 71 passes: 63 merged, 6 rebase conflicts, 2 escaped defects (hidden_pre fail), 0 integration failures, 0 visible failures |
| collisions | 5 of 65 first merge-queue passes collided (all rebase conflicts), mean j = 10.1 merges since base: naive p = 0.0076 per merge |
| timeouts / launch failures | 1 timeout (a lost slot); 58 failed launches and 30 abandoned tasks, all from the CLI incident |
| credit meter | $249 before T0c and after T1 (whole dollars): no measurable burn over about 7 session-hours |

**Throttling as v5 coded it (activity per slot-minute, twelve : one)**: 0.71 raw, 0.77-0.80 without s2 / s3 depending
on the start skip; interval about 0.44-1.15. The one-slot phase had 8-9 tasks, so the rule could not tell 0.8 from 1.
Per task, start-up rose 13% (ratio of geometric means 1.13, 90% interval 0.98-1.31) and coding 16% (0.87-1.54): see
rule 1 below.

## 2. The v6 process as simulated

As v5 (`DESIGN-SEARCH-v5.md` section 1) except:

| Item | v6 value | Source |
|---|---|---|
| Slot cycle | re-arm gamma mean 20.1 s (CV 0.11) -> start-up lognormal median 85 s (log-sd 0.28) -> coding lognormal median 83 s (0.45) -> READY frees the slot; rework message 1.5 s after a slot frees, rework lognormal median 70 s (0.33) | T1 + T0c |
| Drag | the rival's X(N) / N is applied to the whole per-task cycle by stretching only the work legs (coding, rework) by s = (C / g - (C - P)) / P, C the mean cycle at N = 1, P its work part, g = X(N) / N; start-up and re-arm are not dragged. Simulated per-agent lambda at N = 12: Amdahl 0.49, USL 0.31 of N = 1 (targets 0.48, 0.29) | modelling choice (rule 1 needs start-up free of drag) |
| Reviewers | K on one FIFO queue, each review drawn from the T1 + T0c quantiles; K = 1 reproduces T1 | T1 |
| Quality | defect probability 0.20 (task sd 0.5 on the logit), caught 91.5%, good changes rejected 26%, no rework discount: 39% of reviews ask for changes, 3% of approvals escape | T1 |
| Merge queue | hidden_pre 0.3 s, rebase 1.4 s, tests after rebase 4.1 s | T1 mq_timing |
| Collisions | p = 0.0075 per merge since base (Carnot and "T1-like" truths), 90% rebase conflicts; 0.5% background integration failures | T1 |
| Windows | 45 min (5 warm-up) recommended; searched 30-75 min | supply: see below |

**Calibration** (`calib_t1.py`, 1000 simulated T1 logs on T1's exact schedule with K = 1 and s2 / s3 lost): every T1
measure lies inside the simulated 5-95% range: lambda (72nd / 46th percentile), reviewer busy share 0.88 against 0.93
(13th), the queue at minutes 35-70 (34th-60th), reviews and submissions in the twelve-slot phase (25th / 45th), share
asking for changes (45th), merges (72nd), mean review (44th). Table in OPERATING-CHARACTERISTICS-v6.md.

**Task supply.** At lambda 14 a twelve-slot fleet launches about 180-190 tasks an hour, so the 220 tasks of a window
last about 70-75 min. v5's 90-min windows would run dry at N = 12: an N = 12, K = 3 window runs out more than 10 min
before its end in 19-31% of 75-min windows and 0.5-5% of 60-min windows (linear workers, stage A); 45-min windows use
about 150 launches and are flagged in about 1% of studies (20% if the window CV is 0.5).

## 3. Search

**Stage A** (`search6.py --stage A`): window banks for six truths (linear, mild, Amdahl, USL, T1-like, and linear with
3x faster reviews as the CAP null) x window length 30 / 45 / 60 / 75 min x cells (N = 1 at K = 1, 3; N = 12 at
K = 1, 2, 3; N = 8 at K = 1, 3 for 45 and 60 min); 659 designs within budget (a) (sweep <= 87.3 session-hours), 200
studies per truth, SCALE and CAP only. **Stage B** (`--stage B`): ten 45-min designs, 19 truths, 600 studies each, all
codings. **OC** (`oc_v6.py`): the recommended and degrade designs, 23 truths, 1000 freshly simulated studies each.

### What stage A shows

1. **K = 2 is not enough.** Two reviewers at N = 12 under linear workers are busy 0.80 of the time on average and
   0.98 in the busiest window: review still binds. K = 3 brings it to 0.57 (0.94 at the 95th percentile).
2. **SCALE is no longer the scarce test.** With lambda at 14, three N = 12, K = 3 windows and a handful of N = 1
   windows give power >= 0.95 against Amdahl at every window length; extra windows buy little.
3. **CAP is the scarce test** and needs N = 12 windows at both K. Its effect is large (one reviewer finishes 0.61-0.68
   of three) but the pre-registered window CV of 0.3 makes three or four windows per arm necessary: power under
   linear workers is 0.1-0.2 with 1 + 1 windows, 0.45 with 3 + 3, 0.6 with 4 + 4.
4. **45 min is the best window length.** 30-min windows lose ratio (0.72 instead of 0.67: the queue has less time to
   build) and 60 / 75-min windows cost more per window and start to run out of tasks.
5. **N = 8 is dominated**: at N = 8 one reviewer only just binds (K = 1 : 3 ratio 0.86-0.94), so CAP's power falls
   to 0.05-0.17 and SCALE's to 0.72-0.98 for the same money.

| 45-min design (N = 1 at K = 1 / 3; N = 12 at K = 3 / 1) | $ at 2.10 | fits to | SCALE FPR / Amdahl | CAP FPR / linear / T1-like |
|---|---|---|---|---|
| 6 / 6; 3 / 1 | 110 | 3.79 | 0.02 / 0.96 | 0.04 / 0.09 / 0.27 |
| 6 / 6; 2 / 2 | 110 | 3.79 | 0.02 / 0.92 | 0.04 / 0.25 / 0.49 |
| 6 / 6; 3 / 3 | 148 | 2.82 | 0.02 / 0.97 | 0.03 / 0.42 / 0.71 |
| **6 / 6; 3 / 4** | **167** | **2.50** | **0.03 / 0.94** | **0.05 / 0.48 / 0.79** |
| 6 / 6; 4 / 4 | 186 | 2.25 | 0.02 / 0.98 | 0.04 / 0.60 / 0.85 |
| 4 / 4; 5 / 4 | 198 | 2.11 | 0.02 / 0.98 | 0.05 / 0.67 / 0.91 |
| K = 3 only: 12; 5 / 0 (30 min) | 102 | 4.10 | 0.03 / 1.00 | |

### Stage B (600 studies per cell; 45-min windows)

| design | $ (1.5x) | SCALE FPR / Amdahl / mild | CAP FPR / linear / T1-like / USL | COLL p = 0.0075, USL-like | ESC-N 2x |
|---|---|---|---|---|---|
| **6 / 6; 3 / 4** | **167 (250)** | **0.02 / 0.98 / 0.30** | **0.05 / 0.54 / 0.72 / 0.04** | **0.82** | **0.45** |
| 6 / 6; 3 / 3 | 148 (222) | 0.01 / 0.97 / 0.29 | 0.03 / 0.44 / 0.65 / 0.04 | 0.78 | 0.48 |
| 6 / 6; 4 / 3 | 167 (250) | 0.02 / 0.98 / 0.36 | 0.04 / 0.49 / 0.75 / 0.06 | 0.78 | 0.48 |
| 6 / 6; 4 / 4 | 186 (279) | 0.01 / 0.98 / 0.38 | 0.04 / 0.61 / 0.80 / 0.06 | 0.81 | 0.45 |
| 3 / 3; 3 / 4 | 158 (236) | 0.02 / 0.92 / 0.25 | 0.04 / 0.57 / 0.73 / 0.04 | 0.68 | 0.29 |
| **5 / 5; 2 / 3 (degrade)** | **126 (189)** | **0.02 / 0.89 / 0.23** | **0.04 / 0.40 / 0.56 / 0.05** | **0.71** | **0.40** |
| 5 / 5; 2 / 2 | 107 (161) | 0.02 / 0.90 / 0.25 | 0.07 / 0.27 / 0.43 / 0.06 | 0.66 | 0.40 |
| 6 / 6; 3 / 2 | 129 (194) | 0.02 / 0.98 / 0.31 | 0.03 / 0.34 / 0.53 / 0.04 | 0.71 | 0.47 |
| K = 3 only: 12 at N = 1; 3 at N = 12 | 91 (137) | 0.02 / 0.97 / 0.31 | – | 0.54 | 0.47 |
| **K = 1 only (v5 as it would have run): 12; 3** | 91 (137) | **0.53 (linear), 0.88 (T1-like)** / 0.98 | – | 0.58 | 0.36 |

**Why 6 / 6; 3 / 4 and not the $186-198 designs.** They buy 0.06-0.15 more CAP power but fit only up to $2.11-2.25 per
session-hour, i.e. 0-7% above the assumed burn; the recommended design fits up to $2.50 (19% over) and its degrade
design up to $3.32 (58% over), the same margins v5 chose. The fourth N = 12 window goes to K = 1 because SCALE is
already saturated and CAP is not. If budget (b) applies (plan usage, PLAN-v6 section 5.2), the 4 / 4 design
(`--cells 1x1=6 1x3=6 12x3=4 12x1=4`) is the natural upgrade: CAP 0.80 (T1-like), 9 more session-hours.

**Why a full 2 x 2 at N = 1.** At N = 1 the reviewer is busy 14% (K = 1) or 5% (K = 3) of the time and the model
predicts no K effect; the N = 1 windows cost the same at either K, so splitting them turns CAP into the interaction of
a 2 x 2 at no cost and checks the premise (K1: flagged in 1% of studies).

**SCALE test choice.** As v5: the fixed-CV LR test (false alarm 0.02-0.03 at CV <= 0.3; 0.12 if the true CV is 0.5).
The CV-estimated version is anti-conservative with three N = 12 windows (0.09-0.11) and the Welch test nominal (0.06)
with power 0.91 against Amdahl; both are reported beside SCALE. CAP uses the same NB model with the same fixed CV.

## 4. Grades (the recommended design; final figures in OPERATING-CHARACTERISTICS-v6.md)

| Question | Result | Grade | Why |
|---|---|---|---|
| 1. Does the workers' own output bend? | SCALE (K = 3 and N = 1 windows) | **confirmatory, primary** | false alarm 0.02, power 0.97 / 1.00 / 1.00; review does not bind at K = 3 |
| 4. Does one reviewer cap finished work? | CAP (N = 12, K = 1 vs 3) | **confirmatory, secondary** | false alarm 0.025; power 0.74 (T1-like) / 0.51 (linear); correctly quiet under drag |
| 3. Collisions | COLL | **confirmatory, secondary** | false alarm 0.015-0.035; power 0.78 at T1's p with USL-like workers, >= 0.99 with linear ones |
| 4. The 2 x 2 interaction at N = 1 | K1 | descriptive | a premise check |
| 4. Output against the ceiling from V | CAPFIT | descriptive | attainment per cell |
| 2. Escapes rising with N | ESC-N | descriptive | power 0.44 for a doubling of a 3% rate |
| 2. The escape rate | ESC | descriptive (estimate) | ±0.012 |
| 1. Which rival | FAMILY | descriptive | linear 0.98, Amdahl 0.86; USL / Carnot not separable |
| 4. Utilisation, throttling in the sweep | UTIL, THROTTLE | descriptive | |

## 5. Caveats

- **The budget is unresolved.** The meter did not move over about 7 session-hours; routine runs may draw Max-plan
  usage instead of credits. Budget (a) is costed at the v5 assumption; budget (b) is given as session-hours per day in
  PLAN-v6 section 5.2. The owner decides which applies.
- **CAP depends on the workers being near-linear.** If drag is strong (Amdahl or USL), one reviewer keeps up and CAP
  reads NOT-DETECTED; that is the model's prediction, not a failure, but it means CAP's power is conditional on what
  SCALE finds. T1 suggests near-linear workers (lambda 13.6 at twelve slots against 13.3-16 at one).
- **Parallel reviewers may slow each other or hit plan limits.** Three local Opus calls running pytest at once were
  not measured. With 20% contention per extra reviewer, the K = 3 reviewers are busy 0.77 at N = 12 (rule 4 flags 84%
  of studies) and SCALE's false alarm stays at 0.02. A free calibration with `--parallel 3` before T1b measures it.
- **Drag is modelled on the work legs only.** A rival's X(N) / N is imposed on the whole cycle; start-up is kept
  free of drag so rule 1 can read it as a service-side quantity. If real coordination also slows start-up, rule 1
  would read drag as throttling (STOP): the start-up of T1's twelve-slot phase was 13% slower (within noise).
- **The window CV of 0.3 is still assumed**; the N = 1 windows' observed CV is reported. At CV 0.5 SCALE's false
  alarm is 0.12.
- **Everything is conditional on T1's afternoon.** lambda, the review rate and the request-for-changes share come
  from one day; rule 3 (lambda floor 7) and UTIL watch them.
