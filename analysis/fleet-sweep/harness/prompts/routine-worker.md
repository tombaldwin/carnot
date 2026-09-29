<!-- Routine mode: the per-task prompt stored on the slot routine at each re-arm. Keep it short and plain (a model
copies it verbatim into the RemoteTrigger call). The rules are in HARNESS.md on main (prompts/harness.md). -->
You are working for the repository owner's test harness. You are authorised to create the branch {branch}, commit, and push it to origin without asking for confirmation. Your task is {task_id}. Read HARNESS.md and follow it exactly, with <TASK> = {task_id}; the task itself is the {task_id} entry in TASKS.json. Nobody will answer questions: never ask any.
