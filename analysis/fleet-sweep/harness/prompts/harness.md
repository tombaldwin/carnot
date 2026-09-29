<!-- Routine mode: the fixed working rules, written to the sandbox's main as HARNESS.md by `reset` (next to
TASKS.json). The per-task routine prompt (prompts/routine-worker.md) is kept short because a model copies it into
the RemoteTrigger call on every launch (T0b, 2026-09-28: a 3 KB prompt failed to copy intact). Same rules as
prompts/worker.md; <TASK> stands for the task id given in the session's prompt. -->
# How to work (for the harness's task sessions)

You are working alone on one task in this repository. Nobody will answer questions: never ask any. If
something is unclear, make the most reasonable choice, say so in your commit message, and carry on.

Your task is the entry for <TASK> in `TASKS.json`: its `title`, `text` and acceptance criteria. Read that one entry
only; the other entries are other sessions' tasks.

Follow these steps exactly; an automated harness watches the repository. Your branch is `claude/task-<TASK>`.

1. Run `git fetch origin`, create the branch `claude/task-<TASK>` from `origin/main`, and push it at once, before
   any other work: `git checkout -b claude/task-<TASK> origin/main && git push -u origin claude/task-<TASK>`. Work
   only on this branch.
2. Implement the task. Change only what the task needs.
3. Run the visible tests, `{visible_cmd}`, and make them pass.
4. Commit. Your final commit message must start with `READY: <TASK>` (for example
   `READY: <TASK> <one-line summary>`).
5. Run `git fetch origin`. If `origin/main` has moved since you branched, merge it into your branch
   (`git merge origin/main`), keep both your change and everything now on `main`, re-run the visible tests,
   and make the merge commit's message start with `READY: <TASK>` too. Never rebase and never force-push.
6. Push the branch (`git push origin claude/task-<TASK>`), then stop. Do not wait for a review and do not start
   anything else.

Time budget: about {budget_min} minutes. If you are running out of time, commit and push what you have with
a `READY: <TASK>` message rather than nothing.

Never push to `main` or to any other branch, never force-push, and never edit `TASKS.json` or `HARNESS.md`.

If you later receive a message about this task, it is review feedback: follow it on the same branch, finish
with a new commit whose message starts with `READY: <TASK>`, push, and stop.
