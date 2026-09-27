# Event log schema (fleet sweep, study 2)

The orchestrator writes one JSON object per line to `runs/<run_id>/events.jsonl`. The analysis reads
only this file plus `runs/<run_id>/run.json`. Times are UTC ISO 8601 with milliseconds.

## run.json

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
| `worker_start` | worker, session_id | a worker session is running |
| `worker_down` / `worker_restart` | worker, reason | worker died / restarted |
| `claim` | worker, task, branch | branch `claude/task-<id>` created by this worker |
| `claim_race` | worker, task | push rejected because another worker claimed it |
| `submit` | worker, task, branch, head, attempt_no, lines_changed, files, k, m | a `READY:` push. attempt_no = 1 for the first submission of the task, 2+ for re-submissions. k = changes in flight at this moment (submitted, not finished or censored), m = those sharing a file with this change. k, m are logged on every submit but analysed on attempt_no = 1 |
| `review_start` | task, head, queue_depth | reviewer handed this change; queue_depth = changes waiting for review, excluding this one |
| `review_end` | task, head, verdict (`approve`/`request_changes`), reason, tokens_in, tokens_out, duration_s | |
| `review_error` | task, head, error | reviewer call failed (retried once) |
| `hidden_pre` | task, head, passed | hidden tests on the exact approved head |
| `rebase` | task, head, new_head, conflict | rebase on current main |
| `tests_post` | task, head, visible_passed, hidden_passed | visible + hidden tests after rebase |
| `bounce` | task, head, cause (`review`/`rebase_conflict`/`visible_fail`/`escaped_defect`/`integration_failure`) | FEEDBACK.md pushed to the branch |
| `merge` | task, head, main_sha | merged to main |
| `queue_idle` / `queue_busy` | | merge queue state changes (for utilisation) |
| `reviewer_idle` / `reviewer_busy` | | reviewer state changes (for V per busy hour) |
| `usage` | worker, tokens_in, tokens_out, cost_usd_est | per-worker usage increment, if a product token source exists (none is known; PLAN-v4 section 7.5). If logged in T1, `harness throttle` uses it for abort rule 1 |
| `meter` | credits_left_usd, source | credit meter reading entered by the operator |
| `note` | text | anything else. Operator readings for abort rule 1 use `plan_usage pct=<%> src=<where read>` (PLAN-v4 section 7.5); the harness's own prefixes are listed in harness/README.md |

Derived by the analysis, never logged: finished (merged by window end + grace, and hidden tests
green), censored (submitted, not finished, not bounced at end), V, b_review, b_hidden, b, λ.
