# Study 2 plan, version 4: is review the binding limit?

Supersedes PLAN-v3.md. The design comes from a simulation search over worker model, task size,
pilot, reviewer pace, fleet sizes and windows (analysis/design-search/DESIGN-SEARCH.md). The search
found that **no design within the budget reaches every target**: with realistic run-to-run noise,
the rivals USL and Amdahl cannot be told apart at affordable fleet sizes. What *can* be answered
reliably is whether output is capped by review, which is the paper's headline claim. This version is
built around that, and says in advance which results are confirmatory and which are descriptive.

Everything in PLAN-v3 carries over unless changed here: the git-only worker protocol, the local
single-change reviewer, hidden tests kept local, the accounting definitions (§6), exclusions (§8)
and the event-log schema (SCHEMA.md).

## 1. Claims, graded in advance

> **Superseded in part by section 7 (v4.2).** The grading below is reframed there: O2 is the primary
> quantitative confirmatory test, P1 a confirmatory manipulation check, S3 and O3 descriptive. The operating
> characteristics quoted below (0.87 under USL truth, 0.70 at λ −30%) are superseded by
> OPERATING-CHARACTERISTICS.md (0.80; 0.66); only the OPERATING-CHARACTERISTICS.md figures are pre-registered.

**Confirmatory, primary.** *Review is the binding limit.* Carnot's review-capped prediction has a
higher likelihood than the best of the uncapped rivals (USL, Amdahl, linear), with the likelihood
ratio reported. Simulated operating characteristics at the design point: correct 0.97 under Carnot
truth; 0.87 / 1.00 / 1.00 under USL / Amdahl / linear truth; 0.70 if agents are 30% slower than
assumed. These numbers go into the pre-registration.

**Confirmatory, secondary** (point predictions that are not identities of the harness):
- O2: finished work at N = 12 lies inside Carnot's 95% predictive interval.
- S3: the share of reviews that bounce, b, does not rise with fleet size (Fisher exact).
- O3: attempts rise with fleet size (exact rate-ratio test).

**Conditionally confirmatory.** *The reviewer's pace does not change with load.* V(12)/V(1) with its
interval, called stable if the interval lies inside [0.8, 1.25], and a Welch test on log review
durations. Confirmatory only if the pilot shows review-time CV ≤ 0.5, decided and recorded before
the first sweep window; otherwise descriptive.

**Descriptive** (reported whatever they show, with their simulated power stated in advance so a null
result is not read as evidence):
- the four-way ranking of rivals with the simulated confusion matrix (USL vs Amdahl is not claimed);
- escaped defects against reviewer queue depth (power ≈ 0.10);
- collisions against in-flight changes (power 0.09 at p = 0.01, 0.34 at p = 0.05);
- S1r/S2r surprise ratios (S2r fires about half the time under the model's own truth at N = 12, so it
  is not a criterion).

**Circularity, stated.** At the design point the reviewer is 92% loaded with one agent and about 3.2×
overloaded with twelve. A flat finished count at N = 12 is therefore expected by construction and is
not itself evidence; the claims that are not identities are listed above.

## 2. Design

| Item | Value |
|---|---|
| Workers | Claude Code cloud sessions, **Haiku 4.5**, identical prompt |
| Task size target | ≈ 8 first attempts per agent-hour (≈ 7.5 min of agent work). The 120 validated tasks (3–147 app lines, median ≈ 45) are used as they are; the pilot measures λ |
| Reviewer | one serial local reviewer (Opus 5.5, fresh call per change, never sees the queue), with the job defined so that V ≈ 2 × pilot λ (q = 2). Calibrated offline before the pilot, adjusted only before the sweep |
| Fleet sizes | **N = 1 and N = 12**, fixed in advance. No pilot gate |
| Windows | 3 per size, 120 min each (10 min warm-up, 10 min grace), order ABBAAB |
| T1 | 1 worker × 30 min, then 11 more × 15 min (12 at once): concurrency and throttling. *v4.2 (§7.5): 12 at once for 30 min* |
| T2 pilot | 8 separate 60-min windows of 1 worker, on different days / plan windows |
| Calibration | 120 local reviews (pilot PRs re-reviewed, reference and deliberately broken solutions), no credits |
| Merge queue | serial time ≤ 0.75 min per change |
| Analysis | as adopted: `completion` reading, S1r/S2r, V ratio + Vdur, `logit_cluster_task`; see analysis/README.md |

## 3. Budget

| Step | Session-hours | At $2.10 (Haiku, assumed) |
|---|---|---|
| T1 | 3.25 | $7 |
| T2 | 8 | $17 |
| Sweep | 3 × (1 + 12) × 2 = 78 | $164 |
| **Total** | 89.25 | **$187** (v4.2: 92.5 h, $194, §7.2) |

The credit meter is read by the user before and after T1 and after T2. The real burn per
session-hour replaces the assumption; each sweep window starts only if the predicted balance after it
stays above $50. At 1.5× burn the pre-set degrade rule shortens the sweep windows (simulated: $199,
primary test 0.85).

## 4. Abort rules (checked in order; any failure stops the study before sweep spend)

1. T1: 12 concurrent sessions run, and per-worker tokens/hour at 12 is no more than 20% below 1.
   *(v4.2: measured as activity per slot-minute, §7.5.)*
2. T1/T2: merge-queue serial time ≤ 0.75 min per change.
3. T2: pilot λ ≥ 0.5 × target (4 per agent-hour).
4. Calibration: V within ±30% of 2 × pilot λ, else redefine the review job and recalibrate.
   *(v4.2: the consequence of a failure is replaced by §7.3.)*
5. Credits: the predicted balance after the sweep ≥ $50.

If rule 3 fails, the study is reported as a pilot with its measurements and no sweep. If λ is low but
above the floor, the design's own simulation says the primary test degrades to about 0.70; that
figure is stated in the pre-registration before the sweep.

## 5. Order of work

1. Free: full dry run on the real sandbox and tasks with simulated workers and reviewer; measure
   merge-queue time; run derive/predict/score on the dry-run log.
2. Free: reviewer calibration on reference and broken solutions.
3. Final review of this plan (Fable), then the user's go-ahead.
4. Needs the user: a private GitHub repo for the sandbox, and meter readings.
5. Paid: T1, then T2 (abort rules 1–3).
6. Pre-registration committed and pushed: pilot measurements, calibrated V, point predictions, the
   confirmatory/descriptive split with operating characteristics.
7. Paid: the sweep.

## 6. Amendments before pre-registration (v4.1)

Found by aligning the analysis code with this plan and running it on the dry-run logs
(analysis/README.md decisions 19–29). Settled here, before any hash is taken.

1. **Reviewer pace.** The ratio V(12)/V(1) cannot be shown "stable" inside [0.8, 1.25] at this design:
   with about 40 and 75 reviews its 95% interval is roughly 0.7–1.45. So the conditional-confirmatory
   test is the Welch test on log review durations, N = 1 against N = 12, which **fails if p < 0.05
   (two-sided)**. It is confirmatory only if the pilot's live review-time CV is at most 0.5, and
   descriptive otherwise. The ratio is reported with its interval as descriptive; no equivalence claim
   is made.
2. **Review-time CV** comes from the live T1 and T2 reviews only. Near the 0.5 threshold the decision is
   close to a coin toss, and the pre-registration says so.
3. **Calibration reviews** set the reviewer's job (abort rule 4, from a simple log of review durations).
   They are not pooled into the pilot's V, b or CV, which come from live reviews only.
4. **O2's false-alarm rate** under the model's own truth is about 0.13, not 0.05, at review-time CV 1.
   Stated in the pre-registration.
5. **S3** is on review bounces only (b_review). Rebase conflicts, visible fails, escaped defects and
   integration failures are reported separately by fleet size.
6. **Task supply.** 220 tasks. A window that runs out of tasks before minute 110 is flagged; P1 and O2
   are reported with and without flagged windows, and attempt-based measures stop at the minute the
   tasks ran out.
7. **Completion share** is measured in the eight 60-minute pilot windows and applied to 120-minute
   sweep windows. Kept, because many short pilot windows anchor the rivals better; stated as a
   limitation.
8. **Operating characteristics** are recomputed with 220 tasks, the grace-end fix and live-only pilot
   data, and those figures, not the design-search ones, go into the pre-registration.

## 7. v4.2: framing and pre-spend fixes

Settled after the final pre-spend review (REVIEW-fable-final.md, verdict "go with changes") and before
any hash is taken. Sections 1-6 are left as written; where this section differs, it wins. The code
changes are in `analysis/{common,score,predict,selftest}.py` and the harness (`review.py`,
`calibrate.py`, `throttle.py`, `config.py`, `cli.py`, the four phase configs, `prompts/reviewer.md`).

### 7.1 Framing: a measurement and calibration study (owner's choice "A")

The real reviewer is a fresh call per change that never sees the queue: a fixed-capacity server. Abort
rule 4 sets its capacity near 2λ, so at N = 12 review demand is about three times V. Output at N = 12
is therefore capped by construction, and P1 can fail only through a harness or calibration fault (for
example a calibrated V above the N = 12 demand). The study is graded accordingly:

| Result | Grade (v4.2) | Was (v4.1) |
|---|---|---|
| **O2**: finished at N = 12 inside Carnot's 95% predictive interval, (1 − b_review)(1 − b_hidden(12))(1 − b_other) × V × hours, with V and b from the pilot at N = 1 (and O2-nf beside it) | **confirmatory, primary quantitative test** | confirmatory, secondary |
| **P1**: Carnot's capped prediction has a higher likelihood than the best uncapped rival (coded exactly as before; likelihood ratio reported; and P1-nf) | **confirmatory manipulation check**: a PASS says the harness behaved as designed; a FAIL indicates a harness or calibration fault, not support for a rival | confirmatory, primary |
| **Vdur** (Welch test on log review durations) | conditionally confirmatory (unchanged, §6.1) | same |
| S3 (b_review flat), O3 (attempts rise) | **descriptive** (still coded with their rules) | confirmatory, secondary |
| Vratio, S1r, S2r, RANK, ESC, COLL, BOUNCE, α/β fit | descriptive (unchanged) | same |

Operating characteristics: only the figures in OPERATING-CHARACTERISTICS.md are pre-registered. §1's
0.87 (P1 under USL truth) and 0.70 (at λ −30%) are superseded by 0.80 and 0.66. O2: false alarm 0.10 at
review-time CV 1, 0.04 at CV 0.5; it fails under USL / Amdahl / linear truth 0.63 / 0.99 / 1.00 of the time.

**Footnote to every uncapped-truth figure** (P1 under USL / Amdahl / linear, O2's power): those rows come
from a synthetic reviewer that speeds up with queue depth (`synth.py`, `reviewer_load = 2`). The real
harness cannot produce such a reviewer, so those rows describe a counterfactual, not a result this study
can observe. The one real channel left, rate limits shared with the workers, would make the reviewer
slower at N = 12, not faster, and so cannot produce a P1 FAIL either.

Wording after the study: "in a fleet whose only review stage is a fixed-capacity serial reviewer,
finished output followed (1 − b) × V × hours to within …", never "review was shown to be the binding
limit". What the study buys beyond O2: the first measurements, with real coding agents on a real
codebase, of λ, V, b, r₀ and c for Haiku, and of what a threefold-overloaded review stage does to a
fleet (censored pile-up, k at first submit, rebase conflicts and integration failures by size, escaped
defects against depth, rework time), all descriptive.

In code: `score.py` orders results O2, O2-nf, P1, P1-nf, Vdur, then the descriptive ones
(`V4_ORDER`), gives O2 the role `primary` and P1 the role `manipulation check`, and prints the footnote
beside P1; `predict.py`'s operating-characteristics statement says the same. The self-test checks the
grades, order, roles and footnote.

### 7.2 Credits: the degrade design is a pre-registered alternative

Session-hours (T1 lengthened, §7.5): T1 1 × 60 min + 11 × 30 min = 6.5; T2 8; full sweep 78; degrade
sweep 52 (N = 1 and 12, **two 120-min windows per size, order ABBA**, 10 min warm-up and grace).

| Design | Session-h incl. pilot | Fits the $200 of spend ($250 − $50 floor) up to a burn of | At $2.10 | At 1.5× ($3.15) |
|---|---|---|---|---|
| Full (3 × 120 per size, ABBAAB) | 92.5 | $2.16 / session-h | $194 | $291 (does not fit) |
| **Degrade (2 × 120 per size, ABBA)** | 66.5 | $3.01 / session-h | $140 | $209 (does not fit; $199 with v4's 3.25-h T1) |

Rule, applied at the M1 reading after T1 (burn = (M0 − M1) / T1 session-hours) and again after T2: run
the full design if the predicted balance after it stays ≥ $50; otherwise the degrade design if it
does; otherwise stop and report the pilot. The degrade design's operating characteristics (OC.md, 1.5×
burn row): P1 correct 0.95 under Carnot truth, 0.75 under USL truth (footnote applies); O2 is coded the
same way on the two N = 12 windows. With the longer T1 the 1.5×-burn case itself no longer fits the
degrade design (it needs burn ≤ $3.01, i.e. ≤ 1.43×); the review's $3.40 stop threshold is replaced by
$3.01. The full design's margin over the assumed $2.10 is now 3%, so the degrade design is the more
likely outcome.

Per-window check as well (should-fix): before every sweep window, the predicted balance after the rest
of the chosen design must stay ≥ $50. An interrupted sequence yields: after the first four windows
(ABBA), the degrade design; after fewer, descriptive results only. Never a fourth N = 12 window without
its N = 1 pair.

### 7.3 The review job, frozen before T1

**Job.** For each change, the reviewer gets a fresh temporary checkout of the exact submitted head (an
export of that commit's tree: no `.git`, no other refs, and `.claude/` and `CLAUDE.md` removed so a
change cannot alter the reviewer's settings or instructions; the sandbox's own `.claude/settings.json`
allows `Bash(git *)`), the task text and acceptance criteria, the list of changed files and the diff
against the merge base. It may read and search files and run the visible test suite in that checkout,
nothing else (tool allow-list: `Read`, `Grep`, `Glob`, `Bash(<sandbox python> -m pytest:*)`). It answers
APPROVE or REQUEST_CHANGES with a reason. It never sees the queue, other changes or hidden tests. The
prompt (`harness/prompts/reviewer.md`) is the same for every review. The harness's own visible-test
result is no longer in the packet: the reviewer runs the tests itself.

**Harness.** `[reviewer] job = "checkout"` (default). The command template takes `{model}`,
`{prompt_file}`, `{workdir}`, `{checkout}`, `{allowed_tools}` and `{python}`; `cwd = "{checkout}"`; the
prompt goes in on stdin. The diff-only job stays behind `job = "diff"` (with `prompts/reviewer-diff.md`),
marked superseded; `run` refuses it. The call's duration includes exporting the checkout, live and in
calibration alike.

**Command, still UNVERIFIED** (review must-fix 4, a separate step on the paying account):
`claude -p --model {model} --output-format json --allowedTools {allowed_tools} --no-session-persistence`,
prompt on stdin, in the checkout. To confirm and record before commit A: the Opus 5.5 model id; that
`-p` bills the plan, not the credits; the allow-list syntax and that nothing outside it runs (no edits,
no web, no MCP servers, no user-level settings or hooks loaded; add the CLI's flags for that if they
exist); no session persistence; the JSON fields (`result`, `usage.*`); the exit code on a rate limit;
and whether cloud-session usage counts against the plan's limits. Then `verified = true`.

**Calibration through the same path.** `python -m harness calibrate --config config.t2.toml --tasks
<list> --variants reference,mutant --out <calibration.jsonl> --job <label>` runs the same
`CommandReviewer` (same template, prompt, packet builder and checkout job) on reference solutions
(expected APPROVE) and mechanically broken variants (expected REQUEST_CHANGES: one comparison or
boolean inverted, one `return` made `return None`, one hunk or one new file dropped; a mutant is kept
only if it applies and the task's hidden tests fail on it). Mutants are written in the private tasks
repo's `dryrun/` area; the command refuses a path inside this repo. It appends the calibration-review log
that `predict.py --calibration` reads for abort rule 4. `--parallel k` is allowed for calibration only,
because V from calibration uses call durations and verdicts, never queueing.

**When.** Calibrate the frozen job before T1 against the design λ (6.8-8 per agent-hour, so a target
V of 13.6-16 reviews per busy hour, a mean review of about 225-265 s), using the dry run's λ as a check.
If that pre-T1 V is outside ±30% of the target, the owner may amend the job **before T1** under a new
job label (recorded here before commit A). From the start of T1 the job is fixed.

**Rule-4 consequence** (replaces §4's "redefine the review job and recalibrate"). If rule 4 fails after
T2 (calibrated V outside ±30% of 2 × pilot λ), exactly one of: (a) accept the measured q and re-run
`design-search/oc_v41.py` at that q for the pre-registration (no new spend), or (b) redefine the job
**and re-run the pilot** (about $31 at $2.10). Redefining the job, recalibrating offline and keeping the
old pilot is not allowed: the pilot's V, b and review-time CV would then describe a reviewer that no
longer exists. `predict.py` prints this rule when rule 4 fails.

### 7.4 One config per phase

`harness/config.toml` and its PLAN-v3 defaults (3 workers, 90 min, Sonnet, kind sweep, base_ref
`study2-base`) are removed. Four files differ only in `[run]`:

| File | phase | kind | N | window | start schedule |
|---|---|---|---|---|---|
| `config.t1.toml` | t1 | trial | 12 | 60 min | 1 worker at minute 0, all 12 from minute 30 |
| `config.t2.toml` | t2 | pilot | 1 | 60 min | all at 0 |
| `config.sweep-n1.toml` | sweep-n1 | sweep | 1 | 120 min | all at 0 |
| `config.sweep-n12.toml` | sweep-n12 | sweep | 12 | 120 min | all at 0 |

All: warm-up 10, grace 10, worker model Haiku 4.5 (`claude-haiku-4-5`), reviewer Opus 5.5
(`claude-opus-5-5`; both id strings to be confirmed by the CLI check), `base_ref = "sandbox-v1"`, the
remote URL a placeholder, the checkout review job. `run` refuses a config whose kind, N, window, warm-up,
grace, start schedule, models or review job differ from its named phase (`config.PHASES`), prints kind /
N / window / models / base_ref, and starts only when the operator types the phase name (or passes
`--yes`). There is no default real config: every real command needs `--config`.

### 7.5 T1 and abort rule 1 without a token source

The harness logs no token usage and no product source is known. **Measure** (`python -m harness throttle
runs/<T1>`): per-slot activity = sessions launched after a slot's first + READY submissions, per
slot-minute, in T1's one-slot phase (from the first slot's start + 5 min to the moment a second slot
starts) against its twelve-slot phase (from then, or each slot's start + 5 min, to window end); time a
slot is down (`worker_down` to `worker_restart`) is excluded. **Pre-registered rule:** throttled if the
twelve-slot rate is more than 20% below the one-slot rate (ratio < 0.8). Reported beside it, not part of
the rule: the median minutes from a task's claim to its first READY in each phase, an approximate 95%
interval for the ratio, and the operator's readings. If a product token source turns up at T1 and is
logged as `usage` events, tokens per slot-minute replace activity, with the same threshold.

The honest limit: the one-slot phase is about 25 slot-minutes, a handful of READYs at λ ≈ 7, so the
ratio's interval is roughly a factor of three either way. The rule can catch gross throttling (sessions
that stall), not a 20% drop. It is kept because nothing better exists without a token source.

**Operator readings.** Before and after each phase, and at minutes 25, 35 and 55 of T1: `python -m
harness log --run-id <T1> --type note --field text="plan_usage pct=<plan usage %> src=<where read>"`;
the credit meter with `--type meter` before T1 (M0) and after it (M1).

**T1 procedure, as the harness runs it.** `python -m harness reset --config config.t1.toml --run-id
<T1> --seed <s>`, then `python -m harness run --config config.t1.toml --run-id <T1> --meter-start <M0>`.
With `start_schedule = [[0, 1], [30, 12]]` the harness asks for w1's session at minute 0 and, from a
launcher thread, for w2-w12 at minute 30; the window's own timing (warm-up end, window end) is never
held up by the operator typing session ids. The twelve-slot phase is 30 min (the review's should-fix,
in place of 15), so T1 is 6.5 session-hours (§7.2). The operating characteristics were simulated with the
15-min version and are not re-run: the longer phase only adds live T1 reviews to the pilot's V, b and
review-time CV (λ comes from T2 only), which can only narrow the pilot's error.

### 7.6 Completion share: direction of the bias

c is measured in the 60-min pilot windows and applied to 120-min sweep windows (§6.7). Censoring at
the window end is heavier in a 60-min window, so c is biased low. Expected consequence, stated in
advance: every prediction that uses c is low: all four rivals at N = 1 (the N = 1 sweep windows are
expected to run above every prediction) and the uncapped rivals at N = 12 (which moves them towards the
cap, against a P1 PASS). Carnot's capped branch at N = 12, and so O2, does not use c. `predict.py`
prints this.

### 7.7 Conflict census on the 220 tasks

`pair_conflicts.py` re-run on all 220 tasks (textual pairs and five cumulative orders; the semantic
pair pass was not re-run): 298 of 24,090 pairs conflict textually (1.2%), and 97 of 220 tasks (44%)
conflict with at least one other (120-task set: 297 pairs, 95 of 120 tasks). In random merge orders
67-68 of the 220 tasks cannot be merged unchanged after the others (31%), and every cumulative tree
passed the visible and all merged hidden tests. The harness README's "95 of the 120" is updated.

### 7.8 Worker side: not frozen here

The review's must-fix 5 (the worker prompt must say never stop, never ask, give a per-task time
budget, and settle the start-up claim race) is **not** done in v4.2. Product facts (a cloud session can
push only to its own working branch; `claude --cloud` does not return immediately; idle timeout
undocumented) mean the worker model is being redesigned as one cloud session per task, with rework sent
as a follow-up message to that session. That design, its prompt and its harness changes are specified
separately and must be settled before T1. `prompts/worker.md` is unchanged and is not part of commit A
until then.

### 7.9 Hygiene

References to the PLAN-v3 gate, Sonnet, `config.toml` and "PLAN-v3 §10" removed from the harness README,
configs, `SCHEMA.md` and the refusal messages; the `RunCfg` / `RepoCfg` defaults are PLAN-v4's (120 min,
Haiku, 1 worker, `sandbox-v1`).
