# Study 2 design search for PLAN-v5: what can the credits buy once review is fast?

Simulation only. No network, API, GitHub or cloud calls were made, and no task text, hidden test or private note was
read: the only private input was the 220-task census's aggregate pair statistics (below). The search reuses the
method of DESIGN-SEARCH.md (a discrete-event simulation of the harness through the real analysis code, common random
numbers across designs, a coarse screen and then a larger shortlist run) with the v5 process and v5 codings.

Code: `analysis/synth.py` (the v5 process: `V5_TRUTH`, `make_truth_v5`, `v5_order`), `analysis/derive.py` (collision
exposure, merge-queue utilisation), `analysis/v5.py` (the codings), `dsim5.py` (one simulated study), `search5.py`
(stages A, B and the pilot comparison), `oc_v5.py` (the final operating characteristics) and `tables5.py` (tables
into `tables_v5.md`). Raw results: `v5_pilot.json`, `oc_v5.json` (tracked) and `v5_stageA.json`, `v5_stageB.json` (1.7 MB and 0.4 MB, gitignored; `search5.py --stage A` / `--stage B` regenerate them in about 30 and 55 min on 10 cores).

## Answer in one paragraph

**Recommended: N = 1 and N = 12, twelve 90-minute windows at N = 1 and three at N = 12, no separate pilot
(72 sweep session-hours; $166 with T0 and T1 at the assumed $2.10 per session-hour, fitting up to $2.43).** At 1.5x
burn the pre-registered degrade design drops one N = 12 window ($192 at $3.15; fits up to $3.28). The primary
question, *does finished work bend below proportional?*, can be graded **confirmatory**: the one-sided test of
per-agent output falling with N has a false-alarm rate of about 0.03 and power about 0.9 against the mildest rival
(Amdahl, alpha 0.1) and 1.00 against USL and Carnot. **Collisions** can be graded **confirmatory, secondary**, as a
test of p = 0 with a stated power curve: false alarm about 0.02, power about 0.8 at p = 0.02 per merged change with
USL-like workers, and above 0.8 already at p = 0.005 if the workers scale linearly (more merges); at the study-1 value
p = 0.005 with USL-like workers it is about 0.3, so a null result rules out p of about 0.02 or more, not p = 0.005.
**Escapes rising with N** reach 0.8 power only for a doubling (0.10 to 0.20), with a false alarm slightly above
nominal (0.06), so it is **descriptive**, as is the **escape rate** itself (about ±0.04 at 95%). The **four-way family
pick is descriptive**: linear and Amdahl are recognised 0.97 and 0.85 of the time, but USL and Carnot uncapped differ
by (1 - p)^11, 5% at p = 0.005, and are told apart only at chance. The **reviewer** at 10-30 s per change is far from
saturation under USL-like or Amdahl workers (busy 0.2-0.4), but under linear workers at N = 12 it is busy about 0.8 of
the time in the busiest window (median), and saturates if reviews take 20-40 s; the simulated SCALE false-alarm rate
stays at 0.02-0.03 even then, because a queue only starts to cap output at a utilisation of 1. The merge queue (2 s
per change) never exceeds 6%.

## 1. The v5 process as simulated

| Item | Value | Source |
|---|---|---|
| Worker | one Haiku 4.5 session per task in N slots; READY frees the slot; rework goes to the next free slot as a follow-up to the same session; a session without READY 25 min after launch or rework is abandoned | harness README "Worker model" |
| Start-up | 1.3 min per task (exponential) | dry runs |
| Work time | exponential, mean set so that lambda at N = 1 is 6.0 first submissions per slot-hour (synth `lam1` = 11/h of work after start-up, rework 5 min) | dry runs: lambda about 6 |
| Coordination drag | per-agent speed x X(N)/N: linear; Amdahl alpha 0.1; USL alpha 0.1, beta 0.01; Carnot = USL + collisions at p = 0.005; "mild" = Amdahl alpha 0.03 (not a rival, a sensitivity) | PLAN-v5 section 1 |
| Over-dispersion | Gamma(mean 1, CV 0.3) multiplier on worker speed per window (sensitivity CV 0.15 and 0.5; and CV 0.3 x sqrt(120 / L), variance per unit time fixed) | v4 assumption |
| Reviewer | one serial call per change, uniform 10-30 s (mean 20 s, about 180 per busy hour), never load-dependent; 1% call errors retried; sensitivity 20-40 s | reviewer calibration, PLAN-v5 |
| Quality | first-attempt defect probability 0.35 (task heterogeneity sd 0.5 on the logit), halved on rework; the reviewer catches 77% of defects and rejects 5% of good changes. Escape share about 0.10 of approvals, review-bounce share about 0.28 | chosen to give the base rates in the brief |
| Escapes rising | reviewer miss probability x (1 + e (N - 1)/11): e = 0.62 gives 0.10 -> 0.16 at N = 12, e = 1.25 gives 0.10 -> 0.20 (both measured in the simulation) | brief |
| Merge queue | serial, about 2 s per change (hidden tests 0.6 s, rebase 0.4 s, tests after rebase 1 s) | dry runs |
| Collisions | at a change's first rebase, each other change merged since its base (session launch or last rework message, when the session merges origin/main) collides with probability p; 80% show as rebase conflicts, 20% as integration failures; plus 1% background integration failures unrelated to collisions | model's p per concurrent pair |
| Collision structure (sensitivity) | "census": a fixed pair-conflict graph; 28 files with skewed popularity and 1-4 files per task give 15% of pairs sharing a file, and 8.3% of those conflict (1.2% of all pairs), matching the 220-task census's aggregates; a structurally conflicting pair conflicts when exposed with probability 1 or 0.5 | `pairs-220.json` (aggregates only) |
| Tasks | 220 per window, handed out in a seeded order | PLAN-v4 section 7.7 |
| Budget | $200 of spend ($250 credits, $50 floor); T0 about $1; T1 6.5 session-hours; a window costs N x L / 60 session-hours; $2.10 per session-hour assumed, 1.5x as the robustness case | brief |

The census: 220 tasks, 24,090 pairs; 3,600 share a file (14.9%; 28.7% in the first 120); 298 conflict textually
(1.24%), every one of them among the file-sharing pairs. `synth.py` can also map the private census onto the pool
(`pairs_json`, reading only `files` and `textual_conflicts`); nothing from it is written out, and every number in
this document uses the synthetic graph.

Calibration of the base process at N = 1 / N = 12 (40 windows each, no window noise): lambda 6.0 / 6.2 (linear) and
2.2 (USL) per slot-hour; completion 0.92 / 0.97 (linear) and 0.85 (USL); session timeouts per 120-min window 0.3 /
2 (linear) and 24 (USL: at N = 12 the drag makes a task take about 30 min, so four in ten sessions time out). The
merge queue is busy 5% of the time at most.

## 2. Codings evaluated (v5.py)

- **SCALE** (candidate primary): finished_w ~ NB(theta x slot-hours_w x N_w^gamma), CV fixed at 0.3 as in v4;
  one-sided likelihood-ratio test of gamma < 0. Also evaluated: the same with the CV estimated (`lr_estcv`) and a
  one-sided Welch t-test on log per-agent rates (`welch`).
- **Linear vs bending pick, FAMILY**: each rival's likelihood with its own free level, fitted to all sweep windows;
  pick = highest likelihood; three-way reading merges USL and Carnot. Pilot-anchored variant: level fixed at the
  pilot's finished per single-agent hour (v4's approach).
- **ESC-N**: logistic escaped ~ (N - 1)/(N_max - 1) over approvals with a hidden-test result, one-sided LR, >= 8 escapes.
  **ESC**: the rate with Wilson and task-clustered 95% intervals.
- **COLL**: logistic collided-first-pass ~ j (merges since the change's base), one-sided LR, >= 3 collisions; p-hat
  in the model form 1 - s (1 - p)^j. COLL-m (jm, file-sharing merges) and COLL-k (k in flight at submit, as study 1)
  descriptive.
- **UTIL**: reviewer and merge-queue busy share per window.

Why j and not k: with a 20-s reviewer and a 2-s merge queue a submitted change is in flight for well under a minute
unless it bounces, so the logged k (changes submitted and not merged) is 0-3 even at N = 12. A change is exposed to
every merge between its branch point and its rebase; j counts exactly those, is derived from logged events only
(session_launch, session_message, rebase, merge), and averages about 4 at N = 12 under USL workers and 8 under linear
workers against 0.6 at N = 1. In the simulation the k-slope has about 40% of the j-slope's power (0.21 against 0.52 at p = 0.01, 0.36 against 0.80 at p = 0.02, recommended design). The file-sharing term jm (COLL-m) is uninformative when collisions ignore files (the p-per-merge truths: 0.04-0.08) but detects the census-like graph, where only file-sharing pairs conflict, 0.78 of the time.

## 3. Search

**Stage A** (`search5.py --stage A`): 142 designs that fit $200 at $2.10: sizes (1, 12), (1, 8), (2, 12), (1, 4, 12)
and (1, 6, 12); 60-, 90- and 120-min windows; 2-8 windows at the largest size; the cheap small-size windows filled up
to the budget (at most 16 windows in all). Nine truth cells, 200 studies each. **Stage B** (`--stage B`): 16 designs
(ten of the best, one three-size, one with N_max = 8, six degrade candidates that fit at $3.15) in 20 cells, 1000
studies each, plus the window-CV-grows-as-windows-shorten sensitivity. **Pilot** (`--stage P`): the chosen sweep with
2, 4 or 8 separate 60-min T2 pilot windows and pilot-anchored levels. Full tables: `tables_v5.md`.

### What stage A shows

| sizes | window | best design | $ | SCALE power, Amdahl | ESC-N power, 0.10 -> 0.20 | COLL power, p = 0.01 |
|---|---|---|---|---|---|---|
| (1, 12) | 60 | 1 x 10, 12 x 6 | 187 | 0.90 | 0.70 | 0.60 |
| (1, 12) | 90 | 1 x 10, 12 x 4 | 197 | 0.92 | 0.80 | 0.69 |
| (1, 12) | 120 | 1 x 8, 12 x 3 | 199 | 0.88 | 0.85 | 0.64 |
| (1, 8) | 120 | 1 x 12, 8 x 4 | 199 | 0.79 | 0.61 | 0.70 |
| (2, 12) | 90 | 2 x 6, 12 x 3 | 166 | 0.83 | 0.68 | 0.51 |
| (1, 4, 12) | 90 | 1 x 8, 4 x 3, 12 x 3 | 191 | 0.88 | 0.76 | 0.58 |
| (1, 6, 12) | 90 | 1 x 8, 6 x 1, 12 x 3 | 172 | 0.85 | 0.68 | 0.54 |

1. **N_max = 12.** Every test gets its information from the contrast between the ends; N = 8 loses 0.1-0.15 of SCALE
   power for the same money. A middle size (4 or 6) costs N = 12 hours and buys only a little FAMILY resolution.
2. **N = 1 windows are cheap and valuable.** An N = 1 window costs 1/12 of an N = 12 window and carries a
   comparable share of the between-window noise, so the best designs fill the budget with 8-12 of them. N = 2 as the
   low size is worse (half as many windows for the money).
3. **Window length.** For the same session-hours, more shorter windows average out more window-level noise but lose
   10 min of warm-up each and censor more at the end. 90 min is the best compromise; if window noise is really
   per unit time (CV 0.3 at 120 min growing as sqrt(120/L)), the 90-min designs lose about 0.04 of power and
   60-min designs about 0.1 (stage B), so 90 min stays at or near the top either way.

### Stage B (1000 studies per cell)

| design | $ at 2.10 / 3.15 | windows | SCALE FPR, CV 0.3 / 0.5 | power Amdahl / USL / mild | Amdahl, CV grows as windows shorten | linear-vs-bending pick, linear / Amdahl |
|---|---|---|---|---|---|---|
| 1 x 8, 12 x 3 @120 | 199 / 299 | 11 | 0.025 / 0.11 | 0.89 / 1.00 / 0.23 | 0.89 | 0.97 / 0.92 |
| 1 x 10, 12 x 4 @90 | 197 / 296 | 14 | 0.030 / 0.09 | 0.93 / 1.00 / 0.29 | 0.90 | 0.97 / 0.93 |
| **1 x 12, 12 x 3 @90** | **166 / 249** | **15** | **0.029 / 0.11** | **0.91 / 1.00 / 0.26** | **0.86** | **0.97 / 0.93** |
| 1 x 10, 12 x 3 @90 | 160 / 239 | 13 | 0.020 / 0.12 | 0.88 / 1.00 / 0.22 | 0.87 | 0.97 / 0.90 |
| 1 x 12, 12 x 2 @120 | 166 / 249 | 14 | 0.022 / 0.13 | 0.85 / 1.00 / 0.21 | 0.86 | 0.95 / 0.91 |
| 1 x 10, 12 x 6 @60 | 187 / 280 | 16 | 0.023 / 0.08 | 0.91 / 1.00 / 0.25 | 0.85 | 0.98 / 0.91 |
| **1 x 12, 12 x 2 @90 (degrade)** | **128 / 192** | **14** | **0.027 / 0.12** | **0.81 / 0.99 / 0.20** | **0.81** | **0.95 / 0.89** |
| 1 x 4, 12 x 2 @120 | 132 / 198 | 6 | 0.039 / 0.11 | 0.68 / 0.98 / 0.16 | 0.73 | 0.91 / 0.84 |

| design | ESC-N FPR / 0.15 / 0.20 (USL workers) / 0.20 (linear) | ESC half-width | COLL FPR / p = 0.01 (USL; linear) / 0.02 / 0.05 | census graph, manifest 1 / 0.5 | reviewer util at N = 12, linear workers: median / p95 of the busiest window |
|---|---|---|---|---|---|
| 1 x 8, 12 x 3 @120 | 0.063 / 0.39 / 0.80 / 0.86 | 0.036 | 0.020 / 0.62; 0.99 / 0.89 / 0.99 | 0.72 / 0.41 | 0.78 / 0.97 |
| 1 x 10, 12 x 4 @90 | 0.065 / 0.38 / 0.80 / 0.83 | 0.036 | 0.020 / 0.61; 0.99 / 0.87 / 0.99 | 0.70 / 0.42 | 0.81 / 1.00 |
| **1 x 12, 12 x 3 @90** | **0.060 / 0.38 / 0.79 / 0.87** | **0.038** | **0.017 / 0.52; 0.98 / 0.80 / 0.99** | **0.63 / 0.40** | **0.79 / 1.00** |
| **1 x 12, 12 x 2 @90 (degrade)** | **0.060 / 0.31 / 0.70 / 0.83** | **0.041** | **0.009 / 0.41; 0.94 / 0.70 / 0.94** | **0.52 / 0.28** | **0.74 / 0.98** |

**Why 1 x 12, 12 x 3 at 90 min and not the $197-199 designs.** They buy 0-0.04 more power on SCALE and 0.05-0.1 more
on COLL and ESC-N, but they fit only at the assumed burn itself ($199 needs burn <= $2.11; $197 <= $2.14), so any
overrun sends them to their degrade design. The burn is unmeasured. The recommended design fits up to $2.43 (16%
over the assumption) and has the best SCALE power per dollar among the 90-min designs; its degrade design is simply
the same schedule without the last N = 12 window and fits up to $3.28 (1.56x). Either way the study keeps the same
window length, the same order rule and the same analysis.

**SCALE test choice.** The fixed-CV likelihood-ratio test is kept (as v4 kept its CV 0.3): its false-alarm rate is
0.02-0.04 when the true window CV is 0.3 or less, and power is highest. It is anti-conservative if the true CV is
0.5 (false alarm 0.09-0.13). Estimating the CV instead gives 0.06-0.11 false alarms at CV 0.3 (small-sample bias
with three N = 12 windows) and the Welch test is nominal (0.04-0.05) but loses 0.1-0.35 of power with three N = 12
windows. Both are reported beside SCALE; the window-to-window CV of the N = 1 windows is reported too, so a reader can
see which regime applies.

### Pilot: none

| design | $ | linear-vs-bending pick (linear / Amdahl / USL): free levels | pilot-anchored | four-way pick (Amdahl / USL): free | pilot-anchored |
|---|---|---|---|---|---|
| 1 x 12, 12 x 3 @90, no pilot | 166 | 0.98 / 0.91 / 1.00 | – | 0.82 / 0.42 | – |
| + 2 x 60-min T2 | 170 | same | 0.92 / 0.72 / 0.93 | same | 0.55 / 0.25 |
| + 4 x 60-min T2 | 174 | same | 0.94 / 0.78 / 0.98 | same | 0.66 / 0.32 |
| + 8 x 60-min T2 | 183 | same | 0.96 / 0.84 / 1.00 | same | 0.74 / 0.38 |

Anchoring the rivals on a pilot is worse at every pilot size: the pilot's level carries its own window noise (and a
60-min window's completion share differs from a 90-min window's), whereas a free level is fitted to twelve N = 1
windows of the sweep itself. The tests of PLAN-v5 need no pilot quantity: SCALE and FAMILY compare shapes, ESC and
COLL are within-sweep. What the pilot was to measure (lambda, completion, start-up, merge-queue time) is either
measured in T1 (throttling, start-up, merge-queue time, a first lambda) or in the first two N = 1 sweep windows,
which come before any N = 12 window and carry abort rule 3 (lambda floor). The money goes into N = 1 windows instead.

## 4. Grades (the recommended design; final figures in OPERATING-CHARACTERISTICS-v5.md, 2000 studies per cell)

| Question | Result | Grade | Why |
|---|---|---|---|
| 1. Scaling: does finished work bend? | SCALE | **confirmatory, primary** | false alarm about 0.03 (CV 0.3), power 0.91 (Amdahl) and 1.00 (USL, Carnot); 0.82 at 1.5x burn (degrade), 0.89 at lambda -30%, 0.78 with both |
| 1. Which rival | FAMILY | descriptive | linear 0.97, Amdahl about 0.8 correct, but USL vs Carnot is a coin toss by construction (5% apart at N = 12) |
| 3. Collisions | COLL (j-slope) | **confirmatory, secondary** | a controlled test of p = 0 (false alarm about 0.02) with power about 0.8 at p = 0.02 (USL-like workers) and >= 0.8 at p = 0.005 under linear workers; the power curve is part of the claim |
| 3. H3 file sharing; study 1's k | COLL-m, COLL-k | descriptive | lower power; k is nearly constant with a fast reviewer |
| 2. Escapes rising with N | ESC-N | descriptive | power about 0.8 only for 0.10 -> 0.20, about 0.4 for 0.10 -> 0.15; false alarm about 0.06 |
| 2. The escape rate | ESC | descriptive (estimate) | 95% half-width about 0.04 on about 250 approvals |
| 4. Where the limit moves | UTIL | descriptive | reported with the saturation flags; no test |
| 5. Effort | EFFORT | descriptive | post hoc, no live consequence |

## 5. Caveats

- **Linear workers load the reviewer.** At N = 12 with no drag, about 100 reviews per hour at 20 s each keep the
  reviewer 60-80% busy (the busiest window at the 95th percentile: fully busy). If reviews take 20-40 s it saturates
  in the busiest windows. In the simulation this barely moves finished counts (SCALE's false alarm stays 0.02-0.04),
  but a real reviewer that slows under its own load would, so UTIL carries a flag (busy >= 0.8) and SCALE is also
  reported without flagged windows if any (abort rule 4 in PLAN-v5 section 5).
- **Timeouts shape which tasks finish.** Under USL-like drag four in ten sessions at N = 12 time out; completion falls
  to 0.85 and the escape share drifts up by about 0.006 through selection alone, which is why ESC-N's null false-alarm
  rate is 0.06 rather than 0.05.
- **The p in COLL is per merged change since a branch point.** That is the physical exposure of a rebase conflict.
  It is not the paper's "per concurrent pair" in general, though with N agents each change sees about N - 1 merges
  during its life when drag is low; the study reports p-hat in that unit, with j's distribution beside it.
- **Everything assumes window-level noise like v4's (CV 0.3).** If the real CV is 0.5, SCALE's false alarm is about
  0.1; the N = 1 windows' observed CV is reported so this can be seen.
- **Costs assume $2.10 per session-hour** and that a slot costs whether its session is busy or not. Both are checked
  on T1 before any sweep window (abort rule 2).
