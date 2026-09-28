## Point predictions and design (pre-registration, PLAN-v5)

Design (PLAN-v5 section 5): N = 1, 12; windows 12 at N = 1, 3 at N = 12, 90 min each (10 min warm-up, 10 min grace), order 1, 1, 12, 1, 1, 1, 1, 12, 1, 1, 1, 1, 12, 1, 1. No T2 pilot: every rival's level is fitted to the sweep's own windows (so its N = 1 windows anchor it); T1 checks throttling only. Constants alpha = 0.1, beta = 0.01, p = 0.005; over-dispersion CV 0.3 per window.

### Budget

| | session-hours | at the given burn | at 1.5x |
|---|---|---|---|
| T0 + T1 + sweep | 78.5 (+ T0 ~$1) | $166 at $2.10/h | $249 |
| degrade design (12 x N = 1, 2 x N = 12) | 60.5 | $128 | $192 |

Credits: balance $249; the full sweep (72 session-h x $2.10) leaves $98, the degrade sweep $136 (floor $50) -> **run the full design**.

### What each rival predicts (finished per window relative to N = 1; no pilot needed)

| Rival | N = 1: ratio to 1 | per agent | N = 12: ratio to 1 | per agent |
|---|---|---|---|---|
| Linear | 1.00 | 1.00 | 12.00 | 1.00 |
| Amdahl (alpha only) | 1.00 | 1.00 | 5.71 | 0.48 |
| USL (alpha, beta) | 1.00 | 1.00 | 3.51 | 0.29 |
| Carnot uncapped (USL x collision rework) | 1.00 | 1.00 | 3.32 | 0.28 |

SCALE (confirmatory, primary) reads BEND iff the one-sided LR test of per-agent output falling with N gives p < 0.05. Linear predicts a per-agent ratio of 1 (LINEAR-NOT-REJECTED); every other rival predicts BEND.

### Illustrative counts and loads (lambda 6.00 per slot-hour, completion 0.90, b_review 0.30, review 20 s, merge queue 2 s per change)

| Rival | N | finished / window | 95% | windows | total | 95% | reviews /h | reviewer util | at 1.5x review time | merge-queue util | supply out at min |
|---|---|---|---|---|---|---|---|---|---|---|---|
| Linear | 1 | 7.2 | 2-15 | 12 | 86.4 | 64-111 | 9 | 0.05 | 0.07 | 0.003 | - |
| Linear | 12 | 86.4 | 41-148 | 3 | 259.2 | 174-360 | 103 | 0.57 | 0.86 | 0.040 | - |
| Amdahl (alpha only) | 1 | 7.2 | 2-15 | 12 | 86.4 | 64-111 | 9 | 0.05 | 0.07 | 0.003 | - |
| Amdahl (alpha only) | 12 | 41.1 | 18-72 | 3 | 123.4 | 80-174 | 49 | 0.27 | 0.41 | 0.019 | - |
| USL (alpha, beta) | 1 | 7.2 | 2-15 | 12 | 86.4 | 64-111 | 9 | 0.05 | 0.07 | 0.003 | - |
| USL (alpha, beta) | 12 | 25.3 | 10-46 | 3 | 75.8 | 48-109 | 30 | 0.17 | 0.25 | 0.012 | - |
| Carnot uncapped (USL x collision rework) | 1 | 7.2 | 2-15 | 12 | 86.4 | 64-111 | 9 | 0.05 | 0.07 | 0.003 | - |
| Carnot uncapped (USL x collision rework) | 12 | 23.9 | 10-43 | 3 | 71.7 | 45-104 | 30 | 0.17 | 0.25 | 0.012 | - |

Reviewer: a single serial call per change. Under linear workers at N = 12 it would be busy about 57% of the time (86% if reviews take 1.5x as long): approaching saturation, so UTIL is reported beside SCALE and a window with the reviewer busy >= 80% is flagged. The merge queue stays below 5% busy under every rival.

### Abort rules (PLAN-v5 section 5)

- Rule 1 (throttling, T1, unchanged from PLAN-v4 section 7.5): twelve-slot activity rate / one-slot rate < 0.8 -> stop and report T1.
- Rule 2 (credits): burn = (M0 - M1) / T1 session-hours. Run the full design if the predicted balance after it is >= $50; else the degrade design (the same without one N = 12 window) if that is; else stop and report T0/T1. Before every window the predicted balance after the rest of the chosen design must stay >= $50; if not, drop the remaining N = 12 window(s) first, then stop. An interrupted sequence is analysed as run (every test is defined for >= 1 window per size); an N = 12 window is never run without at least two N = 1 windows before it.
- Rule 3 (lambda floor): after the first two N = 1 sweep windows, pooled lambda < 3 first submissions per slot-hour (half the dry runs' 6) -> stop before the first N = 12 window and report; the operating characteristics were computed down to lambda -30%.
- Rule 4 (review must not bind): reviewer utilisation >= 0.8 in an N = 12 window, or the mean review above 60 s -> the window is flagged in UTIL and SCALE is reported with and without it (not a stop).
- Rule 5 (merge queue): mean merge-queue time per change above 30 s (dry runs: about 2 s) -> stop and fix the harness before the next window.

### Operating characteristics (simulated; OPERATING-CHARACTERISTICS-v5.md, design-search/oc_v5.py, 2000 simulated studies per cell)

- **Confirmatory, primary (SCALE):** per-agent finished output falls from N = 1 to N = 12 (one-sided NB LR, CV 0.3). False alarm under linear workers 0.03 (window CV 0.3), 0.00 at CV 0.15, 0.11 at CV 0.5; power 0.91 under Amdahl (alpha 0.1), 1.00 under USL, 1.00 under Carnot uncapped, 0.26 against a mild bend (alpha 0.03, not a rival). At 1.5x burn (degrade design): power 0.82, false alarm 0.03; at lambda -30%: 0.89 / 0.03.
- **Confirmatory, secondary (COLL):** collisions rise with merges since a change's base (one-sided LR on j). False alarm 0.02; power 0.28 at p = 0.005, 0.52 at p = 0.01, 0.83 at p = 0.02, 0.98 at p = 0.05 with USL-like workers; 0.83 at p = 0.005, 0.98 at p = 0.01, 1.00 at p = 0.02 with linear workers (more merges). A null result rules out p of about 0.02 or more, not p = 0.005.
- **Descriptive:** ESC-N (escapes rising with N): power 0.78 for 0.10 -> 0.20, 0.37 for 0.10 -> 0.15, false alarm 0.06; ESC (the escape rate, median 95% half-width 0.038); FAMILY (four-way pick; USL and Carnot are not separable); UTIL; COLL-m; COLL-k; LAMBDA; BOUNCE; EFFORT.
