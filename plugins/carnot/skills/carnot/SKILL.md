---
name: carnot
description: Size a fleet of parallel AI coding agents for a repository, and estimate delivery time with agents, using the repo's own git/PR history. Use when someone asks how many coding agents to run in parallel, whether adding agents will speed things up, why more agents aren't helping, what limits their agent throughput (review, merge conflicts, rework), or wants a time/effort estimate for work done by agents.
---

# Carnot: find an agent fleet's limit from the repo's own history

Adding coding agents gives diminishing, then negative, returns. Three things eat the output: steps that must happen one at a time (α), agents getting in each other's way (β, and collisions p), and rework (r). Human review usually caps the whole thing. This skill measures what it can from the repository, asks the user only for what it can't, and runs the model.

The model and its sources are in [reference/model.md](reference/model.md). Read it if the user asks how the numbers are derived.

## Steps

1. **Calibrate from history.** From the repository root:

   ```bash
   python3 <skill-dir>/scripts/carnot.py calibrate --repo .
   ```

   It picks the best source automatically: GitHub PRs (if `gh` is authenticated), otherwise merged branches from merge commits, otherwise plain commits. It returns JSON with `params`, `sources` (measured vs default for each) and `evidence`. If `evidence.changes` is small, rerun with `--days 180`. It runs locally; only the user's own `gh` calls leave the machine.

2. **Sanity-check the evidence before trusting it.** Look at:
   - `rework`: r₀ counts a change as reworked if it was reverted, sent back in review, or a fix touched the same file within 14 days. It over-counts in hot files and misses fixes not labelled as fixes. If `r0_agent` and `r0_human` both appear, use the one matching how the user will work.
   - `collisions`: `textual_conflict_rate_smoothed` comes from replaying real pairs of concurrent branches with `git merge-tree`, so it's the best measure. `file_overlap_rate` is an upper bound. If the note says p can't be measured (linear history), say so and keep the default or ask.
   - `throughput`: finished changes per active day for the whole repo, not per agent.

3. **Ask the user only for what history can't show.** Batch these into one question and offer the defaults:
   - How many people review agent work, and focused review hours a day each (defaults: from PR data, else 2 reviewers × 4 h).
   - Roughly what share of checking is done by automation (tests, types, CI) rather than by reading the code (default 50%).
   - How many agents typically ran at once during the period, so per-agent output can be back-solved (`--agents-used`).
   - If they want an estimate: how many changes (PRs) the work is, and how many agents they plan to run.

4. **Run the model** with the answers as overrides:

   ```bash
   python3 <skill-dir>/scripts/carnot.py run --repo . --agents-used 2 --reviewers 1 --hours 3 --auto 0.6 --backlog 80 --plan 4
   ```

   Any parameter can be overridden: `--r0 --p --a --b --lam --loc --reviewers --rate --hours --auto --rho`. Use `--format json` if you need to compute further.

5. **Report back** in a few lines, then the table:
   - Best number of agents, finished changes per day, and speed-up over one agent.
   - What limits them (review or coordination) and what that means in plain words.
   - The top one or two levers from `levers`, with their gain.
   - The estimate, if asked for.
   - Which inputs were measured and which were defaults or guesses. Don't present defaults as findings.

   Keep two things handy for conversation. The **rule of thumb**, for anyone: up to five agents per codebase, and no more than the reviewers can check, which is about one agent per reviewer, two if tests and tools do half the checking, three or four if they do most of it. The **sizing formula**, for engineers: let q = what reviewers can check per day ÷ what one agent produces per day; then **agents ≈ q ÷ (1 − αq − βq²)**, but never more than **(1 − α) ÷ (p + √β)**, and finished work ≈ (1 − r) × what reviewers can check, where r is the share of changes that come back at that fleet size (r₀ for one agent). In words: enough agents to keep reviewers busy, a few more for drag, and stop there. The formula aims at the peak, so it usually runs one agent more than it needs. Collisions (p) are usually small: a pre-registered test on 48,000 agent PRs found concurrency barely raised rejection.

## Improving the estimate: a fleet sweep

α and β can't be read from history reliably. If the user wants a real fit, suggest a sweep: run 1, 2, 4 and 8 agents on comparable work for a few days each, and count changes merged that stayed merged per day. Put the results in a CSV (`agents,finished_per_day`) and run:

```bash
python3 <skill-dir>/scripts/carnot.py fit sweep.csv
```

Then pass the fitted `--a` and `--b` into `run`.

## Measuring a change: before and after

When the user has adopted something meant to raise the limit (a verification tool such as an effect checker, a merge queue, stricter task partitioning, a new review process), measure it:

```bash
python3 <skill-dir>/scripts/carnot.py compare --repo . --pivot 2026-06-15 --before 90 --after-set auto=0.7
```

`--pivot` is the date it took effect. Carnot measures rework, collisions, change size and output separately for each side, runs the model for both, and tests whether the differences in rework and collisions are bigger than noise. History can't show how much checking is automated, so ask the user and pass it per period with `--before-set` / `--after-set` (any parameter works: `auto`, `reviewers`, `hours`…).

Report the change in the best fleet size and finished output, and say plainly when a difference could be noise. Before/after isn't a controlled experiment. Suggest running the same comparison on a similar repo that didn't change, and wait until the after period has at least a few weeks of changes older than the 14-day rework window.

## Cautions

- The model is a planning aid, not a law of nature. Its defaults rest on few published studies.
- A textual conflict is often a quick rebase, not a full redo, so measured p overstates rework slightly. Semantic conflicts (clean merge, broken behaviour) are not measured, which pushes the other way.
- Don't combine a fitted β from a sweep with a large measured p without saying so: sweep throughput already includes conflict rework, so both terms would count it.
