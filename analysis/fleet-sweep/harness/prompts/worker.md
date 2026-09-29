<!-- A session's first prompt: its first task (one session per task, or per slot with later tasks sent as
prompts/next-task.md follow-ups). The harness fills the {placeholders} and passes the
text as the session's first prompt (routine mode: the re-armed slot routine's first event). The harness depends
on the branch name, the READY: message and the push. Routine sessions clone the sandbox with origin set, so no
remote or credential set-up is needed (the step 0 of the `claude --cloud` workaround is gone, 2026-09-28).
DRAFT until the T0 repeat; pre-register the final text. -->
You are working for the repository owner's test harness. You are authorised to create the branch below,
commit, and push it to origin without asking for confirmation.

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

You may later receive messages from the harness. Each says what it is: review feedback on a task you did (follow
it on that task's own branch, finish with a new commit whose message starts with `READY: <that task's id>`,
push, and stop), or a new task (start it from a fresh `origin/main` on the new branch it names). Follow each
message on its own.

## Rules that always apply (re-read these before every commit, even if your earlier context was summarised)

- Every commit that finishes work on a task must have a message starting `READY: <task id>`. Without it the harness never sees the work.
- Push only the task's own branch, `claude/task-<task id>`. Never push `main`, never force-push, never rebase.
- Never open, comment on or merge a pull request. The harness reviews and merges; pull requests are not used.
- Never edit `TASKS.json`. Never ask questions; nobody will answer.

