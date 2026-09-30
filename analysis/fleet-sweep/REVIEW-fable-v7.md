# Review of PLAN-v7 (study 2), before T1b

Reviewer: Claude (Fable 5.1), 2026-09-30. Read in full: PLAN-v7.md, OPERATING-CHARACTERISTICS-v7.md,
analysis/design-search/DESIGN-SEARCH-v7.md, analysis/{v7,t0de_params,synth (v7 path),v5 bend_test,v6 tests}.py,
analysis/design-search/{dsim7,search7,oc_v7,calib_t0de}.py and their JSON outputs (oc_v7, oc_v7_t1b, oc_v7_reuse,
calib_t0de, v7_stageB, t0de_params_public), PLAN-v6.md, REVIEW-fable{,-v2,-final}.md, SCHEMA.md, harness/README.md,
harness/config.t1b.toml, harness/harness/config.py (PHASES), prompts/next-task.md, model.md and the paper. Private
logs read: T1-2026-09-28, T0d-2026-09-29, T0e-2026-09-29 (event types and timings only; nothing copied). Run:
`predict.py` (default `--plan v7`) and small read-only scripts over the JSON and the logs. Not run: the harness,
`claude`, `selftest.py` (its outputs are already modified in the working tree), anything on GitHub.

## Verdict: NOT YET. Fix five things before T1b, then GO.

The design is coherent and honestly graded, the code matches the plan, and the hand-typed `V7_OC` table matches
`oc_v7.json` everywhere I checked (linear, mild, Amdahl, USL, Carnot, measured, the CV, contention, lambda, stall,
task-reuse, tps=8, posfx and upsd sensitivities, the 300 and 500 designs, CAP, COLL, ESC-N, K1). Nothing the earlier
reviews asked for has been reintroduced. But abort rule 1, the one decision T1b exists to make, rests on a
measurement the harness cannot currently take, and two operational risks (the reviewer's plan usage, a second T0e
failure the plan does not mention) can void paid N = 12 windows. None of the fixes needs new credits.

### MUST-FIX before T1b

1. **The start-up legs are quantised at the watcher's poll, so rule 1 as simulated does not exist.** Every `claim`
   and `submit` is timestamped when the watcher's 15-s poll sees the push (SCHEMA.md "Event times are the time the
   watcher saw the push"; `poll_interval_s = 15` in every config). In T0d the gaps between consecutive watcher events
   are all multiples of about 16.5 s (32.6-33.2, 49.0-49.6 s; residual 0.4 s at period 16.5); T0e and T1 the same.
   The "launch start-up 28.7 s, log-sd 0.02" and "follow-up start-up 15.0 s, log-sd 0.04" (PLAN-v7 §0 table,
   DESIGN-SEARCH-v7 §1, `synth.V7_TRUTH`) are therefore the hand-out-to-next-poll distance, not the session's
   speed: the follow-up push happens somewhere within ~13 s of the message, the launch push within 13-29 s, and the
   tiny log-sds are poll alignment. Rule 1's precision (interval x/1.03; OC-v7 "Abort rule 1" table; PLAN §5.4 rule 1)
   is an artefact of simulating lognormal(15 s, 0.05) legs; the real statistic is a step function of the true
   slowdown with an unknown threshold (a follow-up slowed from 8 to 12 s is invisible; from 8 to 16 s it reads as
   2.0x and STOPs). Fix: set `poll_interval_s` to 2-3 s in the T1b and sweep configs (a fetch of a small repo every
   2 s is nothing; the merge queue and reviewers are unaffected), re-derive nothing from T0d/T0e for start-up, and
   pre-register rule 1's operating characteristics at the start-up log-sd 0.25 sensitivity already in OC-v7 (CLEAR
   0.72, STOP 0.54 at 1.5x, 0.98 at 2x) as the ones that apply, unless T1b's own one-slot phase shows a tighter
   spread. Coding legs are quantised too (T0e's four coding times are exactly 4, 4, 3, 4 polls), which the faster
   poll also fixes; the simulator's coding parameters are unaffected in practice.

2. **The reviewer's Max-plan usage has no rule.** The sweep needs about 550 Opus reviews (T1b 94, sweep 458;
   PLAN §5.2) at ~41-48k input tokens each, about 25M input tokens, most of it in three N = 12 windows of 115
   reviews in 25 minutes. A rate limit on the reviewing account shows up as `review_error` downtime, and with
   `max_downtime_min` 3 (PLAN §6.4) a paid N = 12 window is VOID after 15 summed reviewer-minutes. Nothing in
   v7 measures or bounds this (the v4 review's must-fix 4 asked for plan-usage readings; PLAN-v4 §7.5's
   `plan_usage` notes have dropped out of v7). Fix, free: read the plan's usage indicator before and after the
   rule-4a calibration run (~2 x 5 x N reviews) and before and after T1b (94 reviews), record them as `note
   plan_usage ...`, project the sweep's reviews against the remaining 5-hour and weekly allowances, and add to rule
   4a: do not start the sweep if the projection leaves less than, say, 30% headroom; schedule the N = 12 windows so
   no two fall in one 5-hour block if the projection is tight.

3. **T1b's twelve-slot phase must include a session retirement at twelve slots: make it 30 + 15, not 30 + 10.**
   With `tasks_per_session = 4` and a ~2.3-min cycle, a slot's fifth task, i.e. its first concurrent relaunch,
   comes at minute 10-11 of the phase (PLAN §5.5 shape; `synth.v7_dispatch`). At 30 + 10 the sweep's first N = 12
   window would be the first time twelve slots retire and relaunch together, which is exactly where a
   provisioning throttle would bite and what the launch flag is for. The only retirement-relaunch observed in
   slot mode (T0e, minute 33.6) took 3 min to its first push and mis-named its branch (item 4). 30 + 15 is in
   `oc_v7_t1b.json`: 87 tasks (p90 120), CLEAR 1.00, launch flag 1.00 at 2x, coding flag 0.27 at 1.5x, i.e. the
   same OCs for about $10 more; it also makes the twelve-slot phase the same length as a sweep N = 12 window, so
   `h` in rule 2 is measured on the shape it prices. Reserve 90 tasks and tolerate 120.

4. **T0e had two failures in seven hand-outs, and the plan reports one.** After the compaction stall and the
   retirement, the fresh session launched at minute 33.6 first pushed at 36.7 (3 min, against 29 s) to a branch
   named `claude/task-t039-<suffix>` in lower case; the watcher logged "never launched in this window; ignored", no
   `claim` was recorded, and the READY arrived after window end. PLAN §0 says "7 / 5 (the session stalled on its
   6th task)" and DESIGN-SEARCH-v7 §1 lists one incident. Two consequences. (a) Harness, before T1b: attribute a
   `claude/task-<id>*` branch to `<id>` case-insensitively and with any suffix (the README's
   `accept_other_claude_branches` path only works once a `READY: <id>` commit exists, which is too late for the
   start-up leg and for rule 1), and say in `prompts/worker.md` / `next-task.md` that the branch name must be
   exactly `{branch}`, nothing appended. At N = 12 a mis-named branch costs a slot its whole 10-min timeout (most
   of a 15-min window) and a task's credits. (b) Pre-registration: the observed loss rate in slot mode is 2 of 7
   hand-outs (2 of 23 over T0d + T0e); the OC sensitivity is 3% (`linear/stall=0.03`). Add a 10% stall/loss
   sensitivity to `oc_v7.py` and report it; and note that the one post-retirement launch was 6x slower than the
   others, which is the case for the log-sd 0.25 sensitivity in item 1.

5. **Rule 2 needs one definition and a meter protocol.** PLAN §5.4 rule 2 defines c = (before − after + $1) /
   tasks with "±$0.015"; `v7.ABORT_V7` and `predict.py` say (before − after) / tasks with "±$0.03"; `choose_design`
   adds nothing. Two whole-dollar readings give ±$2 on ~$30, i.e. ±$0.03 per task at 66 tasks, and the
   400-task design is affordable only up to $0.48 against a central $0.45 (PLAN §5.2): the 400/300 choice is a
   coin toss around the boundary, and the 400 design at $0.45 ends $13 above the $50 floor, so a 4% overrun
   triggers the per-window drop rule. The meter also lags or is read late: T0d's after-reading was taken 42 min
   after grace end; T0e's after-reading ($236) is not in the event log at all, only in `t0de_params.py`'s
   `--meter` default. Pre-register: one formula (drop the +$1 or keep it everywhere, and say which); readings
   taken at a fixed delay after the last session stops (15-30 min; if the meter has not moved from the
   before-reading, wait and read again); during the sweep use the cumulative c = (meter before T1b − now) / (all
   hand-outs since), not per-window deltas of $3; and state plainly that the drop rule (PLAN §5.4 rule 2) is
   expected to fire in a sizeable share of futures, with the truncated design analysed as run.

### SHOULD-FIX before the pre-registration commit

- **Separate drag from throttling with a free mechanism log.** SCALE reads BEND under a coding-only service
  slowdown of 1.3x at N > 1 in 20% of studies (OC-v7 headline table; PLAN §5.6), and rule 1 cannot see a coding
  throttle. In this harness a real bend can only come through git: merging a moved `origin/main`, conflicts,
  rework, review wait. Have the watcher log on every `submit` whether the branch contains a merge commit of
  `origin/main` and the number of commits, and pre-register a descriptive: coding time at N = 12 vs N = 1 split by
  "merged main" / "did not". Uniformly longer coding with a flat merged-main share is throttling; longer coding
  concentrated in merged-main tasks is drag. Cheap, and the only thing that would let a BEND be interpreted.
- **State the minimum detectable effect and the empirical basis of CV 0.3.** SCALE's precision at N = 12 is set
  by 0.3²/3, not by counts; at 80% power the detectable per-agent ratio is about 0.55 (log-SE ≈ 0.25), so a
  NO-BEND result is consistent with anything from linear to a 25-30% bend. The plan says power 0.26 against
  0.77 but never states the MDE; the paper's own defaults (Amdahl 0.48, USL 0.29) are what the study can test.
  On CV 0.3 itself: the two slot-mode evenings differed in cycle rate by about 1.7x over their first five tasks
  (T0e 1.35 min per task, T0d 2.3), only partly explained by follow-ups and shared context; and 15-min windows
  turn a five-minute API slowdown into window-level noise that a 45-min window would average out. The "observed
  CV of the N = 1 windows" (PLAN §5.3) cannot check this: 12 windows of ~4 finished each have Poisson CV 0.49
  before any speed noise, which is why the CV-estimated test has false alarm 0.08. Pre-register the CV 0.5
  false alarm (0.11) as the honest bound and say so in the paper's revision-log entry.
- **Run every N = 1 window at K = 5** unless rule 2 picks the 500-task design (then 6 + 6 for the 2 x 2). The
  K contrast at N = 1 predicts nothing (K1 flagged 0.000; reviewer busy 0.25 vs 0.05, queue non-empty 0.2% of the
  time; OC-v7 setup and UTIL table), it is not a manipulation check of contention (at N = 1 there is never more
  than one review running), and it makes half the N = 1 windows differ in protocol from the N = 12 arm for no
  information. If it is kept, say it is descriptive only and costs nothing, which PLAN §2 half does.
- **Align the prompt's time budget with the 10-min timeout.** PLAN §6.2 keeps "about 20 minutes" in the prompt
  while rule 6 retires a session at 10 min. A session that believes it has 20 min and spends 12 is killed as a
  stall, its task abandoned and its session retired. Either the prompt says "about 5 minutes; if you have spent
  more than 8, push what you have", or the timeout is 12-15 min with the prompt unchanged; note that a prompt
  change moves you off the T0d/T0e calibration slightly.
- **Retire sessions by messages, not tasks.** Compaction is driven by context, and rework messages add to it:
  T0e compacted after 6 tasks + 2 reworks = 8 turns; 4 tasks + 4 reworks is 8 again. Retire after
  `tasks_per_session` tasks or after N messages of any kind (say 6), whichever first. Also note the mechanism
  risk: more rework at N = 12 (conflicts) means longer contexts, more compaction and more stalls at N = 12 than
  at N = 1, a harness-specific bend. Rule 6's per-window stall count is the right report; add "stalls per
  hand-out by N" to LAMBDA.
- **Rule 1's coding flag is biased by end-censoring in phase B.** `task_legs_v7` counts a coding leg only if
  READY ≤ window end, so in a 10- or 15-min twelve-slot phase the slow tasks handed out late are dropped and the
  B geometric mean is pulled down (masks a slowdown). Count coding legs only for hand-outs before window end − 5
  min, in both phases.
- **Pre-register the day split.** Fifteen windows plus T1b is ~9-10 h of wall clock; say how many days, and
  that each day holds at least one N = 12 window, never first or last of its day (`v6_order` only orders the
  sequence). Log time of day; day is a plausible source of the window CV.
- **Say what COLL's false alarm means.** 0.001-0.008 at nominal 0.05 is a badly under-sized test (about one
  background event per study under p = 0), not a virtue; the power figures (0.79 near-linear, 0.20 under drag at
  T1's p) are the operating characteristics that matter, and 0.20 means COLL is close to uninformative if SCALE
  reads a strong bend. State that the two results are coupled.
- **Say that CAP is gone from the recommended design, in the paper too.** Dropping it is defensible on cost
  (PLAN §5.1; DESIGN-SEARCH-v7 §3 "Why these three": one K = 1 window costs ~95 tasks for power ~0.55), and
  T1's descriptive ceiling (one reviewer busy 0.88, queue to 29) stands. But the paper's sweep section still
  describes 1 and 12 agents crossed with 1 and 3 reviewers, and question 4 in PLAN §1 is answered confirmatorily
  only in the 500-task design (c ≤ $0.39, unlikely). The revision-log entry should say the ceiling is now a
  descriptive result from T1 (routine launcher, λ 14; at λ 23 one reviewer would bind harder).
- **Housekeeping the pre-registration will hash:** `config.py` PHASES and SWEEP_REVIEWERS are still v6 (K 1/3,
  45 min, T1b 90 + 30) and `run` refuses the v7 shapes, which is correct today but every item in PLAN §6 must
  land and be tested before the hashes are taken; `config.t1b.toml` has `parallel = 3`, `task_timeout_min = 25`,
  `poll_interval_s = 15`; SCHEMA.md's design caveat says "up to 8 tasks"; the T0e incident count.

### NOTES

- **Circularity: none in the tests.** T0d/T0e set only the simulator (`synth.V7_TRUTH`), and through it the
  design and the OC tables. Every decision threshold is fixed independently of them: CV 0.3, α 0.05, rule 1's
  1.25, rule 3's floor 10, rule 4a's 1.35, rule 4b's 0.80; rule 2's inputs are measured in T1b and applied
  mechanically (`v7.choose_design`). FAMILY fits levels to the sweep's own windows. The one soft spot: T1b's
  twelve-slot phase is a preview of λ(12) seen before the pre-registration commit; the design choice is
  mechanical so this is fine, but the commit should state that T1b data enter no sweep result.
- **Multiplicity.** SCALE has about seven reported versions (nf, without rule-4b windows, N = 1 K = 5 only,
  CV-estimated, Welch, without rule-6 and rule-7 windows). PLAN §5.3 is clear that the confirmatory result is the
  fixed-CV test on all windows; the commit must name that single p-value and nothing else as the verdict. Three
  confirmatory tests without correction across different questions is stated and acceptable.
- **Outcomes baked in.** Under the linear null the simulated per-agent ratio is 1.02-1.04, not 1.00 (N = 12
  slightly favoured: rework to retired sessions and burst review). Harmless for the false alarm (one-sided for a
  fall) and a small power loss; worth a line.
- **End effects and equal window lengths.** They cancel under the null only. Under drag the slower N = 12 cycle
  loses more rework to the "no rework after window end" rule, and the twelve slots start synchronised (12
  reviews at once, ~50 s to clear with 5 reviewers). Both deepen a real bend rather than create one, which is
  the right direction for a one-sided test; PLAN §5.6 says as much.
- **Warm-up 3 min.** The first task on every slot reaches READY at ~2.1 min at both N and is excluded; the
  second is counted. If launches are slower at twelve slots the first READY may land after minute 3 and be
  counted at N = 12 only, which raises N = 12 output: also the safe direction.
- **Three N = 12 windows** are enough only because the CV is fixed; losing one (rule 4b flag in 40% of studies,
  a stall-heavy window, a VOID) leaves the 300-design power (0.77 against Amdahl). The plan says "robust to losing
  one"; it is robust for USL-sized bends, not Amdahl-sized ones.
- **Reviewer contention.** The simulated model x(1 + c(K − 1)) is crude but the conclusion is robust (SCALE
  false alarm 0.035 even at c = 0.2, busy 0.90). Rule 4a's 1.35x stop is more conservative than SCALE needs; it
  protects rule 4b's flag rate, which is fine. `calibrate --parallel 5` measures local contention (CPU, pytest,
  five CLI processes) but not queueing; item 2 covers the account-level limit it cannot see.
- **λ at twelve slots is costed at λ(1).** DESIGN-SEARCH-v7 §2 gives the argument; the follow-up command's 1.6 s
  is called every ~12 s at twelve slots from one dispatcher thread, so serialisation there is a few per cent at
  most. T1's 0.85-0.97 is the only twelve-slot datum and was under the routine launcher. Fine as a costing
  assumption; drag only makes windows cheaper.
- **Cost per task at N = 12 may exceed N = 1** (conflict-heavy tasks mean longer sessions), and the "$0.10 per
  session + $0.40 per task" fit from two ±$1 points (DESIGN-SEARCH-v7 §1) carries no information. Rule 2's
  running c covers it once the first N = 12 window is metered; say the first N = 12 window is the first N = 12
  cost measurement.
- **Task reuse** with a fresh seed per reset is unbiased in simulation (oc_v7_reuse: 0.020-0.023 vs 0.026-0.037
  fresh pools) and rule 7 is right; the harness's optional refusal of a repeated `--seed` (PLAN §6.6) should not
  be optional.
- **Shared context** (PLAN §5.6) is argued correctly: same per-slot protocol at every N, so first-in-session
  and follow-up tasks mix the same way under the null. The T0e follow-ups' faster coding (66 vs 92 s, n = 5, and
  each of them 3-4 polls) is inside the posfx 0.75 sensitivity.
- **The λ floor** (rule 3, pooled λ < 10 over T1b's one-slot phase and the first two N = 1 windows) catches
  only a disaster; that is all it is for.
- **The paper.** Its sweep section promises fits of α and β ("three sizes are the minimum for a fit"); this
  study has two sizes and fits nothing confirmatorily (FAMILY is descriptive). PLAN §1 is honest about that; the
  paper's text is not yet, and the revision-log entry should fix the 1-and-12 x 1-and-3 description as well.

## 1. Does the design still answer the paper's questions?

Question 1 (scaling) is what the money buys. SCALE is well defined (`v5.bend_test`, one-sided NB LR on
per-agent finished output with CV 0.3, windows from `v6.scale_windows`: all N = 1 windows and the N = 12 K = 5
windows; `exposure_hours` = slot-open hours net of downtime), its false alarm under the null is 0.026 and its power
0.89 / 1.00 / 1.00 against the paper's default Amdahl / USL / Carnot curves, as claimed. What the plan does not
say, and should, is the flip side: an 80%-power MDE near a per-agent ratio of 0.55, so the study tests the
published defaults, not "any bend". Question 3 (COLL) keeps its grade honestly, with power that depends on the
answer to question 1. Question 4 (CAP) is answered confirmatorily only in the 500-task design; dropping it is the
right call for the budget and is stated, but the paper still advertises the 2 x 2. Question 2 (ESC) is a
±0.022 estimate on ~265 approvals; fine as descriptive. Nothing is baked in: the linear null's simulated ratio is
slightly above 1, the safe side. There is no circularity in the tests; the design search's parameters are
simulator inputs only.

## 2. Fifteen-minute windows

Start-up transients are symmetric under the null (every slot launches at 0 and relaunches every fourth task
at every N); they are asymmetric only if provisioning slows under concurrency, which is the launch flag, and
which item 3 makes T1b actually exercise. The 3-min warm-up excludes each slot's first READY at both N; a
12-min counted window then holds ~5 tasks per slot. End-of-window censoring (no rework after window end)
removes roughly 41% x 2/12 ≈ 6% of attempts at both N under the null and more at N = 12 under drag: it cancels
where it must and biases the right way where it does not. The per-window CV: 0.49 under heavy drag (OC-v7
"Reading CAP") is count noise from one or two finished tasks per slot, relevant only to CAP; the 0.3 that SCALE
rests on is unverified and, with 15-min windows, plausibly optimistic (see SHOULD-FIX); at 0.5 the false alarm is
0.11. Three N = 12 windows are enough for the paper's default curves at CV 0.3 and for nothing milder; the
precision is 0.3²/3 against a count term of 1/150, so a fourth window buys +0.03 (stage B) and a lost one costs
0.12.

## 3. Five parallel local Opus reviewers

Contention is simulated crudely and SCALE is insensitive to it (false alarm ≤ 0.035 at c = 0.2); rule 4a
measures the local part for free before T1b and its 1.35x threshold is conservative. The unmodelled part is the
reviewing account's rate limit (item 2): 115 reviews in 25 minutes, three times, plus T1b, is a plausible way to
turn a paid window VOID. Throughput otherwise holds: busy 0.61 at N = 12 under linear workers, 0.80 with 10%
contention, and rule 4b flags the window either way. K = 1 vs K = 5 at N = 1 is not wasted budget (the windows are
needed for SCALE) but it is wasted design: it cannot show anything, and it introduces a protocol difference
between half the N = 1 windows and the N = 12 arm.

## 4. Slot sessions

The shared-context argument in PLAN §5.6 is right: identical per-slot protocol at every N, so the mix of
first-in-session and follow-up tasks is the same under the null, and simulation with follow-ups coding 25%
faster gives false alarm 0.018. `tasks_per_session = 4` is a reasonable guess at the compaction point but the
wrong unit (messages, not tasks; see SHOULD-FIX), and the compaction-stall channel is a harness-specific way for
N = 12 to look bent that rule 6 reports but does not remove. Session retirement affects λ symmetrically (one
launch in ~5 hand-outs at every N; ~17 s per relaunch, 3% of a cycle). The one retirement observed in slot mode
went badly (item 4). The task-reuse and fresh-seed rule is sound and simulated.

## 5. Rule 2 and T1b

The cost estimate from T1b is only as good as two whole-dollar readings taken at pre-registered times, and the
plan's own text disagrees with its code about the formula and the error (item 5). T1b's 10-minute twelve-slot
phase gives rule 1 its ~36 follow-ups, but the follow-up statistic itself has no resolution at the current poll
interval (item 1), and 10 minutes stops just short of the first concurrent relaunch (item 3); 15 minutes fixes
both the rehearsal and the `h` measurement for ~$10. The λ floor is fine.

## 6. What must change, in order

Before T1b: items 1-5 above (poll interval and rule 1's pre-registered OCs; plan-usage readings and a headroom
rule; T1b at 30 + 15; case-insensitive branch attribution and the loss-rate sensitivity; one rule-2 formula and a
meter protocol). Before the pre-registration commit: the SHOULD-FIX list, in particular the merged-main log field,
the MDE and CV statement, the prompt/timeout alignment, and the harness configs actually landing. The NOTES are
for the plan's and the paper's wording.
