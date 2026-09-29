<!-- Follow-up message handing a slot's session its next task ([launcher] session_per = "slot"). The harness fills
the {placeholders} (the same as prompts/worker.md) and sends it with the follow-up command to the session that
just finished its previous task. The harness depends on the branch name, the READY: message and the push.
DRAFT until the slot-session T0; pre-register the final text. -->
New task. This is a new, independent task: it has nothing to do with the task you worked on before, and your
earlier branch is finished (do not touch it again unless a later message asks you to). You are still authorised
to create the branch below, commit, and push it to origin without asking for confirmation.

You are working alone. Nobody will answer questions: never ask any. If something is unclear, make the most
reasonable choice, say so in your commit message, and carry on.

## Task {task_id}: {title}

{text}

Acceptance criteria:
{acceptance}

## How to work (follow exactly; an automated harness watches the repository)

1. Start from a fresh `origin/main`, not from your previous branch: run `git fetch origin`, then create the
   branch `{branch}` from `origin/main` and push it at once, before any other work:
   `git checkout -b {branch} origin/main && git push -u origin {branch}`. Work only on this branch.
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

## Rules that always apply (re-read these before every commit, even if your earlier context was summarised)

- Every commit that finishes work on a task must have a message starting `READY: {task_id}`. Without it the harness never sees the work.
- Push only the task's own branch, `claude/task-{task_id}`. Never push `main`, never force-push, never rebase.
- Never open, comment on or merge a pull request. The harness reviews and merges; pull requests are not used.
- Never edit `TASKS.json`. Never ask questions; nobody will answer.

