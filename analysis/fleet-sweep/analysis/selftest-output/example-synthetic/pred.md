## Point predictions (pre-registration, PLAN-v4)

Design (PLAN-v4 section 2, fixed in advance, no pilot gate): N = 1 and 12, 3 windows per size in ABBAAB order, 120 min each (10 min warm-up, 10 min grace), so hours = 1.833 per window. Constants: alpha = 0.1, beta = 0.01, p = 0.005; over-dispersion CV = 0.3 (fixed). Rival rework reading: `completion`.

### Pilot inputs

| Input | Value |
|---|---|
| lambda (first attempts per worker-hour at N = 1) | 5.400 |
| lambda 95% interval | 3.78 - 7.48 |
| lambda1 (single-agent, USL-anchored) | 5.400 |
| V (reviews per reviewer-busy hour) | 12.59 |
| V 95% interval (n = 68) | 9.8 - 16.0 |
| b_review | 0.338 |
| b_hidden (escaped + integration per approval) | 0.163 |
| b_other (rebase conflict + visible fail per approval) | 0.023 |
| b (implied, at N = 1) | 0.459 |
| b (measured directly) | 0.456 |
| r0 (share of first attempts bounced at least once) | 0.533 |
| completion share c (finished / attempts in the pilot windows) | 0.639 |
| merge-queue time per change (min) | 0.71 |
| escape rate (escaped / approvals) | 0.14 |
| review time (min) | 4.77 |
| review-time CV | 0.922 |
| review tokens | 8.78e+03 |
| worker tokens per attempt | 1.43e+05 |
| session start-up (min) | 2 |
| review_cv_ok (review-time CV <= 0.5; fixes whether V constancy is confirmatory) | **False** |

### Design-point load and abort rules

q = V / lambda1 = 2.33 (target 2). Review demand lambda1 X(N) / (1 - b) as a share of V: N = 1: 0.79, N = 12: 2.94.

- Abort rule 4 (calibrated V within +/-30% of 2 x pilot lambda = 10.8/h): **NOT EVALUATED**, no calibration log given (--calibration). Preview only: live pilot V / target = 1.17.
- Abort rule 3 (pilot lambda >= 0.5 x target = 4/agent-hour): lambda1 = 5.40 -> **OK**.
- Task supply (220 tasks per window): no rival's predicted claim rate uses it up within a window. A window that runs out is flagged by derive.py and its attempt-based measures stop at that minute (README decision 19).

### Finished changes and attempts per window (point prediction, 95% predictive interval: NB with CV 0.3)

| Rival | N | Finished | 95% interval | Attempts | Review demand /h | Reviews /h | Binding | b | V |
|---|---|---|---|---|---|---|---|---|---|
| Carnot, review-capped | 1 | 6.3 | 1-13 | 9.9 | 10.0 | 10.0 | demand | 0.459 | 12.6 |
| USL, no review limit | 1 | 6.3 | 1-13 | 9.9 | 10.0 | 10.0 | none | 0.459 |  |
| Amdahl, alpha only, no review limit | 1 | 6.3 | 1-13 | 9.9 | 10.0 | 10.0 | none | 0.459 |  |
| Linear | 1 | 6.3 | 1-13 | 9.9 | 10.0 | 10.0 | none | 0.459 |  |
| Carnot, review-capped | 12 | 11.8 | 4-23 | 34.7 | 37.0 | 12.6 | review | 0.488 | 12.6 |
| USL, no review limit | 12 | 22.2 | 9-40 | 34.7 | 37.0 | 37.0 | none | 0.488 |  |
| Amdahl, alpha only, no review limit | 12 | 36.1 | 16-64 | 56.6 | 60.3 | 60.3 | none | 0.488 |  |
| Linear | 12 | 75.9 | 35-130 | 118.8 | 126.5 | 126.5 | none | 0.488 |  |

### Totals over the 3 windows per size

| Rival | N | Finished total | 95% interval | Attempts total |
|---|---|---|---|---|
| Carnot, review-capped | 1 | 19.0 | 9-31 | 29.7 |
| USL, no review limit | 1 | 19.0 | 9-31 | 29.7 |
| Amdahl, alpha only, no review limit | 1 | 19.0 | 9-31 | 29.7 |
| Linear | 1 | 19.0 | 9-31 | 29.7 |
| Carnot, review-capped | 12 | 35.5 | 20-54 | 104.2 |
| USL, no review limit | 12 | 66.6 | 41-97 | 104.2 |
| Amdahl, alpha only, no review limit | 12 | 108.4 | 70-154 | 169.7 |
| Linear | 12 | 227.7 | 152-317 | 356.4 |

**O2 (confirmatory, secondary):** the total finished over the 3 N = 12 windows is predicted at 35.5, 95% predictive interval 20-54 (score.py recomputes it from each window's actual hours).

Predicted ratio finished(N = 12) / finished(N = 1) (descriptive only; S1r/S2r compare the observed ratio with Carnot's): Carnot, review-capped 1.87, USL, no review limit 3.51, Amdahl, alpha only, no review limit 5.71, Linear 12.00.

Also predicted by Carnot: V is a property of the reviewer (V(12)/V(1) = 1, review durations unchanged); b_review the same at both sizes; attempts rising with N as lambda1 X(N). The uncapped rivals imply a reviewer that keeps up with demand, i.e. V rising with load.

### Operating characteristics (simulated; PLAN-v4.1 section 6.8, OPERATING-CHARACTERISTICS.md, design-search/oc_v41.py, 2000 simulated studies per cell)

- **Confirmatory, primary: review is the binding limit.** Carnot's review-capped prediction has a higher likelihood than the best uncapped rival (USL, Amdahl, linear); the likelihood ratio is reported. Correct 0.97 under Carnot truth; 0.80 / 1.00 / 1.00 under USL / Amdahl / linear truth. If agents are 30% slower than assumed: 0.76 under Carnot, 0.66 under USL. At 1.5x credit burn with the degrade rule (2 x 120-min windows per size): 0.95 under Carnot, 0.75 under USL.
- **Confirmatory, secondary** (not identities of the harness): O2, finished at N = 12 inside Carnot's 95% predictive interval; S3, the review-bounce share b_review does not rise with N (one-sided Fisher exact); O3, attempts rise with N (exact rate-ratio test).
- **Conditionally confirmatory: the reviewer's pace does not change with load (Vdur).** Welch test on log review durations, N = 1 against N = 12; fails iff two-sided p < 0.05. Confirmatory only if the pilot's live (T1 + T2) review-time CV <= 0.5 (`review_cv_ok`), else descriptive. Power against a +/-25% reviewer: 0.73 at review-time CV 0.5, 0.18 at CV 1; false-positive rate 0.05 / 0.04. Probability that `review_cv_ok` is set: 1.00 / 0.53 / 0.00 at true CV 0.3 / 0.5 / 0.7 (near 0.5 it is close to a coin toss).
- **O2 false-alarm rate** under Carnot's own truth: 0.10 at review-time CV 1, 0.04 at CV 0.5 (above the nominal 0.05). S3 false-alarm rate 0.06; O3 power 1.00.
- **Descriptive** (reported whatever they show; a null result is not evidence):
  - V(12)/V(1) with its exact 95% interval; no equivalence claim (the interval is about 0.7-1.45 at this design);
  - four-way ranking of the rivals (USL vs Amdahl is not claimed). Simulated confusion matrix, rows = truth, columns = family with the highest likelihood:

    | truth | Carnot | USL | Amdahl | linear | tie |
    |---|---|---|---|---|---|
    | carnot | 0.96 | 0.02 | 0.00 | 0.00 | 0.01 |
    | usl | 0.20 | 0.57 | 0.22 | 0.01 | 0.01 |
    | amdahl | 0.00 | 0.16 | 0.68 | 0.17 | 0.00 |
    | linear | 0.00 | 0.00 | 0.07 | 0.93 | 0.00 |

  - escaped defects against reviewer queue depth (`logit_cluster_task`, >= 8 events or not modelled): power 0.12 at alpha 0.05;
  - collisions against in-flight changes (k-slope): power 0.08 at p = 0.01, 0.36 at p = 0.05;
  - S1r / S2r surprise ratios: false-alarm rates 0.05 / 0.53 under the model's own truth. S2r fires about half the time under Carnot at N = 12, so it is not a criterion.
- **Circularity.** At the design point the reviewer is 0.92 loaded with one agent and about 3.2x overloaded with twelve, so a flat finished count at N = 12 is expected by construction and is not itself evidence; the non-identity claims are the ones listed above.
