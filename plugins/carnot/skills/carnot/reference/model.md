# The Carnot model

From Polymorphism's working paper *The Heat Death of the Codebase? Paying Maxwell's Demon a Day Rate* (2026). The best finished output the model allows is the fleet's Carnot limit.

## Equations

With N agents working in parallel:

- Coordination drag (Gunther's Universal Scalability Law), in single-agent units:
  `X(N) = N / (1 + α(N−1) + β·N(N−1))`
- Rework, rising because each change can collide with the N−1 others in flight:
  `r(N) = 1 − (1 − r₀)(1 − p)^(N−1)`
- Review capacity, in changes per day:
  `cap = reviewers × hours × rate × ρ / (1 − auto) / lines_per_change`
- Finished changes per day:
  `U(N) = (1 − r(N)) × min(λ·X(N), cap)`

The best fleet size is the N that maximises U. Rule of thumb, with q = cap ÷ λ (review capacity in single-agent outputs): `N ≈ q / (1 − αq − βq²)`, but never more than `(1 − α) / (p + √β)` (if αq + βq² ≥ 1, review never binds and the cap decides). Finished work ≈ (1 − r₀) × cap when review binds, else ≈ (1 − r₀) × λN / (2 + αN). Tested over 1,050 combinations (α 0.02–0.4, β 0.001–0.035, p 0–0.05, q 0.5 to unlimited): within one agent of the best in 91% of cases; running at the rule's size gets at least 92% of the best output in every case and 98% in nine out of ten; the finished-work estimate is within 15% in three cases out of four.

## Parameters

| Symbol | Meaning | Default | Published range |
|---|---|---|---|
| r₀ | Share of changes needing rework at N = 1 | 0.40 (agents) | ~0.07–0.11 human changes (Capers Jones; Śliwerski et al. 2005); ~0.5 of test-passing agent PRs not mergeable (METR 2026) |
| p | Chance a pair of changes open at the same time collides badly enough to need rework | 0.01 | Textual conflicts are common (0.198 same-agent / 0.417 cross-agent for agent PRs open together, Xu et al.; Uber 5% real conflicts for two changes to the same area) but rarely force a redo: the pre-registered AIDev test (analysis/aidev in this repo) found p ≈ 0–0.004 |
| α | Share of work that must happen one at a time | 0.10 | 0.12 (Khailo 2026 fit); ~0.37 implied by Cursor's lock-based fleet |
| β | Coordination cost per pair of agents | 0.01 | 0.002–0.035 (0.032 in Khailo's fit, measured across separate repos) |
| λ | Changes one agent submits per day | 6 | measure it |
| rate | Lines a reviewer can check properly per hour | 200 | Quality falls above 200 (Kemerer & Paulk 2009); sharp drop above ~500 (SmartBear/Cisco) |
| hours | Focused review hours per reviewer per day | 4 | 2–6 |
| auto | Share of verification done by automation | 0.5 | ask |
| ρ | Target reviewer utilisation | 0.75 | 0.6–0.85; queues grow fast above 0.85 (Kingman) |

## How calibration measures things

- **r₀**: a change counts as reworked if it was reverted, got a "changes requested" review, was closed unmerged after review, or a later fix (by title or branch name) touched one of its files within 14 days. The fix is attributed to the most recent earlier change to that file (a file-level version of the SZZ method).
- **p**: pairs of changes that were open at the same time are replayed with `git merge-tree`. Pairs where one branch already contains the other (rebased or stacked) are skipped. The rate is smoothed as (conflicts + 1) / (pairs + 2), so zero conflicts in a small sample doesn't read as p = 0.
- **λ**: back-solved from finished changes per active day when the user says how many agents typically ran.
- **α, β**: not measurable from history; fit them from a fleet sweep with `carnot.py fit`.
