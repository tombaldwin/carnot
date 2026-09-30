# Study 2 design search for PLAN-v7: the v6 study under a budget counted in cloud tasks

> **After the review (REVIEW-fable-v7.md, 2026-09-30):** `oc_v7_t1b.json`, `oc_v7.json` and `oc_v7_reuse.json` were
> regenerated for the revised plan (every N = 1 window at K = 5 unless the 500-task design is chosen; T1b 30 + 15;
> sessions retired after 4 tasks or 6 messages; rule 1's coding legs cut off 5 min before window end; 10% loss and
> other new sensitivities). OPERATING-CHARACTERISTICS-v7.md holds the current figures. The start-up legs of T0d / T0e
> in section 1 are quantised to the 15-s watcher poll (rule 1 now pre-registers its OCs at a start-up log-sd of 0.25),
> and T0e lost two of its seven slot-mode hand-outs, not one (the relaunch after the stall pushed a mis-named branch).
> The stage A / B screening below is unchanged and ran on the pre-review design.

Simulation only. No network, API, GitHub, harness or cloud calls. The only private inputs were the T0d and T0e event
logs (2026-09-29), read by `analysis/t0de_params.py`, which writes numbers only (counts, medians, quantiles; no task
id, task text, review reason or note body) to `t0de_params_public.json` (tracked).

Code: `analysis/t0de_params.py` (T0d / T0e -> parameters), `analysis/synth.py` (the v7 process: `V7_TRUTH`,
`make_truth_v7`, `simulate_study_v7`; one session per slot), `analysis/v7.py` (the designs, rule 1 in slot mode, rule 2,
the budget ledger; the tests are v6's), `calib_t0de.py` (the simulator against T0d / T0e), `dsim7.py` (window banks,
one study), `search7.py` (stages A and B), `oc_v7.py` (T1b, the final operating characteristics, the task-reuse
check). Raw results: `v7_stageA.json`, `v7_stageB.json`, `oc_v7_t1b.json`, `oc_v7.json`, `oc_v7_reuse.json`,
`calib_t0de.json`.

## Answer in one paragraph

**The budget is now about 400 cloud tasks, T1b included** ($186 above the $50 floor at about $0.45 per task), and
the fleet makes about 26 hand-outs per slot-hour, so v6's design (T1b plus nineteen 45-min windows, seven of them at
N = 12) would use about 2,300 tasks: six times the budget. Per task, the most informative design keeps **N = 12** (N = 8 and N = 6 buy less SCALE power per
task: the bend they would see is smaller), makes the windows **short and all of one length (15 min, 3 min warm-up)**, so
that a task buys N = 12 windows rather than minutes and the end-of-window effects cancel between N = 1 and N = 12, and
runs **five parallel reviewers** at N = 12 (lambda is now about 23, so three reviewers would bind at N = 12: busy 0.88;
four are busy 0.75 and trip rule 4 in most studies; five are busy 0.61). **Recommended (400 tasks): T1b (one slot for
30 min, then twelve for 10 min; about 66 tasks) and fifteen 15-min windows: N = 1 x 12 (six at K = 1, six at K = 5)
and N = 12 x 3 at K = 5; about 384 tasks in all ($173 at $0.45; $115-230 at $0.30-0.60) and about 550 Opus
reviews.** SCALE stays **confirmatory, primary** (final OCs: false alarm 0.026, power 0.89 against Amdahl, 1.00 against
USL / Carnot, 0.26 against a mild bend). CAP needs an N = 12 window with one reviewer, which costs about 95 tasks (more
than a K = 5 window: the held-up rework frees the slots for new tasks), so it is **confirmatory only in the 500-task
design** (one such window added: power 0.58 under T1-like workers, false alarm 0.04) and not run at 300 or 400, where
T1's descriptive evidence of the ceiling stands. COLL stays **confirmatory, secondary** at no extra cost (false alarm
<= 0.008; power 0.79 under near-linear workers at T1's p, but only 0.20 under USL-like workers, whose slowed fleet
makes few merges in short windows). **At 300 tasks** (292; affordable up to $0.64 per task): N = 1 x 10 and N = 12 x 2
(SCALE power 0.77 against Amdahl). **At 500** (479; up to $0.39 per task): the 400-task design plus one N = 12, K = 1
window. The designs are nested, and **rule 2 chooses between them after T1b** from the measured cost per task. **Abort
rule 1 now reads follow-up start-ups only**: they compare like with like (launches carry provisioning and a clone; the
phases mix the two in different proportions), and they are precise (log-sd 0.04 at one slot), so T1b's one-slot phase
can be 30 min instead of 90.

## 1. What changed since v6 (measured; `t0de_params_public.json`)

The harness now launches with `claude --cloud` and the #81776 workaround (routines billed the Max plan; these sessions
draw cloud-session credits) and keeps one session per slot: the first task at launch, later tasks as follow-up
messages, rework to the session that did the task, a session retired after `tasks_per_session` = 4 tasks (was 8).

| quantity | T0d (one slot, 45 min, one session per task) | T0e (one slot, 45 min, one session per slot) | T1 (routines, for reference) |
|---|---|---|---|
| tasks handed out / first submissions | 16 / 16 (all launches) | 7 / 5 (2 launches, 5 follow-ups) | |
| lambda (first submissions per slot-hour) | **21.3** | 35 before the stall (5 in 8.5 min), 6.7 over the window | 16.0 one slot, 13.6 twelve |
| slot freed -> hand-out | launch 4.1 s (the command returns the session id) | follow-up 1.7 s | re-arm 20 s |
| start-up (hand-out -> branch pushed), median | **launch 28.7 s** (27.7-29.3; log-sd 0.02) | **follow-up 15.0 s** (14.1-15.5; log-sd 0.04); launch 30.2 s | 78-93 s |
| coding (pushed -> READY), median | 91.6 s (log-sd 0.47) | 65.6 s (n = 5, log-sd 0.13) | 82-84 s |
| rework (message -> READY), median | 48 s (n = 11) | 39 s (n = 2) | 61-74 s |
| reviews / asking for changes | 27 / 13 (0.48) | 7 / 2 | 117 / 46 (0.39) |
| review time, median | 23.5 s | 19.6 s | 20.2 s |
| merges | 14 | 5 | 63 |
| credit meter (whole dollars) | $247 -> $239: **$0.50 per task** | $239 -> $236: **$0.43 per task** | no movement (plan usage) |
| incidents | none | the session auto-compacted during its 6th task, lost its rules and never sent READY: 25-min timeout | CLI auto-update lost two slots |

Pooled for the simulator: coding median 82 s (log-sd 0.44; T1 83 s), rework median 48 s (0.37), review durations of T1,
T0c, T0d and T0e (168 reviews, median 20.5 s), requests for changes 41% (69 of 168).

**Budget.** $236 left, $50 floor: **$186 to spend**. A task (one hand-out, a first attempt with its rework) costs
$0.30-0.60 (central $0.45; the meter reads whole dollars): **about 413 tasks at $0.45, 310 at $0.60, 620 at $0.30**,
T1b included. (A two-parameter fit, T0d 16 sessions + 16 tasks = $8 and T0e 2 sessions + 7 tasks = $3, gives about
$0.10 per session and $0.40 per task: the session overhead is small, so the design is costed per task.) The local
Opus reviewer runs on the owner's Max plan, not credits; its load is reported per design.

## 2. The v7 process as simulated (`synth.V7_TRUTH`)

As v6 (`DESIGN-SEARCH-v6.md` section 2) except:

| Item | v7 value | Source |
|---|---|---|
| Sessions | one per slot; a session takes `tasks_per_session` = 4 tasks, then the slot's next task launches a fresh one; a timed-out session is retired at once | harness `session_per = "slot"` |
| Hand-out | launch: 4.1 s after the slot frees, then start-up lognormal median 28.7 s; follow-up: 1.6 s, then 15.0 s; log-sd 0.05 for both (measured 0.02 / 0.04 at one slot; 0.25 as a sensitivity) | T0d, T0e |
| Coding, rework | lognormal median 82 s (log-sd 0.44); rework median 48 s (0.37), sent 1.6 s after the task's own session is free (its slot free, or retired and idle) | T0d + T0e (+ T1) |
| Review | K reviewers, one FIFO queue, durations drawn from the 168 pooled reviews; 41% ask for changes (false reject 0.285, catch 0.915, defect 0.20): about 3% of approvals escape | T1-T0e |
| Session timeout | **10 min** (was 25): a stall (T0e) frees the slot within a window | rule 6 |
| Budget unit | hand-outs: `session_launch` + `session_message kind=task` | |
| Drag | as v6 (only the work legs stretch; hand-out legs are service-side) | |

**Calibration** (`calib_t0de.py`, 1000 simulated T0d and T0e logs, linear workers with T1's collision rate):

| run | measure | observed | simulated median (5-95%) | percentile |
|---|---|---|---|---|
| T0d | first submissions in 45 min (lambda x 0.75) | 16 | 16 (9-25) | 0.52 |
| T0d | tasks handed out / reviews / merges | 16 / 27 / 14 | 17 / 28 / 15 | 0.45 / 0.47 / 0.41 |
| T0d | share of reviews asking for changes | 0.48 | 0.42 (0.25-0.57) | 0.75 |
| T0d | launch start-up / coding / rework, medians | 28.7 / 91.6 / 48.2 s | 29.8 / 83.6 / 49.3 s | 0.46 / 0.60 / 0.47 |
| T0d | review time, median | 23.5 s | 20.6 s (18.5-23.2) | **0.97** |
| T0e | first submissions before the stall (8.5 min) | 5 | 3 (2-6) | 0.86 |
| T0e | follow-up start-up / coding, medians | 15.0 / 65.6 s | 15.6 / 84.8 s | 0.46 / 0.21 |

Every measure lies inside the simulated 5-95% range except T0d's review time (23.5 s against a simulated 95th
percentile of 23.2 s): the reviewer was about 15% slower that evening than in T1. The "slow reviews" and contention
sensitivities cover it. Simulated lambda at one slot is 21.3 with every task a launch (T0d as run) and **about 23-24 in
slot mode** (three hand-outs in four skip provisioning); the brief's round figure of 20 (T0d) is a sensitivity
("lam20": every leg 17% longer).

**lambda at twelve slots.** Nothing in the slot's own cycle depends on N except (a) service throttling, which rule 1
tests (T1's routine start-ups were 13% slower at twelve slots, within noise), (b) coordination drag, which is what SCALE
measures, and (c) the reviewers: with K = 1 at N = 12 the review queue holds rework back, the slots take new tasks
instead, and first submissions rise to about 29.5 per slot-hour (31 hand-outs) against 23.5 (25.5 hand-outs) with
K = 5. The harness adds no N-dependence we can see (the follow-up command returns in 1.6 s; at twelve slots it is
called about every 12 s). T1's twelve-slot lambda was 0.85-0.97 of its one-slot lambda. The design is therefore
costed at lambda(12) = lambda(1) under near-linear workers (the most expensive plausible truth: drag makes every
window cheaper, e.g. an N = 12, K = 5 window uses 77 tasks under linear workers, 44 under Amdahl, 32 under USL), and
SCALE's operating characteristics are computed with that null.

**Window length.** Per-agent finished output at N = 12 against N = 1 under linear workers, the same window length at
both (800 simulated windows per cell): 0.995-1.01 at 15-30 min with K = 4 or 5, and with review never binding; **0.90
at 45 min**, because twelve slots hand out all 220 tasks after about 40 min (supply exhaustion). With unequal lengths
the end-of-window effect would not cancel (rework cannot be sent after the window closes, which costs a short window
proportionally more), so every cell uses one length.

## 3. Search

**Stage A** (`search7.py --stage A`): window banks for six truths (linear, mild, Amdahl, USL, T1-like, and linear with
3x faster reviews as the CAP null) x window length 15 / 20 / 25 / 30 min x cells (N = 1 at K = 1, 3; N = 12 at
K = 1, 3, 4, 5; N = 8 at K = 1, 3, 4 and N = 6 at K = 1, 3 for 20 and 30 min), 3 min warm-up; 776 designs costing at
most 560 tasks (T1b's 65 plus the mean hand-outs of their windows under T1-like workers), 150 studies per truth,
SCALE and CAP only. **Stage B** (`--stage B`): 27 designs around the stage-A picks, 15 truths, 600 studies each, all
codings. **OC** (`oc_v7.py`): the three chosen designs, every window freshly simulated, one task pool per study reused
by every window (as the real reset reuses the 220 tasks), 1000 studies per truth.

### What stage A shows

1. **N = 12 beats N = 8 and N = 6 per task.** The tasks buy slot-minutes, and a larger fleet shows a larger bend for the
   same slot-minutes. Best SCALE power against Amdahl at about 400 tasks: N = 6 0.61, N = 8 0.75 (K = 3) / 0.69
   (K = 4), N = 12 0.93 (K = 4) / 0.91 (K = 5). At 300: 0.40 / 0.59 / 0.79.
2. **Three reviewers bind at N = 12.** At lambda 23 the twelve slots demand about 470 reviews an hour: three
   reviewers are busy 0.88-0.89 (v6's non-binding K), four 0.75 (rule 4 flags a window in 65-85% of studies), five
   0.61 (25-45%). K = 5 costs no credits.
3. **Short windows buy more N = 12 windows.** An N = 12, K = 5 window costs about 77 tasks at 15 min, 101 at 20, 142
   at 30; an N = 1 window 6.7 / 8.6 / 12.4. SCALE's precision is set by the number of windows (the pre-registered
   window CV is 0.3), not their length, so at a fixed budget 15-min windows win: at about 400 tasks, three N = 12
   windows of 15 min (SCALE 0.91-0.93 against Amdahl) against two of 20 min (0.88-0.92) or two of 30 min (0.70-0.85).
4. **CAP is expensive now.** An N = 12, K = 1 window costs about 94 tasks at 15 min (rework is held up in the queue and
   the slots take new tasks), and CAP's power with one such window is 0.4-0.6 (0.6-0.8 at 20 min). Adding one to the
   400-task design costs about 0.1 of SCALE's power, since a K = 5 window must go.
5. **COLL loses power under drag.** An N = 12 window of 15 min gives about 66 first merge-queue passes with mean j
   (merges since the change's base) about 8 under near-linear workers, but only about 16 passes with j about 4 under
   USL-like workers, whose slowed fleet merges little. COLL's power at T1's p under USL-like workers falls from v6's
   0.78 (seven 45-min N = 12 windows) to 0.1-0.3; under near-linear workers it is 0.6-0.9.

### Stage B (600 studies per cell)

| design (15-min windows unless marked) | tasks (p90) | reviews | SCALE FPR / Amdahl / USL / mild | CAP FPR / linear / T1-like | COLL p = 0.0075: T1-like / USL-like | ESC-N 2x |
|---|---|---|---|---|---|---|
| 1K1x5 + 1K5x5 + 12K5x2 (**300**) | 284 (328) | 332 | 0.02 / 0.79 / 1.00 / 0.24 | - | 0.69 / 0.10 | 0.21 |
| 1K1x6 + 1K5x6 + 12K5x2 | 297 (339) | 350 | 0.02 / 0.83 / 1.00 / 0.24 | - | 0.68 / 0.11 | 0.24 |
| 1K1x6 + 1K5x6 + 12K5x1 @20m | 268 (303) | 319 | 0.04 / 0.65 / 0.98 / 0.16 | - | 0.63 / 0.10 | 0.23 |
| 1K1x3 + 1K5x3 + 12K5x2 @20m | 317 (368) | 404 | 0.03 / 0.78 / 0.99 / 0.19 | - | 0.66 / 0.20 | 0.29 |
| 1K1x4 + 1K5x4 + 12K5x1 + 12K1x1 | 289 (333) | 256 | 0.03 / 0.53 / 0.94 / 0.14 | 0.03 / 0.37 / 0.41 | 0.70 / 0.10 | 0.15 |
| 1K1x4 + 1K5x4 + 12K5x3 | 347 (394) | 423 | 0.03 / 0.83 / 1.00 / 0.25 | - | 0.72 / 0.20 | 0.31 |
| **1K1x6 + 1K5x6 + 12K5x3 (400)** | **374 (421)** | **466** | **0.02 / 0.91 / 1.00 / 0.27** | - | **0.77 / 0.24** | 0.32 |
| 1K1x6 + 1K4x6 + 12K4x3 | 379 (433) | 465 | 0.03 / 0.89 / 1.00 / 0.24 | - | 0.81 / 0.19 | 0.31 |
| 1K1x6 + 1K5x6 + 12K5x2 @20m | 369 (415) | 478 | 0.02 / 0.89 / 1.00 / 0.23 | - | 0.75 / 0.24 | 0.28 |
| 1K1x5 + 1K5x5 + 12K5x2 + 12K1x1 | 378 (430) | 390 | 0.04 / 0.79 / 1.00 / 0.23 | 0.04 / 0.48 / 0.52 | 0.78 / 0.20 | 0.29 |
| 1K1x6 + 1K5x6 + 12K5x2 + 12K1x1 | 391 (442) | 408 | 0.03 / 0.81 / 1.00 / 0.25 | 0.05 / 0.47 / 0.55 | 0.83 / 0.22 | 0.27 |
| 1K1x6 + 1K5x6 + 12K5x1 + 12K1x1 @20m | 393 (459) | 399 | 0.04 / 0.62 / 0.97 / 0.18 | 0.04 / 0.52 / 0.60 | 0.87 / 0.30 | 0.28 |
| 1K1x6 + 1K5x6 + 12K5x4 | 450 (511) | 586 | 0.02 / 0.94 / 1.00 / 0.32 | - | 0.85 / 0.31 | 0.31 |
| **1K1x6 + 1K5x6 + 12K5x3 + 12K1x1 (500)** | **468 (523)** | **523** | **0.03 / 0.89 / 1.00 / 0.34** | **0.03 / 0.55 / 0.57** | **0.85 / 0.32** | 0.32 |
| 1K1x8 + 1K5x8 + 12K5x3 + 12K1x1 | 494 (558) | 567 | 0.02 / 0.94 / 1.00 / 0.34 | 0.04 / 0.52 / 0.57 | 0.89 / 0.35 | 0.35 |
| 1K1x6 + 1K5x6 + 12K5x2 + 12K1x2 | 486 (553) | 477 | 0.02 / 0.84 / 1.00 / 0.25 | 0.04 / 0.72 / 0.74 | 0.88 / 0.27 | 0.32 |
| 1K1x6 + 1K5x6 + 12K5x2 + 12K1x1 @20m | 494 (562) | 555 | 0.03 / 0.88 / 1.00 / 0.25 | 0.04 / 0.68 / 0.76 | 0.90 / 0.38 | 0.36 |
| 1K1x6 + 1K5x6 + 12K5x3 @20m | 469 (533) | 638 | 0.03 / 0.95 / 1.00 / 0.25 | - | 0.82 / 0.39 | 0.37 |

Tasks include T1b's 65; "reviews" are the sweep's (T1b adds about 94). SCALE's false alarm under linear workers is
0.02-0.04 everywhere (CV 0.5: 0.10-0.18; review contention 0.1: 0.01-0.04); COLL's false alarm (p = 0) is at most
0.017.

**Why these three.** The 400-task design spends its budget on SCALE, the primary question: three N = 12 windows
(robust to losing one) and twelve N = 1 windows, split between K = 1 and K = 5 at no cost. Adding CAP at 400 (one
K = 5 window traded for a K = 1 window) costs SCALE 0.10 of power for a CAP power of 0.55: not worth it while T1's
descriptive evidence of the ceiling (one reviewer busy 88%, a queue of 29) stands. At 500 tasks the extra window goes
to CAP (0.55-0.57) rather than a fourth K = 5 window (+0.03 SCALE power): the 2 x 2 (N x K) then returns as in v6. At
300 tasks, two N = 12 windows of 15 min (0.79) beat one of 20 min (0.65), and CAP is not affordable. The three designs
are nested (300 -> 400 adds two N = 1 windows and one N = 12, K = 5 window; 400 -> 500 adds the N = 12, K = 1 window),
so rule 2 can choose after T1b without changing anything already run.

**Why 15 min and not 20.** Stage B favours 15 min for SCALE at every budget (more N = 12 windows); 20-min windows help
CAP (the queue has longer to build: 0.60-0.76 against 0.47-0.57) and COLL. The short windows make start-of-window
transients weigh more (all twelve sessions launch together, and a slot relaunches every fourth task: a 2x
provisioning slowdown at twelve slots would cost a slot about 5-7% of its time), which rule 1's launch flag reports.

## 4. Rule 1 in slot mode (T1b; `oc_v7_t1b.json`, 1000 simulated T1b logs per cell, K = 5)

Three start-up measures, twelve slots against one: **follow-up** (the rule), **launch** (flag) and **mixed** (v6's
measure on every hand-out), each a ratio of geometric means with a Welch 90% interval and tolerance 1.25.

| T1b shape (one slot + twelve) | tasks, median (p90) | follow-ups A / B | no throttle: CLEAR follow-up / launch / mixed | 1.25x: STOP (follow-up) | 1.5x: STOP follow-up / mixed | 2x: STOP | launch-only 2x: STOP follow-up / launch flag / mixed CLEAR | drag (Amdahl / USL): STOP / coding flag |
|---|---|---|---|---|---|---|---|---|
| 20 + 10 | 61 (83) | 6 / 35 | 0.99 / 0.85 / 0.45 | 0.05 | 0.99 / 0.40 | 1.00 | 0.00 / 0.94 / 0.00 | 0.00 / 0.81-0.96 |
| **30 + 10 (recommended)** | **66 (90)** | **9 / 36** | **1.00 / 0.97 / 0.79** | **0.05** | **1.00 / 0.81** | **1.00** | **0.00 / 1.00 / 0.00** | **0.00 / 0.91-0.98** |
| 45 + 10 | 71 (96) | 12 / 35 | 1.00 / 1.00 / 0.91 | 0.06 | 1.00 / 0.99 | 1.00 | 0.00 / 1.00 / 0.00 | 0.00 / 0.94-0.99 |

With start-up log-sd 0.25 (five times the measured) the follow-up interval widens to x/1.17 at 30 + 10: CLEAR 0.72 with
no throttling, STOP 0.54 at 1.5x and 0.98 at 2x, never under drag. A 3% stall rate leaves CLEAR at 0.996.

**Why follow-ups only.** In slot mode a launch start-up (provisioning and clone, ~30 s) is twice a follow-up's
(~15 s), and the two phases mix them in different proportions: the one-slot phase has one launch in four hand-outs,
the twelve-slot phase starts with twelve launches at once, and drag (fewer tasks per slot) raises its launch share.
The mixed measure therefore moves without any throttling: with no throttle it CLEARs only 0.79 of the time (x/1.18
interval), under USL drag 0.09, and a launch-only slowdown drives it to INCONCLUSIVE, never to a decision. The
follow-up measure compares like with like, is tight (x/1.03 at 30 + 10: nine follow-ups at one slot suffice), and is
blind only to a launch-only slowdown, which the launch flag catches (1.00 at 2x) and which costs a slot at most a few
per cent of its time. The launch-only measure is not the rule because the one-slot phase has only about three
launches. The harness's `throttle` command already prints the follow-up-only and launch-only ratios beside its
(v6, mixed) decision; `v7.throttle_v7` decides on follow-ups.

## 5. Grades (the 400-task design; final figures in OPERATING-CHARACTERISTICS-v7.md)

| Question | Result | Grade | Why |
|---|---|---|---|
| 1. Does the workers' own output bend? | SCALE (N = 12, K = 5 and every N = 1 window) | **confirmatory, primary** | false alarm about 0.02, power 0.9 (Amdahl) / 1.00 (USL, Carnot) |
| 3. Collisions | COLL | **confirmatory, secondary** | false alarm <= 0.02; power about 0.8 under near-linear workers at T1's p, low under drag (stated) |
| 4. Does one reviewer cap finished work? | CAP (N = 12, K = 1 vs 5) | **confirmatory, secondary in the 500-task design only**; N/A at 300 / 400 | one K = 1 window: power about 0.55, false alarm about 0.03 |
| 4, 2, 1 | K1, CAPFIT, ESC-N, ESC, FAMILY, UTIL, THROTTLE, COST, COLL-m, COLL-k, LAMBDA, BOUNCE, EFFORT | descriptive | as v6; COST is the task ledger |

## 6. Caveats

- **Cost per task is uncertain** ($0.30-0.60 from two short trials and a whole-dollar meter). Rule 2 re-measures it
  on T1b's ~66 tasks and picks the design; before every window the rest must still fit.
- **Reviewer contention at K = 5 was not measured.** With 10% extra time per extra reviewer, five reviewers have the
  capacity of 3.6 and are busy about 0.85 at N = 12 (SCALE's false alarm stays 0.01-0.04 in stage B). Rule 4a (free)
  measures it before T1b and stops the plan above 35% total slowdown.
- **Shared context within a session** (PLAN-v7 section 5.6): no confirmatory test is threatened; simulated with
  follow-up tasks coding 25% faster, and with 8 tasks per session, SCALE's false alarm is unchanged.
- **Task reuse across windows**: unbiased when every reset draws a fresh order seed (rule 7); a single seed for
  every window would give the N = 1 windows the same few tasks (OPERATING-CHARACTERISTICS-v7.md, reuse check).
- **The window CV of 0.3 is still assumed**; at 0.5 SCALE's false alarm is 0.10-0.16.
- **Everything rests on two one-slot evenings** (T0d, T0e) plus T1. T1b is the first slot-mode run at twelve slots.
