#!/usr/bin/env python3
"""Markdown tables for DESIGN-SEARCH.md from the stage outputs. Writes tables.md (included by hand)."""
from __future__ import annotations

import collections
import json
import math
from pathlib import Path

HERE = Path(__file__).resolve().parent
F4 = ("carnot", "usl", "amdahl", "linear")


def f(x, nd=2):
    if x is None or (isinstance(x, float) and math.isnan(x)):
        return "-"
    return f"{x:.{nd}f}"


def load(name):
    p = HERE / name
    return json.loads(p.read_text()) if p.exists() else None


def sizes_txt(d):
    s = d["sizes"]
    return "gate" if s and s[0] == "gate" else " & ".join(map(str, s))


def pilot_txt(d):
    return f"{d['n_p']} w x {d['pw']} x {d['L_p']} min" + (f" + cal {d['vcal']}" if d["vcal"] else "") + \
        ("" if d["t1_reviewed"] else ", T1 unrev.")


def V_of(d):
    return d["q"] * d["lam"] * (0.85 if d["worker"] == "haiku" else 1.0)


def ranked(C):
    def k(r):
        m = r["metrics"]
        return (r["kind"] == "search", m["rec_min"], m["bin_min"])
    rows = sorted(C, key=k, reverse=True)
    L = ["| # | Sizes, windows | Worker, lambda, V/h (q) | Pilot (T2) | Cost $ | Agent-h | Load at N_low / N_high (x V) | C / U / A / L recovered | **min** | Capped-vs-uncapped: C / U / A / L | **bin min** | V test +/-25% at N_high: O1n power, FPR (svc CV 1 / 0.5) | Vdur power (svc CV 1 / 0.5), FPR | S1r / S2r false alarm | Escape power a .05 / .01 | Collision k-slope, p .01 / .05 (a .05); p .05 at a .01 |",
         "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for i, r in enumerate(rows, 1):
        d, m = r["design"], r["metrics"]
        v1, v5 = r.get("vN_svc1.0", {}), r.get("vN_svc0.5", {})
        name = r.get("name")
        lab = (f"**{name}**: " if name else "") + f"{sizes_txt(d)}; {d['reps']} x {d['L']} min"
        cost = m.get("cost_carnot")
        cost = r["nominal_cost"] if cost is None or cost != cost else cost
        load = "-" if r.get("load_low") is None else f"{r['load_low']:.2f} / {r['load_high']:.2f}"
        L.append(
            f"| {i} | {lab} | {d['worker']}, {d['lam']:g}, {V_of(d):.0f} ({d['q']:g}) | {pilot_txt(d)} | {cost:.0f} | "
            f"{r['agent_h']:.1f} | {load} | {' / '.join(f(m[f'rec_{t}']) for t in F4)} | **{f(m['rec_min'])}** | "
            f"{' / '.join(f(m[f'bin_{t}']) for t in F4)} | **{f(m['bin_min'])}** | "
            f"{f(v1.get('O1n_powN'))}, {f(v1.get('O1n_carnot'))} / {f(v5.get('O1n_powN'))}, {f(v5.get('O1n_carnot'))} | "
            f"{f(v1.get('Vdur_powN'))} / {f(v5.get('Vdur_powN'))}, {f(v1.get('Vdur_carnot'))} | "
            f"{f(m.get('S1r_carnot'))} / {f(m.get('S2r_carnot'))} | "
            f"{f(m.get('esc0.05_escape'))} / {f(m.get('esc0.01_escape'))} | "
            f"{f(m.get('coll0.05_coll01'))} / {f(m.get('coll0.05_coll05'))}; {f(m.get('coll0.01_coll05'))} |")
    return "\n".join(L), rows


def vtable(C, labels):
    L = ["| Design | truth | O1n | S4n | Vratio (exact CI of V_hi/V_lo excl. 1) | Vdur (Welch, log review durations, hi vs lo) | Vcal (hi vs free calibration) | median V_hi/V_lo |",
         "|---|---|---|---|---|---|---|---|"]
    for r in C:
        if r["label"] not in labels:
            continue
        for svc in ("1.0", "0.5"):
            v = r.get(f"vN_svc{svc}", {})
            for t in ("carnot", "skimN", "slowN"):
                L.append(f"| {r['label']} (service CV {svc}) | {t} | {f(v.get('O1n_' + t))} | {f(v.get('S4n_' + t))} | "
                         f"{f(v.get('Vratio_' + t))} | {f(v.get('Vdur_' + t))} | {f(v.get('Vcal_' + t))} | {f(v.get('VhiLo_' + t))} |")
    return "\n".join(L)


def robtable(rob):
    L = ["| Scenario | Design | realised cost $ | C / U / A / L recovered | min | capped-vs-uncapped C / U / A / L | bin min |",
         "|---|---|---|---|---|---|---|"]
    for r in rob:
        m = r["metrics"]
        rec = " / ".join(f(m.get(f"rec_{t}")) for t in F4)
        bn = " / ".join(f(m.get(f"bin_{t}")) for t in F4)
        L.append(f"| {r['scenario']} | {r['lead']} | {f(m.get('cost_carnot', m.get('cost_usl')), 0)} | {rec} | {f(m.get('rec_min'))} | {bn} | {f(m.get('bin_min'))} |")
    return "\n".join(L)


def ceiling_table(rows):
    L = ["| Worker | N_max allowed | best sizes, reps x min, lambda, q | cost $ | C / U / A / L | min (oracle level) | min (level profiled out) |",
         "|---|---|---|---|---|---|---|"]
    for w in ("sonnet", "haiku"):
        for nm in (6, 8, 10, 12):
            sub = [r for r in rows if r["worker"] == w and max(r["sizes"]) <= nm]
            a = max((r for r in sub if not r["profile"]), key=lambda r: r["min"])
            p = max((r for r in sub if r["profile"]), key=lambda r: r["min"])
            L.append(f"| {w} | {nm} | {'/'.join(map(str, a['sizes']))}, {a['reps']} x {a['L']}, {a['lam']}, {a['q']} | {a['cost']} | "
                     f"{a['carnot']:.2f} / {a['usl']:.2f} / {a['amdahl']:.2f} / {a['linear']:.2f} | {a['min']:.2f} | {p['min']:.2f} |")
    return "\n".join(L)


def pilot_table(B):
    agg = collections.defaultdict(list)
    for r in B:
        d = r["design"]
        agg[(d["n_p"], d["L_p"], d["pw"], d["vcal"], d["t1_reviewed"])].append(r["metrics"])
    L = ["| T2 workers | window min | windows | free calibration reviews | T1 reviewed | T2 worker-h | mean min recovery | mean capped-vs-uncapped min | pilot V / true V, 10-50-90% (Carnot) |",
         "|---|---|---|---|---|---|---|---|---|"]
    for k, ms in sorted(agg.items(), key=lambda kv: -sum(m["rec_min"] for m in kv[1]) / len(kv[1])):
        pv = [m["pilotV_carnot"] for m in ms if m.get("pilotV_carnot")]
        pv = [sum(x[i] for x in pv) / len(pv) for i in range(3)] if pv else None
        L.append(f"| {k[0]} | {k[1]} | {k[2]} | {k[3]} | {'yes' if k[4] else 'no'} | {k[0] * k[1] * k[2] / 60:.1f} | "
                 f"{sum(m['rec_min'] for m in ms) / len(ms):.3f} | {sum(m['bin_min'] for m in ms) / len(ms):.3f} | "
                 + (f"{pv[0]:.2f} / {pv[1]:.2f} / {pv[2]:.2f}" if pv else "-") + " |")
    return "\n".join(L)


def lever_summary(A):
    out = []
    for lever in ("worker", "lam", "q", "L", "reps"):
        g = collections.defaultdict(list)
        for r in A:
            g[r["design"][lever]].append(r["metrics"]["rec_min"])
        out.append(f"- **{lever}**: " + ", ".join(f"{k}: best {max(v):.2f}, mean {sum(v) / len(v):.2f}" for k, v in sorted(g.items())))
    g = collections.defaultdict(list)
    for r in A:
        g["/".join(map(str, r["design"]["sizes"]))].append(r["metrics"]["rec_min"])
    out.append("- **sizes**: " + ", ".join(f"{k}: best {max(v):.2f}" for k, v in sorted(g.items(), key=lambda kv: -max(kv[1]))))
    return "\n".join(out)


if __name__ == "__main__":
    C = load("stageC_final.json") or load("stageC.json")
    parts = []
    t, rows = ranked(C)
    parts += ["## Ranked table", "", t, ""]
    labs = {r["label"] for r in rows[:3]} | {r["label"] for r in C if r["design"]["worker"] == "sonnet" and r["kind"] == "search"}
    parts += ["## V tests", "", vtable(C, labs), ""]
    rob = load("robustness.json")
    if rob:
        parts += ["## Robustness", "", robtable(rob), ""]
    parts += ["## Ceiling", "", ceiling_table(load("ceiling.json")), ""]
    parts += ["## Pilot levers (stage B, averaged over the sweep shapes carried forward)", "", pilot_table(load("stageB.json")), ""]
    parts += ["## Stage A lever summary", "", lever_summary(load("stageA.json")), ""]
    (HERE / "tables.md").write_text("\n".join(parts) + "\n")
    print("\n".join(parts))
