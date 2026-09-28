#!/usr/bin/env python3
"""PLAN-v5 design-search simulation core (imports synth / derive / v5 unchanged; no network, API or harness).

One call of `study5(cfg)` simulates one complete v5 study under a chosen truth and scores it with the v5 codings:

    [optional T2 pilot: pilot_w separate 60-min windows at N = 1]
    -> sweep windows (sizes x replicates per size, window length L, v5_order) with the v5 process (synth.V5_TRUTH:
       one Haiku session per task in N slots, 1.3-min start-up, 25-min timeout, the fast reviewer at 10-30 s per
       change, a ~2 s serial merge queue, collisions per other change merged since a change's base, window CV 0.3)
    -> derive -> v5.bend_test (three methods), v5.family_fit (free level; and pilot-anchored if there is a pilot),
       v5.escape_v5, v5.collision_v5, v5.utilisation_v5

T1 (1 slot 30 min, then 12 slots 30 min) is costed but not simulated: in v5 it only checks throttling and product
facts, and no test reads it.

Cost: burn x (T0 + T1 + pilot + sweep session-hours); T0 is about $1 at the assumed $2.10 per session-hour, i.e.
0.476 session-hours; T1 6.5 session-hours; a window at N slots of L minutes is N x L / 60 session-hours.
"""
from __future__ import annotations

import dataclasses
import math
import sys
from dataclasses import dataclass, asdict
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from derive import derive_window  # noqa: E402
from synth import T0, V5_FAMILIES, make_task_pool, make_truth_v5, simulate, v5_order  # noqa: E402
import v5  # noqa: E402

BUDGET = 200.0            # $250 credits minus the $50 floor
BURN = 2.10               # assumed $ per session-hour (Haiku cloud session; unmeasured)
T0_H = 1.0 / BURN         # T0 costs about $1 at the assumed burn
T1_H = 6.5                # 1 slot x 30 min + 12 slots x 30 min


@dataclass(frozen=True)
class D5:
    sizes: tuple = (1, 12)
    reps: tuple = (8, 3)          # windows per size, aligned with sizes
    L: int = 120                  # sweep window, minutes (10 warm-up, 10 grace)
    pilot_w: int = 0              # separate 60-min T2 pilot windows at N = 1 (v4-style); 0 = none
    anchor: str = "free"          # free: every rival's level fitted to the sweep | pilot: level fixed from the pilot

    def label(self):
        r = " ".join(f"{n}x{k}" for n, k in zip(self.sizes, self.reps))
        return f"N {r} @{self.L}m" + (f" +T2 {self.pilot_w}x60m ({self.anchor})" if self.pilot_w else "")

    def sweep_h(self):
        return sum(n * k for n, k in zip(self.sizes, self.reps)) * self.L / 60.0

    def total_h(self):
        return T0_H + T1_H + self.pilot_w * 1.0 + self.sweep_h()

    def cost(self, burn=BURN):
        return burn * self.total_h()

    def windows(self):
        return sum(self.reps) + self.pilot_w

    def wall_h(self):
        """Operator wall-clock for the sweep + pilot: window + grace + ~15 min reset per window."""
        return sum(k * (self.L + 10 + 15) for k in self.reps) / 60.0 + self.pilot_w * (60 + 10 + 15) / 60.0


def fits(d: D5, burn=BURN, budget=BUDGET):
    return d.cost(burn) <= budget + 1e-9


# ------------------------------------------------------------------------------------------------ truths
# name -> (worker family, synth overrides)
ESC_N = {"esc15": 0.62, "esc20": 1.25}   # reviewer miss x (1 + escape_N) at N = 12: escape share 0.10 -> ~0.15 / ~0.20


def build_truth(tname, over=None):
    fam = tname.split("/")[0]
    kw = {}
    for part in tname.split("/")[1:]:
        if part in ESC_N:
            kw["escape_N"] = ESC_N[part]
        elif part.startswith("p="):
            kw["p"] = float(part[2:])
        elif part == "census":
            kw["collision_model"] = "census"
        elif part.startswith("man="):
            kw["census_manifest"] = float(part[4:])
        elif part.startswith("cv="):
            kw["cv_window"] = float(part[3:])
        elif part == "slowrev":
            kw.update(service_lo_s=20.0, service_hi_s=40.0)
        elif part == "lam70":
            kw["lam1"] = 6.2     # lambda(1) about 4.2 per slot-hour, 30% below the dry runs' 6
        elif part == "nobg":
            kw["integration_bg"] = 0.0
        else:
            raise ValueError(part)
    kw.update(over or {})
    return make_truth_v5(fam, **kw)


def family_of(tname):
    return tname.split("/")[0]


# ------------------------------------------------------------------------------------------------ one study
def study5(cfg):
    """cfg: design (dict of D5), truth (name), seed, cv_len (bool: window CV scales as 0.3 sqrt(120 / L)),
    parts (tuple: 'scale', 'escape', 'collision', 'util'; default all)."""
    d = D5(**{**cfg["design"], "sizes": tuple(cfg["design"]["sizes"]), "reps": tuple(cfg["design"]["reps"])})
    tname = cfg["truth"]
    seed = cfg["seed"]
    over = dict(cfg.get("over") or {})
    truth = build_truth(tname, over)
    if cfg.get("cv_len"):
        truth = dataclasses.replace(truth, cv_window=truth.cv_window * math.sqrt(120.0 / d.L))
    pool = make_task_pool(truth, seed * 31 + 5)
    t0 = T0 + (seed % 100000) * 86400.0
    out = dict(seed=seed, truth=tname)
    pil = []
    for i in range(d.pilot_w):
        r, e = simulate(truth, 1, seed=seed * 1000 + 2 + 100 * i, window_min=60, warmup_min=10, grace_min=10,
                        task_pool=pool, run_id=f"s{seed}-T2-{i}", kind="pilot", t0=t0 + (3 + 1.5 * i) * 3600)
        pil.append(derive_window(r, e)["summary"])
    reps = dict(zip(d.sizes, d.reps))
    order = v5_order(d.sizes, reps)
    derived = []
    for i, n in enumerate(order):
        r, e = simulate(truth, n, seed=seed * 1000 + 10 + i, window_min=d.L, warmup_min=10, grace_min=10,
                        task_pool=pool, run_id=f"s{seed}-N{n}-w{i + 1}", kind="sweep", t0=t0 + (20 + 2.5 * i) * 3600)
        derived.append(derive_window(r, e))
    S = [x["summary"] for x in derived]
    parts = cfg.get("parts") or ("scale", "escape", "collision", "util")
    nmax = max(d.sizes)
    if "scale" in parts:
        b = v5.bend_test(S, method="lr_fixed")
        out["bend_p"] = b["p"]
        out["pa_ratio"] = b["per_agent_ratio"]
        out["pa_ci"] = b.get("per_agent_ratio_ci")
        out["bend_p_estcv"] = v5.bend_test(S, method="lr_estcv")["p"]
        out["bend_p_welch"] = v5.bend_test(S, method="welch")["p"]
        f = v5.family_fit(S)
        out["best"], out["best3"], out["binary"] = f["best"], f["best3"], f["binary"]
        if d.pilot_w:
            fin = sum(s["finished"] for s in pil)
            hrs = sum(v5.exposure_hours(s) for s in pil)
            if fin > 0:
                fa = v5.family_fit(S, anchor_theta=fin / hrs)
                out["best_anchor"], out["binary_anchor"] = fa["best"], fa["binary"]
            else:
                out["best_anchor"] = out["binary_anchor"] = "PILOT_UNUSABLE"
        out["finished"] = {int(n): float(np.mean([s["finished"] for s in S if s["n_workers"] == n])) for n in d.sizes}
        out["lam"] = {int(n): float(np.mean([s["lam"] for s in S if s["n_workers"] == n])) for n in d.sizes}
        out["timeouts"] = {int(n): float(np.mean([s["session_timeouts"] for s in S if s["n_workers"] == n]))
                           for n in d.sizes}
    if "escape" in parts:
        e = v5.escape_v5([a for x in derived for a in x["approvals"]], n_max=nmax)
        out["esc_p"] = e["p_trend"]
        out["esc_rate"] = e["rate"]
        out["esc_n"] = e["n"]
        out["esc_events"] = e["events"]
        out["esc_hw"] = e["halfwidth"]
        out["esc_cl_hw"] = (e["cluster95"][1] - e["cluster95"][0]) / 2 if e["cluster95"][0] == e["cluster95"][0] else None
        out["esc_by_size"] = {k: v["rate"] for k, v in e["by_size"].items()}
    if "collision" in parts:
        c = v5.collision_v5([p for x in derived for p in x["prs"]], model_form=cfg.get("model_form", False))
        out["coll_p"] = c["p_j"]
        out["coll_pm"] = c["p_jm"]
        out["coll_pk"] = c["p_k"]
        out["coll_events"] = c["events"]
        out["coll_n"] = c["n"]
        out["j_mean_max"] = c["by_size"].get(nmax, {}).get("j_mean")
        if cfg.get("model_form") and c.get("p_ci"):
            out["p_hat"], out["p_ci"] = c.get("p_hat"), c.get("p_ci")
    if "util" in parts:
        u = v5.utilisation_v5(S)
        out["ru_max"] = u[nmax]["reviewer_util_max"]
        out["ru_mean"] = u[nmax]["reviewer_util_mean"]
        out["mq_max"] = u[nmax]["mq_util_max"]
        out["mq_mean"] = u[nmax]["mq_util_mean"]
        out["rev_per_h"] = u[nmax]["reviews_per_hour"]
    out["status"] = "SCORED"
    return out
