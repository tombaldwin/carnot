# Study 2 plan, version 6: agent scaling and the review ceiling, with reviewers as a factor

> **Status, 2026-09-28: paused, and not yet reviewed or pre-registered.** The harness launched task
> sessions through Claude Code routines, the only scriptable cloud sessions that can push to GitHub. Routine runs
> draw plan usage rather than cloud-session credits. Plain `claude --cloud` sessions can't push: they get a bundled
> copy of the repository with no remote
> ([anthropics/claude-code#81776](https://github.com/anthropics/claude-code/issues/81776)). The study resumes when
> sessions that draw the credits can hand their work back. The independent review of this plan (section 4, step 2) has
> not been done.

Supersedes PLAN-v5. Drafted on 2026-09-28 from the live trials T1 and T0c (numbers in
`analysis/design-search/DESIGN-SEARCH-v6.md` section 1; `analysis/design-search/t1_params_public.json`, numbers only).
Everything built for v5 carries over unless changed here: the sandbox and 220 tasks, per-task cloud sessions (Haiku 4.5)
launched through per-slot routines, the verified local reviewer command (checkout job, default effort), hidden tests
kept local, the serial merge queue, the event log, the post-hoc effort re-review, and the analysis codings of v5 for
COLL, ESC, FAMILY and the descriptive results.

## 0. Why v6: what T1 showed

v5 rested on one premise: *once a model does the checking, review stops binding at any fleet size the budget can
reach* (v5 predicted a reviewer busy 26-79% of an N = 12 window). T1 contradicts it.

| | v5 assumed | T1 measured |
|---|---|---|
| first submissions per slot-hour (lambda) | 6 | 16.0 at one slot (8 in 30 min), 13.6 at twelve (s2 / s3 excluded); T0c 13.3 |
| per task: re-arm, start-up, coding | 0 s, 78 s, ~5 min | 20 s, 78 / 93 s (median, one / twelve slots), 82 / 84 s |
| reviews asking for changes | ~30% | 39% (46 of 117), the same on rework (15 of 37) |
| review time | 10-30 s | median 20 s, p90 29 s, max 39 s; independent of the queue |
| reviewer busy at N = 12 | 0.26-0.79 | **0.88** of the twelve-slot phase, 0.95 in its last 20 min |
| review queue at N = 12 | short | **grew every minute, to 29 at the window's end** (212 submissions an hour against 152 reviews) |
| escapes (approvals failing hidden tests) | ~10% | 3% (2 of 71) |
| merge queue per change | 2 s | 5.8 s |

With one serial reviewer, twelve Haiku slots produce more than one reviewer can check, so N = 12 measures the
reviewer's ceiling, not the workers. Simulated with T1's process, v5's SCALE would read BEND in 53% of studies with
perfectly linear workers (88% with T1's collision rate). The paper's model says exactly this:
`U(N) = (1 - r(N)) min(lambda X(N), V/h)`; T1 put the study on the V/h branch. v6 therefore makes the number of
parallel reviewers K a factor: with K = 3 review does not bind and SCALE measures the workers, as v5 intended; with
K = 1 at N = 12 it binds, and **"finished work is capped by V/h" becomes a confirmatory target of its own (CAP)**.

T1 also showed that abort rule 1 as coded (total activity per slot-minute, 0.8 threshold) could not decide anything:
0.71 raw, 0.77-0.80 without the two lost slots, interval about 0.44-1.15, from 8-9 tasks in a 30-min one-slot phase.
Activity mixes service speed with rework and review waiting. v6 rebuilds the rule on per-task start-up time (section
5.3) and repeats T1 with a 90-min one-slot phase (T1b).

And the credit meter did not move: $249 before T0c and after T1 (about 7 session-hours; the UI shows whole dollars).
Routine runs appear to draw Max-plan usage rather than the $250 of cloud credits. **The budget is unresolved** (section
5.2): designs are costed under (a) the v5 credit assumption and (b) plan usage; the owner decides which applies.

## 1. Questions

1. **Scaling (agent side).** With review not binding (K = 3), does finished work grow in proportion to fleet size, or
   bend? Rivals as v5: linear; Amdahl; USL; Carnot uncapped (p = 0.0075, T1's collision rate).
2. **Quality.** The escape rate, overall and against N (descriptive; escapes are now rare).
3. **Collisions.** Rebase conflicts and integration failures against j, the merges since a change's base.
4. **The review ceiling.** At N = 12, does one reviewer cap finished work below what three allow (CAP), and how close
   does output come to the ceiling predicted from the reviewer's own service rate (CAPFIT)? Utilisation of the
   reviewers and merge queue per cell (UTIL).
5. **Effort and escapes (free, post hoc).** As v5.

## 2. Reviewers

The verified checkout job at default effort, one fresh local `claude -p` call per change (Opus 5.5), now run by K
parallel reviewer threads on one FIFO queue. K is fixed per window (1 or 3) and logged. A reviewer never sees the
queue. At N = 1 the reviewer is busy 14% (K = 1) or 5% (K = 3) of the time, so K should not matter there; it is
varied at N = 1 anyway, at no cost, to make the design a full 2 x 2. The harness changes are in section 6.

## 3. Settled by the v6 design search (free, done)

Window length (45 min: the 220 tasks last only 70-75 min at N = 12 with lambda 14), K (3: two reviewers are still busy
80% at N = 12), the split of N = 12 windows between K = 1 and K = 3, the N = 1 count, the degrade design, grades and
operating characteristics, and the revised abort rule 1 with the one-slot phase length it needs.

## 4. Order of work

1. Free: this plan, the v6 design search, codings and self-tests (done: `analysis/v6.py`, `--plan v6`).
2. Free: review of this plan (Fable). **Owner: decide the budget (section 5.2).**
3. Free: harness changes for K reviewers (section 6) and their tests; a free reviewer-contention calibration
   (`calibrate --parallel 3` against `--parallel 1` on the same heads).
4. Paid / plan usage: **T1b** (one slot 90 min, then twelve slots 30 min, K = 3): abort rule 1.
5. Pre-registration commit: T1b's rule-1 reading, `predict.py` output, codings with operating characteristics.
6. The sweep (19 windows), then the post-hoc effort re-review, analysis, publication whichever way it comes out, and a
   revision-log entry in the paper.

## 5. Design (from the v6 search)

Settled by the free design search (`analysis/design-search/DESIGN-SEARCH-v6.md`; the v6 process in `analysis/synth.py`,
calibrated on T1 so that every T1 measure lies inside the simulator's 5-95% range; the codings in `analysis/v6.py`);
operating characteristics in `OPERATING-CHARACTERISTICS-v6.md`.

### 5.1 The design

| | |
|---|---|
| Factors | fleet size N in {1, 12} x parallel reviewers K in {1, 3} |
| Windows | **N = 1: six at K = 1 and six at K = 3; N = 12: three at K = 3 and four at K = 1; 45 min each** (5 min warm-up, 10 min grace) |
| Order | 1K3, 1K1, 1K3, 12K1, 1K1, 12K3, 1K3, 1K1, 12K1, 1K3, 12K3, 1K1, 12K1, 1K3, 1K1, 12K3, 1K3, 12K1, 1K1 (`v6.v6_order`: two N = 1 windows first, N = 12 windows spread evenly and alternating K, never first or last) |
| T1b (before the sweep) | one slot for 90 min, then twelve for 30 min, K = 3, 10 min grace: 7.5 session-hours. Abort rule 1 |
| Pilot | none beyond T1b: every rival's level is fitted to the sweep's own windows, as v5 |
| Worker session-hours | sweep 12 x 0.75 + 7 x 9 = 72; T1b 7.5; total 79.5 |
| Harness | K reviewer threads (section 6); phase configs with `window_min = 45`, `warmup_min = 5`, `[reviewer] parallel = 1 or 3`; a T1b config with `start_schedule = [[0, 1], [90, 12]]` and window 120 |
| Degrade design | N = 1: five at each K; N = 12: two at K = 3 and three at K = 1 (52.5 sweep session-hours) |

### 5.2 Budget: UNRESOLVED, the owner decides

**(a) Credits, the v5 assumption** ($2.10 per worker session-hour; $249 balance, $50 floor, so $199 to spend): the
design costs **$167** and fits up to **$2.50** per session-hour; the degrade design costs $126 at $2.10 and **$189 at
1.5x ($3.15)**, fitting up to **$3.32**. The reviewer is local and costs no credits under either reading.

**(b) Plan usage** (if routine sessions draw the Max plan): the constraint is session-hours per day (and per 5-hour
block) of Haiku cloud sessions plus the local Opus reviewer's calls.

| | |
|---|---|
| worker session-hours | 79.5 in all (T1b 7.5, sweep 72); T0c + T1 used about 7 in one afternoon with no visible limit |
| intensity | an N = 12 window is 12 concurrent sessions for 45 min (9 session-hours); an N = 1 window 0.75 |
| reviewer (Opus, local) | about 17 reviews per N = 1 window, 145 per N = 12, K = 1 window, 210 per N = 12, K = 3 window (up to three at once): about 1,400 in the sweep plus about 200 in T1b, each about 48k input tokens (T1) |
| days at an allowance of 10 / 20 / 40 session-hours per day | 8 / 4 / 2 days (an N = 12 window needs 9 within one day) |
| upgrade if (b) applies | four N = 12 windows at each K (`--cells 1x1=6 1x3=6 12x3=4 12x1=4`): 88.5 session-hours, CAP power 0.80 instead of 0.74 (T1-like workers) |

Under (b) the sweep is paced by day; the order and the analysis do not change. `predict.py --session-h-per-day <h>`
prints the table for a given allowance.

### 5.3 Grades and operating characteristics (recommended design; degrade design in brackets)

| Question | Result | Grade | False alarm | Power |
|---|---|---|---|---|
| 1 Scaling | **SCALE**: per-agent finished output falls from N = 1 to 12, on the N = 12, K = 3 windows and every N = 1 window (one-sided NB LR, CV 0.3) | **confirmatory, primary** | 0.020 (0.030); 0.12 at window CV 0.5 | Amdahl 0.97 (0.885), USL 1.00, Carnot 1.00; lambda -30%: 0.93; mild bend (alpha 0.03) 0.32 |
| 4 Ceiling | **CAP**: at N = 12, finished output with K = 1 is lower than with K = 3 (one-sided NB LR on the K contrast, CV 0.3) | **confirmatory, secondary** | 0.025 (0.036) when one reviewer never binds | T1-like workers 0.74 (0.54); linear 0.51 (0.38); slow reviews 0.995; under drag (USL, Amdahl) the model predicts no ceiling and CAP reads CAPPED only 0.03-0.05 |
| 3 Collisions | **COLL**: first merge-queue pass fails by collision more often the more changes merged since the change's base | **confirmatory, secondary** | 0.015-0.035 | USL-like workers p = 0.0035 / 0.0075 / 0.015: 0.48 / 0.78 / 0.98 (0.38 / 0.69 / 0.93); linear workers p = 0.0035: 0.99 |
| 4 | K1: the K contrast at N = 1 (the 2 x 2 interaction's other half; predicted nil) | descriptive | flagged 0.01 | |
| 4 | CAPFIT: finished per hour against (1 - b)(1 - b_mq) K 3600 / mean review s | descriptive | | |
| 2 Quality | ESC-N: escapes rise with N | descriptive | 0.06 | a doubling (0.03 -> 0.06): 0.44 |
| 2 Quality | ESC: the escape rate | descriptive (estimate) | | 95% half-width about ±0.012 on about 860 approvals |
| 1 Which rival | FAMILY: four-way pick with free levels, on the SCALE windows | descriptive | | linear 0.98, Amdahl 0.86, USL 0.48, Carnot 0.50 (USL and Carnot 8% apart at N = 12) |
| 4 Limits | UTIL: reviewer and merge-queue utilisation per cell | descriptive | | reviewers busy at N = 12: K = 1 0.99, K = 3 0.57 (linear workers) |
| 1 | THROTTLE: rule 1b, each N = 12 window's start-up and coding against the N = 1 windows | descriptive | | |
| 3 | COLL-m, COLL-k; LAMBDA, BOUNCE, EFFORT | descriptive | | |

SCALE is also reported without windows that ran out of tasks more than 10 min before their end (SCALE-nf), without
rule-4-flagged windows, with the N = 1, K = 3 windows only, and with the CV-estimated and Welch versions; the observed
window-to-window CV of the N = 1 windows is reported beside it. CAP's power rests on near-linear workers: if SCALE
reads BEND strongly, one reviewer may keep up and CAP should read NOT-DETECTED, which is the model's prediction
there. No multiplicity correction across questions (as v5): each confirmatory test answers a different question.

### 5.4 Budget and abort rules

1. **Throttling (T1b; revised).** T1b runs one slot for 90 min, then twelve for 30 min (K = 3). For every task launched
   in each phase: **start-up** (routine fire -> branch first pushed, from the `run_once_at` of its `launch_detail`
   note; launch -> push if that is missing) and **coding** (branch pushed -> first READY). The statistic is the ratio
   of geometric means, twelve slots over one, with a Welch 90% interval on the log scale; **tolerance 1.25**.
   **STOP** (report T1b) if the start-up interval lies wholly above 1.25; **CLEAR** if it lies wholly below;
   otherwise **INCONCLUSIVE**: proceed, with the ratio and interval reported beside SCALE. A coding interval wholly
   above 1.25 is a **flag, not a stop** (coding contains the coordination drag SCALE measures; in simulation drag
   alone raises it almost always and never moves start-up); it is reported beside SCALE. Slots lost to
   operator-logged failures unrelated to the service (such as T1's CLI update) are excluded before the ratios are
   computed. Both phases share one window, so day-to-day speed differences cancel. Operating characteristics
   (simulated T1b): no throttling -> CLEAR 0.94, STOP 0.000; start-up and coding 1.25x slower at twelve -> STOP 0.04;
   1.5x -> 0.77; 2x -> 1.00; drag only -> STOP 0.000. The 90% interval is about x/1.12 with 90 min at one slot
   (about 21 tasks) against x/1.21 with T1's 30 min; T1 itself reads INCONCLUSIVE (start-up 1.13, 90% 0.98-1.31).
   `v6.throttle_v6(run, events, exclude=[...])` codes it.
   **1b** (in the sweep, descriptive): each N = 12 window's start-up and coding against all N = 1 windows so far,
   same statistic (THROTTLE). Across windows it carries the between-window speed noise, so it decides nothing.
2. **Budget.** The owner decides (a) or (b) before T1b. Under (a): burn = (M0 - M1) / session-hours over T1b (if the
   meter moves at all); run the full design if the predicted balance after it stays >= $50 (burn <= $2.50); else the
   degrade design (burn <= $3.32); else stop and report T1b. Before every window the rest of the chosen design must
   still fit; if not, drop N = 1 windows beyond ten first, then N = 12 windows alternately by K, keeping at least two
   of each. Under (b): pace the sweep to the daily allowance; the order and analysis do not change. An interrupted
   sequence is analysed as run (every v6 test is defined for >= 1 window per cell it uses).
3. **Lambda floor.** The sweep starts with two N = 1 windows. Pooled lambda there below 7 first submissions per
   slot-hour (half of T1's 14): stop before the first N = 12 window and report.
4. **Reviewer binding, by K.** An N = 12, K = 3 window with the reviewers busy >= 80% of its counted time (per
   reviewer) is flagged in UTIL and SCALE is reported with and without it (not a stop; simulated: some window is
   flagged in 23% of studies under linear workers, SCALE's false alarm stays 0.02). An N = 12, K = 1 window with its
   reviewer busy < 80% means the ceiling did not bind in that window; it is reported beside CAP (not a stop). A mean
   review above 60 s in any window is reported (T1: 21 s).
5. **Merge queue.** Mean merge-queue time per change above 30 s (T1: 5.8 s): stop and fix the harness before the next
   window.

`predict.py` (default `--plan v6`) prints the design, the model's prediction per cell (which cells bind, reviewer
utilisation, illustrative counts), the budget under (a) and (b), these abort rules and the operating characteristics;
`score.py` (default `--plan v6`) codes the results with these grades. The v5 codings stay behind `--plan v5`, v4 behind
`--plan v4`.

## 6. Harness changes needed for K parallel reviewers (specified, not implemented)

The harness has one serial reviewer thread (`orchestrator._review_loop`). Required:

1. **Config.** `[reviewer] parallel = K` (default 1). Phase configs: `sweep-n1-k1`, `sweep-n1-k3`, `sweep-n12-k1`,
   `sweep-n12-k3` (or one config per N with a `--reviewers K` override that is logged), all with `window_min = 45`,
   `warmup_min = 5`, `grace_min = 10`; `t1b` with `start_schedule = [[0, 1], [90, 12]]`, `window_min = 120`,
   `warmup_min = 0`, `parallel = 3`. `run` refuses a sweep config whose K is not 1 or 3.
2. **Threads.** Start K reviewer threads `reviewer-r1..rK`, each running `_review_loop(rid)` on the shared `review_q`
   under the existing lock and condition variable. `self.reviewing` becomes a dict rid -> Change; `idle()` checks every
   entry; each thread keeps its own `busy` flag, retry and back-off. Reviewer downtime is accounted per reviewer;
   a window is VOID if the reviewers' combined capacity was down more than `max_downtime_min` (i.e. summed downtime
   / K).
3. **One task at a time.** A thread takes the first queued change whose task is not under review by another thread
   (the supersede rule in `_submit` already replaces a queued head of the same task; with K > 1 an older head of the
   task may still be in review elsewhere, and its newer head must wait for it). Changes whose visible-test prep is
   not done are skipped, not blocked on (today the loop waits for the queue head's `prepared`).
4. **Events.** `review_start`, `review_end`, `review_error`, `reviewer_busy`, `reviewer_idle` carry
   `reviewer: "r1".."rK"` (always, also at K = 1, in v6 runs); `run.json` gets `n_reviewers: K`. `queue_depth` =
   changes waiting, excluding those under review. SCHEMA.md documents both (done; `validate_schema.py` and
   `derive.py` already accept them and compute per-reviewer busy time).
5. **Isolation.** Each call already gets a fresh checkout; make its temp directory unique per call (`mkdtemp`, with
   the reviewer id in the prefix) and run pytest with `-p no:cacheprovider` so concurrent calls share no cache.
   All threads use the pinned CLI copy; the CLI-start retry applies per call.
6. **Merge queue.** Unchanged (serial). Approvals enter it in the order reviews finish.
7. **Grace.** All K reviewers continue through grace; a review still open at grace end is noted per reviewer
   (`review_open_at_grace_end task=… head=… reviewer=…`); the analysis clips that reviewer's busy time only.
8. **Throttle command.** `harness throttle` implements revised rule 1 (start-up and coding ratios, Welch 90%
   intervals, tolerance 1.25, `--exclude s2,s3`), or calls `analysis/v6.throttle_v6`; the activity ratio stays as a
   reported extra.
9. **Tests.** Sim-reviewer tests at K = 2 and 3: no change reviewed twice at once, no task in two reviews at once,
   every reviewer event tagged, busy/idle paired per reviewer, at most K reviews open, `idle()` false while any
   reviewer works, and a dry run whose log passes `validate_schema.py`.
10. **Calibration (free).** `calibrate --parallel 3` and `--parallel 1` on the same reference and mutant heads,
    to measure contention (the simulation assumed none, and showed that 20% per extra reviewer is harmless to
    SCALE but raises rule 4's flag rate).
