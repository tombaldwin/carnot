# Pre-registration: does rework rise with concurrency the way the Carnot model says?

Written 2026-09-27, before any outcome in the data was looked at. Only the column names of the
AIDev tables had been read (`pull_request`, `pr_reviews`, `repository`, `pr_commits`,
`pr_commit_details`). Dataset: `hao-li/AIDev` on Hugging Face, last modified 2026-08-31.

## The claim under test

The Carnot model says a change's chance of needing rework rises with the number of other changes
in flight alongside it:

    r(k) = 1 − (1 − r₀)(1 − p)^k        k = other changes open at the same time

Equivalently, the chance a change goes through cleanly falls geometrically:
`log(1 − r) = log(1 − r₀) + k·log(1 − p)`. This term is the new part of the model; nothing else in
the paper depends on it being tested here.

## Data and definitions

- **Sample.** Pull requests in `pull_request.parquet` that are closed (merged or closed unmerged).
  Open PRs are excluded. Repositories with fewer than 20 closed PRs are excluded, because the test
  is within repositories and needs variation inside each one.
- **Concurrency k.** The number of other PRs in the same repository that were open at the moment
  this PR was created (created before it, and not yet closed). Measured at creation, not over the
  PR's life, because a PR that stays open longer overlaps more PRs *and* is more likely to be
  abandoned; counting over its life would manufacture the effect.
  Only PRs in the dataset count, which means only agent-authored PRs. Human PRs open at the same
  time are invisible, so k undercounts true concurrency. This biases towards finding no effect.
- **Outcome (primary).** Closed without being merged.
- **Outcome (secondary).** Closed without being merged, or received at least one
  `CHANGES_REQUESTED` review.
- **File overlap.** Two PRs overlap if their commits touched at least one common file, ignoring
  lockfiles, Markdown and docs (the same filter as `carnot.py`).

## Hypotheses and predictions

- **H1 (the effect exists).** Within repositories, the chance of being closed unmerged rises with
  k. Test: log-binomial model with a per-repository intercept, `P(merged) = exp(c_repo + θ·k)`,
  θ = log(1 − p). Likelihood-ratio test of θ = 0. **Prediction: θ < 0, LR p < 0.01.**
- **H2 (the shape).** The geometric form fits at least as well as a straight line with the same
  per-repository intercepts (`P(unmerged) = r_repo + c·k`). **Prediction: AIC of the geometric
  model is no more than 2 worse than the linear one.** Also reported: rejection rate against k in
  bins, after subtracting each repository's own baseline, to show the shape without a model.
- **H3 (the mechanism).** If the effect is collisions, it should come from concurrent PRs that
  touch the same files. Split k into m (concurrent PRs sharing a file with this one) and k − m.
  **Prediction: the per-PR effect of m is larger than the per-PR effect of k − m, and the
  effect of k − m is close to zero.**
- **H4 (the size).** The p fitted from outcomes is below the share of concurrent pairs that share
  a file, because a shared file is an upper bound on a conflict. **Prediction: fitted p between
  0.005 and 0.05** (the paper's default is 0.05), **and below the file-overlap rate.**

## What would count against the model

- H1 fails: rework does not rise with concurrency within repositories. The r(N) term is then
  unsupported by this data, and the paper says so in its revision log.
- H3 fails in the specific way that k − m carries the effect as strongly as m: then something
  other than collisions (load, bursts of low-quality PRs, reviewer attention) drives it, and the
  paper's mechanism is wrong even if the curve holds.

## Sensitivity analyses (reported whatever they show)

1. k counting only concurrent PRs from a *different* agent or user, since one agent submitting a
   batch of weak PRs at once would produce the effect without any collision.
2. Agent fixed effects added (Codex, Copilot, Devin, Cursor, Claude Code merge at different rates).
3. k measured over the PR's first 24 hours instead of at creation.
4. Excluding repositories with more than 500 PRs, so a few very busy repos do not dominate.

## Known limitations, stated in advance

Observational data: busy periods may differ from quiet ones in ways the model does not capture.
"Closed unmerged" mixes rework with abandonment and duplicates. Only agent PRs are visible. None of
this is corrected after seeing the results; anything added later is labelled as exploratory.
