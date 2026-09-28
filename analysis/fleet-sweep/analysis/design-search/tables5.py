#!/usr/bin/env python3
"""Markdown tables for DESIGN-SEARCH-v5.md and OPERATING-CHARACTERISTICS-v5.md from v5_stageA.json, v5_stageB.json,
v5_pilot.json and oc_v5.json (whichever exist). Writes tables_v5.md next to this file."""
from __future__ import annotations

import json
import math
from pathlib import Path

HERE = Path(__file__).resolve().parent


def f(x, fmt="{:.2f}"):
    return "–" if x is None or (isinstance(x, float) and math.isnan(x)) else fmt.format(x)


def main():
    L = []
    A = HERE / "v5_stageA.json"
    if A.exists():
        rows = json.loads(A.read_text())
        sc = lambda r: r["head"]["bend_amdahl"] + 0.5 * r["head"]["bend_mild"] + 0.5 * r["head"]["esc20"] + 0.5 * r["head"]["coll01"]
        rows.sort(key=lambda r: -sc(r))
        L += ["## Stage A: every in-budget design, 200 studies per cell (top 30 by the screening score)", "",
              "Score = SCALE power (Amdahl) + 0.5 x (power vs a mild bend + ESC-N power 0.10 -> 0.20 + COLL power p = 0.01).", "",
              "| design | $ at 2.10 | windows | SCALE FPR | power Amdahl | mild | linear-vs-bending min | ESC-N 0.20 | ESC-N FPR | "
              "COLL p = 0.01 | p = 0.02 | COLL FPR | reviewer util N_max (linear, median max) |",
              "|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
        for r in rows[:30]:
            h = r["head"]
            L.append(f"| {r['label']} | {r['cost']:.0f} | {r['windows']} | {f(h['bend_fpr'])} | {f(h['bend_amdahl'])} | {f(h['bend_mild'])} | "
                     f"{f(h['bin_min'])} | {f(h['esc20'])} | {f(h['esc_fpr'])} | {f(h['coll01'])} | {f(h['coll02'])} | {f(h['coll_fpr'])} | "
                     f"{f(h['ru_max_linear'])} |")
        L.append("")
        by = {}
        for r in rows:
            k = (tuple(r["design"]["sizes"]), r["design"]["L"])
            if k not in by or sc(r) > sc(by[k]):
                by[k] = r
        L += ["Best design per (sizes, window length):", "", "| sizes | window | best design | $ | SCALE power Amdahl | ESC-N 0.20 | COLL p = 0.01 |",
              "|---|---|---|---|---|---|---|"]
        for k in sorted(by):
            r = by[k]
            L.append(f"| {k[0]} | {k[1]} | {r['label']} | {r['cost']:.0f} | {f(r['head']['bend_amdahl'])} | {f(r['head']['esc20'])} | "
                     f"{f(r['head']['coll01'])} |")
        L.append("")
    B = HERE / "v5_stageB.json"
    if B.exists():
        rows = json.loads(B.read_text())
        L += ["## Stage B: shortlist and degrade candidates, 1000 studies per cell", "",
              "| design | $ at 2.10 / 3.15 | windows | SCALE FPR (CV 0.3 / 0.15 / 0.5) | power Amdahl / USL / mild | Amdahl, CV growing as "
              "windows shorten | Welch FPR / power | linear-vs-bending pick (linear / Amdahl) | four-way pick (Amdahl / USL / Carnot) |",
              "|---|---|---|---|---|---|---|---|---|"]
        for r in rows:
            m, c = r["metrics"], r["cv_len"]
            L.append(f"| {r['label']} | {r['cost']:.0f} / {r['cost15']:.0f} | {r['windows']} | {f(m['bend|linear'], '{:.3f}')} / "
                     f"{f(m['bend|linear/cv=0.15'], '{:.3f}')} / {f(m['bend|linear/cv=0.5'], '{:.3f}')} | {f(m['bend|amdahl'])} / "
                     f"{f(m['bend|usl'])} / {f(m['bend|mild'])} | {f(c['bend_amdahl'])} | {f(m['bend_welch|linear'], '{:.3f}')} / "
                     f"{f(m['bend_welch|amdahl'])} | {f(m['bin|linear'])} / {f(m['bin|amdahl'])} | {f(m['fam4|amdahl'])} / {f(m['fam4|usl'])} / "
                     f"{f(m['fam4|carnot'])} |")
        L += ["", "| design | ESC-N FPR / 0.15 / 0.20 (USL workers) / 0.20 (linear) | ESC half-width | COLL FPR / p = 0.005 (linear) / 0.01 "
              "(USL; linear) / 0.02 / 0.05 | census graph (manifest 1 / 0.5) | reviewer util N = 12, linear: median / p95; 20-40 s reviews | "
              "SCALE FPR with 20-40 s reviews |", "|---|---|---|---|---|---|---|"]
        for r in rows:
            m = r["metrics"]
            L.append(f"| {r['label']} | {f(m['esc|usl'], '{:.3f}')} / {f(m['esc|usl/esc15'])} / {f(m['esc|usl/esc20'])} / "
                     f"{f(m['esc|linear/esc20'])} | {f(m['esc_hw|usl'], '{:.3f}')} | {f(m['coll|usl'], '{:.3f}')} / {f(m['coll|linear/p=0.005'])} / "
                     f"{f(m['coll|carnot/p=0.01'])}; {f(m['coll|linear/p=0.01'])} / {f(m['coll|carnot/p=0.02'])} / {f(m['coll|carnot/p=0.05'])} | "
                     f"{f(m['coll|carnot/census'])} / {f(m['coll|carnot/census/man=0.5'])} | {f(m['ru_max|linear'])} / {f(m['ru_max_p95|linear'])}; "
                     f"{f(m['ru_max|linear/slowrev'])} | {f(m['bend|linear/slowrev'], '{:.3f}')} |")
        L.append("")
    P = HERE / "v5_pilot.json"
    if P.exists():
        rows = json.loads(P.read_text())
        L += ["## Pilot: a separate T2 pilot with pilot-anchored rival levels, against none (free levels), 1000 studies", "",
              "| design | $ at 2.10 | fits | linear-vs-bending pick: free (linear / Amdahl / USL) | pilot-anchored | four-way pick "
              "(Amdahl / USL): free | pilot-anchored |", "|---|---|---|---|---|---|---|"]
        for r in rows:
            m = r["metrics"]
            anc = "bin_anchor|linear" in m
            L.append(f"| {r['label']} | {r['cost']:.0f} | {'yes' if r.get('fits') else 'no'} | {f(m['bin|linear'])} / {f(m['bin|amdahl'])} / "
                     f"{f(m['bin|usl'])} | " + (f"{f(m['bin_anchor|linear'])} / {f(m['bin_anchor|amdahl'])} / {f(m['bin_anchor|usl'])}" if anc else "–") +
                     f" | {f(m['fam4|amdahl'])} / {f(m['fam4|usl'])} | " +
                     (f"{f(m['fam4_anchor|amdahl'])} / {f(m['fam4_anchor|usl'])}" if anc else "–") + " |")
        L.append("")
    (HERE / "tables_v5.md").write_text("\n".join(L) + "\n")
    print("\n".join(L))


if __name__ == "__main__":
    main()
