#!/usr/bin/env python3
"""Self-test of the fleet-sweep analysis on synthetic sweeps (synth.py -> derive -> predict -> score), PLAN-v4.

    python selftest.py              # full run (a few minutes, uses all cores)
    python selftest.py --quick      # fewer replicates, for a smoke test
    python selftest.py --reps 400 --out selftest-output

Parts:
  U  unit checks: derive on hand-built logs with known answers (accounting, the grace-end V correction,
     task-supply truncation from a note and from reset.json); schema validator accepts synth output and
     rejects broken lines; CLI end to end at the PLAN-v4 design (synth --v4 -> validate -> derive ->
     predict -> score) in a temp dir, checking that predict has no pilot gate by default (no REDESIGN_*),
     predicts all four rivals at N = 1 and 12 with the operating-characteristics statement, keeps the
     superseded gate behind --v3-gate, and that every result in RESULTS-draft.md carries a grade.
  V  the PLAN-v4 design point through the whole pipeline (T1 1 -> 12, eight 60-min T2 windows of one
     worker, sizes 1 and 12, three 120-min windows each, ABBAAB, 220 tasks) under Carnot, USL, Amdahl and
     linear truths and a reviewer 25% faster / slower at N = 12 (skimN / slowN), at review-time CV 1 and
     0.5: rates of every v4 coding (P1, O2, S3, O3, Vratio, Vdur, S1r, S2r) and of review_cv_ok, with
     DESIGN-SEARCH's figures alongside.
  S  task supply: linear truth with only 120 tasks at the design point, so N = 12 windows run out; lambda
     and the per-agent attempt ratio with and without the truncation.
  D  escaped defects: planted depth effect vs none; detection and false-positive rates per model.
  E  collisions: planted p (and p_m), recovery of p-hat, CI coverage, k-slope rates; p = 0 false positives;
     censoring check (review v2, C): the k-slope with censored excluded vs the naive "not finished" outcome.
The PLAN-v3 parts (A: the gate, B: sizes 1/5 with the v3 codings, C: review-comparable N = 3/8 table,
F: v3 surprise codings) were retired with PLAN-v4; their last results are in git history
(selftest-output/SELFTEST.md at commit 5f213af).
Writes <out>/selftest.json and <out>/SELFTEST.md.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import subprocess
import sys
import tempfile
import time
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from common import CONDITIONAL, CONFIRMATORY, DESCRIPTIVE, V4_OC, iso  # noqa: E402
from derive import derive_window, pilot_params  # noqa: E402
from predict import Params  # noqa: E402
from score import score  # noqa: E402
from synth import T0, make_truth, simulate, simulate_study  # noqa: E402
import validate_schema  # noqa: E402

PY = sys.executable


# ---------------------------------------------------------------------- U: unit checks
def _ev(t_min, typ, **f):
    return {"t": iso(T0 + 60 * t_min), "type": typ, **f}


def unit_derive():
    run = {"run_id": "unit", "kind": "sweep", "n_workers": 1, "window_start": iso(T0), "window_end": iso(T0 + 3600),
           "warmup_min": 10, "grace_min": 10, "task_order_seed": 1, "sandbox_commit": "x", "harness_commit": "x",
           "worker_model": "x", "reviewer_model": "x", "notes": ""}
    S = lambda t, task, head, a, k=0, m=0: _ev(t, "submit", worker="w1", task=task, branch=f"claude/task-{task}", head=head,
                                               attempt_no=a, lines_changed=50, files=["a.py"], k=k, m=m)
    RS = lambda t, task, head, d=0: _ev(t, "review_start", task=task, head=head, queue_depth=d)
    RE = lambda t, task, head, v: _ev(t, "review_end", task=task, head=head, verdict=v, reason="r", tokens_in=1,
                                      tokens_out=1, duration_s=60.0)
    ok_merge = lambda t, task, head: [_ev(t, "hidden_pre", task=task, head=head, passed=True),
                                      _ev(t, "rebase", task=task, head=head, new_head=head + "r", conflict=False),
                                      _ev(t + 0.5, "tests_post", task=task, head=head, visible_passed=True, hidden_passed=True),
                                      _ev(t + 0.5, "merge", task=task, head=head, main_sha="m" + head)]
    ev = [_ev(0, "worker_start", worker="w1", session_id="s"),
          S(5, "E", "e1", 1), _ev(5, "reviewer_busy"), RS(5, "E", "e1"), RE(10, "E", "e1", "approve"), _ev(10, "reviewer_idle"),
          *ok_merge(11, "E", "e1"),
          S(15, "A", "a1", 1), _ev(15, "reviewer_busy"), RS(15, "A", "a1"), RE(20, "A", "a1", "approve"), _ev(20, "reviewer_idle"),
          *ok_merge(21, "A", "a1"),
          S(30, "B", "b1", 1), _ev(30, "reviewer_busy"), RS(30, "B", "b1"), RE(36, "B", "b1", "request_changes"),
          _ev(36, "bounce", task="B", head="b1", cause="review"), _ev(36, "reviewer_idle"),
          S(40, "C", "c1", 1, k=1, m=1), _ev(40, "reviewer_busy"), RS(40, "C", "c1"), RE(46, "C", "c1", "approve"),
          _ev(46, "reviewer_idle"), _ev(47, "hidden_pre", task="C", head="c1", passed=False),
          _ev(47, "bounce", task="C", head="c1", cause="escaped_defect"),
          S(50, "B", "b2", 2, k=1), _ev(50, "reviewer_busy"), RS(50, "B", "b2"), RE(55, "B", "b2", "approve"),
          _ev(55, "reviewer_idle"), *ok_merge(56, "B", "b2"),
          S(58, "D", "d1", 1, k=1), _ev(58, "reviewer_busy"), RS(58, "D", "d1")]
    d = derive_window(run, ev)
    s = d["summary"]
    prs = {p["task"]: p for p in d["prs"]}
    checks = {
        "attempts == 4 (warm-up E excluded)": s["attempts"] == 4,
        "finished == 2 (A, B)": s["finished"] == 2,
        "censored == 1 (D)": s["censored"] == 1,
        "rework_open == 1 (C)": s["rework_open"] == 1,
        "reviews == 5": s["reviews"] == 5,
        "V == 5 / (27/60): D's review, open at grace end, is not counted and nor is its busy time":
            abs(s["V"] - 5 / (27 / 60)) < 1e-6 and s["review_open_at_end"] and abs(s["busy_clipped_min"] - 12) < 1e-6,
        "review durations (5 x 60 s), CV 0": s["review_durations_s"] == [60.0] * 5 and s["review_time_cv"] == 0.0,
        "no supply known -> not truncated": s["task_supply"] is None and not s["supply_truncated"],
        "b_review == 0.2": abs(s["b_review"] - 0.2) < 1e-9,
        "b_hidden == 0.25": abs(s["b_hidden"] - 0.25) < 1e-9,
        "b == 0.4": abs(s["b"] - 0.4) < 1e-9,
        "lambda == 4 / (50/60)": abs(s["lam"] - 4.8) < 1e-9,
        "escaped == 1": s["escaped"] == 1,
        "B bounced, cause review": prs["B"]["bounced"] and prs["B"]["first_cause"] == "review",
        "D unresolved (excluded from collision logistic)": not prs["D"]["resolved"],
        "C k=1 m=1": prs["C"]["k"] == 1 and prs["C"]["m"] == 1,
    }
    errs, _ = validate_schema.validate_events(list(enumerate(ev, 1)), run)
    checks["hand-built log passes the validator"] = not errs
    bad = dict(ev[1])
    bad["k"] = "3"
    checks["validator rejects a string k"] = bool(validate_schema.check_event(bad))
    bad2 = dict(ev[1])
    bad2["extra"] = 1
    checks["validator rejects an extra field"] = bool(validate_schema.check_event(bad2))
    bad3 = dict(ev[4])
    bad3["verdict"] = "lgtm"
    checks["validator rejects a bad verdict"] = bool(validate_schema.check_event(bad3))
    return checks


def unit_supply():
    """Task supply of 3: claims at 12, 20, 30 min; the list is exhausted at minute 30 of a 60-min window."""
    run = {"run_id": "unit-supply", "kind": "sweep", "n_workers": 2, "window_start": iso(T0), "window_end": iso(T0 + 3600),
           "warmup_min": 10, "grace_min": 10, "task_order_seed": 1, "sandbox_commit": "x", "harness_commit": "x",
           "worker_model": "x", "reviewer_model": "x", "notes": ""}
    S = lambda t, w, task, head: _ev(t, "submit", worker=w, task=task, branch=f"claude/task-{task}", head=head, attempt_no=1,
                                     lines_changed=5, files=["a.py"], k=0, m=0)
    C = lambda t, w, task: _ev(t, "claim", worker=w, task=task, branch=f"claude/task-{task}")
    base = [_ev(0, "worker_start", worker="w1", session_id="s1"), _ev(0, "worker_start", worker="w2", session_id="s2"),
            C(12, "w1", "A"), C(20, "w2", "B"), S(22, "w1", "A", "a1"), S(28, "w2", "B", "b1"), C(30, "w1", "C"),
            S(45, "w1", "C", "c1")]
    note = [_ev(0, "note", text="task_supply n=3"), _ev(30, "note", text="tasks_exhausted n=3")]
    ev = sorted(note + base, key=lambda e: e["t"])
    s = derive_window(run, ev)["summary"]
    s2 = derive_window(run, base, supply=3, supply_source="reset.json")["summary"]
    s3 = derive_window(run, sorted([_ev(0, "note", text="task_supply n=10")] + base, key=lambda e: e["t"]))["summary"]
    errs, _ = validate_schema.validate_events(list(enumerate(ev, 1)), run)
    return {
        "supply note: truncated at minute 30": s["supply_truncated"] and abs(s["t_exhausted_min"] - 30) < 1e-9
            and s["task_supply"] == 3 and s["task_supply_source"] == "note",
        "supply: attempts 2 to exhaustion (C at 45 min excluded), attempts_full 3": s["attempts"] == 2 and s["attempts_full"] == 3,
        "supply: lambda = 2 / (2 workers x 20 min), lam_full = 3 / (2 x 50 min)":
            abs(s["lam"] - 2 / (40 / 60)) < 1e-9 and abs(s["lam_full"] - 3 / (100 / 60)) < 1e-9,
        "supply: attempts per hour over the 20 min before exhaustion": abs(s["attempts_per_hour"] - 2 / (20 / 60)) < 1e-9,
        "supply from reset.json (no notes): same truncation from the claims": s2["supply_truncated"]
            and abs(s2["t_exhausted_min"] - 30) < 1e-9 and s2["task_supply_source"] == "reset.json" and s2["attempts"] == 2,
        "supply 10 > 3 claimed: not truncated": not s3["supply_truncated"] and s3["attempts"] == 3,
        "supply notes pass the validator": not errs,
    }


V4_SYNTH = ["--truth", "carnot", "--v4", "--set", "lam1=6.8", "V0=13.6", "defect_p=0.49", "cv_window=0.3",
            "rework_min=3.53", "ci_hidden_min=0.25", "ci_post_min=0.5", "n_tasks=220"]
V4_IDS = {"P1": CONFIRMATORY, "O2": CONFIRMATORY, "S3": CONFIRMATORY, "O3": CONFIRMATORY, "Vratio": CONDITIONAL,
          "Vdur": CONDITIONAL, "V": CONDITIONAL, "S1r": DESCRIPTIVE, "S2r": DESCRIPTIVE, "RANK": DESCRIPTIVE,
          "ESC": DESCRIPTIVE, "COLL": DESCRIPTIVE, "BOUNCE": DESCRIPTIVE}


def unit_cli(outdir=None):
    out = {}
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        r = subprocess.run([PY, str(HERE / "synth.py"), *V4_SYNTH, "--seed", "11", "--out", str(td / "runs")],
                           capture_output=True, text=True)
        out["synth --v4 runs"] = r.returncode == 0
        dirs = sorted((td / "runs").iterdir())
        r = subprocess.run([PY, str(HERE / "validate_schema.py"), *map(str, dirs)], capture_output=True, text=True)
        out["validate_schema OK on all synthetic runs"] = r.returncode == 0
        pil = [d for d in dirs if d.name.endswith("T1") or "-T2-" in d.name]
        sw = [d for d in dirs if "-N" in d.name]
        out["v4 layout: T1 + 8 T2 + 3 x N=1 + 3 x N=12"] = (len(pil) == 9 and sum("-N1-" in d.name for d in sw) == 3
                                                          and sum("-N12-" in d.name for d in sw) == 3)
        r = subprocess.run([PY, str(HERE / "derive.py"), "--pilot", *map(str, pil), "--out", str(td / "pilot.json")],
                           capture_output=True, text=True)
        pj = json.loads((td / "pilot.json").read_text()) if r.returncode == 0 else {}
        out["derive --pilot (with review_time_cv and review_cv_ok)"] = (r.returncode == 0 and "review_cv_ok" in pj
                                                                        and pj.get("review_time_cv") is not None)
        r = subprocess.run([PY, str(HERE / "derive.py"), *map(str, sw), "--csv-dir", str(td / "tables")],
                           capture_output=True, text=True)
        out["derive windows + CSV"] = r.returncode == 0 and (td / "tables" / "prs.csv").exists()
        r = subprocess.run([PY, str(HERE / "predict.py"), "--pilot", str(td / "pilot.json"), "--task-supply", "220",
                            "--md", str(td / "pred.md"), "--json", str(td / "pred.json")], capture_output=True, text=True)
        md = r.stdout
        pj2 = json.loads((td / "pred.json").read_text()) if r.returncode == 0 else {}
        rows = pj2.get("predictions", {}).get("rows", [])
        out["predict (v4 default) runs"] = r.returncode == 0 and (td / "pred.md").exists()
        out["predict: no pilot gate on the v4 path (no REDESIGN_*, no gate decision)"] = ("REDESIGN" not in md and "gate" not in pj2
                                                                                         and "Decision" not in md)
        out["predict: all four rivals at N = 1 and 12, 120-min windows"] = (
            {(x["rival"], x["N"]) for x in rows} == {(r_, n) for r_ in ("carnot", "usl", "amdahl", "linear") for n in (1, 12)}
            and pj2.get("params", {}).get("window_min") == 120.0 and pj2.get("params", {}).get("rival_rework") == "completion")
        out["predict: O2 interval and operating-characteristics statement"] = ("O2 (confirmatory" in md
                                                                             and "Operating characteristics" in md
                                                                             and "0.97" in md and "Circularity" in md)
        r = subprocess.run([PY, str(HERE / "predict.py"), "--pilot", str(td / "pilot.json"), "--v3-gate"],
                           capture_output=True, text=True)
        out["predict --v3-gate: the gate only as SUPERSEDED"] = r.returncode == 0 and "SUPERSEDED" in r.stdout
        r = subprocess.run([PY, str(HERE / "score.py"), "--pilot", str(td / "pilot.json"), *map(str, sw),
                            "--out-dir", str(td / "res")], capture_output=True, text=True)
        ok = r.returncode == 0 and (td / "res" / "RESULTS-draft.md").exists() and (td / "res" / "results.json").exists()
        out["score writes RESULTS-draft.md + results.json"] = ok
        if not ok:
            out["score stderr"] = r.stderr[-2000:]
        else:
            R = json.loads((td / "res" / "results.json").read_text())
            res_md = (td / "res" / "RESULTS-draft.md").read_text()
            ids = {o["id"]: o["grade"] for o in R["outcomes"]}
            out["score: the PLAN-v4 results, each with its pre-set grade"] = ids == V4_IDS
            heads = [ln for ln in res_md.splitlines() if ln.startswith("## ") and not ln.startswith("## Summary")]
            out["RESULTS-draft.md: every section labelled confirmatory / conditional / descriptive"] = all(
                any(t in h for t in ("[confirmatory]", "[conditional", "[descriptive]")) for h in heads)
            cv_ok = R["review_cv_ok"]
            vc = {o["id"]: o["counts_as"] for o in R["outcomes"] if o["grade"] == CONDITIONAL}
            out["V constancy counts as confirmatory iff review_cv_ok"] = all(
                v == (CONFIRMATORY if cv_ok else DESCRIPTIVE) for v in vc.values())
            pj["review_cv_ok"] = not cv_ok
            (td / "pilot-flip.json").write_text(json.dumps(pj))
            r2 = subprocess.run([PY, str(HERE / "score.py"), "--pilot", str(td / "pilot-flip.json"), *map(str, sw),
                                 "--out-dir", str(td / "res2")], capture_output=True, text=True)
            R2 = json.loads((td / "res2" / "results.json").read_text()) if r2.returncode == 0 else {"outcomes": []}
            vc2 = {o["id"]: o["counts_as"] for o in R2["outcomes"] if o["grade"] == CONDITIONAL}
            out["flipping review_cv_ok flips the V-constancy grade"] = bool(vc2) and all(
                v == (CONFIRMATORY if not cv_ok else DESCRIPTIVE) for v in vc2.values())
            if outdir is not None:
                import shutil
                ex = Path(outdir) / "example-synthetic"
                if ex.exists():
                    shutil.rmtree(ex)
                ex.mkdir(parents=True, exist_ok=True)
                for src in (td / "pilot.json", td / "pred.md", td / "res" / "RESULTS-draft.md", td / "res" / "results.json"):
                    shutil.copy(src, ex / src.name)
    return out


# ---------------------------------------------------------------------- one simulated study
def derive_all(runs):
    return [derive_window(r, e) for r, e in runs]


# PLAN-v4 design point in synth terms (as design-search/dsim.py builds it for "H lam8 q2 N1/12 120m x3"): Haiku at
# 0.85 x the 8/h task-size target, V = 2 x that, Haiku's defect rate, window CV 0.3, a 0.75-min merge queue, and the
# planned ~220 tasks.
V4_TRUTH = dict(lam1=6.8, V0=13.6, defect_p=0.49, cv_window=0.3, rework_min=6.0 * 4.0 / 6.8, ci_hidden_min=0.25,
                ci_post_min=0.5, n_tasks=220)


def study_v4(cfg):
    """One simulated PLAN-v4 study: v4 pilot -> sweep 1/12 x 3 x 120 min (ABBAAB) -> derive -> score (v4).
    cfg: truth (carnot|usl|amdahl|linear|skimN|slowN), seed, over (dict, e.g. service_cv, n_tasks)."""
    import dataclasses
    tname = cfg["truth"]
    base = {"skimN": "carnot", "slowN": "carnot"}.get(tname, tname)
    truth = make_truth(base, **{**V4_TRUTH, **cfg.get("over", {})})
    seed = cfg["seed"]
    res = dict(seed=seed, truth=tname)
    runs = simulate_study(truth, (1, 12), seed=seed, reps=0, with_pilot=True, pilot_design="v4")
    try:
        pil = pilot_params(derive_all(runs))
        P = Params.from_pilot(pil, window_min=120.0)
        if P.completion is None or not P.completion > 0:
            raise ValueError
    except (ValueError, KeyError, TypeError, ZeroDivisionError):
        res["status"] = "PILOT_UNUSABLE"
        return res
    res["review_cv_ok"] = pil["review_cv_ok"]
    res["pilot_review_cv"] = pil["review_time_cv"]
    truth_hi = truth
    if tname in ("skimN", "slowN"):  # the reviewer's pace at N = 12 is x1.25 / x0.75 of its pace at N = 1 and in the pilot
        truth_hi = dataclasses.replace(truth, V0=truth.V0 * (1.25 if tname == "skimN" else 0.75))
    sweep = []
    for i, n in enumerate((1, 12, 12, 1, 1, 12)):
        sweep.append(simulate(truth_hi if n == 12 else truth, n, seed=seed * 1000 + 10 + i, window_min=120.0,
                              run_id=f"v4-{seed}-N{n}-w{i + 1}", kind="sweep", t0=T0 + (seed % 1000) * 86400 + (20 + 2.5 * i) * 3600,
                              task_pool=None))
    derived = derive_all(sweep)
    parts = cfg.get("parts", ())
    R = score(derived, pil, do_escape="escape" in parts, do_collision="collision" in parts, do_fit=False)
    res["status"] = "SCORED"
    res["codes"] = {o["id"]: o["code"] for o in R["outcomes"]}
    res["best"] = R["rivals"]["best"]
    res["log_lr"] = R["primary"]["log_lr"]
    res["V_ratio"] = R["V4"]["ratio"]
    res["truncated"] = [s["run_id"] for s in R["windows"] if s.get("supply_truncated")]
    hi = [s for s in R["windows"] if s["n_workers"] == 12]
    res["lam12"] = sum(s["attempts"] for s in hi) / sum(s["worker_hours"] for s in hi)
    res["lam12_full"] = sum(s["attempts_full"] for s in hi) / sum(s["worker_hours_full"] for s in hi)
    lo = [s for s in R["windows"] if s["n_workers"] == 1]
    res["lam1"] = sum(s["attempts"] for s in lo) / sum(s["worker_hours"] for s in lo)
    res["per_agent_ratio"] = R["attempts"]["per_agent_ratio"]
    return res


def study(cfg):
    """One simulated sweep at fixed sizes for the escape and collision parts (D, E). cfg keys: truth (name), over
    (dict), seed, sizes, parts (tuple of 'escape','collision'), window_min."""
    truth = make_truth(cfg["truth"], **cfg.get("over", {}))
    seed = cfg["seed"]
    res = dict(seed=seed)
    pil_runs = simulate_study(truth, (1, 1), seed=seed, reps=0, with_pilot=True)
    pil = pilot_params(derive_all(pil_runs))
    sizes = cfg["sizes"]
    runs = simulate_study(truth, sizes, seed=seed, reps=2, with_pilot=False, window_min=cfg.get("window_min", 90.0))
    derived = derive_all(runs)
    parts = cfg.get("parts", ())
    try:
        R = score(derived, pil, window_min=cfg.get("window_min", 90.0), do_escape="escape" in parts,
                  do_collision="collision" in parts, do_fit=False)
    except ValueError:
        res["status"] = "PILOT_UNUSABLE"
        return res
    if "escape" in R:
        e = R["escape"]
        res["escape"] = {"events": e["events"], e["model"]: e.get("p_one_sided"),
                         **{m: es.get("p_one_sided") for m, es in R["escape_sensitivity"].items()}}
    if "collision" in R:
        c = R["collision"]
        res["collision"] = {k: c.get(k) for k in ("fit_ok", "p_hat", "p_ci", "p_m_hat", "coef_k", "p_k_one_sided",
                                                  "p_m_one_sided", "n_resolved", "n_censored_excluded")}
        res["naive"] = R["collision_naive_not_finished"]
    return res


def run_many(cfgs, workers, fn=None):
    fn = fn or study
    if workers <= 1:
        return [fn(c) for c in cfgs]
    with ProcessPoolExecutor(max_workers=workers) as ex:
        return list(ex.map(fn, cfgs, chunksize=4))


# ---------------------------------------------------------------------- summaries
def code_rates(results, ids):
    ok = [r for r in results if r.get("status") == "SCORED"]
    out = {}
    for i in ids:
        c = Counter(r["codes"].get(i) for r in ok)
        out[i] = {k: v / len(ok) for k, v in c.items()} if ok else {}
    return len(ok), out


# ---------------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--reps", type=int, default=300)
    ap.add_argument("--quick", action="store_true")
    ap.add_argument("--workers", type=int, default=max(1, (os.cpu_count() or 2) - 1))
    ap.add_argument("--out", default=str(HERE / "selftest-output"))
    ap.add_argument("--parts", default="UVSDE")
    a = ap.parse_args()
    R = 30 if a.quick else a.reps
    outdir = Path(a.out)
    outdir.mkdir(parents=True, exist_ok=True)
    T = {}
    md = ["# Self-test of the fleet-sweep analysis", "",
          f"Replicates per cell: {R} (seeded, reproducible). Generated by `selftest.py`.", ""]
    t_start = time.time()
    all_ok = True

    if "U" in a.parts:
        u = unit_derive()
        us = unit_supply()
        c = unit_cli(outdir)
        T["unit"] = {**u, **us, **c}
        md += ["## U. Unit checks", ""]
        for k, v in {**u, **us, **c}.items():
            if k == "score stderr":
                md.append(f"- score stderr: `{v}`")
                continue
            md.append(f"- {'ok  ' if v else 'FAIL'} {k}")
            all_ok &= bool(v)
        md.append("")
        print("U done", all_ok, flush=True)

    if "V" in a.parts:
        md += ["## V. The PLAN-v4 design point, whole pipeline", "",
               "Synth truth as design-search/dsim.py builds the recommended design: lambda1 = 6.8/h (Haiku at 0.85 x the 8/h "
               "target), V0 = 13.6 (q = 2), defect rate 0.49, window CV 0.3, 0.75-min merge queue, 220 tasks. Pilot: T1 (1 "
               "worker, then 12) + eight 60-min windows of one worker, *without* the 120 free calibration reviews the design "
               "search added (so pilot V is noisier here). Sweep: N = 1 and 12, 3 x 120 min, ABBAAB. Uncapped truths: the "
               "reviewer speeds up with queue depth. skimN / slowN: the reviewer's pace at N = 12 is 1.25x / 0.75x its pace at "
               "N = 1 and in the pilot. Cells: share of scored studies coding the outcome FAIL (P1, O3: share PASS). "
               "DESIGN-SEARCH figures in brackets where it reports one.", ""]
        T["V"] = {}
        ids = ("P1", "O2", "S3", "O3", "Vratio", "Vdur", "V", "S1r", "S2r")
        ref = {("carnot", "P1"): V4_OC["primary_correct"]["carnot"],
               ("usl", "P1"): 1 - V4_OC["primary_correct"]["usl"], ("amdahl", "P1"): 1 - V4_OC["primary_correct"]["amdahl"],
               ("linear", "P1"): 1 - V4_OC["primary_correct"]["linear"],
               ("carnot", "S1r"): V4_OC["S1r_false_alarm"], ("carnot", "S2r"): V4_OC["S2r_false_alarm"]}
        vdur_ref = {1.0: {"carnot": 0.06, "skimN": 0.21, "slowN": 0.19}, 0.5: {"carnot": 0.05, "skimN": 0.68, "slowN": 0.78}}
        md.append("| review CV | truth | n | review_cv_ok share | P1 PASS | O2 | S3 | O3 PASS | Vratio FAIL / INCONCL. | Vdur | V | "
                  "S1r | S2r | Carnot best | median V12/V1 |")
        md.append("|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|")
        for scv in (1.0, 0.5):
            for tr in ("carnot", "usl", "amdahl", "linear", "skimN", "slowN"):
                if scv == 0.5 and tr in ("usl", "amdahl", "linear"):
                    continue
                cfgs = [dict(truth=tr, over=dict(service_cv=scv), seed=800000 + int(scv * 10) * 10000 + 1000 * ["carnot", "usl",
                        "amdahl", "linear", "skimN", "slowN"].index(tr) + i) for i in range(R)]
                res = run_many(cfgs, a.workers, study_v4)
                n, cr = code_rates(res, ids)
                ok = [r for r in res if r.get("status") == "SCORED"]
                g = lambda i, c="FAIL": cr.get(i, {}).get(c, 0.0)
                cvok = float(np.mean([r["review_cv_ok"] for r in ok])) if ok else math.nan
                cbest = float(np.mean([r["best"] == "carnot" for r in ok])) if ok else math.nan
                vr = float(np.median([r["V_ratio"] for r in ok if r["V_ratio"] is not None])) if ok else math.nan
                T["V"][f"cv={scv},{tr}"] = dict(n=n, rates=cr, review_cv_ok=cvok, carnot_best=cbest, median_V_ratio=vr,
                                                truncated_share=float(np.mean([bool(r["truncated"]) for r in ok])) if ok else math.nan)
                rf = lambda i: f" [{ref[(tr, i)]:.2f}]" if (tr, i) in ref and scv == 1.0 else ""
                vd = f" [{vdur_ref[scv][tr]:.2f}]" if tr in vdur_ref[scv] else ""
                md.append(f"| {scv} | {tr} | {n} | {cvok:.2f} | {g('P1', 'PASS'):.2f}{rf('P1')} | {g('O2'):.2f} | {g('S3'):.2f} | "
                          f"{g('O3', 'PASS'):.2f} | {g('Vratio'):.2f} / {g('Vratio', 'INCONCLUSIVE'):.2f} | {g('Vdur'):.2f}{vd} | "
                          f"{g('V'):.2f} | {g('S1r'):.2f}{rf('S1r')} | {g('S2r'):.2f}{rf('S2r')} | {cbest:.2f} | {vr:.2f} |")
                print("V", scv, tr, n, {i: g(i) for i in ids}, flush=True)
        md.append("")
        md.append("P1 PASS under an uncapped truth is a wrong call (DESIGN-SEARCH bracket = 1 - its correct rate). Vratio PASS "
                  "needs the exact 95% interval of V(12)/V(1) inside [0.8, 1.25]; with the review counts of this design it is "
                  "mostly INCONCLUSIVE, which is why the claim rests on Vdur as well and is conditional on review_cv_ok.")
        md.append("")

    if "S" in a.parts:
        md += ["## S. Task supply at the design point (linear truth, 120 tasks)", "",
               "With the dry run's 120 tasks, a linear fleet of 12 at 6.8/h claims them all before the window ends. derive.py "
               "cuts lambda and the attempt-based measures at the exhaustion minute; 'full' is the uncut value. The truth's "
               "per-agent rate is the same at N = 1 and 12 (ratio 1).", ""]
        T["S"] = {}
        md.append("| tasks | truth | n | N = 12 windows truncated (share of studies) | lambda(1) | lambda(12) cut | lambda(12) full | "
                  "per-agent ratio cut | O3 PASS |")
        md.append("|---|---|---|---|---|---|---|---|---|")
        for nt in (120, 220):
            cfgs = [dict(truth="linear", over=dict(n_tasks=nt), seed=900000 + nt * 10 + i) for i in range(max(R // 2, 20))]
            res = [r for r in run_many(cfgs, a.workers, study_v4) if r.get("status") == "SCORED"]
            tr_share = float(np.mean([bool(r["truncated"]) for r in res]))
            l1 = float(np.median([r["lam1"] for r in res]))
            l12 = float(np.median([r["lam12"] for r in res]))
            l12f = float(np.median([r["lam12_full"] for r in res]))
            par = float(np.median([r["per_agent_ratio"] for r in res]))
            o3 = float(np.mean([r["codes"]["O3"] == "PASS" for r in res]))
            T["S"][f"n_tasks={nt}"] = dict(n=len(res), truncated_share=tr_share, lam1=l1, lam12=l12, lam12_full=l12f,
                                           per_agent_ratio=par, O3_pass=o3)
            md.append(f"| {nt} | linear | {len(res)} | {tr_share:.2f} | {l1:.2f} | {l12:.2f} | {l12f:.2f} | {par:.2f} | {o3:.2f} |")
            print("S", nt, tr_share, l1, l12, l12f, flush=True)
        md.append("")

    if "D" in a.parts:
        md += ["## D. Escaped defects vs queue depth", "",
               "Carnot truth, sizes (1, 5), 4 windows. Planted effect: the reviewer's catch probability falls as "
               "0.7 exp(-g depth). Rates are over all simulated sweeps (a sweep with < 8 escaped defects counts as 'not detected'); "
               "'given >= 8' conditions on the model being run. Test: one-sided p < 0.05 on the depth coefficient.", ""]
        T["D"] = {}
        md.append("| g | share with >= 8 events | median events | clogit_task (PLAN literal) | clogit_task_first | logit_cluster_task | given >= 8: clogit_task / first / logit |")
        md.append("|---|---|---|---|---|---|---|")
        for gval in (0.0, 0.1, 0.2):
            res = run_many([dict(truth="carnot", over=dict(escape_depth=gval), sizes=(1, 5), parts=("escape",),
                                 seed=500000 + int(gval * 1000) * 10 + i) for i in range(R)], a.workers)
            res = [r for r in res if "escape" in r]
            ev = np.array([r["escape"]["events"] for r in res])
            rates, cond = {}, {}
            for mname in ("clogit_task", "clogit_task_first", "logit_cluster_task"):
                ps = np.array([np.nan if r["escape"].get(mname) is None else r["escape"][mname] for r in res], float)
                det = (ev >= 8) & (ps < 0.05)
                rates[mname] = float(det.mean())
                cond[mname] = float(det[ev >= 8].mean()) if (ev >= 8).any() else math.nan
            T["D"][f"g={gval}"] = dict(share_ge8=float((ev >= 8).mean()), median_events=float(np.median(ev)), rates=rates, given_ge8=cond)
            md.append(f"| {gval} | {(ev >= 8).mean():.2f} | {np.median(ev):.0f} | {rates['clogit_task']:.2f} | {rates['clogit_task_first']:.2f} | "
                      f"{rates['logit_cluster_task']:.2f} | {cond['clogit_task']:.2f} / {cond['clogit_task_first']:.2f} / {cond['logit_cluster_task']:.2f} |")
            print("D", gval, rates, cond, flush=True)
        md.append("")

    if "E" in a.parts:
        md += ["## E. Collisions and the censoring check", "",
               "Carnot truth. Planted p per pair of changes in flight (p_m extra for pairs sharing a file). 'k-slope' = one-sided "
               "p < 0.05 on k in the logistic (bounced at least once ~ k + m + window FE, censored excluded). p-hat from the "
               "model form 1 - s_w (1-p)^k (1-p_m)^m with a profile 95% interval. 'naive' = outcome 'not finished', censored "
               "included (the accounting review v2 C rejected).", ""]
        T["E"] = {}
        md.append("| sizes | planted p | planted p_m | mean p-hat | median p-hat | CI covers p | CI excludes 0 | k-slope rate | m-slope rate | mean censored excluded | naive k-slope rate |")
        md.append("|---|---|---|---|---|---|---|---|---|---|---|")
        for sizes in ((1, 5), (3, 8)):
            for p, pm in ((0.0, 0.0), (0.005, 0.0), (0.02, 0.0), (0.05, 0.0), (0.0, 0.1)):
                res = run_many([dict(truth="carnot", over=dict(p=p, p_m=pm), sizes=sizes, parts=("collision",),
                                     seed=600000 + sizes[1] * 100000 + int(p * 10000) * 10 + int(pm * 100) + i * 7) for i in range(R)],
                               a.workers)
                res = [r for r in res if "collision" in r]
                cc = [r["collision"] for r in res if r["collision"].get("fit_ok")]
                ph = np.array([c["p_hat"] for c in cc])
                cover = np.mean([c["p_ci"][0] <= p <= c["p_ci"][1] for c in cc])
                excl0 = np.mean([c["p_ci"][0] > 0 for c in cc])
                ks = np.mean([c["p_k_one_sided"] < 0.05 for c in cc])
                ms = np.mean([c["p_m_one_sided"] < 0.05 for c in cc])
                cens = np.mean([c["n_censored_excluded"] for c in cc])
                nv = [r["naive"] for r in res if r["naive"].get("fit_ok")]
                naive = np.mean([x["p_k_one_sided"] < 0.05 for x in nv]) if nv else math.nan
                T["E"][f"sizes={sizes},p={p},p_m={pm}"] = dict(n=len(cc), mean_p_hat=float(ph.mean()), median_p_hat=float(np.median(ph)),
                                                                coverage=float(cover), ci_excludes_0=float(excl0), k_slope_rate=float(ks),
                                                                m_slope_rate=float(ms), mean_censored=float(cens), naive_k_slope_rate=float(naive))
                md.append(f"| {sizes} | {p} | {pm} | {ph.mean():.4f} | {np.median(ph):.4f} | {cover:.2f} | {excl0:.2f} | {ks:.2f} | {ms:.2f} | {cens:.1f} | {naive:.2f} |")
                print("E", sizes, p, pm, ph.mean(), cover, ks, naive, flush=True)
        md.append("")

    md.append(f"Total time {time.time() - t_start:.0f} s.")
    (outdir / "selftest.json").write_text(json.dumps(T, indent=2, default=str) + "\n")
    (outdir / "SELFTEST.md").write_text("\n".join(md) + "\n")
    print("\n".join(md))
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
