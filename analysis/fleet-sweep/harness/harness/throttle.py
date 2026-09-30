"""Abort rule 1 (throttling), PLAN-v6 section 5.4 (revised after T1), and the old activity measure.

    python -m harness throttle runs/<T1b run id> [--exclude s2,s3 | --exclude none] [--split-min 90] [--json out.json]

T1b (like T1) runs one slot, then all twelve (``start_schedule = [[0, 1], [90, 12]]``). For every task launched
in each phase (phase A: launched before the second start_schedule minute; phase B: at or after it):

* **start-up** = the task's branch first pushed (``claim``) minus the routine's ``run_once_at`` (from the task's
  first ``launch_detail … run_once_at=…`` note), or minus its ``session_launch`` if that note is missing, or,
  with one session per slot, minus its ``session_message kind=task`` for a task handed out by follow-up;
* **coding** = its first READY (``submit`` with attempt_no 1, before window_end) minus the ``claim``.

The statistic is the ratio of geometric means, many slots over one, with a Welch 90% interval on the log scale
(two-sided 90%: each bound a one-sided 5% test); **tolerance 1.25**:

* **STOP** (report T1b) if the start-up interval lies wholly above 1.25;
* **CLEAR** if it lies wholly below 1.25;
* **INCONCLUSIVE** otherwise: proceed, with the ratio and interval reported beside SCALE.

A coding interval wholly above 1.25 is a **flag, not a stop** (coding contains the coordination drag SCALE
measures). Slots lost to operator-logged failures unrelated to the service (T1: the CLI auto-update that took
s2 and s3) are excluded before the ratios are computed: ``--exclude s2,s3``; without ``--exclude`` the slots with
an operator ``worker_down`` (reason starting ``operator``) are excluded and listed; ``--exclude none`` keeps all.
The same statistic is ``analysis/v6.py`` ``throttle_v6`` (pure Python here: the harness has no numpy/scipy).

Reported beside it, as before (PLAN-v4 section 7.4; not part of the rule any more): *activity* per slot-minute
(session launches + READY submissions per slot-open minute, phase A from the first slot's start + ``skip_min`` to
the second group's start, phase B from then, down time excluded) and its ratio, with the old 0.8 threshold's
reading; T1 showed it cannot decide anything (it mixes service speed with rework and review waiting).
"""
from __future__ import annotations

import datetime as dt
import json
import math
import re
import statistics
from pathlib import Path

from .events import read_events

THRESHOLD = 0.8        # the retired activity rule (PLAN-v4), still reported
SKIP_MIN = 5.0
TOLERANCE = 1.25       # PLAN-v6 rule 1: a service-side slowdown of 25% or more at twelve slots is throttling
LEVEL = 0.90           # two-sided interval level
LEAD_RE = re.compile(r"launch_detail slot=(\S+) task=(\S+) .*run_once_at=(\S+)")
SCHED_RE = re.compile(r"start_schedule minute=([\d.]+) slots=(\S+)")


def _t(s: str) -> dt.datetime:
    return dt.datetime.fromisoformat(s.replace("Z", "+00:00"))


def _overlap(a0, a1, b0, b1) -> float:
    return max(0.0, (min(a1, b1) - max(a0, b0)).total_seconds() / 60.0)


def activity_report(run_dir: Path, skip_min: float = SKIP_MIN, threshold: float = THRESHOLD) -> dict:
    """The PLAN-v4 activity measure (retired as the rule; reported beside rule 1)."""
    run_dir = Path(run_dir)
    ev = read_events(run_dir / "events.jsonl")
    rj = json.loads((run_dir / "run.json").read_text()) if (run_dir / "run.json").exists() else {}
    notes = [e for e in ev if e["type"] == "note"]
    if rj.get("window_start"):
        w0, w1 = _t(rj["window_start"]), _t(rj["window_end"])
    else:
        w0 = next(_t(e["t"]) for e in notes if e["text"] == "window_start")
        w1 = next(_t(e["t"]) for e in notes if e["text"] == "window_end")

    starts: dict[str, list[dt.datetime]] = {}
    for e in ev:
        if e["type"] == "worker_start":
            starts.setdefault(e["worker"], []).append(_t(e["t"]))
    if len(starts) < 2:
        raise SystemExit(f"{run_dir}: throttling needs a one-slot phase and a many-slot phase; "
                         f"found {len(starts)} worker slot(s)")
    first = {w: min(ts) for w, ts in starts.items()}
    order = sorted(first, key=first.get)
    w_a = order[0]
    t_split = min(first[w] for w in order[1:])

    # down intervals per worker
    down: dict[str, list[tuple[dt.datetime, dt.datetime]]] = {}
    open_: dict[str, dt.datetime] = {}
    for e in ev:
        if e["type"] == "worker_down":
            open_.setdefault(e["worker"], _t(e["t"]))
        elif e["type"] == "worker_restart" and e["worker"] in open_:
            down.setdefault(e["worker"], []).append((open_.pop(e["worker"]), _t(e["t"])))
    for w, t in open_.items():
        down.setdefault(w, []).append((t, w1))

    skip = dt.timedelta(minutes=skip_min)
    expo = {"A": {w_a: (first[w_a] + skip, t_split)}, "B": {}}
    for w in order:
        expo["B"][w] = (max(t_split, first[w] + skip), w1)

    def minutes(ph):
        tot = 0.0
        for w, (a, b) in expo[ph].items():
            if b <= a:
                continue
            m = (b - a).total_seconds() / 60.0
            m -= sum(_overlap(a, b, d0, d1) for d0, d1 in down.get(w, []))
            tot += max(0.0, m)
        return tot

    def in_phase(ph, w, t):
        iv = expo[ph].get(w)
        return iv is not None and iv[0] <= t < iv[1] and not any(d0 <= t < d1 for d0, d1 in down.get(w, []))

    res = {}
    session_launches = [(e["slot"], _t(e["t"])) for e in ev if e["type"] == "session_launch"]
    claims = {}   # task -> (slot, time the task started): its first session_launch, else its first claim
    for e in ev:
        if e["type"] == "session_launch" and e["task"] not in claims:
            claims[e["task"]] = (e["slot"], _t(e["t"]))
    if not session_launches:
        for e in ev:
            if e["type"] == "claim" and e["task"] not in claims:
                claims[e["task"]] = (e["worker"], _t(e["t"]))
    first_ready = {}
    for e in ev:
        if e["type"] == "submit" and e["task"] not in first_ready:
            first_ready[e["task"]] = _t(e["t"])
    for ph in ("A", "B"):
        mins = minutes(ph)
        if session_launches:
            launches = sum(1 for w, t in session_launches if in_phase(ph, w, t))
        else:
            launches = sum(1 for w, ts in starts.items() for t in ts if t != first[w] and in_phase(ph, w, t))
        submits = sum(1 for e in ev if e["type"] == "submit" and in_phase(ph, e["worker"], _t(e["t"])))
        tok = [e for e in ev if e["type"] == "usage" and in_phase(ph, e["worker"], _t(e["t"]))]
        tokens = sum((e.get("tokens_in") or 0) + (e.get("tokens_out") or 0) for e in tok)
        c2r = [(first_ready[t] - tc).total_seconds() / 60.0 for t, (w, tc) in claims.items()
               if t in first_ready and in_phase(ph, w, tc)]
        res[ph] = dict(slots=len(expo[ph]), slot_minutes=round(mins, 2), launches=launches, submits=submits,
                       activity=launches + submits,
                       activity_per_slot_min=(launches + submits) / mins if mins > 0 else None,
                       usage_events=len(tok), tokens=tokens, tokens_per_slot_min=tokens / mins if mins > 0 and tok else None,
                       claim_to_ready_n=len(c2r),
                       claim_to_ready_median_min=round(statistics.median(c2r), 2) if c2r else None)
    A, B = res["A"], res["B"]
    ratio = (B["activity_per_slot_min"] / A["activity_per_slot_min"]
             if A["activity_per_slot_min"] and B["activity_per_slot_min"] is not None else None)
    ci = None
    if ratio and A["activity"] and B["activity"]:
        se = math.sqrt(1 / A["activity"] + 1 / B["activity"])
        ci = [round(ratio * math.exp(-1.96 * se), 3), round(ratio * math.exp(1.96 * se), 3)]
    tok_ratio = (B["tokens_per_slot_min"] / A["tokens_per_slot_min"]
                 if A["tokens_per_slot_min"] and B["tokens_per_slot_min"] is not None else None)
    measure, used = ("tokens", tok_ratio) if tok_ratio is not None else ("activity", ratio)
    c2r_ratio = (B["claim_to_ready_median_min"] / A["claim_to_ready_median_min"]
                 if A["claim_to_ready_median_min"] and B["claim_to_ready_median_min"] is not None else None)
    readings = [dict(t=e["t"], type="meter", credits_left_usd=e["credits_left_usd"], source=e.get("source"))
                for e in ev if e["type"] == "meter"]
    readings += [dict(t=e["t"], type="note", text=e["text"]) for e in notes
                 if e["text"].startswith("plan_usage") or e["text"].startswith("usage")]
    if used is None:
        verdict = "NOT EVALUATED"
    else:
        verdict = "THROTTLED" if used < threshold else "OK"
    return dict(run_dir=str(run_dir), rule="abort rule 1: throttled if per-slot rate at the many-slot phase < "
                f"{threshold} x the one-slot phase", measure=measure, ratio=None if used is None else round(used, 3),
                activity_ratio=None if ratio is None else round(ratio, 3), activity_ratio_ci95_approx=ci,
                token_ratio=None if tok_ratio is None else round(tok_ratio, 3),
                claim_to_ready_ratio=None if c2r_ratio is None else round(c2r_ratio, 3),
                verdict=verdict, split_at=t_split.isoformat(), skip_min=skip_min, phases=res,
                operator_readings=readings)


def format_activity(r: dict) -> str:
    A, B = r["phases"]["A"], r["phases"]["B"]
    L = [f"Activity (the PLAN-v4 measure, reported only), {r['run_dir']}",
         f"  one slot  : {A['activity']} activity ({A['launches']} launches + {A['submits']} READY) in "
         f"{A['slot_minutes']} slot-min -> {A['activity_per_slot_min']}; launch->READY median {A['claim_to_ready_median_min']} min (n={A['claim_to_ready_n']})",
         f"  {B['slots']} slots  : {B['activity']} activity ({B['launches']} launches + {B['submits']} READY) in "
         f"{B['slot_minutes']} slot-min -> {B['activity_per_slot_min']}; launch->READY median {B['claim_to_ready_median_min']} min (n={B['claim_to_ready_n']})",
         f"  activity ratio {r['activity_ratio']} (approx. 95% {r['activity_ratio_ci95_approx']}); token ratio {r['token_ratio']}; "
         f"launch->READY ratio {r['claim_to_ready_ratio']} (reported only)",
         f"  measure: {r['measure']}; ratio {r['ratio']} -> {r['verdict']} under the retired 0.8 rule"]
    for x in r["operator_readings"]:
        L.append(f"  reading {x['t']}: " + (f"meter ${x['credits_left_usd']} ({x['source']})" if x["type"] == "meter" else x["text"]))
    return "\n".join(L)


# ---------------------------------------------------------------------------------------------- rule 1 (PLAN-v6)
def _betacf(a: float, b: float, x: float) -> float:
    """Continued fraction of the regularized incomplete beta function (Numerical Recipes 6.4)."""
    tiny, qab, qap, qam = 1e-300, a + b, a + 1.0, a - 1.0
    c, d = 1.0, 1.0 - qab * x / qap
    d = 1.0 / (d if abs(d) > tiny else tiny)
    h = d
    for m in range(1, 300):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1.0 + aa * d
        d = 1.0 / (d if abs(d) > tiny else tiny)
        c = 1.0 + aa / c
        c = c if abs(c) > tiny else tiny
        h *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1.0 + aa * d
        d = 1.0 / (d if abs(d) > tiny else tiny)
        c = 1.0 + aa / c
        c = c if abs(c) > tiny else tiny
        de = d * c
        h *= de
        if abs(de - 1.0) < 1e-14:
            break
    return h


def _betainc(a: float, b: float, x: float) -> float:
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    lbt = math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b) + a * math.log(x) + b * math.log(1.0 - x)
    if x < (a + 1.0) / (a + b + 2.0):
        return math.exp(lbt) * _betacf(a, b, x) / a
    return 1.0 - math.exp(lbt) * _betacf(b, a, 1.0 - x) / b


def t_cdf(t: float, df: float) -> float:
    """Student t distribution function."""
    p = 0.5 * _betainc(df / 2.0, 0.5, df / (df + t * t))
    return 1.0 - p if t > 0 else p


def t_ppf(q: float, df: float) -> float:
    """Quantile of Student t (0.5 < q < 1), by bisection on t_cdf."""
    lo, hi = 0.0, 1.0
    while t_cdf(hi, df) < q:
        hi *= 2.0
    for _ in range(200):
        mid = (lo + hi) / 2.0
        if t_cdf(mid, df) < q:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2.0


def welch_log_ratio(a: list[float], b: list[float], level: float = LEVEL) -> dict:
    """Ratio of geometric means b / a with a Welch interval on the log scale (as analysis/v6._welch_log_ratio)."""
    la = [math.log(x) for x in a]
    lb = [math.log(x) for x in b]
    if len(la) < 2 or len(lb) < 2:
        return dict(ratio=None, ci=[None, None], se_log=None, df=None, n_a=len(la), n_b=len(lb))
    va = statistics.variance(la) / len(la)
    vb = statistics.variance(lb) / len(lb)
    se = math.sqrt(va + vb)
    df = (va + vb) ** 2 / (va ** 2 / (len(la) - 1) + vb ** 2 / (len(lb) - 1)) if va + vb > 0 else 1.0
    tq = t_ppf(0.5 + level / 2.0, df)
    d = statistics.fmean(lb) - statistics.fmean(la)
    return dict(ratio=math.exp(d), ci=[math.exp(d - tq * se), math.exp(d + tq * se)], se_log=se, df=df,
                n_a=len(la), n_b=len(lb))


def decide(startup: dict, coding: dict, tol: float = TOLERANCE) -> tuple[str, bool]:
    """Rule 1: STOP if the start-up interval lies wholly above ``tol``, CLEAR if wholly below, else
    INCONCLUSIVE; the coding flag is raised when the coding interval lies wholly above ``tol``."""
    lo, hi = startup["ci"]
    if lo is not None and lo > tol:
        dec = "STOP"
    elif hi is not None and hi < tol:
        dec = "CLEAR"
    else:
        dec = "INCONCLUSIVE"
    clo = coding["ci"][0]
    return dec, bool(clo is not None and clo > tol)


CODING_CUTOFF_S = 300.0   # PLAN-v7: a coding leg counts only for hand-outs >= 5 min before window end (both phases)


def task_legs(run: dict, events: list[dict], coding_cutoff_s: float = CODING_CUTOFF_S) -> list[dict]:
    """Per task: slot, hand-out (s from window start), start-up s, coding s (as analysis/v7.task_legs_v7). A coding leg
    counts only for a task handed out at least ``coding_cutoff_s`` before window end, so slow tasks handed out late in
    a short many-slot phase are not dropped selectively (READY after window end), which would mask a slowdown."""
    t0 = _t(run["window_start"])
    we = (_t(run["window_end"]) - t0).total_seconds()
    rows: dict[str, dict] = {}
    once: dict[str, float] = {}
    for e in events:
        t = (_t(e["t"]) - t0).total_seconds()
        ty = e["type"]
        if ty == "note":
            m = LEAD_RE.match(e.get("text", ""))
            if m and m.group(2) not in once:
                try:
                    once[m.group(2)] = (_t(m.group(3)) - t0).total_seconds()
                except ValueError:
                    pass
        elif ty == "session_launch":
            r = rows.setdefault(e["task"], {})
            r.setdefault("launch", t)
            r.setdefault("slot", e["slot"])
        elif ty == "session_message" and e.get("kind") == "task":   # one session per slot: a follow-up hand-out
            r = rows.setdefault(e["task"], {})
            r.setdefault("launch", t)
            r.setdefault("slot", e["slot"])
            r["followup"] = True
        elif ty == "claim":
            rows.setdefault(e["task"], {}).setdefault("claim", t)
        elif ty == "submit" and e.get("attempt_no") == 1:
            rows.setdefault(e["task"], {}).setdefault("ready", t)
    out = []
    for task, r in rows.items():
        if "launch" not in r or "claim" not in r:
            continue
        up = r["claim"] - (r["launch"] if r.get("followup") else max(r["launch"], once.get(task, r["launch"])))
        code = (r["ready"] - r["claim"] if "ready" in r and r["ready"] <= we and r["launch"] <= we - coding_cutoff_s
                else None)
        out.append(dict(task=task, slot=r.get("slot"), launch=r["launch"], followup=bool(r.get("followup")),
                        startup_s=up if up > 0 else None,
                        coding_s=code if code is not None and code > 0 else None))
    return out


def operator_down_slots(events: list[dict]) -> list[str]:
    """Slots with an operator-logged ``worker_down`` (reason starting 'operator')."""
    return sorted({e["worker"] for e in events if e["type"] == "worker_down"
                   and str(e.get("reason", "")).lower().startswith("operator")}, key=lambda x: int(x[1:]))


def _split_min(run: dict, events: list[dict]) -> float | None:
    mins = sorted({float(m.group(1)) for e in events if e["type"] == "note"
                   for m in [SCHED_RE.match(e.get("text", ""))] if m})
    if len(mins) > 1:
        return mins[1]
    starts = sorted({_t(e["t"]) for e in events if e["type"] == "worker_start"})
    if len(starts) > 1:     # older logs without the note: the second slot group's start
        w0 = _t(run["window_start"])
        later = [s for s in starts if (s - starts[0]).total_seconds() > 1.0]
        if later:
            return round((later[0] - w0).total_seconds() / 60.0, 3)
    return None


def throttle_report(run_dir: Path, skip_min: float = SKIP_MIN, exclude: list[str] | None = None,
                    split_min: float | None = None, tol: float = TOLERANCE, level: float = LEVEL) -> dict:
    """Rule 1 (PLAN-v6) on a one-slot-then-many log, with the old activity measure under ``activity``.
    ``exclude``: None = the slots with an operator worker_down; [] = none."""
    run_dir = Path(run_dir)
    ev = read_events(run_dir / "events.jsonl")
    run = json.loads((run_dir / "run.json").read_text()) if (run_dir / "run.json").exists() else {}
    if not run.get("window_start"):
        notes = [e for e in ev if e["type"] == "note"]
        run = dict(run, window_start=next(e["t"] for e in notes if e["text"] == "window_start"),
                   window_end=next(e["t"] for e in notes if e["text"] == "window_end"))
    if split_min is None:
        split_min = _split_min(run, ev)
    if split_min is None:
        raise SystemExit(f"{run_dir}: rule 1 needs a one-slot phase and a many-slot phase (a second "
                         "start_schedule group); none found")
    auto = exclude is None
    excl = operator_down_slots(ev) if auto else list(exclude)
    legs = [x for x in task_legs(run, ev) if x["slot"] not in set(excl)]
    A = [x for x in legs if x["launch"] < split_min * 60]
    B = [x for x in legs if x["launch"] >= split_min * 60]
    up = welch_log_ratio([x["startup_s"] for x in A if x["startup_s"]], [x["startup_s"] for x in B if x["startup_s"]],
                         level)
    code = welch_log_ratio([x["coding_s"] for x in A if x["coding_s"]], [x["coding_s"] for x in B if x["coding_s"]],
                           level)
    # One session per slot (PLAN-v7 rule 1): the phases mix launch start-ups (provisioning + clone) and follow-up
    # start-ups in different proportions, so the rule decides on follow-ups only; a launch interval wholly above the
    # tolerance is a flag. Logs without follow-ups (one session per task) decide on every hand-out, as before.
    up_f = welch_log_ratio([x["startup_s"] for x in A if x["startup_s"] and x["followup"]],
                           [x["startup_s"] for x in B if x["startup_s"] and x["followup"]], level)
    up_l = welch_log_ratio([x["startup_s"] for x in A if x["startup_s"] and not x["followup"]],
                           [x["startup_s"] for x in B if x["startup_s"] and not x["followup"]], level)
    slot_mode = any(x["followup"] for x in legs)
    measure = "followup" if slot_mode else "mixed"
    dec, flag = decide(up_f if slot_mode else up, code, tol)
    llo = up_l["ci"][0]
    launch_flag = bool(slot_mode and llo is not None and llo > tol)

    def med(xs):
        xs = [x for x in xs if x]
        return round(statistics.median(xs), 1) if xs else None
    phases = {ph: dict(tasks=len(L), startup_median_s=med([x["startup_s"] for x in L]),
                       coding_median_s=med([x["coding_s"] for x in L])) for ph, L in (("A", A), ("B", B))}
    try:
        act = activity_report(run_dir, skip_min=skip_min)
    except SystemExit as e:
        act = dict(error=str(e))
    rnd = lambda d: {k: (round(v, 3) if isinstance(v, float) else [round(x, 3) if x is not None else None for x in v]
                         if k == "ci" else v) for k, v in d.items()}
    return dict(run_dir=str(run_dir),
                rule=(f"abort rule 1 (PLAN-v6): start-up ratio (geometric means, many slots / one), Welch "
                      f"{level:.0%} interval on the log scale; STOP if wholly above {tol}, CLEAR if wholly below, "
                      "else INCONCLUSIVE; coding interval wholly above it is a flag"),
                decision=dec, coding_flag=flag, launch_flag=launch_flag, measure=measure, tolerance=tol, level=level,
                split_min=split_min, coding_cutoff_s=CODING_CUTOFF_S,
                excluded=excl, excluded_source=("operator worker_down" if auto else "--exclude"),
                startup=rnd(up), coding=rnd(code), phases=phases, activity=act,
                followup_tasks=sum(1 for x in legs if x["followup"]),
                startup_followup_only=rnd(up_f), startup_launch_only=rnd(up_l))


def format_report(r: dict) -> str:
    def iv(x):
        lo, hi = x["ci"]
        return "n/a" if x["ratio"] is None else f"{x['ratio']:.3f} (90% {lo:.3f}-{hi:.3f})"
    A, B = r["phases"]["A"], r["phases"]["B"]
    L = [f"Throttling (abort rule 1, PLAN-v6 / v7), {r['run_dir']}",
         f"  split at minute {r['split_min']:g}; excluded slots: {','.join(r['excluded']) or 'none'} "
         f"({r['excluded_source']})",
         f"  one slot  : {A['tasks']} tasks; start-up median {A['startup_median_s']} s, coding median "
         f"{A['coding_median_s']} s",
         f"  many slots: {B['tasks']} tasks; start-up median {B['startup_median_s']} s, coding median "
         f"{B['coding_median_s']} s",
         f"  start-up ratio, every hand-out {iv(r['startup'])}  n = {r['startup']['n_a']} / {r['startup']['n_b']}"
         + ("  (reported only)" if r.get("measure") == "followup" else ""),
         f"  coding ratio   {iv(r['coding'])}  n = {r['coding']['n_a']} / {r['coding']['n_b']}"
         + ("  FLAG: coding interval wholly above the tolerance (reported beside SCALE, not a stop)"
            if r["coding_flag"] else ""),
         ]
    if r.get("followup_tasks"):
        f, l = r["startup_followup_only"], r["startup_launch_only"]
        L.append(f"  one session per slot ({r['followup_tasks']} tasks handed out by follow-up): the rule reads follow-ups")
        L.append(f"  start-up ratio, follow-ups only {iv(f)}  n = {f['n_a']} / {f['n_b']}  <- rule 1")
        L.append(f"  start-up ratio, launches only   {iv(l)}  n = {l['n_a']} / {l['n_b']}"
                 + ("  FLAG: launch interval wholly above the tolerance (reported beside SCALE, not a stop)"
                    if r.get("launch_flag") else ""))
    L.append(f"  coding legs only for hand-outs >= {r.get('coding_cutoff_s', 300) / 60:g} min before window end")
    L.append(f"  tolerance {r['tolerance']} on the {'follow-up' if r.get('measure') == 'followup' else 'every-hand-out'} "
             f"start-up: {r['decision']}")
    act = r.get("activity") or {}
    if "phases" in act:
        L.append(format_activity(act))
    elif act.get("error"):
        L.append(f"Activity (the PLAN-v4 measure): not computed ({act['error']})")
    return "\n".join(L)
