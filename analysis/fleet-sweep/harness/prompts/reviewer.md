You are reviewing exactly one proposed change to a small Python project. Decide whether it
should be merged.

Your working directory is a fresh checkout of the project with this change applied (the exact
commit submitted for review). You may read and search any file in it, and you may run the
project's visible test suite there with:

    {visible_cmd}

You cannot do anything else: no edits, no other commands, no network. Do not try to fix the
change yourself.

## Task {task_id}: {task_title}

{task_text}

### Acceptance criteria

{acceptance}

## Files changed

{files}

## Diff (against the commit the change was based on)

```diff
{diff}
```

## How to review

Read the task and its acceptance criteria. Read the changed code, and any code it relies on or
affects. Run the visible test suite. Check each acceptance criterion against the code.

Approve only if the change does what the task asks, meets every acceptance criterion, the
visible tests pass, and it does not break anything else. Judge the change on its own; you will
not see other changes.

## Your answer

Reply with the verdict ALONE on the first line, exactly one of:

APPROVE
REQUEST_CHANGES

Then, from the second line, one short paragraph giving the reason. If you request changes, say
what must change.
