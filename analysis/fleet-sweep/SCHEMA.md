# Event log schema (fleet sweep, study 2)

The orchestrator writes one JSON object per line to `runs/<run_id>/events.jsonl`. The analysis reads
only this file plus `runs/<run_id>/run.json`. Times are UTC ISO 8601 with milliseconds.

## run.json

`n_workers` is the number of **slots** (worker model: one cloud session per task, harness README "Worker
model"); `notes` carries `phase=<t0|t1|t2|sweep-n1|sweep-n12>` and `worker_model_design=session-per-task`.

```json
{"run_id": "2026-10-02-N12-r1", "kind": "sweep|pilot|trial|dry-run", "n_workers": 12,
 "window_start": "...", "window_end": "...", "warmup_min": 10, "grace_min": 10,
 "task_order_seed": 1234, "sandbox_commit": "sha", "harness_commit": "sha",
 "worker_model": "claude-haiku-4-5", "reviewer_model": "claude-opus-5-5",
 "notes": "free text"}
```

## events.jsonl: one object per line, always with `t` and `type`

| type | fields | meaning |
|---|---|---|
| `worker_start` | worker, session_id | slot `worker` (s1..sN) opens (at window start, or at its `start_schedule` minute). session_id is null: sessions are per task. Kept for compatibility: worker-hours are slot-open hours |
| `worker_down` / `worker_restart` | worker, reason | slot down / back (operator entries); down time is excluded from worker-hours |
| `slot_busy` / `slot_idle` | slot | a session starts / stops occupying the slot (launch or rework message / READY, timeout, window end) |
| `session_launch` | slot, task, session_id, attempt_no | the task's session was launched on this slot (session_id null if none was seen). attempt_no = launch attempt for this task in this window (2+ only after a launch command failed outright); a task is never re-launched after its session ran |
| `session_message` | slot, task, session_id, kind (`rework`/`probe`) | a follow-up message delivered to the task's own session: `rework` after a bounce, when a slot freed (the slot is then that session's until its next READY); `probe` = T0's delivery check |
| `session_timeout` | slot, task, session_id | no READY within `task_timeout_min` (25) of the launch or of the last rework message: the task is abandoned, never re-launched in the window, and its slot freed |
| `claim` | worker, task, branch | the task's branch first seen on the remote (its session's first push); worker = the slot. Branch `claude/task-<id>`, or another `claude/*` branch whose `READY: <id>` commit names the task |
| `claim_race` | worker, task | RETIRED: no claims with one session per task; never logged by the current harness, accepted for older logs |
| `submit` | worker, task, branch, head, attempt_no, lines_changed, files, k, m | a `READY: <id>` push; worker = the slot the session held. attempt_no = 1 for the first submission of the task, 2+ for re-submissions. k = changes in flight at this moment (submitted, not finished or censored), m = those sharing a file with this change. k, m are logged on every submit but analysed on attempt_no = 1 |
| `review_start` | task, head, queue_depth | reviewer handed this change; queue_depth = changes waiting for review, excluding this one |
| `review_end` | task, head, verdict (`approve`/`request_changes`), reason, tokens_in, tokens_out, duration_s | |
| `review_error` | task, head, error | reviewer call failed (retried once) |
| `hidden_pre` | task, head, passed | hidden tests on the exact approved head |
| `rebase` | task, head, new_head, conflict | rebase on current main |
| `tests_post` | task, head, visible_passed, hidden_passed | visible + hidden tests after rebase |
| `bounce` | task, head, cause (`review`/`rebase_conflict`/`visible_fail`/`escaped_defect`/`integration_failure`) | rework queued for the task's session (nothing is pushed to its branch); delivered as a `session_message` when a slot frees |
| `merge` | task, head, main_sha | merged to main |
| `queue_idle` / `queue_busy` | | merge queue state changes (for utilisation) |
| `reviewer_idle` / `reviewer_busy` | | reviewer state changes (for V per busy hour) |
| `usage` | worker, tokens_in, tokens_out, cost_usd_est | per-worker usage increment, if a product token source exists (none is known; PLAN-v4 section 7.5). If logged in T1, `harness throttle` uses it for abort rule 1 |
| `meter` | credits_left_usd, source | credit meter reading entered by the operator |
| `note` | text | anything else. Operator readings for abort rule 1 use `plan_usage pct=<%> src=<where read>` (PLAN-v4 section 7.5); the harness's own prefixes are listed in harness/README.md |

Harness `note` lines for sessions (not events): `slots n=… task_timeout_min=… task_budget_min=…`,
`tasks_exhausted n=<N>` (logged when the last task in the list is handed to a slot), `launch_detail …`,
`session_launch_failed …`, `followup_failed …`, `task_abandoned task=… reason=…`, `probe_ack …` / `probe_timeout …`
(T0), `session_open_at_window_end …`, `rework_not_sent_at_window_end …`, `unattributed branch …`.

Derived by the analysis, never logged: finished (merged by window end + grace, and hidden tests
green), censored (submitted, not finished, not bounced at end), V, b_review, b_hidden, b, λ (first
submissions per slot-open hour), start-up time (session_launch -> claim), slot busy share, timeouts.
