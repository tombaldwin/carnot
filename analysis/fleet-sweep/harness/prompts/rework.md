<!-- Follow-up message sent to the session that did the task (per task or per slot) after its change bounced (any cause). The harness fills
the {placeholders}; {cause_text} is orchestrator.CAUSE_TEXT (never names hidden tests); {details} is the
reviewer's reason or the visible-test output tail, or empty. DRAFT until T0; pre-register the final text. -->
Review feedback for task {task_id} (your submission {head_short}, attempt {attempt_no}).

Cause: {cause}
{cause_text}
{details}
You are still authorised to commit and push `{branch}` to origin without asking for confirmation.

What to do now, on the same branch `{branch}`:

1. `git fetch origin` and switch back to `{branch}` as you left it (`git checkout {branch}`; if it is not in
   your clone, `git checkout -b {branch} origin/{branch}`). You may have worked on other tasks since; this
   message is only about task {task_id}.
2. Fix the change as described above. If `origin/main` has moved, merge it into your branch
   (`git merge origin/main`), resolve any conflicts so that both your change and everything now on `main`
   are kept and work. Never rebase and never force-push.
3. Run the visible tests, `{visible_cmd}`, and make them pass.
4. Commit with a message starting `READY: {task_id}`, push `{branch}`, then stop.

Do not ask questions; nobody will answer. Time budget: about {budget_min} minutes.
