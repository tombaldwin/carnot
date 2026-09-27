#!/usr/bin/env python3
"""Self-test of the fleet-sweep analysis on synthetic sweeps (synth.py -> derive -> predict -> score).

    python selftest.py              # full run (several minutes, uses all cores)
    python selftest.py --quick      # fewer replicates, for a smoke test
    python selftest.py --reps 400 --out selftest-output

Parts:
  U  unit checks: derive on a hand-built log with known answers; schema validator accepts synth output
     and rejects broken lines; CLI end to end (synth -> validate -> derive -> predict -> score) in a temp dir.
  A  family recovery through the whole pipeline: simulated T1 + T2 pilot -> gate -> 4 windows (ABBA) ->
     scorer, under each truth (Carnot capped, USL, Amdahl, linear). Reports gate decisions and, for runs
     the gate lets through, how often each rival wins, under both readings of the rivals' rework term.
  B  the same with the sizes fixed at the typical gate outcome (1, 5), so scorer performance is not mixed
     with gate outcomes; also pilot bias (pilot V / true V).
  C  review-comparable table: the review's design (N = 3, 8; oracle parameters; q = 1.5-4; CV 0 / 0.3;
     Amdahl truth with a reviewer that keeps up and no rework), to compare with REVIEW-fable-v2.md.
  D  escaped defects: planted depth effect vs none; detection and false-positive rates per model.
  E  collisions: planted p (and p_m), recovery of p-hat, CI coverage, k-slope rates; p = 0 false positives;
     censoring check (review v2, C): the k-slope with censored excluded vs the naive "not finished" outcome.
  F  surprises under a reviewer that skims (speeds up) or slows down with load.
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
from common import RIVALS, X_usl, iso  # noqa: E402
from derive import derive_window, pilot_params  # noqa: E402
from predict import Params, gate  # noqa: E402
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
        "V == 5 / (39/60)": abs(s["V"] - 5 / (39 / 60)) < 1e-6,
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


def unit_cli(outdir=None):
    out = {}
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        r = subprocess.run([PY, str(HERE / "synth.py"), "--truth", "carnot", "--sizes", "1", "5", "--seed", "11",
                            "--out", str(td / "runs")], capture_output=True, text=True)
        out["synth runs"] = r.returncode == 0
        dirs = sorted((td / "runs").iterdir())
        r = subprocess.run([PY, str(HERE / "validate_schema.py"), *map(str, dirs)], capture_output=True, text=True)
        out["validate_schema OK on all synthetic runs"] = r.returncode == 0
        t1 = [d for d in dirs if d.name.endswith("T1")][0]
        t2 = [d for d in dirs if d.name.endswith("T2")][0]
        sw = [d for d in dirs if "-N" in d.name]
        r = subprocess.run([PY, str(HERE / "derive.py"), "--pilot", str(t1), str(t2), "--out", str(td / "pilot.json")],
                           capture_output=True, text=True)
        out["derive --pilot"] = r.returncode == 0
        r = subprocess.run([PY, str(HERE / "derive.py"), *map(str, sw), "--csv-dir", str(td / "tables")],
                           capture_output=True, text=True)
        out["derive windows + CSV"] = r.returncode == 0 and (td / "tables" / "prs.csv").exists()
        r = subprocess.run([PY, str(HERE / "predict.py"), "--pilot", str(td / "pilot.json"), "--sizes", "1", "5",
                            "--md", str(td / "pred.md"), "--json", str(td / "pred.json")], capture_output=True, text=True)
        out["predict"] = r.returncode == 0 and (td / "pred.md").exists()
        r = subprocess.run([PY, str(HERE / "score.py"), "--pilot", str(td / "pilot.json"), *map(str, sw),
                            "--out-dir", str(td / "res")], capture_output=True, text=True)
        out["score writes RESULTS-draft.md + results.json"] = (r.returncode == 0 and (td / "res" / "RESULTS-draft.md").exists()
                                                             and (td / "res" / "results.json").exists())
        if r.returncode != 0:
            out["score stderr"] = r.stderr[-2000:]
        elif outdir is not None:
            import shutil
            ex = Path(outdir) / "example-synthetic"
            ex.mkdir(parents=True, exist_ok=True)
            for src in (td / "pilot.json", td / "pred.md", td / "res" / "RESULTS-draft.md", td / "res" / "results.json"):
                shutil.copy(src, ex / src.name)
    return out


# ---------------------------------------------------------------------- one simulated study
def derive_all(runs):
    return [derive_window(r, e) for r, e in runs]


def study(cfg):
    """One simulated study. cfg keys: truth (name), over (dict), seed, sizes (None = gate), oracle (dict|None),
    pilot (bool), parts (tuple of 'escape','collision'), window_min."""
    truth = make_truth(cfg["truth"], **cfg.get("over", {}))
    seed = cfg["seed"]
    res = dict(seed=seed)
    sizes = cfg.get("sizes")
    oracle = cfg.get("oracle")
    if oracle is None:
        pil_runs = simulate_study(truth, (1, 1), seed=seed, reps=0, with_pilot=True)
        pil = pilot_params(derive_all(pil_runs))
        res["pilot_V_ratio"] = pil["V"] / truth.V0
        res["pilot_lambda"] = pil["lambda_pilot"]
        res["pilot_reviews"] = pil["reviews"]
        res["pilot_attempts"] = pil["attempts"]
        if any(pil.get(k) is None or (isinstance(pil.get(k), float) and math.isnan(pil[k]))
               for k in ("lambda_pilot", "V", "b_review", "b_hidden", "r0")) or pil["lambda_pilot"] <= 0:
            res["decision"] = "PILOT_UNUSABLE"
            return res
    else:
        pil = dict(oracle)
    try:
        P = Params.from_pilot(pil)
        if P.completion is None and oracle is None:
            raise ValueError("no completion share")
    except ValueError:
        res["decision"] = "PILOT_UNUSABLE"
        return res
    g = gate(P)
    res["decision"] = g["decision"]
    res["gate_sizes"] = (g["N_low"], g["N_high"])
    if sizes is None:
        if g["decision"] != "RUN":
            return res
        sizes = (g["N_low"], g["N_high"])
    res["sizes"] = tuple(sizes)
    runs = simulate_study(truth, sizes, seed=seed, reps=2, with_pilot=False, window_min=cfg.get("window_min", 90.0))
    derived = derive_all(runs)
    parts = cfg.get("parts", ())
    out = {}
    R = score(derived, pil, rival_rework="plan", do_escape="escape" in parts, do_collision="collision" in parts,
              do_fit=False)
    for reading, rv in R["rivals_by_reading"].items():
        ll = rv["ll"]
        out[reading] = dict(best=rv["best"], best3=max(("carnot", "amdahl", "linear"), key=lambda r: ll[r]))
    out["outcomes"] = {o["id"]: o["code"] for o in R["outcomes"]}
    out["ratio"] = R["pooled"]["ratio"]
    out["V_hi_lo"] = R["V"]["high_over_low"]
    out["finished"] = [w["finished"] for w in R["windows"]]
    if "escape" in R:
        e = R["escape"]
        out["escape"] = {"events": e["events"], e["model"]: e.get("p_one_sided"),
                         **{m: es.get("p_one_sided") for m, es in R["escape_sensitivity"].items()}}
    if "collision" in R:
        c = R["collision"]
        out["collision"] = {k: c.get(k) for k in ("fit_ok", "p_hat", "p_ci", "p_m_hat", "coef_k", "p_k_one_sided",
                                                 "p_m_one_sided", "n_resolved", "n_censored_excluded")}
        out["naive"] = R["collision_naive_not_finished"]
    res.update(out)
    return res


def run_many(cfgs, workers):
    if workers <= 1:
        return [study(c) for c in cfgs]
    with ProcessPoolExecutor(max_workers=workers) as ex:
        return list(ex.map(study, cfgs, chunksize=4))


def oracle_pilot(truth_name, over, n_runs=60, seed=990000):
    """The pilot's expected value: 60 pooled N = 2 windows of sweep length (so the completion share is
    measured over the same window as the sweep) for lambda, completion; V, b, r0 pooled over the same."""
    truth = make_truth(truth_name, **over)
    derived = []
    for i in range(n_runs):
        r, e = simulate(truth, 2, seed=seed + i, window_min=90.0, kind="pilot", run_id=f"oracle-{i}")
        derived.append(derive_window(r, e))
    return pilot_params(derived)


# ---------------------------------------------------------------------- summaries
def pick_rates(results, reading="plan", key="best"):
    """Share of studies in which each rival alone had the highest likelihood; 'tie' = several rivals with
    identical predictions (in practice Carnot = USL when review never binds)."""
    ok = [r for r in results if reading in r]
    n = len(ok)
    c = Counter(r[reading][key] if "|" not in r[reading][key] else "tie" for r in ok)
    return n, {k: c.get(k, 0) / n if n else math.nan for k in RIVALS + ("tie",)}


def outcome_rates(results):
    ok = [r for r in results if "outcomes" in r]
    ids = sorted({k for r in ok for k in r["outcomes"]})
    out = {}
    for i in ids:
        c = Counter(r["outcomes"].get(i) for r in ok)
        out[i] = {k: v / len(ok) for k, v in c.items()}
    return out


def fmt_rates(d):
    return ", ".join(f"{k} {v:.2f}" for k, v in d.items())


# ---------------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--reps", type=int, default=300)
    ap.add_argument("--quick", action="store_true")
    ap.add_argument("--workers", type=int, default=max(1, (os.cpu_count() or 2) - 1))
    ap.add_argument("--out", default=str(HERE / "selftest-output"))
    ap.add_argument("--parts", default="UABCDEF")
    a = ap.parse_args()
    R = 40 if a.quick else a.reps
    outdir = Path(a.out)
    outdir.mkdir(parents=True, exist_ok=True)
    T = {}
    md = ["# Self-test of the fleet-sweep analysis", "",
          f"Replicates per cell: {R} (seeded, reproducible). Generated by `selftest.py`.", ""]
    t_start = time.time()
    all_ok = True

    if "U" in a.parts:
        u = unit_derive()
        c = unit_cli(outdir)
        T["unit"] = {**u, **c}
        md += ["## U. Unit checks", ""]
        for k, v in {**u, **c}.items():
            if k == "score stderr":
                md.append(f"- score stderr: `{v}`")
                continue
            md.append(f"- {'ok  ' if v else 'FAIL'} {k}")
            all_ok &= bool(v)
        md.append("")
        print("U done", all_ok, flush=True)

    truths = ["carnot", "usl", "amdahl", "linear"]
    if "A" in a.parts:
        md += ["## A. Whole pipeline: pilot -> gate -> sweep -> scorer", "",
               "Truth parameters (synth.py defaults): lambda1 = 4 attempts/h, alpha = 0.1, beta = 0.01, V0 = 12.7 reviews per busy hour "
               "(q(1-b) ~ 1.9, the middle of the gate's feasible range), defect rate 0.35, reviewer catch 0.7, false reject 0.1, "
               "p = 0.005, rework 6 min done by the worker, review service exponential. Rival truths: the reviewer speeds up with "
               "queue depth (rate x (1 + 2 depth)), so review never binds. 'rework lost' = bounced changes are abandoned, which is "
               "what the rivals' (1 - r0) term assumes.", ""]
        T["A"] = {}
        rows = []
        for tr in truths:
            for lost in ((False, True) if tr != "carnot" else (False,)):
                over = {"rework_returns": False} if lost else {}
                cfgs = [dict(truth=tr, over=over, seed=100000 * (1 + truths.index(tr)) + 5000 * lost + i) for i in range(R)]
                res = run_many(cfgs, a.workers)
                dec = Counter(r["decision"] for r in res)
                sizes = Counter(tuple(r["sizes"]) for r in res if "sizes" in r)
                n, pr = pick_rates(res, "plan")
                _, prr = pick_rates(res, "recovered")
                _, prc = pick_rates(res, "completion")
                key = f"{tr}{' (rework lost)' if lost else ''}"
                T["A"][key] = dict(decisions={k: v / R for k, v in dec.items()}, sizes={str(k): v for k, v in sizes.items()},
                                   n_run=n, picks_plan=pr, picks_recovered=prr, picks_completion=prc, outcomes=outcome_rates(res))
                rows.append((key, dec, sizes, n, pr, prr, prc))
                print("A", key, dict(dec), pr, flush=True)
        md.append("| Truth | gate RUN share | sizes chosen (top 3) | n scored | picked: `plan` (C/U/A/L/tie) | `recovered` | `completion` |")
        md.append("|---|---|---|---|---|---|---|")
        for key, dec, sizes, n, pr, prr, prc in rows:
            md.append(f"| {key} | {dec.get('RUN', 0) / R:.2f} ({', '.join(f'{k} {v / R:.2f}' for k, v in dec.items() if k != 'RUN')}) | "
                      f"{', '.join(f'{k}: {v}' for k, v in sizes.most_common(3))} | {n} | "
                      f"{' / '.join(f'{pr[r]:.2f}' for r in RIVALS + ('tie',))} | {' / '.join(f'{prr[r]:.2f}' for r in RIVALS + ('tie',))} | "
                      f"{' / '.join(f'{prc[r]:.2f}' for r in RIVALS + ('tie',))} |")
        md.append("")

    if "B" in a.parts:
        md += ["## B. Scorer with sizes fixed at (1, 5), pilot-estimated parameters", "",
               "Same truths; the gate is bypassed so every study is scored. `pilot V / V0` shows the pilot's estimation error.", ""]
        T["B"] = {}
        md.append("| Truth | picked: `plan` (C/U/A/L/tie) | `recovered` | `completion` | pilot V / V0: median [10%, 90%] | pilot reviews (median) |")
        md.append("|---|---|---|---|---|---|")
        oracle_rows = ["", "The same, with the pilot replaced by its expected value (60 pooled N = 2, 90-min windows), which removes "
                       "pilot noise and shows the ceiling:", "",
                       "| Truth | picked: `plan` (C/U/A/L/tie) | `recovered` | `completion` | oracle pilot |", "|---|---|---|---|---|"]
        md_b2 = ["", "Outcome coding rates in the same runs (share FAIL; O3 share PASS):", "",
                 "| Truth | S1 | S1r | S2 | S2r | S3 | S4 | S4n | S5 | O1 | O1n | O2 | O3 PASS |",
                 "|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
        for tr in truths:
            for lost in ((False, True) if tr != "carnot" else (False,)):
                over = {"rework_returns": False} if lost else {}
                cfgs = [dict(truth=tr, over=over, sizes=(1, 5), seed=200000 * (1 + truths.index(tr)) + 5000 * lost + i)
                        for i in range(R)]
                res = run_many(cfgs, a.workers)
                n, pr = pick_rates(res, "plan")
                _, prr = pick_rates(res, "recovered")
                _, prc = pick_rates(res, "completion")
                vr = np.array([r["pilot_V_ratio"] for r in res if "pilot_V_ratio" in r])
                prev = np.array([r["pilot_reviews"] for r in res if "pilot_reviews" in r])
                orc = oracle_pilot(tr, over)
                res_o = run_many([dict(c, oracle=orc, seed=c["seed"] + 77) for c in cfgs], a.workers)
                _, po = pick_rates(res_o, "plan")
                _, pro = pick_rates(res_o, "recovered")
                _, pco = pick_rates(res_o, "completion")
                oracle_rows.append(f"| {tr}{' (rework lost)' if lost else ''} | {' / '.join(f'{po[r]:.2f}' for r in RIVALS + ('tie',))} | "
                                   f"{' / '.join(f'{pro[r]:.2f}' for r in RIVALS + ('tie',))} | {' / '.join(f'{pco[r]:.2f}' for r in RIVALS + ('tie',))} | "
                                   f"lambda {orc['lambda_pilot']:.2f}, V {orc['V']:.1f}, b {orc['b']:.2f}, r0 {orc['r0']:.2f}, c {orc['completion']:.2f} |")
                orates = outcome_rates(res)
                key = f"{tr}{' (rework lost)' if lost else ''}"
                T["B"][key] = dict(n=n, picks_plan=pr, picks_recovered=prr, picks_completion=prc,
                                   oracle=dict(pilot=orc, picks_plan=po, picks_recovered=pro, picks_completion=pco),
                                   pilot_V_ratio_q=list(np.quantile(vr, [0.1, 0.5, 0.9])),
                                   pilot_V_ratio_mean=float(vr.mean()), pilot_reviews_median=float(np.median(prev)), outcomes=orates)
                g = lambda i, c: orates.get(i, {}).get(c, 0.0)
                md.append(f"| {key} | {' / '.join(f'{pr[r]:.2f}' for r in RIVALS + ('tie',))} | {' / '.join(f'{prr[r]:.2f}' for r in RIVALS + ('tie',))} | "
                          f"{' / '.join(f'{prc[r]:.2f}' for r in RIVALS + ('tie',))} | "
                          f"{np.median(vr):.2f} [{np.quantile(vr, 0.1):.2f}, {np.quantile(vr, 0.9):.2f}] | {np.median(prev):.0f} |")
                md_b2.append(f"| {key} | " + " | ".join(f"{g(i, 'FAIL'):.2f}" for i in
                                                         ("S1", "S1r", "S2", "S2r", "S3", "S4", "S4n", "S5", "O1", "O1n", "O2"))
                             + f" | {g('O3', 'PASS'):.2f} |")
                print("B", key, pr, prr, prc, flush=True)
        md += oracle_rows
        md += md_b2
        md.append("")

    if "C" in a.parts:
        md += ["## C. Review-comparable: N = 3, 8, oracle parameters (REVIEW-fable-v2 must-fix A table)", "",
               "Truth simplified to the review's simulation: approval probability 0.6 per review (defect 0.4, always caught), "
               "p = 0.005 collisions in the merge queue, bounced changes return after a fixed 10 min without using a worker, "
               "exponential review service, no start-up, negligible CI time. Amdahl truth: alpha-only workers, a reviewer that "
               "keeps up, bounced changes abandoned (the review's `finite_reviewer=False`). Predictions from the true lambda, V, b. "
               "'3 rivals' scores Carnot / Amdahl / linear as the review did; '4 rivals' adds USL (the plan).", ""]
        base = dict(defect_p=0.4, task_sd=0.0, rework_defect_factor=1.0, catch0=1.0, false_reject=0.0, visible_fail=0.0,
                    conflict_share=1.0, rework_min=10.0, rework_dist="fixed", rework_uses_worker=False, startup_min=0.0,
                    claim_race_p=0.0, ci_hidden_min=0.001, ci_post_min=0.001, rebase_s=0.01, review_error_p=0.0,
                    usage_every_min=0.0)
        T["C"] = {}
        md.append("| q | CV | P(Carnot picked \\| Carnot) 3 rivals / 4 rivals | review: cv 0 / 0.3 | P(Amdahl picked \\| Amdahl) 3 / 4 rivals | review: cv 0 / 0.3 |")
        md.append("|---|---|---|---|---|---|")
        review_tab = {1.5: ("1.00 / 1.00", "0.96 / 0.82"), 2.0: ("1.00 / 1.00", "0.93 / 0.78"), 2.5: ("0.98 / 0.99", "0.89 / 0.72"),
                      3.0: ("0.95 / 0.96", "0.85 / 0.64"), 4.0: ("0.75 / 0.82", "0.75 / 0.55")}
        lam = 4.0
        for q in (1.5, 2.0, 2.5, 3.0, 4.0):
            for cv in (0.0, 0.3):
                V0 = q * lam
                oracle = dict(lambda_pilot=lam * float(X_usl(2)) / 2, n_pilot=2, V=V0, b_review=0.4, b_hidden=0.0, b_other=0.0,
                              r0=0.4, ci_time_min=0.0)
                ores_c = run_many([dict(truth="carnot", over=dict(base, V0=V0, cv_window=cv), sizes=(3, 8), oracle=oracle,
                                        seed=300000 + int(q * 1000) + int(cv * 100) * 7 + i * 13) for i in range(R)], a.workers)
                oracle_a = dict(oracle, lambda_pilot=lam * (2 / (1 + 0.1)) / 2)
                ores_a = run_many([dict(truth="amdahl", over=dict(base, V0=V0, cv_window=cv, reviewer_load=50.0, rework_returns=False),
                                        sizes=(3, 8), oracle=oracle_a, seed=400000 + int(q * 1000) + int(cv * 100) * 7 + i * 13)
                                   for i in range(R)], a.workers)
                _, c3 = pick_rates(ores_c, "plan", "best3")
                _, c4 = pick_rates(ores_c, "plan", "best")
                _, a3 = pick_rates(ores_a, "plan", "best3")
                _, a4 = pick_rates(ores_a, "plan", "best")
                fin_c = np.array([r["finished"] for r in ores_c])  # windows: 3, 8, 8, 3
                T["C"][f"q={q},cv={cv}"] = dict(carnot3=c3["carnot"], carnot4=c4["carnot"], amdahl3=a3["amdahl"], amdahl4=a4["amdahl"],
                                                carnot4_picks=c4, amdahl4_picks=a4,
                                                carnot_mean_finished_N3=float(fin_c[:, [0, 3]].mean()),
                                                carnot_mean_finished_N8=float(fin_c[:, [1, 2]].mean()))
                md.append(f"| {q} | {cv} | {c3['carnot']:.2f} / {c4['carnot']:.2f} | {review_tab[q][0]} | {a3['amdahl']:.2f} / {a4['amdahl']:.2f} | {review_tab[q][1]} |")
                print("C", q, cv, c3["carnot"], c4["carnot"], a3["amdahl"], a4["amdahl"], flush=True)
        md.append("")
        md.append("Mean finished per window under Carnot truth, N = 3 -> 8: " + "; ".join(
            f"q {k.split(',')[0][2:]}: {v['carnot_mean_finished_N3']:.1f} -> {v['carnot_mean_finished_N8']:.1f}"
            for k, v in T["C"].items() if k.endswith("cv=0.0")) + " (review: 3.9->3.7, 5.4->5.2, 6.6->6.7, 7.7->8.3, 9.5->10.9).")
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

    if "F" in a.parts:
        md += ["## F. Surprise outcomes under a reviewer whose pace depends on load", "",
               "Carnot workers, sizes (1, 5), pilot-estimated V. 'skim': service rate x (1 + 0.08 depth); 'slow': x (1 - 0.04 depth), "
               "floor 0.25. Share of sweeps coding each outcome FAIL (S = surprise happened; O1 = V not stable).", ""]
        T["F"] = {}
        md.append("| Truth | S1 | S1r | S2 | S2r | S3 | S4 | S4n | S5 | O1 | O1n | O2 | median V(high)/V(low) | median finished ratio |")
        md.append("|---|---|---|---|---|---|---|---|---|---|---|---|---|---|")
        for tr in ("carnot", "carnot-skim", "carnot-slow"):
            res = run_many([dict(truth=tr, sizes=(1, 5), parts=("escape",), seed=700000 + 10000 * ["carnot", "carnot-skim", "carnot-slow"].index(tr) + i)
                            for i in range(R)], a.workers)
            res = [r for r in res if "outcomes" in r]
            o = outcome_rates(res)
            g = lambda i: o.get(i, {}).get("FAIL", 0.0)
            vh = np.median([r["V_hi_lo"] for r in res if r.get("V_hi_lo") is not None])
            fr = np.median([r["ratio"] for r in res if r.get("ratio") is not None])
            T["F"][tr] = dict(outcomes=o, median_V_hi_lo=float(vh), median_ratio=float(fr))
            md.append(f"| {tr} | " + " | ".join(f"{g(i):.2f}" for i in ("S1", "S1r", "S2", "S2r", "S3", "S4", "S4n", "S5", "O1", "O1n", "O2"))
                      + f" | {vh:.2f} | {fr:.2f} |")
            print("F", tr, {k: g(k) for k in ('S1', 'S2', 'S4', 'O1')}, flush=True)
        md.append("")

    md.append(f"Total time {time.time() - t_start:.0f} s.")
    (outdir / "selftest.json").write_text(json.dumps(T, indent=2, default=str) + "\n")
    (outdir / "SELFTEST.md").write_text("\n".join(md) + "\n")
    print("\n".join(md))
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
