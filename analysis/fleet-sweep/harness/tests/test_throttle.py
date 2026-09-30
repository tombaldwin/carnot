"""Abort rule 1. PLAN-v6 (the rule): start-up and coding, ratio of geometric means (many slots / one), Welch 90%
interval on the log scale, tolerance 1.25 -> STOP / CLEAR / INCONCLUSIVE. PLAN-v4 (reported only): activity
(session launches + READY) per slot-minute, one-slot phase against twelve-slot phase, threshold 0.8."""
import math
import random
import datetime as dt
import json

import pytest

from harness import cli
from harness.throttle import activity_report, t_ppf, task_legs, throttle_report, welch_log_ratio

W0 = dt.datetime(2026, 10, 1, 9, 0, tzinfo=dt.timezone.utc)


def _iso(m):
    t = W0 + dt.timedelta(minutes=m)
    return t.strftime("%Y-%m-%dT%H:%M:%S.") + f"{t.microsecond // 1000:03d}Z"


def _t1_log(tmp_path, every_1=5.0, every_12=5.0, usage=None, down=None, sessions=True):
    """s1 alone from minute 0, s2..s12 from minute 30; each slot submits a task every `every` minutes
    (its session launched 3 min before each READY; sessions=False: the old claim-based log)."""
    ev = [dict(t=_iso(0), type="note", text="window_start")]
    starts = {"s1": 0.0, **{f"s{i}": 30.0 + 0.2 * i for i in range(2, 13)}}
    k = 0
    for w, s in starts.items():
        ev.append(dict(t=_iso(s), type="worker_start", worker=w, session_id=None))
        m = s + 1
        while m < 60:
            every = every_1 if m < 30 else every_12
            m += every
            if m >= 60:
                break
            k += 1
            if sessions:
                ev.append(dict(t=_iso(m - 3), type="slot_busy", slot=w))
                ev.append(dict(t=_iso(m - 3), type="session_launch", slot=w, task=f"T{k:03d}", session_id=f"x{k}",
                               attempt_no=1))
            else:
                ev.append(dict(t=_iso(m - 3), type="claim", worker=w, task=f"T{k:03d}", branch=f"claude/task-T{k:03d}"))
            ev.append(dict(t=_iso(m), type="submit", worker=w, task=f"T{k:03d}", branch="b", head=f"h{k}",
                           attempt_no=1, lines_changed=5, files=["a.py"], k=0, m=0))
    for w, m, tok in usage or []:
        ev.append(dict(t=_iso(m), type="usage", worker=w, tokens_in=tok, tokens_out=0, cost_usd_est=None))
    for w, a, b in down or []:
        ev.append(dict(t=_iso(a), type="worker_down", worker=w, reason="x"))
        ev.append(dict(t=_iso(b), type="worker_restart", worker=w, reason="x"))
    ev.append(dict(t=_iso(31), type="note", text="plan_usage pct=41"))
    ev.append(dict(t=_iso(60), type="note", text="window_end"))
    ev.sort(key=lambda e: e["t"])
    d = tmp_path / "T1"
    d.mkdir()
    (d / "events.jsonl").write_text("".join(json.dumps(e) + "\n" for e in ev))
    (d / "run.json").write_text(json.dumps(dict(run_id="T1", kind="trial", n_workers=12, window_start=_iso(0),
                                                window_end=_iso(60))))
    return d


def test_same_pace_is_ok(tmp_path):
    r = activity_report(_t1_log(tmp_path))
    assert r["measure"] == "activity" and r["verdict"] == "OK"
    assert r["phases"]["A"]["slots"] == 1 and r["phases"]["B"]["slots"] == 12
    assert 0.8 <= r["ratio"] <= 1.25
    assert r["claim_to_ready_ratio"] == pytest.approx(1.0)
    assert [x["text"] for x in r["operator_readings"]] == ["plan_usage pct=41"]
    A = r["phases"]["A"]
    assert A["launches"] > 0 and A["activity"] == A["launches"] + A["submits"]     # a launch per task


def test_old_claim_log_still_reads(tmp_path):
    r = activity_report(_t1_log(tmp_path, sessions=False))
    assert r["verdict"] == "OK" and r["phases"]["A"]["launches"] == 0


def test_half_pace_at_twelve_is_throttled(tmp_path):
    r = activity_report(_t1_log(tmp_path, every_12=10.0))
    assert r["verdict"] == "THROTTLED" and r["ratio"] < 0.8
    lo, hi = r["activity_ratio_ci95_approx"]
    assert lo < r["activity_ratio"] < hi


def test_down_time_is_not_exposure(tmp_path):
    base = activity_report(_t1_log(tmp_path))
    (tmp_path / "T1").rename(tmp_path / "T1-base")
    r = activity_report(_t1_log(tmp_path, down=[("s5", 40, 50)]))
    assert r["phases"]["B"]["slot_minutes"] == pytest.approx(base["phases"]["B"]["slot_minutes"] - 10)


def test_token_source_replaces_activity(tmp_path):
    usage = [("s1", 20, 1000)] + [(f"s{i}", 50, 300) for i in range(1, 13)]
    r = activity_report(_t1_log(tmp_path, usage=usage))
    assert r["measure"] == "tokens" and r["token_ratio"] is not None
    assert r["verdict"] == ("THROTTLED" if r["token_ratio"] < 0.8 else "OK")


def test_needs_two_phases(tmp_path):
    d = tmp_path / "one"
    d.mkdir()
    (d / "events.jsonl").write_text(json.dumps(dict(t=_iso(0), type="worker_start", worker="s1", session_id=None)) + "\n")
    (d / "run.json").write_text(json.dumps(dict(window_start=_iso(0), window_end=_iso(60))))
    with pytest.raises(SystemExit):
        activity_report(d)
    with pytest.raises(SystemExit):
        throttle_report(d)


def _rule_log(tmp_path, n_a=21, n_b=100, up_a=70.0, up_b=70.0, code_a=90.0, code_b=90.0, sigma=0.2, seed=1,
              split=90, window=120, slow_slot=None, lead_notes=True, name="T1b"):
    """A T1b-style log: s1 alone until `split`, then s1..s12. Per task: session_launch at the re-arm, a
    launch_detail note with run_once_at 30 s later, claim after start-up (lognormal around up_*), first READY after
    coding (around code_*). `slow_slot`: that slot's tasks start 5x slower and it has an operator worker_down."""
    rng = random.Random(seed)
    ev = [dict(t=_iso(0), type="note", text="window_start"),
          dict(t=_iso(0), type="note", text="start_schedule minute=0 slots=s1"),
          dict(t=_iso(split), type="note", text="start_schedule minute=%d slots=s2,s3,s4,s5,s6,s7,s8,s9,s10,s11,s12" % split)]
    k = 0

    def task(slot, m, up, code):
        nonlocal k
        k += 1
        tid = f"T{k:03d}"
        f = 5.0 if slot == slow_slot else 1.0
        u = up * f * math.exp(rng.gauss(0, sigma))
        c = code * math.exp(rng.gauss(0, sigma))
        ev.append(dict(t=_iso(m), type="slot_busy", slot=slot))
        ev.append(dict(t=_iso(m), type="session_launch", slot=slot, task=tid, session_id=None, attempt_no=1))
        if lead_notes:
            ev.append(dict(t=_iso(m + 0.01), type="note", text=f"launch_detail slot={slot} task={tid} detached_by=routine "
                           f"returncode=0 session_id_seen=false trigger=trig_x run_once_at={_iso(m + 0.5)[:19]}Z"))
        t_claim = m + (0.5 if lead_notes else 0) + u / 60
        ev.append(dict(t=_iso(t_claim), type="claim", worker=slot, task=tid, branch=f"claude/task-{tid}"))
        ev.append(dict(t=_iso(t_claim + c / 60), type="submit", worker=slot, task=tid, branch="b", head=f"h{k}",
                       attempt_no=1, lines_changed=5, files=["a.py"], k=0, m=0))
        ev.append(dict(t=_iso(t_claim + c / 60), type="slot_idle", slot=slot))
    ev.append(dict(t=_iso(0), type="worker_start", worker="s1", session_id=None))
    for i in range(n_a):
        task("s1", 1 + i * (split - 5) / n_a, up_a, code_a)
    for w in range(2, 13):
        ev.append(dict(t=_iso(split), type="worker_start", worker=f"s{w}", session_id=None))
    slots = [f"s{w}" for w in range(1, 13)]
    for i in range(n_b):
        task(slots[i % 12], split + 0.5 + (i // 12) * (window - split - 8) / max(1, n_b // 12), up_b, code_b)
    if slow_slot:
        ev.append(dict(t=_iso(split + 20), type="worker_down", worker=slow_slot, reason="operator: CLI replaced"))
    ev.append(dict(t=_iso(window), type="note", text="window_end"))
    ev.sort(key=lambda e: e["t"])
    d = tmp_path / name
    d.mkdir()
    (d / "events.jsonl").write_text("".join(json.dumps(e) + "\n" for e in ev))
    (d / "run.json").write_text(json.dumps(dict(run_id=name, kind="trial", n_workers=12, window_start=_iso(0),
                                                window_end=_iso(window))))
    return d


def test_t_quantile_and_welch():
    assert t_ppf(0.95, 10) == pytest.approx(1.8125, abs=1e-3)
    assert t_ppf(0.975, 5) == pytest.approx(2.5706, abs=1e-3)
    assert t_ppf(0.95, 1e6) == pytest.approx(1.6449, abs=1e-3)
    w = welch_log_ratio([1.0, 2.0, 4.0], [2.0, 4.0, 8.0])
    assert w["ratio"] == pytest.approx(2.0) and w["ci"][0] < 2.0 < w["ci"][1]
    assert welch_log_ratio([1.0], [2.0, 3.0])["ratio"] is None


def test_rule1_same_speed_is_clear(tmp_path):
    r = throttle_report(_rule_log(tmp_path))
    assert r["split_min"] == 90 and r["excluded"] == []
    assert r["decision"] == "CLEAR" and not r["coding_flag"]
    assert r["startup"]["n_a"] == 21 and r["startup"]["n_b"] == 100
    assert 0.85 < r["startup"]["ratio"] < 1.15
    assert "activity_ratio" in r["activity"]            # the old measure is still reported


def test_rule1_slow_startup_at_twelve_stops(tmp_path):
    r = throttle_report(_rule_log(tmp_path, up_b=140.0))
    assert r["decision"] == "STOP" and r["startup"]["ci"][0] > 1.25


def test_rule1_slow_coding_is_a_flag_not_a_stop(tmp_path):
    r = throttle_report(_rule_log(tmp_path, code_b=180.0))
    assert r["decision"] == "CLEAR" and r["coding_flag"] is True


def test_rule1_t1_like_numbers_are_inconclusive(tmp_path):
    # T1: 9 tasks in a 30-min one-slot phase, 73 at twelve, start-up 1.13x, coding 1.16x (PLAN-v6 s5.4)
    r = throttle_report(_rule_log(tmp_path, n_a=9, n_b=73, up_a=67.0, up_b=76.0, code_a=80.0, code_b=93.0,
                                  sigma=0.3, split=30, window=60, seed=3))
    assert r["decision"] == "INCONCLUSIVE"
    lo, hi = r["startup"]["ci"]
    assert lo < 1.25 < hi or hi > 1.25 > lo
    assert not r["coding_flag"]


def test_rule1_start_up_runs_from_run_once_at(tmp_path):
    # the 30-s re-arm lead is split off: start-up is claim - run_once_at, so a lead-only log reads the same
    a = throttle_report(_rule_log(tmp_path, name="lead"))
    b = throttle_report(_rule_log(tmp_path, lead_notes=False, name="nolead"))
    assert a["phases"]["A"]["startup_median_s"] == pytest.approx(b["phases"]["A"]["startup_median_s"], rel=0.02)


def test_rule1_excludes_operator_down_slots(tmp_path):
    d = _rule_log(tmp_path, slow_slot="s3")
    r = throttle_report(d)
    assert r["excluded"] == ["s3"] and r["excluded_source"] == "operator worker_down"
    assert r["decision"] == "CLEAR"
    kept = throttle_report(d, exclude=[])
    assert kept["excluded"] == [] and kept["startup"]["ratio"] > r["startup"]["ratio"]
    assert throttle_report(d, exclude=["s3", "s4"])["startup"]["n_b"] < r["startup"]["n_b"]


def test_cli(tmp_path, capsys):
    d = _rule_log(tmp_path, up_b=140.0)
    assert cli.main(["throttle", str(d), "--json", str(tmp_path / "r.json")]) == 0
    out = capsys.readouterr().out
    assert "tolerance 1.25 on the every-hand-out start-up: STOP" in out and "Activity (the PLAN-v4 measure" in out
    r = json.loads((tmp_path / "r.json").read_text())
    assert r["decision"] == "STOP" and r["excluded"] == []
    assert cli.main(["throttle", str(d), "--exclude", "s2,s3", "--split-min", "90"]) == 0
    assert "excluded slots: s2,s3 (--exclude)" in capsys.readouterr().out
    assert cli.main(["throttle", str(_t1_log(tmp_path, every_12=10.0))]) == 0     # old-style log still reads
    assert "THROTTLED under the retired 0.8 rule" in capsys.readouterr().out


def _slot_log(tmp_path, up_follow_b=15.0, up_launch_b=29.0, code_b=80.0, n_a=10, per_slot_b=4, seed=2, late_slow=False):
    """A T1b-style slot-mode log (PLAN-v7): s1 alone for 30 min, then s1..s12 for 15; every 4th task of a slot is a
    session_launch (start-up ~29 s), the rest session_message kind=task (~15 s)."""
    rng = random.Random(seed)
    ev = [dict(t=_iso(0), type="note", text="window_start"),
          dict(t=_iso(0), type="note", text="start_schedule minute=0 slots=s1"),
          dict(t=_iso(30), type="note", text="start_schedule minute=30 slots=s2,s3,s4,s5,s6,s7,s8,s9,s10,s11,s12")]
    k = [0]
    count = {}

    def task(slot, m, up_f, up_l, code):
        k[0] += 1
        tid = f"T{k[0]:03d}"
        n = count.get(slot, 0)
        count[slot] = n + 1
        launch = n % 4 == 0
        ev.append(dict(t=_iso(m), type="slot_busy", slot=slot))
        if launch:
            ev.append(dict(t=_iso(m), type="session_launch", slot=slot, task=tid, session_id=f"s-{slot}-{n}", attempt_no=1))
        else:
            ev.append(dict(t=_iso(m), type="session_message", slot=slot, task=tid, session_id=f"s-{slot}", kind="task"))
        u = (up_l if launch else up_f) * math.exp(rng.gauss(0, 0.2))
        c = code * math.exp(rng.gauss(0, 0.2))
        ev.append(dict(t=_iso(m + u / 60), type="claim", worker=slot, task=tid, branch=f"claude/task-{tid}"))
        ev.append(dict(t=_iso(m + (u + c) / 60), type="submit", worker=slot, task=tid, branch="b", head=f"h{k[0]}",
                       attempt_no=1, lines_changed=5, files=["a.py"], k=0, m=0))
        ev.append(dict(t=_iso(m + (u + c) / 60), type="slot_idle", slot=slot))
    ev.append(dict(t=_iso(0), type="worker_start", worker="s1", session_id=None))
    for i in range(n_a):
        task("s1", 0.5 + i * 29.0 / n_a, 15.0, 29.0, 80.0)
    for w in range(2, 13):
        ev.append(dict(t=_iso(30), type="worker_start", worker=f"s{w}", session_id=None))
    for j in range(per_slot_b):
        for w in range(1, 13):
            m = 30.5 + j * 3.2
            slow = late_slow and m > 39
            task(f"s{w}", m, up_follow_b, up_launch_b, code_b * (4.0 if slow else 1.0))
    ev.append(dict(t=_iso(45), type="note", text="window_end"))
    ev.sort(key=lambda e: e["t"])
    d = tmp_path / f"slot{seed}{up_follow_b}{up_launch_b}{code_b}{late_slow}"
    d.mkdir()
    (d / "events.jsonl").write_text("".join(json.dumps(e) + "\n" for e in ev))
    (d / "run.json").write_text(json.dumps(dict(run_id="T1b", kind="trial", n_workers=12, window_start=_iso(0),
                                                window_end=_iso(45))))
    return d


def test_slot_mode_decides_on_follow_ups(tmp_path):
    r = throttle_report(_slot_log(tmp_path))
    assert r["measure"] == "followup" and r["decision"] == "CLEAR" and not r["launch_flag"]
    assert r["startup_followup_only"]["n_a"] >= 6 and r["startup_followup_only"]["n_b"] >= 30
    r = throttle_report(_slot_log(tmp_path, up_follow_b=30.0))
    assert r["decision"] == "STOP"
    # launches 2x slower at twelve slots: a flag, not a stop
    r = throttle_report(_slot_log(tmp_path, up_launch_b=60.0, n_a=24))
    assert r["decision"] == "CLEAR" and r["launch_flag"] is True
    out = __import__("harness.throttle", fromlist=["format_report"]).format_report(r)
    assert "<- rule 1" in out and "FLAG: launch interval" in out


def test_coding_legs_cut_off_5_min_before_window_end(tmp_path):
    # tasks handed out in the last minutes are slow; without the cut-off only the fast ones would be counted
    d = _slot_log(tmp_path, late_slow=True)
    r = throttle_report(d)
    legs = task_legs(json.loads((d / "run.json").read_text()),
                     [json.loads(x) for x in (d / "events.jsonl").read_text().splitlines()])
    late = [x for x in legs if x["launch"] > 40 * 60]
    assert late and all(x["coding_s"] is None for x in late)
    assert r["coding_cutoff_s"] == 300
