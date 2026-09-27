# Carnot

**Find the Carnot limit of an AI coding agent fleet: how many agents to run, what's holding them back, and how long the work will take.**

Adding coding agents gives diminishing, then negative, returns. Three things eat the output: steps that must happen one at a time, agents getting in each other's way, and rework. Human review usually caps the whole thing. Carnot measures these from your repository's own history and runs a small model to find your best fleet size.

It's an [Agent Skill](https://docs.claude.com/en/docs/agents-and-tools/agent-skills/overview): you ask your coding agent, and the agent does the measuring.

> "How many agents should we run on this repo?"
> "Why isn't adding agents speeding us up?"
> "Estimate 120 PRs of work with four agents."

## Install

**Claude Code**

```
/plugin marketplace add tombaldwin/carnot
/plugin install carnot@carnot
```

**Other agents, or by hand:** copy [`plugins/carnot/skills/carnot`](plugins/carnot/skills/carnot) into your agent's skills folder (for Claude Code, `~/.claude/skills/carnot`).

**Without an agent:** the script works on its own.

```bash
python3 plugins/carnot/skills/carnot/scripts/carnot.py run --repo /path/to/repo
```

Requirements: Python 3.9+ and `git` (2.38+ for conflict replay). The [GitHub CLI](https://cli.github.com/) is optional; with it, Carnot reads pull requests and reviews. No other dependencies.

## What it measures

| Input | How |
|---|---|
| Rework rate r₀ | Changes later reverted, sent back in review, closed unmerged after review, or followed within 14 days by a fix to the same files |
| Collision chance p | Real pairs of changes that were open at the same time, replayed with `git merge-tree` to see whether they conflict. Branches already rebased onto each other are skipped |
| Output | Finished changes per active day, and lines per change |
| Review | Regular reviewers (from PR data), CI config and test coverage as evidence |

It reads GitHub pull requests if `gh` is signed in, merged branches from merge commits on any host (Bitbucket, GitLab, self-hosted), or plain commits. It tells you which inputs it measured and which are defaults, and asks for the rest: how many people review, how much checking is automated, how many agents you usually run.

Everything runs locally. No repository data leaves your machine, apart from the GitHub API calls your own `gh` makes.

## Example

With the default values and two reviewers:

```
Best fleet size: 4 agents, finishing about 9.31 changes/day (2.59× one agent).
Limited by review. Rework at that size: 42%.

Rule of thumb q ÷ (1 − αq − βq²), capped at (1 − α) ÷ (p + √β): 4.0 agents,
about 9.6 finished/day.

Estimate: 120 changes with 4 agents ≈ 12.9 working days
(51.5 agent-days; one agent: 33.3 days).
```

It also lists which change would help most, such as adding a reviewer, automating more checks, or cutting collisions.

## Before and after

Adopted something meant to raise the limit, such as an effect checker, a merge queue or a new way of splitting work? Measure it:

```bash
python3 plugins/carnot/skills/carnot/scripts/carnot.py compare --repo . --pivot 2026-06-15 --after-set auto=0.7
```

Carnot measures each side of the date separately, runs the model for both, and reports whether the changes in rework and collisions are bigger than noise, with 95% ranges. Parameters history can't show, like how much checking is automated, can be set per period.

## The model

With N agents:

```
X(N) = N / (1 + α(N−1) + β·N(N−1))        coordination drag (Gunther's Universal Scalability Law)
r(N) = 1 − (1 − r₀)(1 − p)^(N−1)          rework rises with concurrent changes
U(N) = (1 − r(N)) × min(λ·X(N), V / h)     finished changes per day, capped by review capacity V
```

Rule of thumb, with q = what reviewers can check per day ÷ what one agent produces per day: **agents ≈ q ÷ (1 − αq − βq²)**, but never more than **(1 − α) ÷ (p + √β)**; finished work ≈ (1 − r₀) × what reviewers can check. In words: enough agents to keep reviewers busy, a few more for drag, and stop there.

The collision term has been tested once, on 48,000 agent pull requests, with a pre-registered analysis: see [`analysis/aidev`](analysis/aidev/RESULTS.md). It mostly failed, which is why the default collision chance is 1% and the rule leads with review capacity.

Parameters, defaults and their sources are in [`reference/model.md`](plugins/carnot/skills/carnot/reference/model.md). α and β can't be read reliably from history. To fit them, run 1, 2, 4 and 8 agents on comparable work and use `carnot.py fit`.

The model and its evidence are set out in the working paper *The Heat Death of the Codebase? Paying Maxwell's Demon a Day Rate* (Polymorphism, 2026).

## Limits

This is a planning aid, not a law of nature. The defaults rest on few published studies. Rework detection over-counts in frequently changed files and misses fixes that aren't labelled as fixes. Textual conflicts are often cheap rebases, while conflicts that merge cleanly but break behaviour aren't measured at all.

## Licence

MIT
