## Point predictions (pre-registration, PLAN-v3 sections 2-3)

Constants: alpha = 0.1, beta = 0.01, p = 0.005; over-dispersion CV = 0.3 (fixed). Window 90 min, warm-up 10 min, so hours = 1.333 per window. Rival rework reading: `plan`.

### Pilot inputs

| Input | Value |
|---|---|
| lambda (first attempts per worker-hour at N = 2) | 5.400 |
| lambda 95% interval | 2.47 - 10.25 |
| lambda1 (single-agent, USL-anchored) | 6.048 |
| V (reviews per reviewer-busy hour) | 10.36 |
| V 95% interval (n = 13) | 5.5 - 17.7 |
| b_review | 0.385 |
| b_hidden (escaped + integration per approval) | 0.143 |
| b_other (rebase conflict + visible fail per approval) | 0.143 |
| b (implied, at N = 2) | 0.548 |
| b (measured directly) | 0.538 |
| r0 (share of first attempts bounced at least once) | 0.500 |
| completion share (finished / attempts in the pilot window) | 0.111 |
| merge-queue time per change (min) | 2.33 |
| escape rate (escaped / approvals) | 0.143 |
| review time (min) | 5.67 |
| review tokens | 8.79e+03 |
| worker tokens per attempt | 1.65e+05 |
| session start-up (min) | 2 |

### Gate

q = V / lambda1 = 1.71. Review demand lambda1 X(N) / (1 - b), reviews per hour:

| N | 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 | 9 | 10 |
|---|---|---|---|---|---|---|---|---|---|---|
| demand | 13.3 | 23.9 | 32.0 | 38.1 | 42.4 | 45.5 | 47.5 | 48.8 | 49.5 | 49.7 |
| demand / V | 1.28 | 2.30 | 3.09 | 3.67 | 4.09 | 4.39 | 4.59 | 4.71 | 4.77 | 4.80 |

N_low = None, N_high = 2. **Decision: REDESIGN_SATURATED_AT_1** - demand at N = 1 (13.3/h) exceeds 0.7 V (7.3/h): the reviewer is saturated even with one worker, so no size sits below the knee. Lighten the review job or enlarge tasks (pre-registered), or run as a plateau-only study.

The gate can pass only if V lies in [19.0, 31.4] reviews/h (q in [3.14, 5.19]). With these alpha, beta, X(8)/X(2) = 1.98 < 1.5/0.7 = 2.14, so N_low = 1 is the only size that can sit below the knee with N_high <= 8.

### Finished changes per window (point prediction and 95% predictive interval, NB with CV 0.3)

| Rival | N | Finished | 95% interval | Attempts | Review demand /h | Reviews /h | Binding | b | V |
|---|---|---|---|---|---|---|---|---|---|
| Carnot, review-capped | 1 | 6.3 | 1-13 | 8.1 | 13.3 | 10.4 | review | 0.546 | 10.4 |
| USL, no review limit | 1 | 4.0 | 0-9 | 8.1 | 13.3 | 13.3 | none | 0.546 |  |
| Amdahl, alpha only, no review limit | 1 | 4.0 | 0-9 | 7.9 | 13.1 | 13.1 | none | 0.546 |  |
| Linear | 1 | 3.6 | 0-9 | 7.2 | 11.9 | 11.9 | none | 0.546 |  |
| Carnot, review-capped | 5 | 6.2 | 1-13 | 25.2 | 42.4 | 10.4 | review | 0.555 | 10.4 |
| USL, no review limit | 5 | 12.6 | 4-24 | 25.2 | 42.4 | 42.4 | none | 0.555 |  |
| Amdahl, alpha only, no review limit | 5 | 14.1 | 5-27 | 28.3 | 47.6 | 47.6 | none | 0.555 |  |
| Linear | 5 | 18.0 | 7-33 | 36.0 | 60.6 | 60.6 | none | 0.555 |  |

Predicted ratio finished(N_high) / finished(N_low) (descriptive only): Carnot, review-capped 0.98, USL, no review limit 3.13, Amdahl, alpha only, no review limit 3.57, Linear 5.00.

Also predicted, all rivals: V equal to the pilot's within +/-25% at both sizes and in both half-windows (Carnot; the uncapped rivals imply a reviewer that keeps up, i.e. V rising with load); b and the escape rate as in the pilot, with the Carnot model predicting escapes rising with queue depth.

