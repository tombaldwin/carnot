# Results: does rework rise with concurrency? (AIDev, study 1)

Pre-registration: [PREREG.md](PREREG.md), committed and pushed before any outcome was read.
Script: `analyze.py` (pre-registered analysis), `exploratory.py` (written after seeing the results,
and labelled as such). Numbers: `results.json`, `exploratory.json`.

**Short version.** In this data, agent PRs that are open alongside many others are *not* rejected
more often, and the model's collision term is not supported at the size the paper assumed. Within
repositories that look review-gated, there is a small rise in rejection with concurrency, about
0.2% per concurrent PR, but it comes from concurrent PRs that touch *different* files, not the
same ones. That points at load on reviewers rather than collisions between changes.

## Implementation note (before the results)

The first run's confidence intervals were wrong: the interval search started with a step of 0.002,
which floored every interval at about ±0.001. That made the main effect look both "significant"
(by the likelihood-ratio test, which was unaffected) and "consistent with zero" (by the interval).
Fixed on 2026-09-27 by starting the search at 10⁻⁶; checked against synthetic data with a known
answer (true p = 0.03, recovered 0.029, 95% interval 0.022–0.037). No other change was made to the
analysis.

## Sample

48,128 closed agent PRs in 455 repositories with at least 20 each. 22% closed unmerged. Median
concurrency at creation k = 5; mean 33, because a few repositories run hundreds of agent PRs at
once. The ten largest repositories hold a third of the sample, and several of them are agents
opening and merging their own PRs within seconds (one repository has 13,194 PRs with a median life
of 36 seconds).

## Pre-registered hypotheses

| | Prediction | Result | Verdict |
|---|---|---|---|
| H1 | Rejection rises with k within repos (θ < 0, LR p < 0.01) | θ = +0.0004 (95% interval +0.00037 to +0.00042): rejection *falls* very slightly with k. LR p < 10⁻⁴⁸ | **Fails** (wrong direction) |
| H2 | Geometric form fits no more than 2 AIC worse than linear | 4.4 AIC worse | **Fails** |
| H3 | Effect comes from concurrent PRs sharing a file (m), not the rest | p per file-sharing PR = 0.0017 (interval 0.0004–0.0029); per non-sharing PR ≈ −0.0004 | **Passes**, with a tiny effect |
| H4 | Fitted p between 0.005 and 0.05, and below the file-overlap rate | p ≈ −0.0004; file overlap among concurrent pairs is 9.9% | **Fails** on size |

Sensitivity analyses, as pre-registered: counting only concurrent PRs from a different user
(p = 0.002, interval −0.004 to 0.009) or a different agent (p = 0.004, interval −0.002 to 0.011)
gives small positive estimates that are not distinguishable from zero. Agent fixed effects and
k over 24 hours reproduce the primary result. Repositories with at most 500 PRs: p = 0.000
(interval −0.0001 to 0.0002). The secondary outcome (unmerged or changes requested) matches the
primary.

## Exploratory (not pre-registered)

Restricting to the 298 repositories whose median PR stays open at least an hour, a crude marker of
a review step, leaves 19,325 PRs with 37% closed unmerged:

- Rejection rises with concurrency: p = 0.0018 per concurrent PR (interval 0.0003–0.0033, LR p = 0.02).
- The rise comes from concurrent PRs that touch *different* files (0.0084, interval 0.0048–0.0122),
  not from those sharing a file (−0.0001, interval −0.0018 to 0.0012).

This is the opposite split to H3 in the full sample, and it was found after looking, so it is a
hypothesis, not a finding: in review-gated repositories, concurrency costs through reviewer load
rather than through collisions.

## What this means for the model

1. **The paper's default p = 0.05 is not supported as "chance a concurrent change forces rework".**
   Measured textual-conflict rates of 20–42% between concurrent agent PRs do not turn into
   rejections at anything like that rate here; conflicts appear to be resolved cheaply.
2. **Collisions look like the weakest of the three forces in this data; review looks like the
   strongest.** That supports the paper's third line ("never more agents than your reviewers can
   check") more than its first.
3. **Limits.** Only agent PRs are visible, "closed unmerged" is a proxy for rework, and the
   repositories are mostly small, open-source and often single-maintainer. A controlled fleet
   sweep (study 2) is the better test, and should now be designed around review load as well as
   collisions.
