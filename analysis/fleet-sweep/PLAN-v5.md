# Study 2 plan, version 5: what limits an agent fleet when review is automated?

**Superseded by PLAN-v6** (2026-09-28): T1 showed one serial reviewer binding at N = 12, contradicting this plan's
premise; see PLAN-v6 section 0. Kept for the record; its codings stay behind `--plan v5`.

Supersedes PLAN-v4 (which assumed a reviewer at human pace). Chosen by the owner on 2026-09-28 after
reviewer calibration showed the frozen checkout review job running at 10–30 s per change on default and
high effort (≈ 120–200 reviews per hour) and 14–241 s at max effort (≈ 40 per hour), with 12 of 12
verdicts correct on reference and mechanically broken solutions (calibration/effort-*.jsonl, private).
The design needed ≈ 15 per hour. Once a model does the checking, review stops binding at any fleet size
the budget can reach, so v4's central claim could only have been produced by slowing the reviewer
artificially. v5 studies the regime that actually arises.

Everything built for v4 carries over unless changed here: the sandbox and 220 tasks, per-task cloud
sessions (Haiku 4.5), the verified local reviewer command (checkout job, read-only tools plus pytest),
hidden tests kept local, the serial merge queue, the event log, the per-phase configs and refusals,
and the $250 credit budget with its $50 floor.

## 1. Questions

1. **Scaling.** Does finished work grow in proportion to fleet size, or bend? Rivals, each turned into
   point predictions before the sweep: linear; Amdahl (α only); USL (α, β); Carnot uncapped
   (USL × collision rework, r(N) = 1 − (1 − r₀)(1 − p)^(N−1)). Parameters λ, r₀ and completion come
   from the pilot; α, β and p from the defaults and study 1 unless the design search says otherwise.
2. **Quality.** The escape rate: the share of reviewer-approved changes that fail their hidden tests
   on the approved head. Measured overall (the verifier's ceiling, in Stroebl et al.'s sense) and
   against fleet size, with integration failures (fail only after rebase) reported separately as the
   collision channel.
3. **Collisions.** Rebase conflicts and integration failures against in-flight changes k and
   file-sharing in-flight changes m, per change: study 1's H1/H3 replicated under control, now with
   enough merges to have power.
4. **Where the limit moves.** Utilisation of the reviewer and of the serial merge queue at each size,
   and whether either approaches saturation.
5. **Effort and escapes (free, post hoc).** After the sweep, every submitted change is re-reviewed
   locally at max effort. Escape rate by effort is reported descriptively. It costs plan usage, not
   credits, and does not affect the live runs.

## 2. Reviewer

Natural pace: the verified checkout job at **default effort**, one fresh call per change, never seeing
the queue. Chosen because it is how the tool is used by default; max effort is the post-hoc comparison.
V is measured and reported; it is not a design parameter and there is no reviewer gate.

## 3. To be settled by the design search (free, before any spend)

Fleet sizes (e.g. 1 & 12, or 1, 4 & 12), window length and replicates within the credits; the pilot
(λ, r₀, completion, start-up, merge-queue time), which no longer needs to estimate V and can shrink;
which of questions 1–3 can be graded confirmatory at the achievable power, with operating
characteristics computed for each; and the abort rules (throttling at 12, merge-queue time,
pilot λ floor, credits). The merge queue's serial time (≈ 2 s per change in dry runs) must stay far
below the arrival rate at the largest size.

## 4. Order of work

1. Free: design search for v5 and updated analysis codings; self-tests.
2. Free: review of this plan (Fable).
3. Owner: GitHub access for cloud sessions to tombaldwin/carnot-sandbox; credit meter reading.
4. Paid, tiny: T0 (one session, one task).
5. Paid: T1 (throttling at 12), pilot.
6. Pre-registration commit: pilot measurements, point predictions, codings with operating
   characteristics.
7. Paid: the sweep. Then the free post-hoc effort re-review, analysis, publication whichever way it
   comes out, and a revision-log entry in the paper.

## 5. Design (from the v5 search)

Settled by the free design search (`analysis/design-search/DESIGN-SEARCH-v5.md`; simulation only, the v5 process in
`analysis/synth.py`, the codings in `analysis/v5.py`); operating characteristics in `OPERATING-CHARACTERISTICS-v5.md`.
Where this section differs from sections 1-4 (a pilot, predictions from pilot parameters), it wins.

### 5.1 The design

| | |
|---|---|
| Sizes | N = 1 and N = 12 (N = 8 loses 0.1-0.15 of power; a middle size costs N = 12 hours and adds little) |
| Windows | **twelve at N = 1 and three at N = 12, 90 min each** (10 min warm-up, 10 min grace), order 1, 1, 12, 1, 1, 1, 1, 12, 1, 1, 1, 1, 12, 1, 1 (`synth.v5_order`: N = 12 windows spread evenly, never first or last) |
| Pilot | **none beyond T0 and T1.** No test needs a pilot quantity: SCALE and FAMILY fit each rival's level to the sweep's own windows (its N = 1 windows anchor it), ESC and COLL are within the sweep. Anchoring the rivals on a separate pilot was worse at every pilot size tried (2, 4 or 8 x 60 min). T1 measures throttling, start-up, merge-queue time and a first lambda; the first two N = 1 sweep windows carry the lambda floor (rule 3) before any N = 12 window |
| Harness | the existing phase configs with `window_min = 90` for the sweep phases (120 in the current files) and the T1 schedule of section 4 (1 slot 30 min, then 12 slots 30 min); T2 is not run |
| Session-hours | sweep 12 x 1.5 + 3 x 18 = 72; T1 6.5; T0 about $1 |
| Cost | **$166 at the assumed $2.10 per session-hour**; the full design fits the $200 of spend up to **$2.43** |
| Degrade design | the same schedule **without one N = 12 window** (12 x N = 1, 2 x N = 12; 54 sweep session-hours): $128 at $2.10, **$192 at 1.5x ($3.15)**, fits up to **$3.28** |

### 5.2 Grades and operating characteristics (recommended design; degrade design in brackets)

| Question | Result | Grade | False alarm | Power |
|---|---|---|---|---|
| 1 Scaling | **SCALE**: per-agent finished output falls from N = 1 to 12 (one-sided NB LR, CV 0.3) | **confirmatory, primary** | 0.03 (0.03); 0.11 if the true window CV is 0.5 | Amdahl 0.91 (0.82), USL 1.00, Carnot 1.00; lambda -30%: 0.89 (0.78); mild bend (alpha 0.03) 0.26 |
| 3 Collisions | **COLL**: first merge-queue pass fails by collision more often the more changes merged since the change's base (one-sided LR on j) | **confirmatory, secondary** | 0.02-0.03 | USL-like workers p = 0.005 / 0.01 / 0.02 / 0.05: 0.28 / 0.52 / 0.83 / 0.98 (0.21 / 0.42 / 0.72 / 0.95); linear workers p = 0.005: 0.83 (0.72) |
| 2 Quality | ESC-N: escapes rise with N | descriptive | 0.05-0.06 | 0.10 -> 0.20: 0.78-0.86 (0.69-0.83); 0.10 -> 0.15: 0.37-0.42 |
| 2 Quality | ESC: the escape rate | descriptive (estimate) | | 95% half-width about 0.04 (USL-like workers, about 240 approvals) to 0.03 (linear, about 500) |
| 1 Which rival | FAMILY: four-way pick with free levels | descriptive | | correct: linear 0.97, Amdahl 0.84, USL 0.43, Carnot 0.35 (USL and Carnot 5% apart at N = 12; three-way 0.77-0.78) |
| 4 Limits | UTIL: reviewer and merge-queue utilisation | descriptive | | reviewer busiest N = 12 window: 0.26 (USL) to 0.79 (linear; saturates with 20-40 s reviews); merge queue <= 0.06 |
| 3 | COLL-m (H3, file-sharing merges), COLL-k (study 1's k) | descriptive | | |
| | LAMBDA, BOUNCE, EFFORT (post-hoc max-effort re-review) | descriptive | | |

A NOT-DETECTED COLL rules out p of about 0.02 or more per merged change (0.005 or more under linear workers), not
p = 0.005. SCALE is repeated without windows that ran out of tasks before minute 80 of a 90-min window (SCALE-nf). The
CV-estimated and Welch versions of SCALE and the observed window-to-window CV of the N = 1 windows are reported beside
it.

### 5.3 Budget and abort rules

1. **Throttling (T1, unchanged: PLAN-v4 section 7.5).** Twelve-slot activity per slot-minute below 0.8 x the one-slot
   rate: stop; report T1.
2. **Credits.** Burn = (M0 - M1) / T1 session-hours (T0's reading may be pooled). Run the full design if the
   predicted balance after it is >= $50 (burn <= $2.43 from $250 less T0 and T1); else the degrade design if that is
   (burn <= $3.28); else stop and report T0/T1. Before every sweep window, the predicted balance after the rest of the
   chosen design must stay >= $50; if it does not, drop the remaining N = 12 window(s) first, then stop. An interrupted
   sequence is analysed as run (every v5 test is defined for >= 1 window per size); an N = 12 window is never run
   without at least two N = 1 windows before it.
3. **Lambda floor.** The sweep starts with two N = 1 windows. Pooled lambda there below 3 first submissions per
   slot-hour (half the dry runs' 6): stop before the first N = 12 window and report (the operating characteristics
   were computed down to lambda -30%, about 4.4).
4. **Review must not bind.** An N = 12 window with the reviewer busy >= 80% of its counted time, or a mean review above
   60 s, is flagged in UTIL, and SCALE is reported with and without the flagged windows (not a stop; the simulated
   false alarm stays at 0.03 even with a saturated reviewer).
5. **Merge queue.** Mean merge-queue time per change above 30 s (dry runs: about 2 s): stop and fix the harness before
   the next window.

`predict.py` (default `--plan v5`) prints the design, each rival's ratio to N = 1, illustrative counts and loads, the
budget with the degrade rule, these abort rules and the operating characteristics; `score.py` (default `--plan v5`)
codes the results with these grades. The v4 codings stay behind `--plan v4`.
