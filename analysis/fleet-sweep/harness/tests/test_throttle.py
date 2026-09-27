"""Abort rule 1 without a token source: activity (session launches + READY) per slot-minute, T1's
one-slot phase against its twelve-slot phase; threshold 0.8."""
import datetime as dt
import json

import pytest

from harness import cli
from harness.throttle import throttle_report

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
    r = throttle_report(_t1_log(tmp_path))
    assert r["measure"] == "activity" and r["verdict"] == "OK"
    assert r["phases"]["A"]["slots"] == 1 and r["phases"]["B"]["slots"] == 12
    assert 0.8 <= r["ratio"] <= 1.25
    assert r["claim_to_ready_ratio"] == pytest.approx(1.0)
    assert [x["text"] for x in r["operator_readings"]] == ["plan_usage pct=41"]
    A = r["phases"]["A"]
    assert A["launches"] > 0 and A["activity"] == A["launches"] + A["submits"]     # a launch per task


def test_old_claim_log_still_reads(tmp_path):
    r = throttle_report(_t1_log(tmp_path, sessions=False))
    assert r["verdict"] == "OK" and r["phases"]["A"]["launches"] == 0


def test_half_pace_at_twelve_is_throttled(tmp_path):
    r = throttle_report(_t1_log(tmp_path, every_12=10.0))
    assert r["verdict"] == "THROTTLED" and r["ratio"] < 0.8
    lo, hi = r["activity_ratio_ci95_approx"]
    assert lo < r["activity_ratio"] < hi


def test_down_time_is_not_exposure(tmp_path):
    base = throttle_report(_t1_log(tmp_path))
    (tmp_path / "T1").rename(tmp_path / "T1-base")
    r = throttle_report(_t1_log(tmp_path, down=[("s5", 40, 50)]))
    assert r["phases"]["B"]["slot_minutes"] == pytest.approx(base["phases"]["B"]["slot_minutes"] - 10)


def test_token_source_replaces_activity(tmp_path):
    usage = [("s1", 20, 1000)] + [(f"s{i}", 50, 300) for i in range(1, 13)]
    r = throttle_report(_t1_log(tmp_path, usage=usage))
    assert r["measure"] == "tokens" and r["token_ratio"] is not None
    assert r["verdict"] == ("THROTTLED" if r["token_ratio"] < 0.8 else "OK")


def test_needs_two_phases(tmp_path):
    d = tmp_path / "one"
    d.mkdir()
    (d / "events.jsonl").write_text(json.dumps(dict(t=_iso(0), type="worker_start", worker="s1", session_id=None)) + "\n")
    (d / "run.json").write_text(json.dumps(dict(window_start=_iso(0), window_end=_iso(60))))
    with pytest.raises(SystemExit):
        throttle_report(d)


def test_cli(tmp_path, capsys):
    d = _t1_log(tmp_path, every_12=10.0)
    assert cli.main(["throttle", str(d), "--json", str(tmp_path / "r.json")]) == 0
    assert "THROTTLED" in capsys.readouterr().out
    assert json.loads((tmp_path / "r.json").read_text())["verdict"] == "THROTTLED"
