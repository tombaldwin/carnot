# Rework's shape: repeat returns and knock-on (AIDev, exploratory)

Exploratory: written on 2026-09-30, after study 1's results, and **not pre-registered**. Script:
`rework.py`. Numbers: `rework.json`. The heat-death paper's model treats rework as one constant rate
*r*. This asks two things of that assumption, on the same data as study 1.

**Sample.** Review-gated repositories as `exploratory.py` defines them (median PR life at least one
hour) with at least 20 agent PRs, as in `analyze.py`: 331 repositories, 21,858 PRs, of which 10,899
were closed after at least one review. (Study 1's exploratory figure of 298 repositories used a
different floor.) A wider sample without the 20-PR floor, 3,912 repositories, is reported beside it.
A "return" here is a CHANGES_REQUESTED review: the "sent back in review" part of the paper's *r*,
not reverts or later fixes. AIDev has no commit timestamps or line counts, so the *size* of rework
cannot be measured, and two requests with no push between them cannot be told apart.

## A. Returns cluster

| Rounds sent back so far | 0 | 1 | 2 | 3 | 4 |
|---|---|---|---|---|---|
| Chance of another round, every request a round | 11% | 35% | 45% | 48% | 57% |
| ... requests within 10 min counted as one | 11% | 34% | 42% | 44% | 66% |
| ... requests within 60 min counted as one | 11% | 25% | 29% | 41% | 47% |

(Wider sample, every request a round: 14%, 34%, 44%, 51%, 57%.) A constant rate would keep each row
flat. However rounds are counted, a PR that has come back once is two to three times as likely as a
fresh one to come back again, and the chance keeps rising. Total rounds per PR are more than a
geometric series with the same first-return rate predicts: by +52% counting every request, +46%
merging requests within 10 minutes, and +22% within an hour (wider sample +46%, +39%, +15%). The
median gap between consecutive requests on a PR is about an hour, so the true figure is likely in
that range: **between a fifth and a half again**.

**Per change or per review.** The clustering matters only for *r* counted per change, as the share
sent back at least once (11% here). Counted per review, as the share of reviews that send a change
back, *r* already includes the repeats (16% here, and plugged into the model that way it gives the
right total). The paper's controlled trial counted per review, and there its reviewer, a model,
sent rework back at the same rate as first attempts (15 of 37, 41%, against 31 of 80, 39%): no
clustering, on a small sample. The clustering here is in reviews by people, of agent-written
changes.

For comparison, in human-written code fixes come back more often than first changes do: about 10%
of changes need a follow-up fix (Śliwerski et al. found 11.4% in Eclipse), while 22–33% of resolved
bugs needed more than one fix (Park et al., MSR 2012) and at least 14.8–24.4% of fixes to
post-release bugs in four large operating systems, one of them commercial, were themselves wrong
(Yin et al., ESEC/FSE 2011).

## B. Knock-on: none found in send-backs; abandonment unresolved

PRs with exactly one other PR merged into their files while they were open: 2,219, of which 202
merges had needed rework; 91 repositories have PRs in both arms. Within repositories (Mantel–
Haenszel risk differences, 95% bootstrap over repositories):

| Outcome for the PR | Reworked merge vs clean merge |
|---|---|
| Sent back *after* that merge | +1.0 points (−5.3 to +6.6) |
| Sent back *before* that merge (placebo) | +8.9 (+1.9 to +15.6) |
| After minus before | −7.8 (−18.1 to +2.3) |
| Closed unmerged | +5.9 (−2.9 to +15.4) |

A PR whose files received a reworked merge was not detectably more likely to be sent back
afterwards, though it had been sent back more often *before* that merge: strict reviewers and busy
repositories. The clean arm's raw rate is about 2%, so the upper bound of +6.6 points does not rule
out a real knock-on in relative terms. The placebo is not neutral either: on the wider sample
after-minus-before is significantly negative (−8.8, −17.3 to −0.3), which weakens reading the
difference as a clean test. Abandonment rises (+5.9 here; wider sample +10.1, +2.9 to +18.5, which
excludes zero), and there is no placebo for it, so whether reworked merges get other PRs abandoned
is open.

**For the model.** Counted per change, *r* is a floor: returns cluster, and the rework it implies is
understated by between a fifth and a half. Counted per review it already includes the repeats. The
repeat returns are re-reviews: if the paper's q counts only first submissions, review binds at
fewer agents than its equation 1 says; counted with resubmissions, as its section 9 does, q
already allows for them. No knock-on was found in send-backs, and none can be ruled out;
knock-on through abandonment is unresolved.
