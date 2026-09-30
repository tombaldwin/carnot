# Study 2 plan, version 7: agent scaling and the review ceiling, under a budget counted in cloud tasks

> **Status, 2026-09-30: reviewed (REVIEW-fable-v7.md: NOT YET, five must-fix items) and revised; not yet
> pre-registered.** The harness now launches task sessions with `claude --cloud` and the #81776 workaround, which draws
> cloud-session credits, and keeps one session per slot. **Revisions after the review** (all free): the watcher polls
> every 2 s and rule 1's operating characteristics are pre-registered at a start-up log-sd of 0.25 (the T0d / T0e
> spreads were the 15-s poll's quantisation); a plan-usage headroom stop in rule 4a; T1b is 30 + 15 min; branch
> attribution is case-insensitive and accepts suffixes, the prompts require the exact branch name, and T0e's second
> failure is reported with a 10% loss sensitivity; one rule-2 formula with fixed meter times and a cumulative cost;
> every N = 1 window at K = 5 unless the 500-task design is chosen; sessions retired after 4 tasks or 6 messages; a
> 5-min prompt budget under the 10-min timeout; rule 1's coding legs cut off 5 min before window end; `merged_main`
> on every submit; the day split, SCALE's minimum detectable effect and single confirmatory p-value, and COLL's
> under-sizing stated. The operating characteristics were re-simulated after these changes (section 5.3,
> OPERATING-CHARACTERISTICS-v7.md). Nothing has been run beyond the free simulations.

Supersedes PLAN-v6. Drafted on 2026-09-30 from the live trials T0d and T0e (2026-09-29; numbers in
`analysis/design-search/DESIGN-SEARCH-v7.md` section 1 and `analysis/design-search/t0de_params_public.json`, numbers
only). Everything built for v6 carries over unless changed here: the sandbox and its 220 tasks, the verified local
reviewer command (checkout job, default effort) run by K parallel reviewer threads on one FIFO queue, hidden tests kept
local, the serial merge queue, the event log, the post-hoc effort re-review, and the v6 codings of SCALE, CAP, COLL and
the descriptive results (`analysis/v6.py`, re-graded by `analysis/v7.py`).

## 0. Why v7: the launch path and the budget changed

Routine runs billed the owner's Max plan, so v6 was paused (PLAN-v6, status). The harness now launches each slot's
session with `claude --cloud` plus the #81776 workaround (the session clones the sandbox from GitHub and can push), and
these sessions draw the cloud-session credits. It also keeps **one session per slot**: the slot's first task at launch,
later tasks as follow-up messages, rework to the session that did the task, and a fresh session after
`tasks_per_session` = 4 tasks (lowered from 8 after T0e's session auto-compacted during its sixth task and lost its
rules). Two one-slot trials measured the new path. **Slot-mode losses:** 2 of 7 hand-outs in T0e (2 of 23 over T0d +
T0e) never produced a counted attempt; the harness now attributes a `claude/task-<id>*` branch case-insensitively
and with a suffix, and the prompts require the exact name, but a 10% loss rate is carried as a sensitivity (section
5.3). The one post-retirement launch observed was 6x slower than the others, which is part of the case for rule 1's
wider start-up spread.

| | v6 assumed (T1, routines) | T0d (per-task sessions, 45 min) | T0e (slot sessions, 45 min) |
|---|---|---|---|
| tasks handed out / first submissions | | 16 / 16, 14 merged | 7 / 5, **two losses**: the session compacted and stalled on its 6th task; the fresh session launched after it (minute 33.6) first pushed 3 min later (not 29 s) to a mis-named branch `claude/task-t039-<suffix>`, which the watcher ignored, and its READY came after window end |
| first submissions per slot-hour (lambda) | 14 | **21.3** | 35 before the stall |
| slot freed -> branch pushed (start-up; **quantised to the 15-s watcher poll**, see rule 1) | 20 s re-arm + 85 s | launch: 4 s + **29 s** | follow-up: 2 s + **15 s**; launch 30 s |
| coding (pushed -> READY), median | 83 s | 92 s | 66 s |
| rework (message -> READY), median | 70 s | 48 s | 39 s |
| reviews / asking for changes | 39% | 27 / 13 (48%) | 7 / 2 |
| review time, median | 20 s | 23.5 s | 19.6 s |
| credits (meter reads whole dollars) | none moved | $247 -> $239: **$0.50 per task** | $239 -> $236: **$0.43 per task** |

**The budget is now counted in tasks.** A task is one hand-out, a first attempt together with its rework (rework runs
in the same session and is already in the per-task figure): **$0.30-0.60, central $0.45.** $236 of credits are left and
a $50 floor is kept, so **$186 is spendable: about 413 tasks at $0.45 (310 at $0.60, 620 at $0.30), T1b included.** The
local Opus reviewer runs on the owner's Max plan, not credits; its load (reviews per design) is reported but does not
set the budget.

At about 26 hand-outs per slot-hour, **v6's design would use about 2,300 tasks** (seven 45-min N = 12 windows alone
about 1,900), six times the budget. And lambda is now about 23 rather than 14, so at N = 12 the twelve slots demand
about 470 reviews an hour: v6's three "non-binding" reviewers would be busy 0.88, i.e. binding. v7 re-runs the design
search for a budget in tasks.

## 1. Questions

1. **Scaling (agent side).** With review not binding (K = 5), does finished work grow in proportion to fleet size, or
   bend? Rivals as v6: linear; Amdahl; USL; Carnot uncapped (p = 0.0075).
2. **Quality.** The escape rate, overall and against N (descriptive).
3. **Collisions.** Rebase conflicts and integration failures against j, the merges since a change's base.
4. **The review ceiling.** At N = 12, does one reviewer cap finished work below what five allow (CAP; in the 500-task
   design only), and how close does output come to the ceiling from the reviewers' own service rate (CAPFIT)?
   Utilisation per cell (UTIL).
5. **Effort and escapes (free, post hoc).** As v5.

## 2. Reviewers

As v6, with **K = 5** as the non-binding count (was 3). At N = 12 and lambda 23, five reviewers are busy 0.61 of a
window (four 0.75, three 0.88). **Every N = 1 window runs at K = 5** in the 300- and 400-task designs: the K contrast at
N = 1 predicts nothing (busy 0.25 against 0.05; never more than one review at a time), is no check of contention, and
would make half the N = 1 windows differ in protocol from the N = 12 arm. Only the 500-task design keeps the 2 x 2 (six
N = 1 windows at each K), where K1 is descriptive and costs nothing. Parallel local reviewers may slow each other (pytest in each call): with
10% extra time per extra reviewer, five have the capacity of 3.6 and are busy 0.80 at N = 12, which SCALE tolerates
(false alarm 0.027), but more would bind. Rule 4a measures it for free before T1b. Reviews cost plan usage only.

## 3. Settled by the v7 design search (free, done)

Fleet size at the top (N = 12: N = 8 and N = 6 buy less power per task), window length (15 min for every cell), the
non-binding K (5), whether CAP is affordable (only at 500 tasks), the N = 1 count, the three budget variants and how
rule 2 chooses between them, the grades and operating characteristics, abort rule 1 in slot mode (follow-up start-ups)
and T1b's shape (30 + 10 min in the search; **30 + 15 after the review**, section 5.5). `analysis/design-search/DESIGN-SEARCH-v7.md`;
OCs in `OPERATING-CHARACTERISTICS-v7.md` (re-simulated after the review).

## 4. Order of work

1. Free: this plan, the v7 design search, codings and self-tests (done: `analysis/v7.py`, `--plan v7`).
2. Free: review of this plan (Fable).
3. Free: the harness configuration changes of section 6 and their tests (done 2026-09-30); **rule 4a**, the reviewer
   contention calibration (`calibrate --parallel 5` against `--parallel 1` on the same heads), with the Max plan's
   usage read before and after it (`note plan_usage`).
4. Credits: **T1b** (one slot for 30 min, then twelve for 15 min, K = 5): rules 1, 2, 3 and 4a's plan-usage
   projection. About 87 tasks (p90 120), 135 reviews.
5. Pre-registration commit: T1b's readings, rule 2's choice of design, `predict.py` output, the codings with their
   operating characteristics.
6. The sweep (12, 15 or 16 windows), then the post-hoc effort re-review, analysis, publication whichever way it comes
   out, and a revision-log entry in the paper.

## 5. Design (from the v7 search)

Settled by the free design search (`analysis/design-search/DESIGN-SEARCH-v7.md`; the v7 process in
`analysis/synth.py`, calibrated on T0d and T0e so that every measure but one (T0d's review time, 97th percentile) lies
inside the simulator's 5-95% range; the codings in `analysis/v7.py`); operating characteristics in
`OPERATING-CHARACTERISTICS-v7.md`.

### 5.1 The design

| | |
|---|---|
| Factors | fleet size N in {1, 12} x parallel reviewers K in {1, 5} |
| **Recommended (400 tasks)** | **N = 1: twelve windows at K = 5; N = 12: three at K = 5; 15 min each** (3 min warm-up, 10 min grace) |
| 300 tasks (if a task costs $0.46-0.59) | N = 1: ten at K = 5; N = 12: two at K = 5 |
| 500 tasks (if a task costs up to $0.37) | N = 1: six at K = 1 and six at K = 5 (the 2 x 2); N = 12: three at K = 5 and **one at K = 1** (CAP) |
| Order | `v7.v6_order`: two N = 1 windows first, N = 12 windows spread evenly, never first or last; for 400: 1K5 x 4, **12K5**, 1K5 x 3, **12K5**, 1K5 x 3, **12K5**, 1K5 x 2 (N = 12 fifth, ninth and thirteenth) |
| **Days (pre-registered)** | T1b on its own day (or before the sweep on day 1). The sweep over **two days**: day 1 windows 1-7 (one N = 12 window, the fifth), day 2 windows 8-15 (two, the ninth and thirteenth), so each day holds at least one N = 12 window, never first or last of its day; 300: windows 1-6 and 7-12 (N = 12 fifth and tenth); 500: windows 1-8 and 9-16. If rule 4a's plan-usage projection is tight, **three days** with one N = 12 window each (no two in one 5-hour block). Time of day is in every log; day enters the analysis only as a reported covariate of the window CV |
| Window length | one length for every cell, so the end-of-window effect (no rework after the window closes) cancels between N = 1 and N = 12; 15 min so that the budget buys N = 12 windows rather than minutes; an N = 12 window hands out about 80 of the 220 tasks (a 45-min one would exhaust them) |
| T1b (before the sweep) | one slot for 30 min, then twelve for 15 min, K = 5, one session per slot: about 87 tasks (p90 120), 135 reviews |
| Pilot | none beyond T1b: every rival's level is fitted to the sweep's own windows |
| Tasks and cost | section 5.2 |
| Harness | section 6 |

### 5.2 Budget

| design | T1b + sweep tasks (simulated mean; sweep p90) | $ at $0.45 (0.30-0.60) | affordable up to | Opus reviews (T1b + sweep) | windows / wall-clock |
|---|---|---|---|---|---|
| 300 | 87 + 225 = **312** (266) | $140 ($94-187) | $0.59 per task | 135 + 325 = 460 | 12 / about 6 h + T1b |
| **400** | 87 + 317 = **404** (367) | **$182** ($121-242) | **$0.46 per task** | **135 + 458 = 593** | **15 / about 7.5 h + T1b** |
| 500 | 87 + 412 = **499** (474) | $225 ($150-299) | $0.37 per task | 135 + 522 = 657 | 16 / about 8 h + T1b |

**The budget is tight and rule 2's drop rule is likely to fire.** At the central $0.45 the 400-task design ends about
$4 above the $50 floor on average, while one window's hand-outs vary by more than that (the sweep's p90 is 50 tasks,
about $22, above its mean): in a sizeable share of futures (roughly half of those in which rule 2 picks 400 near its
threshold) some N = 1 windows beyond eight will be dropped. That is planned for: the drop order keeps SCALE's N = 12
windows longest, and a truncated design is analysed as run.

Tasks are simulated hand-outs under near-linear workers, the most expensive plausible truth: drag makes every window
cheaper (an N = 12, K = 5 window hands out about 79 tasks under linear workers, 44 under Amdahl, 32 under USL). Per
cell: an N = 1 window about 6.7 tasks, an N = 12, K = 5 window about 79, an N = 12, K = 1 window about 95 (one reviewer
holds the rework back, so the slots take new tasks). Reviews: about 9.5 per N = 1 window, 115 per N = 12, K = 5 window
(up to five at once) and 64 per N = 12, K = 1 window, each about 41-48k input tokens (T1), about 25M input tokens in
all on the owner's Max plan: rule 4a's plan-usage projection guards it. `predict.py --budget 300|400|500`
prints the table; `predict.py --usd-per-task <c> [--balance <meter>]` prints rule 2's choice.

**lambda at twelve slots.** Nothing in a slot's cycle depends on N except service throttling (rule 1 tests it; T1's
routine start-ups were 13% slower at twelve slots, within noise), coordination drag (what SCALE measures) and the
reviewers (above). The harness adds no N-dependence we can see: a follow-up command returns in 1.6 s and at twelve
slots is called about every 12 s. T1 measured twelve-slot lambda at 0.85-0.97 of one-slot. The design is costed at
lambda(12) = lambda(1), about 23-24 first submissions (26.5 hand-outs) per slot-hour, the upper end; the brief's round
figure of 20 (T0d, every task a launch) is a sensitivity (the 400-task design's sweep then uses about 276 tasks,
SCALE's false alarm 0.024, power 0.89). The first N = 12 window is the first N = 12 cost measurement (conflict-heavy
tasks may make a task dearer there); rule 2's cumulative cost picks it up.

### 5.3 Grades and operating characteristics (400-task design; 300 / 500 in brackets; re-simulated after the review)

| Question | Result | Grade | False alarm | Power |
|---|---|---|---|---|
| 1 Scaling | **SCALE**: per-agent finished output falls from N = 1 to 12, on the N = 12, K = 5 windows and every N = 1 window (one-sided NB LR, CV 0.3) | **confirmatory, primary** | 0.029 (0.026 / 0.028); **0.10 at window CV 0.5** | Amdahl 0.89 (0.78 / 0.89), USL 1.00, Carnot 1.00; mild bend (alpha 0.03) 0.26; **with 10% of hand-outs lost: Amdahl 0.60** |
| 3 Collisions | **COLL**: first merge-queue pass fails by collision more often the more changes merged since the change's base | **confirmatory, secondary** | 0.001-0.003 (under-sized; see below) | T1-like workers p = 0.0035 / 0.0075 / 0.015: 0.45 / 0.76 / 0.96 (0.64 / 0.85 at 0.0075); USL-like: 0.05 / 0.20 / 0.46 |
| 4 Ceiling | **CAP**: at N = 12, finished output with K = 1 is lower than with K = 5 (one-sided NB LR, CV 0.3) | **confirmatory, secondary, 500-task design only** (N/A at 300 / 400) | 0.036 when one reviewer never binds | T1-like 0.58, linear 0.52 (ratio 0.48-0.51); reads CAPPED 0.12 under USL / Carnot, where the model predicts no ceiling |
| 4 | K1: the K contrast at N = 1 (500-task design only) | descriptive | flagged 0.011 | |
| 4 | CAPFIT, UTIL | descriptive | | reviewers busy at N = 12, K = 5: 0.61 (linear), 0.28 (Amdahl) |
| 2 Quality | ESC-N: escapes rise with N | descriptive | 0.03 | a doubling (0.03 -> 0.06): 0.30 |
| 2 Quality | ESC: the escape rate | descriptive (estimate) | | 95% half-width about +-0.022 on about 265 approvals |
| 1 Which rival | FAMILY: four-way pick with free levels | descriptive | | linear 0.96, Amdahl 0.79, USL 0.32, Carnot 0.64 |
| 1 | THROTTLE: rule 1b, each N = 12 window's follow-up start-up and coding against the N = 1 windows | descriptive | | |
| 1 | DRAG: coding time at N = 12 against N = 1, split by whether the submitted branch merged `origin/main` (`merged_main` on every `submit`): longer coding concentrated in merged-main tasks is drag, uniformly longer coding with a flat merged-main share is throttling | descriptive | | |
| budget | COST: tasks, sessions and reviews per window (the ledger) | descriptive | | |
| 3 | COLL-m, COLL-k; LAMBDA (with stalls, branch losses and retirements per hand-out by N), BOUNCE, EFFORT | descriptive | | |

**The verdict on question 1 is one p-value:** the fixed-CV (0.3) one-sided NB LR test (`v5.bend_test`, "lr_fixed") on
all N = 1 windows and all N = 12, K = 5 windows, as run. SCALE is also reported without windows that ran out of tasks
(SCALE-nf; none did in simulation), without rule-4-flagged windows, without rule-6 and rule-7 windows, and with the
CV-estimated and Welch versions (false alarms 0.08-0.09 with three N = 12 windows); these are sensitivities and none
of them is the verdict. The observed window-to-window CV of the N = 1 windows is reported beside it, but cannot check
the assumed 0.3: twelve windows of about four finished tasks each have a Poisson CV near 0.5 before any speed noise.

**Honest limits.** *Minimum detectable effect:* with the CV fixed at 0.3 the per-agent ratio's log-SE is about 0.25,
set by the three N = 12 windows, not by counts; at 80% power SCALE detects a per-agent ratio of about **0.55** or lower.
It tests bends the size of the paper's default Amdahl (0.48) and USL (0.29) curves, not any bend: a mild bend (0.77) is
found one time in four, and a NO-BEND result is consistent with anything from linear to a 25-30% bend. *CV 0.3 is
assumed, not measured:* the two slot-mode evenings differed in cycle rate by about 1.7x over their first five tasks,
and a 15-min window turns a five-minute API slowdown into window-level noise; at CV 0.5 the false alarm is 0.10, which
is the honest bound and goes into the paper's revision-log entry. *Losses:* at T0e's slot-mode loss rate (10% of
hand-outs never producing a counted attempt) the false alarm stays 0.023 but the power against Amdahl falls to 0.60
(stalls and lost branches cost an N = 1 slot most of a 15-min window, which flattens the per-agent ratio); the branch
attribution fix and the prompts target that rate. *COLL:* a false alarm of 0.001-0.003 at nominal 0.05 means the test
is badly under-sized (about one background event per study under p = 0), not that it is conservative in a useful way;
its power (0.76 near-linear, 0.20 under drag at T1's p) is coupled to SCALE: if SCALE reads a strong bend, the slowed
fleet merges little in 15 minutes and COLL is close to uninformative. *CAP* is answered confirmatorily only in the
500-task design (c <= $0.37, unlikely); otherwise the review ceiling stays T1's descriptive result (one reviewer busy
0.88 at twelve slots under the routine launcher, lambda 14; at lambda 23 one reviewer would bind harder), and the
paper's revision-log entry says so. *Under the linear null* the simulated per-agent ratio is 1.02-1.04 (rework to
retired sessions and burst review slightly favour N = 12): the safe side for a one-sided test of a fall. *Three N = 12
windows* are enough for Amdahl-sized bends only because the CV is fixed; losing one leaves the 300-task design's power
(0.78). No multiplicity correction across questions (as v5, v6): each confirmatory test answers a different question.

### 5.4 Abort rules

1. **Throttling (T1b, slot mode; revised again).** T1b runs one slot for 30 min, then twelve for 15 min (K = 5). The
   statistic is the **follow-up start-up**: for every task handed out by follow-up (`session_message kind=task`) in
   each phase, the time to its branch's first push; ratio of geometric means, twelve slots over one, Welch 90%
   interval on the log scale; **tolerance 1.25**. **STOP** (report T1b) if the interval lies wholly above 1.25;
   **CLEAR** if wholly below; otherwise **INCONCLUSIVE**: proceed, with the ratio reported beside SCALE. The **launch**
   start-up (session_launch -> first push: provisioning and clone) is compared the same way and an interval wholly
   above 1.25 is a **flag**, reported beside SCALE; so is a coding interval wholly above 1.25 (coding contains the drag
   SCALE measures). A coding leg counts only for a task handed out at least 5 min before window end, in both phases
   (otherwise the slow tasks handed out late in the short twelve-slot phase end after the window and are dropped,
   which pulls the phase's geometric mean down and masks a slowdown). Slots lost to operator-logged failures unrelated
   to the service are excluded first.
   *The watcher's poll.* Every `claim` and `submit` is timestamped when the watcher's poll sees the push. Until
   2026-09-30 the poll was 15 s, so every T0d / T0e start-up leg was quantised to about 16.5 s: the "launch 28.7 s,
   log-sd 0.02" and "follow-up 15.0 s, log-sd 0.04" are the hand-out-to-next-poll distance, not the sessions' speed
   (a follow-up push happens somewhere within ~13 s of the message), and a rule on them would be a step function of
   the true slowdown with an unknown threshold. **The watcher now polls every 2 s** in T1b and every sweep window
   (a fetch of a small repo; the reviewers are unaffected and the merge queue waits at most one fetch per git call,
   which rule 5 watches), and nothing is derived from T0d / T0e's start-up spreads.
   *Why follow-ups only:* a launch start-up (about 30 s) is about twice a follow-up's, and the two phases mix them in
   different proportions (one launch in about four hand-outs at one slot; twelve launches at once when the twelve
   slots open; more launches under drag). The mixed statistic moves with the mix: simulated with no throttling it
   CLEARs only 0.78 of the time, 0.36 under USL drag. A launch-only rule would rest on about four launches in the
   one-slot phase.
   *Pre-registered operating characteristics (simulated T1b, 30 + 15, 1000 logs per cell,
   `oc_v7_t1b.json`) are those at a start-up log-sd of 0.25* (five times the quantised figures; the one
   post-retirement launch in T0e was 6x slower than the others): no throttling -> **CLEAR 0.75**, STOP 0.000;
   start-up and coding 1.5x slower at twelve slots -> **STOP 0.54**; 2x -> **STOP 0.985**; drag only (Amdahl) ->
   STOP 0.000, CLEAR 0.71; a 2x slowdown of launches only -> STOP 0.000, launch flag 0.78. If T1b's own one-slot phase
   shows a tighter spread the rule simply decides more often; at the simulator's tight spread (log-sd 0.05) it reads
   CLEAR 1.00, STOP 0.04 at 1.25x and 1.00 at 1.5x (for comparison only). With 10% of hand-outs lost: CLEAR 0.95. The
   coding flag (with the cut-off) is raised by a coding-only 1.5x throttle 0.34 of the time and by drag almost always
   (Amdahl 0.95, USL 0.99); it is not a stop. `v7.throttle_v7(run, events, split_min=30)` codes it; the harness's
   `throttle` command decides on follow-ups in a slot-mode log and prints the launch and coding flags.
   **1b** (in the sweep, descriptive): each N = 12 window's follow-up start-up and coding against all N = 1 windows so
   far (THROTTLE).
2. **Budget, in tasks (T1b decides the design; section 5.5).** One formula everywhere (`v7.usd_per_task_t1b`,
   `v7.choose_design`, `v7.rule2_before_window`, `predict.py`). *Meter readings* (the credit meter shows whole
   dollars and lags): immediately before T1b's first launch and **10 min after T1b's grace end** (if the meter has not
   moved from the before-reading, wait 10 min and read again), and likewise immediately before and 10 min after every
   sweep window; each is logged as a `meter` event. After T1b: **c = (meter before T1b - meter after T1b) / T1b's
   tasks** (hand-outs), with no whole-dollar correction (two readings within $1 each: c to about +-$0.023 at 87 tasks),
   and h = hand-outs per slot-hour in T1b's twelve-slot phase (the same 15-min shape as a sweep N = 12 window). Run the
   largest design whose sweep, at c and h, costs no more than the meter after T1b less $50: **500 if c <= $0.37, 400 if
   c <= $0.46, 300 if c <= $0.59, else stop and report T1b** (thresholds at the simulated h). The 400/300 choice near
   $0.46 is close to a coin toss given the meter's resolution; that is accepted. *Before every window* the cost per
   task is **cumulative**: c = (meter before T1b - meter now) / every hand-out since T1b began, never a per-window delta
   of a few dollars, and the rest of the chosen design must still fit at c above $50; if not, drop N = 1 windows beyond
   eight (alternately by K in the 500-task design), then the N = 12, K = 1 window, then N = 12, K = 5 windows beyond
   two. **The drop rule is expected to fire in a sizeable share of futures** (section 5.2); an interrupted or
   truncated sequence is analysed as run (every test is defined for >= 1 window per cell it uses).
3. **Lambda floor.** T1b's one-slot phase and, again, the first two N = 1 sweep windows: pooled lambda below 10 first
   submissions per slot-hour (under half of the 21-24 measured and simulated): stop before the first N = 12 window and
   report.
4. **Reviewers.** **4a (free, before T1b):** `calibrate --parallel 5` against `--parallel 1` on the same reference and
   mutant heads; if the mean review time at 5 is more than 1.35x that at 1 (predicted N = 12 busy share >= 0.85), do
   not start T1b: re-plan (K = 5 would bind at N = 12). **Plan usage (the local Opus reviewers bill the owner's Max
   plan):** the sweep needs about 460-660 reviews of 41-48k input tokens (about 25M input tokens for the 400-task
   design), most of them in the N = 12 windows (about 115 reviews in 25 minutes each). The operator reads the plan's
   usage indicators (5-hour and weekly) **before and after the 4a calibration run and before and after T1b**, logs each
   reading as a `note plan_usage ...` (`harness log --plan-usage "session=..% week=..% src=..."`), and projects the
   sweep's reviews from the usage per review measured there. **Do not start the sweep if the projection leaves less
   than 30% headroom** in the 5-hour or the weekly allowance; if it leaves 30-50%, run the three-day split (no two
   N = 12 windows in one 5-hour block). A reviewer call that fails on a rate or usage limit is logged as a
   `reviewer_rate_limited` note beside its `review_error`, so a limit shows up as itself rather than as anonymous
   downtime (a paid N = 12 window is VOID after 3 min of reviewer downtime / K). **4b (in the sweep):** an N = 12,
   K = 5 window with the reviewers busy >= 80% of its counted time (per reviewer) is flagged in UTIL and SCALE is
   reported with and without it (not a stop; simulated: some window flagged in 41% of studies under linear workers,
   SCALE's false alarm stays 0.029). An N = 12, K = 1 window with its reviewer busy < 80% did not bind: reported beside
   CAP. A mean review above 60 s in any window is reported.
5. **Merge queue.** Mean merge-queue time per change above 30 s: stop and fix the harness before the next window.
6. **Stalled sessions and session length.** A session with no READY within **10 min** (was 25) of a hand-out or rework
   message is timed out and retired; the slot's next task goes to a fresh session. The prompts state a **5-min**
   budget and tell the session to push what it has after 8 min (was "about 20 minutes": a session that believed it
   had 20 and spent 12 would be killed as a stall); this moves the prompt slightly off the T0d / T0e calibration.
   Sessions are also retired after **4 tasks or 6 messages of any kind** (launch prompt, next tasks, rework),
   whichever comes first: compaction is driven by context, and T0e compacted after 6 tasks + 2 reworks = 8 messages
   (4 tasks + 4 reworks would be 8 again). Mechanism risk: more rework at N = 12 (conflicts) means more messages per
   task, so more retirements (and without the message limit more compaction and stalls) at N = 12 than at N = 1, a
   harness-specific route to a bend; timeouts, branch losses and retirements are reported per window and **per
   hand-out by N** (LAMBDA); a window with more than two stalls is noted and SCALE is also shown without it.
   Simulated: 3% of hand-outs stalling, SCALE's false alarm 0.029; 10%, 0.023 (power against Amdahl 0.60); the old
   rule (8 tasks, no message limit) 0.037.
7. **Task order.** Every window's `reset` uses a fresh order seed (recorded in reset.json). A repeated seed is a
   protocol deviation: the window is reported and SCALE is also shown without it (section 5.6).

`predict.py` (default `--plan v7`) prints the design, the tasks and dollars of all three designs, rule 2's choice for a
given cost per task, the reviews, the model's prediction per cell, these rules and the operating characteristics;
`score.py` (default `--plan v7`) codes the results with these grades and the COST ledger. The v6 codings stay behind
`--plan v6`, v5 behind `--plan v5`, v4 behind `--plan v4`.

### 5.5 The pre-sweep trial (T1b) and its decisions

T1b is the first slot-mode run at twelve slots and the only paid step before the pre-registration commit.

| | |
|---|---|
| Shape | one slot for 30 min, then twelve for 15 min (`start_schedule = [[0, 1], [30, 12]]`, window 45 min, no warm-up, 10 min grace), K = 5, one session per slot, retired after 4 tasks or 6 messages, session timeout 10 min, watcher poll 2 s (`harness/config.t1b.toml`) |
| Tasks it may use | **about 87 (p90 120); the plan reserves 90 and tolerates up to 120** (about $39 at $0.45). The one-slot phase costs about 0.4 tasks a minute, the twelve-slot phase about 5; it gives about 8 follow-ups at one slot and 50 at twelve |
| Why 30 + 15 (was 30 + 10; v6 90 + 30) | with 4 tasks per session and a ~2.3-min cycle, a slot's fifth task, i.e. its first concurrent retirement and relaunch, comes at minute 10-11 of the twelve-slot phase: at 30 + 10 the sweep's first N = 12 window would have been the first time twelve slots retire and relaunch together, which is where a provisioning throttle would bite and what the launch flag is for. 30 + 15 also makes the twelve-slot phase the same length as a sweep N = 12 window, so rule 2's h is measured on the shape it prices. About 21 tasks (~$10) more than 30 + 10, the same rule-1 OCs (CLEAR 0.75 / 0.70 at log-sd 0.25) |
| Decides | **rule 1** (STOP / CLEAR / INCONCLUSIVE on follow-ups; launch and coding flags); **rule 2** (cost per task c and hand-outs per slot-hour h -> the 500-, 400- or 300-task design, or stop); **rule 3** (lambda at one slot); **rule 4a's plan-usage projection** (plan usage read before and after) |
| Reports (descriptive) | lambda at twelve slots, the five reviewers' busy share and mean review time at twelve slots (a first check of 4a's contention under load), sessions launched, stalls, merge-queue time |
| Then | the pre-registration commit (T1b's readings, the chosen design, `predict.py --budget <choice>` output, the day split; it states that T1b's data enter no sweep result: T1b's twelve-slot phase previews lambda(12), and rule 2's choice from it is mechanical), then the sweep |

### 5.6 Design caveats

**Shared context within a session.** A slot's session carries up to four tasks (or six messages), so a later task sees what the
session did on earlier ones: faster start-up (15 s against 29 s: no provisioning, measured), possibly faster coding
(T0e's follow-ups coded in 66 s against T0d's 92 s, n = 5), and the risk of carry-over (a stale base, leftover files)
and of compaction (T0e's sixth task). Assessment per test:

- **SCALE: not threatened.** Every slot at every N runs the same session protocol, and with one window length for all
  cells a slot hands out about the same number of tasks at N = 1 and N = 12 under the null, so the mix of first-in-
  session and follow-up tasks is the same at both sizes. Simulated with follow-up tasks coding 25% faster: false alarm
  0.017 (0.029 without); with 8 tasks per session (and the 6-message limit): 0.026; the old rule (8 tasks, no message
  limit): 0.037 (within simulation noise). Under a real bend the N = 12 slots
  hand out fewer tasks, a larger share of them first-in-session (slower), which can only deepen a real bend slightly,
  not create one.
- **CAP**: same N, same protocol in both arms: not threatened. **Rule 1**: compares follow-ups with follow-ups.
- **COLL**: j counts merges since the hand-out (the next-task prompt starts a fresh branch from `origin/main`). A
  session that built on a stale base would carry more exposure than j records, raising p-hat at every j alike; it
  cannot produce a j-trend where collisions are absent. Not threatened; p-hat's calibration could be.
- **ESC / ESC-N (descriptive)**: escapes may cluster by session (a misreading repeated across its tasks); the
  reported interval clusters by task only, so it may be too narrow if they do.
- **Per-task quantities** (start-up, coding, escapes) are not comparable with T0-T1's one-session-per-task logs; the
  analysis reports launch and follow-up start-ups apart (SCHEMA.md).

Recorded as a caveat; no confirmatory test depends on sessions being independent across tasks.

**Task reuse across windows.** Each window resets the sandbox and hands out the first tasks of its own seeded order of
the same 220 (an N = 12 window about 80, an N = 1 window about 7): every task recurs across windows. Workers and the
reviewer carry no memory between windows (fresh sessions, fresh review calls). Simulated with per-task coding time
varying (log-sd 0.3 and 0.5, the same task in every window) and one pool reused by every window: SCALE's false alarm
0.027-0.029, as with a fresh pool per window (0.027-0.036); unbiased. **With one seed for every window**, the N = 1
windows would all take the same few tasks while the N = 12 windows take 80, and the false alarm rises to 0.063 with the
per-agent ratio off by 10%: hence rule 7. Supply never runs out in 15-min windows (a 45-min N = 12 window would exhaust
the 220 tasks and bias the per-agent ratio to 0.90).

**Branch names and losses.** T0e's relaunched session pushed `claude/task-t039-<suffix>`; the watcher ignored it and
the slot was lost for the rest of the window (at N = 12 a mis-named branch would cost a slot its whole 10-min timeout,
most of a 15-min window, and a task's credits). The harness now attributes `claude/task-<id>` case-insensitively and
with any suffix after a separator (rejecting names that fit two ids), and the prompts require the exact name. Losses
are reported per hand-out by N (rule 6); at a 10% loss rate SCALE's false alarm stays 0.023 but its power against
Amdahl falls to 0.60 (section 5.3).

**Drag or throttling.** A coding-only service slowdown of 1.3x at N > 1 reads as BEND 19% of the time, and rule 1
cannot see a coding throttle. In this harness a real bend can only come through git (merging a moved `origin/main`,
conflicts, rework, review wait): the watcher logs on every `submit` whether the branch merged `origin/main`
(`merged_main`), and DRAG (section 5.3) splits coding time at N = 12 against N = 1 by it. Uniformly longer coding with a
flat merged-main share reads as throttling; longer coding concentrated in merged-main tasks as drag.

**Other caveats.** The cost per task comes from two short trials read on a whole-dollar meter (rule 2 re-measures
it); reviewer contention at K = 5 is unmeasured (rule 4a); the window CV of 0.3 is still assumed (at 0.5 SCALE's false
alarm is 0.10); everything rests on T0d, T0e and T1, and T1b is the first twelve-slot run in slot mode.

## 6. Harness configuration changes the design needs (made 2026-09-30, with tests)

1. **Phases** (`harness/config.py` `PHASES`, `SWEEP_REVIEWERS = (1, 5)`, and the phase configs): `t1b`:
   `window_min = 45`, `warmup_min = 0`, `grace_min = 10`, `start_schedule = [[0, 1], [30, 12]]`, `[reviewer]
   parallel = 5`. Sweep phases `sweep-n1-k5`, `sweep-n12-k5` (files `config.sweep-n1-k5.toml`,
   `config.sweep-n12-k5.toml`) and, for the 500-task design, `sweep-n1-k1`, `sweep-n12-k1`: `window_min = 15`,
   `warmup_min = 3`, `grace_min = 10`. The `-k3` phases and files are retired.
2. **Every v7 phase pins** (and `run` checks): `task_timeout_min = 10` (rule 6), `task_budget_min = 5` (the prompts
   say to push what they have after `task_timeout_min - 2`), `poll_interval_s = 2` (rule 1), `[reviewer]
   max_downtime_min = 3`, `[launcher] session_per = "slot"`, `tasks_per_session = 4`, `messages_per_session = 6`;
   `mode = "command"`.
3. **Branch attribution**: `claude/task-<id>` case-insensitively and with a suffix; ambiguous names rejected; the
   prompts require the exact name.
4. **Events and notes**: `submit` carries `merged_main`; `reviewer_rate_limited` notes; `harness log --plan-usage`
   writes `note plan_usage ...`; `session_retired ... reason=messages_per_session`.
5. **`harness throttle`**: decides on the follow-up-only ratio in a slot-mode log, prints the launch and coding flags,
   cuts coding legs off 5 min before window end; default split at the second `start_schedule` minute (30).
6. **Reset seeds**: not yet enforced: the operator uses a fresh `--seed` per `reset` (recorded in reset.json); a
   refusal of a repeated seed is still to be added (rule 7).
7. **Free before T1b**: `calibrate --parallel 5` and `--parallel 1` on the same heads (rule 4a), with plan usage read
   before and after.
