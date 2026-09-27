<!-- Per-task session prompt (one cloud session per task). The harness fills the {placeholders} and passes the
text as the session's first prompt. The harness depends on the branch name, the READY: message and the push.
DRAFT until T0 (harness/README.md, "T0 operator checklist"); pre-register the final text. -->
You are working alone on one task in this repository. Nobody will answer questions: never ask any. If
something is unclear, make the most reasonable choice, say so in your commit message, and carry on.

## Task {task_id}: {title}

{text}

Acceptance criteria:
{acceptance}

## How to work (follow exactly; an automated harness watches the repository)

1. Run `git fetch origin`, create the branch `{branch}` from `origin/main`, and push it at once, before any
   other work: `git checkout -b {branch} origin/main && git push -u origin {branch}`. Work only on this
   branch.
2. Implement the task. Change only what the task needs.
3. Run the visible tests, `{visible_cmd}`, and make them pass.
4. Commit. Your final commit message must start with `READY: {task_id}` (for example
   `READY: {task_id} <one-line summary>`).
5. Run `git fetch origin`. If `origin/main` has moved since you branched, merge it into your branch
   (`git merge origin/main`), keep both your change and everything now on `main`, re-run the visible tests,
   and make the merge commit's message start with `READY: {task_id}` too. Never rebase and never force-push.
6. Push the branch (`git push origin {branch}`), then stop. Do not wait for a review and do not start
   anything else.

Time budget: about {budget_min} minutes. If you are running out of time, commit and push what you have with
a `READY: {task_id}` message rather than nothing.

Never push to `main` or to any other branch, never force-push, and never edit `TASKS.json`.

If you later receive a message about this task, it is review feedback: follow it on the same branch, finish
with a new commit whose message starts with `READY: {task_id}`, push, and stop.
