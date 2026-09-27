# Final pre-spend review of study 2 (PLAN-v4 as amended by §6, "v4.1")

Reviewer: Claude (Fable 5.1), 2026-09-27. Read: PLAN-v4.md (with §6), PLAN-v3.md (inherited protocol),
OPERATING-CHARACTERISTICS.md, DRYRUN-REPORT.md (incl. §7 rerun), analysis/design-search/DESIGN-SEARCH.md,
analysis/README.md, harness/README.md, SCHEMA.md, the two earlier reviews, analysis/aidev/RESULTS.md, model.md,
the tasks repo's TASK-FORMAT.md and validate.py only. Code read: analysis/{common,derive,predict,score}.py (the
parts that fix the design constants, pilot pooling, codings and abort rules), harness/{orchestrator,launchers,
review,config,cli,reset}.py, both prompts, both configs. Run: `selftest.py --quick` (all unit checks pass, 29 s;
its output files were restored to the committed 300-replicate version afterwards) and the harness suite
(40 passed, 84 s). No network, no `claude`, no API, no cloud session.

## Verdict: GO WITH CHANGES

None of the changes needs a new simulation or a new dry run. They are wording in the pre-registration, three
config/runbook fixes that would otherwise void or mis-key paid windows, and one gap in the abort logic
(the review job may be redefined after the pilot that measured it). With those made, the paid steps can start.

### Status of the earlier must-fix items

| Source | Item | Status in v4.1 |
|---|---|---|
| Round 1, 1-2 | No α/β fit; no imposed token bucket; point predictions | Fixed (likelihood over four rivals; V measured, not imposed) |
| Round 1, 3-5 | In-flight accounting, k/m logged, merge queue measured, rework definitions | Fixed (SCHEMA.md; derive.py; merge queue 0.04 min vs 0.75 limit) |
| Round 1, 6-7 | Seeded order per window; reconcile with study 1's p | Fixed (reset.py; p = 0.005 carried) |
| Round 1, 8 | Verify product facts before spending | **Still open by design**: launch syntax, `claude -p` flags, metering, concurrency, unattended operation, Haiku availability are all UNVERIFIED and gated to T1/T2. See must-fix 3-5 for the two that can void windows after spend |
| Round 2, A | Gate on q | Superseded, correctly: the design search shows the gate passes 33-55% of the time; fixed sizes with a calibrated V do better. Rule 4 replaces it |
| Round 2, B | Break the circularity; pre-register surprises | **Partly.** The circularity is stated (PLAN-v4 §1). But P1 is still labelled the primary confirmatory claim, and the OC table's rows for uncapped truths describe a counterfactual the harness excludes. See §1 below |
| Round 2, C | Queue delay not scored as rework; V on busy-hour basis; escaped vs integration split | Fixed (censored category; V busy-hour with grace-end fix; both causes logged and separated) |
| Round 2, D | One-PR-at-a-time reviewer, fresh call, failure rule | Fixed (CommandReviewer per call; 10-min downtime void) |
| Round 2, E | Pilot estimates V | Fixed and improved (T1 PRs reviewed; eight T2 windows; ~66 live reviews) |
| Round 2, F | Budget from the $50 floor; concurrency at peak | Fixed (rule 5; T1 at 12) |
| Round 2, G | Likelihood criterion; ≥ 8 escape events | Fixed |
| DRYRUN §6, 1-5 | Supply, S3 on b_review, v4 alignment, grace-end bias, worker prompt on conflicts | Fixed (220 tasks + minute-110 flag; decisions 19-33; prompt paragraph) |
| DRYRUN §6, 6 | Real config and `validate-tasks` against GitHub before T1 | **Open**, and `config.toml` is stale (must-fix 1) |

## 1. The primary claim: is it true by construction, and is the study worth $187?

**Yes, P1 is true by construction in the real harness, and the pre-registration should say so more plainly
than PLAN-v4 §1 does.** Three facts together make it so:

1. The real reviewer is a fresh `claude -p` per change that never sees the queue. It is a fixed-capacity
   server. Abort rule 4 forces its capacity to V ∈ [0.7, 1.3] × 2λ, so review demand at N = 12 is at least
   λ·X(12)/(1 − b) ≈ 8.4λ/(1 − b) against a V of at most 2.6λ: overloaded by a factor of about 3 even at the
   loosest calibration. Finished(12) ≤ (approval share) × V × hours whatever the workers do.
2. Under the `completion` reading, Carnot and USL coincide at N = 1, so the N = 1 windows cannot separate them.
   The whole contrast is at N = 12, where USL predicts c·λ·X(12)·hours ≈ 4× the cap. The NB likelihood at that
   distance is not close.
3. The simulated "P1 correct 0.80 under USL truth" (and 1.00 under Amdahl and linear) is computed with a
   synthetic reviewer that speeds up with queue depth (`synth.py`: `FAST = dict(reviewer_load=2.0)`, i.e. rate
   = V0 × (1 + 2 × depth)). That is the only way an uncapped truth can produce data in this harness, and the
   harness excludes it: a reviewer handed one diff cannot know the queue is deep. The one real channel left
   is rate limiting shared with the workers, which would make the reviewer *slower* at N = 12, not faster,
   and so cannot produce a P1 FAIL either.

So P1 fails only if (a) rule 4 was violated (a tie when V exceeds demand at N = 12), or (b) a harness fault.
A P1 PASS is therefore a manipulation check that the harness behaved, not evidence that "review is the
binding limit" for fleets in general. The design search and PLAN-v4 already reach that conclusion for the
flat-finished-count observation; they should apply it to P1 itself.

**What is not an identity, and is worth buying:**

- **O2**: finished at N = 12 inside the interval of (1 − b_review)(1 − b_hidden(12))(1 − b_other) × V × hours,
  with V, b from the pilot at N = 1. This is a quantitative prediction of the paper's rule of thumb
  ("finished ≈ (1 − b) × cap") and it can fail in several real ways: b rising at N = 12 (reviewer stricter,
  or workers submitting worse changes under conflict churn), re-review loops eating capacity, rebase-conflict
  loops (95 of 120 original tasks conflict with another; the count for 220 is not reported and should be),
  integration failures from agents' own resolutions (the dry run's T108/T019 case), reviewer pace drift.
  Simulated false alarm 0.10 (review-time CV 1). This should be the headline confirmatory result.
- **Vdur / Vratio**: whether V is a property of the reviewer or of the load. Weak (power 0.18 at CV 1, 0.73 at
  CV 0.5, and `review_cv_ok` is a coin toss at CV 0.5), honestly graded conditional/descriptive. Fine.
- **S3** (b_review flat), **O3** (attempts rise; near-certain under every truth, so uninformative but cheap).
- **Descriptive but new**: what a 3× overloaded review stage does to a fleet in practice: censored pile-up,
  k at first submit (dry run: mean 55 at N = 12), rebase conflicts and integration failures by size, escaped
  defects against depth, worker time spent on rework. No published number exists for any of these with
  real coding agents. Plus real measurements of λ, V, b, r₀, c for Haiku on a real codebase, which the paper's
  calibration currently has to guess.

**Is that worth $187 of credits that cannot be spent on anything else?** Yes, provided the pre-registration
is reframed: (i) P1 stays coded exactly as it is (the likelihood ratio is still the right descriptive of how
far the data sit from the uncapped curves), but is labelled "confirmatory manipulation check: the harness's
fixed-capacity reviewer caps output; a FAIL indicates a harness or calibration fault, not support for a
rival", and the OC table's uncapped-truth rows carry the footnote that they assume a reviewer that speeds
up with queue depth, which the harness excludes; (ii) O2 is named the primary quantitative test of the model;
(iii) the paper's wording after the study says "in a fleet whose only review stage is a fixed-capacity
serial reviewer, finished output followed (1 − b) × V × hours to within …" and does not say "review was
shown to be the binding limit". If the owner is not willing to publish it that way, do not run: the
$187 would buy a confirmation of an accounting identity.

The $187 figure itself assumes $2.10 per session-hour for Haiku, which nobody has measured. T1's meter
delta is the first real number; rule 5 is coded (`predict.py --burn --balance`), and at the plan's own
arithmetic the full sweep needs burn ≤ ($250 − ~$24 pilot − $50) / 78 ≈ $2.26. That is a 7% margin over the
assumption. The degrade design (2 × 120 min per size, $199 at 1.5× burn, P1 0.95 / O2 unchanged in kind) is
the likelier outcome and should be written up as a first-class alternative, not a fallback footnote.

## 2. What would waste the paid runs

### Must-fix (before T1)

1. **`harness/config.toml` and the `RunCfg` defaults are PLAN-v3 values.** `n_workers = 3` with the comment
   "set per window from the pilot gate", `window_min = 90`, `worker_model = "claude-sonnet-5"`,
   `kind = "sweep"`. PLAN-v4 is Haiku 4.5, 120 min, N ∈ {1, 12}, and T1/T2 must be `kind = "trial"` /
   `"pilot"`: `derive.py --pilot` refuses any other kind, and `run.json` is written once and is part of the
   pre-registered record, so a T2 window run with `kind = "sweep"` is a $2 window that cannot enter the pilot
   without editing a pre-registered log. Ship three configs (`config.T1.toml`: trial, 12 workers, 45 min;
   `config.T2.toml`: pilot, 1 worker, 60 min; `config.sweep.toml`: 120 min, `n_workers` per window) or one
   config with the values documented per phase in the runbook, and make the harness print `kind`,
   `n_workers`, `window_min`, `worker_model` at `run` start for the operator to confirm. Also
   `[repo] base_ref = "study2-base"` while the sandbox tag is `sandbox-v1`: create the tag or change the
   value, and check it with `reset` against the GitHub remote before T1.

2. **Abort rule 1 (throttling) has no measurement path.** The harness never emits `usage` (only the
   reviewer's tokens, and only with `output_format = "json"`). `python -m harness log --type usage` does
   accept the event, but the plan does not say where per-worker token counts come from in a cloud session,
   nor how often. With only 15 minutes at N = 12, an attempts-based proxy cannot detect a 20% change. Before
   T1, pre-register: the exact source of per-session tokens (the session's usage display, `/usage`, or the
   account usage page), the reading cadence (at minute 30 and 45 of T1, per worker), the comparison
   (tokens per worker-minute at 12 vs the single worker's minutes 10-30), and a fallback if tokens are not
   exposed (e.g. tool calls per minute from the session transcript, or wall-clock time from claim to first
   `READY:` for the first task, 12 vs 1). Also write the T1 procedure for the harness as coded: `run_window`
   starts all workers at window start via `launcher.start_all`, so for "1 worker × 30 min then 11 more" the
   operator types w1's session id at minute 0 and holds w2-w12 until minute 30 (the `input()` blocks only the
   main thread; the watcher, prep, review and merge threads run). Say so in the runbook, or T1 will be run
   as 12 from the start.

3. **The review job may be redefined after the pilot that measured it.** Rule 4 (calibrated V within ±30% of
   2 × pilot λ, else "redefine the review job and recalibrate") is evaluated after T2. The pilot's V, b,
   review-time CV and `review_cv_ok`, which fix the pre-registration, come from the live T1 + T2 reviews done
   under the *old* job. If the job changes, those numbers describe a reviewer that no longer exists, and the
   sweep's reviewer is uncalibrated against the live pilot. Fix: (a) calibrate the job before T1 against
   the design λ (6.8-8/h, i.e. target V ≈ 13.6-16/h) using the dry-run λ as a check; (b) freeze the job
   (prompt template + `claude -p` flags) before T1; (c) if rule 4 still fails after T2, the choices are to
   accept the measured q and re-run `oc_v41.py` at that q for the pre-registration (no new spend), or to
   redefine the job **and re-run the pilot** ($24). Write that rule down; do not allow "redefine, recalibrate
   offline, keep the old pilot".

   Related: the calibration reviews must go through the harness's `CommandReviewer` (same command, same
   `prompts/reviewer.md`, same packet layout, `cwd` an empty temp dir), otherwise the calibrated V is not the
   live V. There is no harness entry point for this. Add a tiny one (`python -m harness review-one --task
   T042 --diff ref.patch`) or a documented shell loop that renders the template the same way, and log to the
   calibration JSONL with `job` set. Expect the diff-only job to be *too fast*: Opus reading a 45-line diff
   with the task text will likely answer in 1-2 min, giving V of 30-60/h, q of 4-9, and no cap at N = 12
   (a P1 tie). The plan's heavier-job options (run the visible tests, check each criterion) need a checkout,
   which the packet does not give; decide this before T1, because the job is part of the pre-registration.

4. **Verify the reviewer command locally before any credit is spent, and pin its flags.** The template
   `sh -c "claude -p --model {model} < {prompt_file}"` runs in an empty temp dir with default tools. Check
   on the paying account: the model id string for Opus 5.5; that `-p` here bills the Max plan and not the
   credits; that tools are disabled (a reviewer with Bash and web access in a temp dir is not "judging the
   diff on its own", and can burn minutes); no session persistence; `--output-format json` so `tokens_in/out`
   and `duration_s` are recorded (the parser expects `result` and `usage.*`); and the exit code on a rate
   limit. Record the exact command in the pre-registration and set `verified = true` only then. Also record
   in the pre-registration whether cloud-session usage counts against the Max plan's 5-hour/weekly limits
   (PLAN-v3 §10). If it does, the local reviewer at N = 12 is throttled by the workers' own consumption, which
   is a slowN truth with power 0.18 to detect and would also void windows through downtime after $50 is
   spent. T1 is the test: watch the plan's usage indicator during the 12-worker quarter hour, and log the
   plan usage % as a `note` before and after every window.

5. **Worker prompt: two omissions that cost worker-hours at $2.10 each.**
   - "Loop until told to stop" has no meaning to a cloud session; the model ends its turn when it thinks it
     is done, or stops to ask a question. Add: there is no end condition; never stop, summarise or ask the
     operator anything; if a task is impossible, commit a note on the branch as `READY:` and take the next.
     A worker that idles is only noticed if the operator watches twelve sessions; there is no
     `worker_down` detection in the harness. T1's 30-min single worker is the first test of this and the
     prompt should be right before it.
   - No time budget per task. The design assumes 7.5 min per attempt; Haiku spending 40 min on one task is
     a plausible failure that the pilot would only report as a low λ. Add a soft rule ("aim for under 10
     minutes; if a task has taken more than 20, submit what passes the visible tests").
   Should also be stated (not blocking): all twelve workers scan `TASKS.json` from the top, so the first
   minutes of every N = 12 window are a 12-way race on task 1, 11-way on task 2, and so on (about 60
   rejected pushes and race markers, inside warm-up). Either pre-register that as expected, or have worker
   wN start scanning at position N; SimWorker and the dry run used the from-the-top rule, so changing it
   is a small deviation that should be written down.

### Should-fix

- **Lengthen T1's 12-worker phase to 30 min (+$5).** It is the only exposure to 12 concurrent sessions before
  the first $50 N = 12 sweep window, and 15 min tests launch, not two hours of unattended operation, idle
  timeouts, or VM reclaim. Cheaper than one voided window.
- **Per-window credit rule.** PLAN-v4 §3 checks each window; `predict.py` checks the whole 78 h once. Do both,
  and pre-register what an interrupted ABBAAB yields: after four windows, the degrade design; after fewer,
  descriptive only. Never a fourth N = 12 window without its N = 1 pair.
- **220-task conflict census.** DRYRUN §3's pair analysis (95 of 120 tasks conflict; 297 pairs) was for 120
  tasks. Re-run `pair_conflicts.py` on 220 and put the numbers in the pre-registration, since b_other at
  N = 12 is what the study will mostly see.
- **Prompt and README hygiene.** `harness/README.md` still says "95 of the 120 real tasks" and refers the
  operator to "PLAN-v3 §10"; `SCHEMA.md`'s example has `claude-sonnet-5`; `config.toml`'s launcher comment
  mentions the gate. Harmless, but the pre-registration hashes these files.
- **PLAN-v4 §1's numbers (0.87 under USL, 0.70 at λ −30%) are superseded by OPERATING-CHARACTERISTICS.md
  (0.80, 0.66).** §6.8 says so; the pre-registration should quote only the OC.md figures and say §1 is
  superseded, or §1 should be amended in place.
- **Completion share from 60-min pilot windows** (§6.7) is biased low relative to 120-min windows (heavier
  censoring), so every rival's N = 1 prediction is low and the N = 1 sweep windows will run above all four
  predictions. This does not touch P1 or O2 at N = 12 (cap branch), but it will look odd in RESULTS. State
  it in advance with the expected direction.
- **Startup stagger at N = 12 with the manual launcher.** Typing twelve session ids takes minutes; workers
  started after minute 10 lose counted worker-hours. `derive.py` uses `worker_start` times, so λ(12) is
  right, but the prediction assumes twelve from minute 0. Either verify the command launcher in T1 and use
  it, or start all twelve before `run` and log their ids at once.

## 3. The pre-registration commit

Two commits, both pushed before the spend they gate.

**Commit A, before T1** (the design and the code):

1. Hashes: `shasum -a 256` of `analysis/*.py`, `harness/harness/*.py`, `harness/prompts/*.md`, the three
   phase configs, `analysis/design-search/oc_v41.py` and `oc_v41.json`; the sandbox repo's frozen tag
   commit; the tasks repo commit (private; hash only) and the 220-task `validate.py --all` summary
   (220/220, sizes, `files_expected` overlap census on 220).
2. PLAN-v4.md with §6, plus a §7 (or a PREREG.md) containing: the reframed grading (P1 as manipulation
   check; O2 primary quantitative test; Vdur conditional; the rest descriptive); the OC.md table with the
   footnote on the uncapped truths' reviewer; the frozen review job (prompt template, exact `claude -p`
   command, `output_format`); the frozen worker prompt; the T1 measurement rule for abort rule 1 and its
   source; the rule-4 consequence (accept measured q with recomputed OCs, or re-run the pilot); the credit
   rule (whole-sweep and per-window) and the degrade design as a named alternative; exclusion rules
   (throttling 20%, worker > 10 min down → restart, > 2 restarts void, reviewer > 10 min down void, rerun
   never splice, `.claude/` diff must be empty); the decision list (README decisions 1-34, decision 7/9/11/
   22/27 marked superseded); the definitions section of analysis/README.md.
3. `selftest-output/SELFTEST.md`, `selftest.json`, `example-synthetic/` at 300 replicates (already committed).
4. What will be published whichever way it comes out, and the paper's revision-log template.

**Commit B, after T2 and before the first sweep window:**

5. `pilot.json` from `derive.py --pilot <T1> <T2-1..8>` (live-only; `review_source` line), including
   `review_cv_ok`.
6. The calibration JSONL and `predict.py`'s rule-4 line; the T1 throttling reading and rule-1 verdict; the
   merge-queue `mq_timing` summary from T1/T2 (rule 2); rule 3 (λ ≥ 4).
7. `prediction.md` / `prediction.json`: four rivals × {1, 12}, finished and attempts per window and per
   three windows with intervals; the O2 interval; design-point loads; rule 5 with the measured burn and the
   meter readings (before/after T1, after T2) as logged `meter` events.
8. The six run ids and seeds for ABBAAB, chosen in advance.

**Code against PLAN-v4.1.** I found no contradiction in the analysis code: `common.PLAN_V4` has sizes (1, 12),
3 reps, 120/10/10 min, `completion`, `logit_cluster_task`, λ target 8, q 2, CV max 0.5, 78 session-hours,
$50 floor; `common.V4_OC` matches OPERATING-CHARACTERISTICS.md line for line; `derive.pilot_params` pools
λ from `pilot` windows only and V/b/CV from live `trial` + `pilot`; `score.py` reads `review_cv_ok` from
pilot.json only; P1 ties fail; S3 is on b_review; Vdur is the Welch test; P1-nf/O2-nf exist. The
contradictions are all in the harness's operator surface: `config.toml` / `RunCfg` defaults (must-fix 1),
`base_ref`, and the README's references to the gate and PLAN-v3 §10.

## 4. Order of paid steps and human checkpoints

0. **Free, before anything:** fix must-fix 1-5; create the private GitHub sandbox repo from the frozen tag
   (no hidden tests, no `tasks/`, no `.claude/` state; run the harness's every-blob leak test against the
   remote); no branch protection on `main` (reset force-pushes it); the orchestrator's git identity can
   force-push `main` and delete `claude/*` branches; `python -m harness validate-tasks --config
   config.sweep.toml` against that remote: 220/220; `python -m harness reset` once and inspect
   `TASKS.json` on the remote; verify the reviewer command (must-fix 4) and run the 120 calibration
   reviews through it; commit A pushed. **Checkpoint: the owner confirms Haiku 4.5 is in the cloud-session
   model list on the paying account** (if not, the design is the Sonnet fallback and the OCs must be
   recomputed before commit A) **and reads the credit meter (M0).**
1. **T1 ($7 assumed, 3.25 session-h; consider 5.75 h):** one worker 30 min, then eleven more. Verifies
   launch syntax (`claude --cloud` or the UI; write the exact syntax and the observed session-id format
   into the runbook), `claude/task-*` and `claude/race-*` pushes, unattended looping, dependency install on
   the VM, 12-way concurrency, rule 1 reading, the plan-usage indicator during the burst, rule 2
   (`mq_timing`). **Checkpoint: meter M1; burn = (M0 − M1) / session-hours; stop if burn > $3.40 (the
   degrade design's ceiling) or if rule 1 fails.**
2. **T2 ($17, 8 × 60 min, separate days/plan windows):** `kind = "pilot"`, 1 worker each. **Checkpoint after
   the fourth window: interim λ and V; if λ < 4 stop (rule 3) and report as a pilot.** Meter M2 after the
   eighth.
3. **Free:** `derive --pilot`, `predict --calibration --burn --balance --task-supply 220`; rules 3-5; if
   rule 4 fails, the pre-registered consequence (§2 must-fix 3). Commit B pushed. **Checkpoint: the owner
   reads `prediction.md` and the degrade decision, and confirms the six run ids and seeds.**
4. **Sweep ($164 or degraded):** N = 1 first (a $4 shakedown of the live path under the frozen commit), then
   12, 12, 1, 1, 12. Meter before and after every window (`--meter-start`, then `log --type meter`);
   `validate-log` and `status` after each; a VOID reruns under a new id if rule 5 still holds. Plan-usage %
   noted before and after each window.
5. **Free:** `derive`, `score`; publish RESULTS whichever way; paper revision-log entry.

## Checklist for the owner before the first paid step (T1)

- [ ] Must-fix 1: phase configs (kind trial/pilot/sweep; 120 min; Haiku; N per window); `base_ref` matches
      the tag; `run` prints and the operator confirms kind/N/window/model.
- [ ] Must-fix 2: rule-1 measurement source and procedure written; T1 worker-start procedure written.
- [ ] Must-fix 3: review job frozen before T1; rule-4 consequence written; calibration done through the
      harness's reviewer path with the frozen job; calibration JSONL committed.
- [ ] Must-fix 4: `claude -p` command verified on the paying account (model id, no tools, JSON output,
      billed to the plan); `verified = true`; whether cloud sessions count against plan limits recorded.
- [ ] Must-fix 5: worker prompt says never stop / never ask, and gives a per-task time budget; the race
      behaviour at startup pre-registered.
- [ ] Pre-registration reframed: P1 a manipulation check with the footnote on the OC table; O2 the primary
      quantitative test; PLAN-v4 §1 numbers marked superseded by OC.md.
- [ ] Haiku 4.5 confirmed available for cloud sessions on this account.
- [ ] Private GitHub repo created from the frozen tag; leak test against the remote passes; `main`
      unprotected; `validate-tasks` 220/220 against the remote; one `reset` inspected.
- [ ] Commit A pushed (hashes, plan + prereg text, self-test output, decisions).
- [ ] Meter M0 read and recorded; the stop thresholds for M1 (burn > $3.40) and rule 1 written on the runbook.
