"""PLAN-v6 section 6: K parallel reviewers (r1..rK) on one FIFO review queue.

No change is reviewed twice at once, no task is in two reviews at once, every reviewer event is tagged,
busy/idle pair per reviewer, at most K reviews are open, idle() is false while any reviewer works, each call
gets its own checkout and no pytest cache, merges stay serial and follow the order reviews finish, and a dry
run's log passes both validators."""
import json
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest
from conftest import ScriptedReviewer, count

from harness import config as config_mod
from harness.dryrun import dry_run
from harness.events import read_events
from harness.review import CommandReviewer, ReviewPacket
from harness.schema import REVIEWER_FIELD_TYPES, validate_events, validate_run_json
from harness.tasks import Task

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE.parent / "analysis"))
try:            # the analysis validator needs numpy/scipy (analysis/common.py); the harness venv may lack them
    import validate_schema as analysis_validator  # noqa: E402
except ImportError:
    analysis_validator = None


ANALYSIS_PY = Path("/Users/tom/git/carnot/analysis/aidev/.venv/bin/python")   # has numpy/scipy (test_calibrate)


def _analysis_errors(path):
    """analysis/validate_schema.py on ``path``: in-process if importable, else with the analysis interpreter."""
    if analysis_validator is not None:
        return analysis_validator.validate_path(path)[0]
    if not ANALYSIS_PY.exists():
        return []
    p = subprocess.run([str(ANALYSIS_PY), str(HERE.parent / "analysis" / "validate_schema.py"), str(path)],
                       capture_output=True, text=True)
    return [] if p.returncode == 0 and ": OK (" in p.stdout else [p.stdout + p.stderr]

TASKS = ["004", "005", "006", "008"]


def _per_reviewer_checks(evs, k):
    rev = [e for e in evs if e["type"] in REVIEWER_FIELD_TYPES]
    assert rev and all(e.get("reviewer") in {f"r{i}" for i in range(1, k + 1)} for e in rev)
    for rid in {e["reviewer"] for e in rev}:
        seq = [e["type"] for e in rev if e["reviewer"] == rid and e["type"] in ("reviewer_busy", "reviewer_idle")]
        assert seq[::2] == ["reviewer_busy"] * len(seq[::2]) and seq[1::2] == ["reviewer_idle"] * len(seq[1::2])
    open_, tasks_open, most = {}, {}, 0
    for e in rev:
        if e["type"] == "review_start":
            assert e["reviewer"] not in open_
            assert e["task"] not in tasks_open.values()
            open_[e["reviewer"]] = e["head"]
            tasks_open[e["reviewer"]] = e["task"]
            most = max(most, len(open_))
        elif e["type"] in ("review_end", "review_error"):
            assert open_.pop(e["reviewer"]) == e["head"]
            tasks_open.pop(e["reviewer"])
    assert most <= k
    return most


@pytest.mark.parametrize("k", [2, 3])
def test_k_reviewers_review_everything_once_and_merge_serially(make_env, k):
    rev = ScriptedReviewer(delay_s=2.0)   # long enough that the next change is prepared while one is in review
    env = make_env(reviewer=rev, reviewer_cfg={"parallel": k})
    ws = {f"s{i + 1}": env.session(t, f"s{i + 1}") for i, t in enumerate(TASKS)}
    heads = {}
    for i, t in enumerate(TASKS):
        heads[t] = ws[f"s{i + 1}"].submit()
        env.orch.poll()
    env.start()
    evs = env.wait_for(lambda evs: count(evs, "merge") == len(TASKS) and count(evs, "queue_idle") >= 1, timeout=60)
    # every change reviewed exactly once, by reviewers r1..rK, with up to K at once (and really in parallel)
    ends = [e for e in evs if e["type"] == "review_end"]
    assert sorted((e["task"], e["head"]) for e in ends) == sorted(heads.items())
    most = _per_reviewer_checks(evs, k)
    # really in parallel (how many overlap depends on how fast the prep thread readies changes on a loaded machine)
    assert 2 <= most <= k and rev.max_active == most
    assert 2 <= len({e["reviewer"] for e in ends}) <= k
    # FIFO hand-out: review_start order is submit order; queue_depth excludes changes under review
    starts = [e for e in evs if e["type"] == "review_start"]
    assert [e["task"] for e in starts] == TASKS
    assert [e["queue_depth"] for e in starts][:k] == [len(TASKS) - 1 - i for i in range(k)]
    # merge queue: serial (one busy stretch per backlog, validated) and in the order the approvals finished
    approvals = [e["task"] for e in ends if e["verdict"] == "approve"]
    assert [e["task"] for e in evs if e["type"] == "hidden_pre"] == approvals
    assert [e["task"] for e in evs if e["type"] == "merge"] == approvals
    for e in evs:
        if e["type"] in ("queue_busy", "queue_idle"):
            assert "reviewer" not in e
    assert validate_events(env.log.path, n_reviewers=k) == []
    assert _analysis_errors(env.log.path) == []


def test_a_task_is_never_in_two_reviews(make_env):
    """With K = 3 and idle reviewers, a newer head of a task under review waits for that review to end."""
    rev = ScriptedReviewer(delay_s=1.5)
    env = make_env(reviewer=rev, reviewer_cfg={"parallel": 3})
    w = env.session("004", "s1")
    env.start()
    first = w.submit()
    env.wait_for(lambda evs: count(evs, "review_start", task="004") == 1)
    assert not env.orch.idle()                     # a reviewer is working
    env.orch.sessions["004"].status = "working"    # the session pushes again before its review ends
    second = w.submit()
    evs = env.wait_for(lambda evs: count(evs, "review_end", task="004") == 2, timeout=60)
    r = [(e["type"], e["head"]) for e in evs if e["type"] in ("review_start", "review_end") and e["task"] == "004"]
    assert r == [("review_start", first), ("review_end", first), ("review_start", second), ("review_end", second)]
    assert rev.max_active == 1
    _per_reviewer_checks(evs, 3)


def test_unprepared_change_is_skipped_not_blocked_on(make_env):
    env = make_env(reviewer=ScriptedReviewer(delay_s=0.1), reviewer_cfg={"parallel": 2})
    ws = {s: env.session(t, s) for s, t in (("s1", "004"), ("s2", "005"))}
    for s in ("s1", "s2"):
        ws[s].submit()
        env.orch.poll()
    env.orch.phase = "window"
    with env.orch.lock:
        env.orch.prep_q.clear()
        env.orch.review_q[1].prepared.set()        # only the second is prepared
        ch = env.orch._take_review("r1")
        assert ch.task == "005" and env.orch.reviewing == {"r1": ch}
        assert env.orch._take_review("r2") is None # the first is still in prep
        assert [c.task for c in env.orch.review_q] == ["004"]
        assert not env.orch.idle()


def test_failed_review_goes_back_for_any_reviewer(make_env):
    rev = ScriptedReviewer({"004": ["error", "error", "approve"]})
    env = make_env(reviewer=rev, reviewer_cfg={"parallel": 2, "retry_backoff_s": 400})
    w = env.session("004", "s1")
    env.start()
    w.submit()
    evs = env.wait_for(lambda evs: count(evs, "review_end") == 1, timeout=60)
    errs = [e["reviewer"] for e in evs if e["type"] == "review_error"]
    end = next(e for e in evs if e["type"] == "review_end")
    assert len(errs) == 2 and len(set(errs)) == 1 and end["reviewer"] != errs[0]   # the other reviewer took it
    _per_reviewer_checks(evs, 2)


def test_downtime_voids_on_combined_capacity(make_env):
    env = make_env(reviewer_cfg={"parallel": 2, "max_downtime_min": 10})
    o = env.orch
    o.downtime_s = 15 * 60          # one of two reviewers down 15 min: 7.5 min of capacity
    o._check_downtime()
    assert not o.void_reasons
    o.downtime_s = 21 * 60
    o._check_downtime()
    assert o.void_reasons and "summed over 2 reviewers" in o.void_reasons[0]


def test_each_call_gets_its_own_checkout_and_no_pytest_cache(tmp_path):
    fake = tmp_path / "fake_reviewer.py"
    fake.write_text(
        "import sys, os, json, time\n"
        "time.sleep(0.5)\n"
        "open(sys.argv[1], 'a').write(json.dumps({'cwd': os.getcwd(), "
        "'addopts': os.environ.get('PYTEST_ADDOPTS', ''), 'nobyte': os.environ.get('PYTHONDONTWRITEBYTECODE')}) + '\\n')\n"
        "print(json.dumps({'result': 'APPROVE\\nok', 'usage': {}}))\n")
    calls = tmp_path / "calls.jsonl"
    cfg = config_mod.from_dict({"reviewer": {"job": "diff", "prompt_on_stdin": False,
                                             "command": [sys.executable, str(fake), str(calls)]}})
    cfg.reviewer.prompt_template = str(HERE / "prompts" / "reviewer-diff.md")
    r = CommandReviewer(cfg)

    def one(rid):
        pk = ReviewPacket(Task("001", "t", "x", ["a"]), "abc", "def", "diff\n+1\n", ["x"], True, "ok",
                          reviewer_id=rid)
        assert r.review(pk).verdict == "approve"
    th = [threading.Thread(target=one, args=(f"r{i}",)) for i in (1, 2, 3)]
    t0 = time.monotonic()
    for t in th:
        t.start()
    for t in th:
        t.join()
    rows = [json.loads(x) for x in calls.read_text().splitlines()]
    assert len(rows) == 3 and time.monotonic() - t0 < 1.4            # ran at once
    dirs = {Path(x["cwd"]).name for x in rows}
    assert len(dirs) == 3 and {d.split("-")[2] for d in dirs} == {"r1", "r2", "r3"}
    assert all("-p no:cacheprovider" in x["addopts"] and x["nobyte"] == "1" for x in rows)
    assert not any(Path(x["cwd"]).exists() for x in rows)          # temporary


def test_dry_run_with_three_reviewers(tmp_path):
    cfg = config_mod.load(HERE / "config.dryrun.toml")
    cfg.run.output_dir = str(tmp_path / "runs")
    cfg.run.window_min = 45
    cfg.run.warmup_min = 5
    cfg.run.n_workers = 4
    cfg.reviewer.parallel = 3
    cfg.sim.time_scale = 400
    cfg.sim.n_toy_tasks = 12
    run_dir, summary, errs = dry_run(cfg, "k3", seed=5)
    assert not errs, errs
    run = json.loads((run_dir / "run.json").read_text())
    assert run["n_reviewers"] == 3 and validate_run_json(run_dir / "run.json") == []
    ev = read_events(run_dir / "events.jsonl")
    assert any(e["type"] == "note" and e["text"] == "reviewers n=3 ids=r1,r2,r3" for e in ev)
    _per_reviewer_checks(ev, 3)
    assert _analysis_errors(run_dir) == []


def test_open_review_at_grace_end_is_noted_per_reviewer(tmp_path):
    cfg = config_mod.load(HERE / "config.dryrun.toml")
    cfg.run.output_dir = str(tmp_path / "runs")
    cfg.run.window_min = 20
    cfg.run.warmup_min = 0
    cfg.run.grace_min = 1
    cfg.run.end_grace_early_when_idle = False
    cfg.run.n_workers = 4
    cfg.reviewer.parallel = 2
    cfg.sim.time_scale = 300
    cfg.sim.n_toy_tasks = 10
    cfg.sim.review_mean_s = 600          # reviews outlast the one-minute grace
    cfg.sim.p_review_crash = 0.0
    run_dir, _, errs = dry_run(cfg, "grace", seed=2)
    assert not [e for e in errs if "thread error" in e], errs
    ev = read_events(run_dir / "events.jsonl")
    notes = [e["text"] for e in ev if e["type"] == "note" and e["text"].startswith("review_open_at_grace_end")]
    assert notes and all(" reviewer=r" in n for n in notes)
