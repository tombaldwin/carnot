# Event log schema (fleet sweep, study 2)

The orchestrator writes one JSON object per line to `runs/<run_id>/events.jsonl`. The analysis reads
only this file plus `runs/<run_id>/run.json`. Times are UTC ISO 8601 with milliseconds.

## run.json

`n_workers` is the number of **slots** (worker model: one cloud session per task, harness README "Worker
model"); `notes` carries `phase=<t0|t1|t2|t1b|sweep-n1-k1|sweep-n1-k3|sweep-n12-k1|sweep-n12-k3>` (PLAN-v4/v5 logs:
`sweep-n1|sweep-n12`) and `worker_model_design=session-per-task`.

```json
{"run_id": "2026-10-02-N12-r1", "kind": "sweep|pilot|trial|dry-run", "n_workers": 12,
 "window_start": "...", "window_end": "...", "warmup_min": 10, "grace_min": 10,
 "task_order_seed": 1234, "sandbox_commit": "sha", "harness_commit": "sha",
 "worker_model": "claude-haiku-4-5", "reviewer_model": "claude-opus-5-5",
 "notes": "free text", "n_reviewers": 3}
```

**PLAN-v6 (K parallel reviewers, `[reviewer] parallel = K`; harness section 6, implemented):** `run.json` carries
`n_reviewers` (integer K; always written by the v6 harness; absent in older logs = 1, the serial reviewer).
`review_start`, `review_end`, `review_error`, `reviewer_busy` and `reviewer_idle` carry `reviewer` ("r1".."rK";
always in v6 logs, also at K = 1; absent in older logs). Each reviewer reviews one change at a time, no task is under
review by two reviewers at once (a newer head of a task under review waits for that review), at most K reviews are
open, and each reviewer's `reviewer_busy` / `reviewer_idle` alternate with its reviews inside its busy stretches. The
merge queue stays serial; approvals enter it in the order reviews finish. `validate_schema.py` and the harness's own
validator check this; `derive.py` computes busy time per reviewer and `reviewer_util` = busy time / (K x counted
time).

## events.jsonl: one object per line, always with `t` and `type`

| type | fields | meaning |
|---|---|---|
| `worker_start` | worker, session_id | slot `worker` (s1..sN) opens (at window start, or at its `start_schedule` minute). session_id is null: sessions are per task. Kept for compatibility: worker-hours are slot-open hours |
| `worker_down` / `worker_restart` | worker, reason | slot down / back; down time is excluded from worker-hours. Operator entries, or `worker_down` from the harness itself with reason `launch_failures n=<N> last_error=<text>` after N consecutive failed launches on the slot (it stays down for the window) |
| `slot_busy` / `slot_idle` | slot | a session starts / stops occupying the slot (launch or rework message / READY, timeout, window end) |
| `session_launch` | slot, task, session_id, attempt_no | the task's session was launched on this slot (session_id null if none was seen). attempt_no = launch attempt for this task in this window (2+ only after a launch command failed outright); a task is never re-launched after its session ran. Routine launcher (`[launcher] mode = "routine"`): logged when the slot routine's re-arm is confirmed, **before the run exists**, so session_id is always null; the id (`cse_...`) arrives later in a `launch_detail … session_id_found=true session_id=…` note |
| `session_message` | slot, task, session_id, kind (`rework`/`probe`) | a follow-up message delivered to the task's own session: `rework` after a bounce, when a slot freed (the slot is then that session's until its next READY); `probe` = T0's delivery check |
| `session_timeout` | slot, task, session_id | no READY within `task_timeout_min` (25) of the launch or of the last rework message: the task is abandoned, never re-launched in the window, and its slot freed |
| `claim` | worker, task, branch | the task's branch first seen on the remote (its session's first push); worker = the slot. Branch `claude/task-<id>`, or another `claude/*` branch whose `READY: <id>` commit names the task |
| `claim_race` | worker, task | RETIRED: no claims with one session per task; never logged by the current harness, accepted for older logs |
| `submit` | worker, task, branch, head, attempt_no, lines_changed, files, k, m | a `READY: <id>` push; worker = the slot the session held. attempt_no = 1 for the first submission of the task, 2+ for re-submissions. k = changes in flight at this moment (submitted, not finished or censored), m = those sharing a file with this change. k, m are logged on every submit but analysed on attempt_no = 1 |
| `review_start` | task, head, queue_depth, reviewer | reviewer handed this change; queue_depth = changes waiting for review, excluding every change under review (this one included) |
| `review_end` | task, head, verdict (`approve`/`request_changes`), reason, tokens_in, tokens_out, duration_s, reviewer | |
| `review_error` | task, head, error, reviewer | reviewer call failed (retried once; after that the change goes back to the front of the queue, for any reviewer, and this reviewer backs off) |
| `hidden_pre` | task, head, passed | hidden tests on the exact approved head |
| `rebase` | task, head, new_head, conflict | rebase on current main |
| `tests_post` | task, head, visible_passed, hidden_passed | visible + hidden tests after rebase |
| `bounce` | task, head, cause (`review`/`rebase_conflict`/`visible_fail`/`escaped_defect`/`integration_failure`) | rework queued for the task's session (nothing is pushed to its branch); delivered as a `session_message` when a slot frees |
| `merge` | task, head, main_sha | merged to main |
| `queue_idle` / `queue_busy` | | merge queue state changes (for utilisation) |
| `reviewer_idle` / `reviewer_busy` | reviewer | that reviewer's state changes (for V per busy hour; per reviewer since PLAN-v6) |
| `usage` | worker, tokens_in, tokens_out, cost_usd_est | per-worker usage increment, if a product token source exists (none is known; PLAN-v4 section 7.5). If logged in T1, `harness throttle` uses it for abort rule 1 |
| `meter` | credits_left_usd, source | credit meter reading entered by the operator |
| `note` | text | anything else. Operator readings for abort rule 1 use `plan_usage pct=<%> src=<where read>` (PLAN-v4 section 7.5); the harness's own prefixes are listed in harness/README.md |

Harness `note` lines for sessions (not events): `slots n=… task_timeout_min=… task_budget_min=…`,
`tasks_exhausted n=<N>` (logged when the last task in the list is handed to a slot), `launch_detail …`
(routine mode: one with `detached_by=routine trigger=<id> run_once_at=<t>` at the re-arm, then one with
`session_id_found=true|false session_id=<cse_…> after_s=<s>` when the lookup ends),
`session_launch_failed …` (routine mode: the re-arm failed or was not confirmed), `routine_disabled …` /
`routine_disable_failed …` (window end), `routine_rate_wait …`, `routine_budget_refund trigger=… reason=not_started`,
`routine_list_runs_failed …`, `followup_failed …`, `reviewers n=<K> ids=r1,…` (at window start),
`review_open_at_grace_end task=… head=… reviewer=…` (one per reviewer still reviewing at grace end; the analysis
clips that reviewer's busy time only), `reviewer_downtime reviewer=… s=…` (at grace end, reviewers with downtime; the
window is VOID when the summed downtime / K exceeds `[reviewer] max_downtime_min`), `cli_pinned path=… version=… source=… reused=…` (or
`cli_pinned disabled …`; first line of a real run), `slot_backoff slot=… fail_streak=… backoff_s=…`,
`dispatch_paused reason=launch_failures slots=… window_s=… pause_s=… last_error=…` / `dispatch_resumed`, `task_abandoned task=… reason=…`, `probe_ack …` / `probe_timeout …`
(T0), `session_open_at_window_end …`, `rework_not_sent_at_window_end …`, `unattributed branch …`.

Derived by the analysis, never logged: finished (merged by window end + grace, and hidden tests
green), censored (submitted, not finished, not bounced at end), V, b_review, b_hidden, b, λ (first
submissions per slot-open hour), start-up time (session_launch -> claim), slot busy share, timeouts.

Start-up time under the command launcher (all phase configs from 2026-09-29) runs from `session_launch`, logged when
the CLI has printed the session id, to the branch's first push. Under the routine launcher (2026-09-28) it ran from the confirmed re-arm, so
it includes the re-arm lead (`[launcher.routine] lead_s`, 30 s, less the ~9 s the re-arm call itself takes),
the routine firing delay (runs start 40-75 s after `run_once_at`, observed 2026-09-28), provisioning and the
clone. The `run_once_at` in the first `launch_detail` note lets the analysis split off the lead. Runs launched
with `claude --cloud` (mode `command` / `manual`) are not comparable on this measure.
