# Study 2 plan, version 5: what limits an agent fleet when review is automated?

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
