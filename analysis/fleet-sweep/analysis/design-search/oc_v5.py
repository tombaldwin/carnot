#!/usr/bin/env python3
"""Operating characteristics of the recommended PLAN-v5 design and its degrade design (PLAN-v5 section 5).

    PY=/Users/tom/git/carnot/analysis/aidev/.venv/bin/python
    $PY oc_v5.py [--reps 2000] [--out oc_v5.json]

Simulation only: synth (V5_TRUTH) -> derive -> v5 codings, through dsim5.study5 (the design search's code). The designs
are read from v5.DESIGN_V5 (full) and DESIGN_V5["degrade"] (1.5x burn). Cells (truth names as in dsim5.build_truth):
SCALE and FAMILY under linear / mild / Amdahl / USL / Carnot workers at window CV 0.3, 0.15 and 0.5 (linear) and at
lambda -30%; ESC-N under no reviewer effect and escape shares 0.10 -> 0.15 / 0.20; COLL at p = 0 (USL workers; 1%
background integration failures), 0.005, 0.01, 0.02, 0.05, under linear workers, and on the census-like pair-conflict
graph; UTIL with 10-30 s and 20-40 s reviews. Common random numbers: one seed block per truth, shared by both designs.
Writes oc_v5.json; OPERATING-CHARACTERISTICS-v5.md and v5.V5_OC are written from it by hand.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))
import search5  # noqa: E402
from dsim5 import D5, fits  # noqa: E402
import v5  # noqa: E402

CELLS = ("linear", "mild", "amdahl", "usl", "carnot",
         "linear/cv=0.15", "amdahl/cv=0.15", "usl/cv=0.15", "linear/cv=0.5", "amdahl/cv=0.5",
         "linear/lam70", "amdahl/lam70", "usl/lam70",
         "usl/esc15", "usl/esc20", "linear/esc15", "linear/esc20",
         "carnot/p=0.01", "carnot/p=0.02", "carnot/p=0.05", "linear/p=0.005", "linear/p=0.01", "linear/p=0.02",
         "usl/nobg", "carnot/census", "carnot/census/man=0.5", "linear/census",
         "linear/slowrev", "amdahl/slowrev", "usl/slowrev")


def design_of(dd):
    sizes = tuple(dd["sizes"])
    return D5(sizes, tuple(dd["reps"][n] for n in sizes), int(dd["window_min"]))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--reps", type=int, default=2000)
    ap.add_argument("--out", default=str(HERE / "oc_v5.json"))
    a = ap.parse_args()
    full = design_of(v5.DESIGN_V5)
    deg = design_of(v5.DESIGN_V5["degrade"])
    t = time.time()
    out = dict(meta=dict(reps=a.reps, full=full.label(), degrade=deg.label(), full_cost=full.cost(),
                         full_cost_15=full.cost(1.5 * 2.10), degrade_cost_15=deg.cost(1.5 * 2.10),
                         fits_full=fits(full), fits_degrade_15=fits(deg, 1.5 * 2.10), started=time.strftime("%Y-%m-%d %H:%M")))
    res = search5.run([full, deg], CELLS, a.reps, seed0=900_000, model_form=True)
    out["full"] = search5.summarise(res[full])
    out["degrade"] = search5.summarise(res[deg])
    out["meta"]["seconds"] = time.time() - t
    Path(a.out).write_text(json.dumps(out, indent=1, default=float) + "\n")
    print(json.dumps({k: search5.headline(out[k]) for k in ("full", "degrade")}, indent=1))


if __name__ == "__main__":
    main()
