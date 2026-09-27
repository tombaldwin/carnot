#!/usr/bin/env python3
"""Final runs: shortlist completion, V-test power against a reviewer +/-25% at the high size, robustness."""
import json, time
from dataclasses import asdict
import runner
from dsim import Design
from design_search import ALL, row, REFERENCES, HERE

R = 800
LEADS = {
    "recommended": Design(worker="haiku", lam=8, q=2.5, sizes=(1, 12), L=120, reps=3, n_p=1, L_p=60, pw=8, vcal=120),
    "alt: reviewer nearer the knee (q 2)": Design(worker="haiku", lam=8, q=2, sizes=(1, 12), L=120, reps=3, n_p=1, L_p=60, pw=8, vcal=120),
    "alt: Sonnet workers": Design(worker="sonnet", lam=8, q=2.5, sizes=(1, 10), L=120, reps=1, n_p=1, L_p=60, pw=8, vcal=120),
}
EXTRA = [LEADS["alt: Sonnet workers"],
         Design(worker="sonnet", lam=8, q=2.5, sizes=(1, 4, 12), L=90, reps=1, n_p=1, L_p=60, pw=8, vcal=120),
         Design(worker="haiku", lam=8, q=2.5, sizes=(1, 12), L=120, reps=2, n_p=1, L_p=60, pw=8, vcal=120)]

if __name__ == "__main__":
    t = time.time()
    C = json.loads((HERE / "stageC.json").read_text())
    have = {json.dumps(r["design"], sort_keys=True) for r in C}
    new = [d for d in EXTRA if json.dumps({**asdict(d), "sizes": list(d.sizes)}, sort_keys=True) not in have]
    res = runner.run(new, ALL, R, seed0=100_000, extra=True)
    res5 = runner.run(new, ["carnot", "skim", "slow"], R, seed0=200_000, service_cv=0.5)
    for d in new:
        r_ = row(d, runner.summarise(res[d]), dict(kind="search"))
        m = runner.summarise(res5[d])
        r_["svc05"] = {k: m[k] for k in m if k.split("_")[0] in ("O1n", "S4n", "Vratio", "Vdur", "Vcal")}
        C.append(r_)
    print("extra done", time.time() - t, flush=True)
    ds = [Design(**{**r["design"], "sizes": tuple(r["design"]["sizes"])}) for r in C]
    for svc in (1.0, 0.5):
        resN = runner.run(ds, ["carnot", "skimN", "slowN"], R, seed0=500_000, service_cv=svc)
        for r_, d in zip(C, ds):
            m = runner.summarise(resN[d])
            r_[f"vN_svc{svc}"] = {k: m[k] for k in m if k.split("_")[0] in ("O1n", "S4n", "Vratio", "Vdur", "Vcal", "VhiLo")}
        print("vN", svc, time.time() - t, flush=True)
    (HERE / "stageC_final.json").write_text(json.dumps(C, indent=1))
    scen = {"base": {}, "burn x1.5": dict(burn_mult=1.5), "lambda x0.7": dict(lam_scale=0.7),
            "burn x1.5 and lambda x0.7": dict(burn_mult=1.5, lam_scale=0.7),
            "slow merge queue (2.6 min/change)": dict(ci_slow=True), "calibration shift sd 0.2": dict(vcal_shift_sd=0.2),
            "no window over-dispersion": dict(cv_window=0.0), "haiku lambda 0.7 x target": dict(haiku_lam=0.7),
            "haiku lambda 1.0 x target": dict(haiku_lam=1.0), "anchor on low arm (analysis variant)": dict(anchor_low=True),
            "uncapped truths: rework lost": dict(truth_over=dict(rework_returns=False))}
    rob = []
    for name, kw in scen.items():
        for lab, d in LEADS.items():
            if name.startswith("haiku") and d.worker != "haiku":
                continue
            tr = ["carnot", "usl", "amdahl", "linear"] if name != "uncapped truths: rework lost" else ["usl", "amdahl", "linear"]
            rr = runner.run([d], tr + (["skimN", "slowN"] if name == "base" else []), R, seed0=600_000, **kw)
            m = runner.summarise(rr[d])
            rob.append(dict(scenario=name, lead=lab, label=d.label(), metrics=m))
            print(name, lab, {k: round(m[k], 2) for k in m if k.startswith(("rec_", "bin_", "cost_carnot"))}, flush=True)
    (HERE / "robustness.json").write_text(json.dumps(rob, indent=1, default=float))
    print("time", time.time() - t)
