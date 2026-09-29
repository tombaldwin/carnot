"""Verdict parsing, retry/downtime rules, the command reviewer (with a fake command), and the
guard that refuses a real run while templates are UNVERIFIED."""
import sys
import textwrap

import pytest
from conftest import ScriptedReviewer, count

from harness import config as config_mod
from harness.launchers import NotVerified, require_verified
from harness.review import CommandReviewer, ReviewError, ReviewPacket, parse_verdict
from harness.tasks import Task


@pytest.mark.parametrize("text,verdict,reason", [
    ("APPROVE\nLooks right.", "approve", "Looks right."),
    ("REQUEST_CHANGES\nMissing edge case.", "request_changes", "Missing edge case."),
    ("  **APPROVE**\n\nfine", "approve", "fine"),
    ("request changes: the function is wrong", "request_changes", "the function is wrong"),
    ("Request-Changes\nx", "request_changes", "x"),
    ("# Verdict: APPROVE\nok", "approve", "ok"),
    ("`REQUEST_CHANGES` - tests fail", "request_changes", "tests fail"),
    ("Let me look.\nREQUEST_CHANGES\nbad", "request_changes", "bad"),
    ("approved. meets criteria", "approve", "meets criteria"),
])
def test_parse_verdict(text, verdict, reason):
    assert parse_verdict(text) == (verdict, reason)


@pytest.mark.parametrize("text", ["", "   \n ", "I think this is mostly fine", "NOT APPROVE"])
def test_parse_verdict_rejects_ambiguous(text):
    with pytest.raises(ReviewError):
        parse_verdict(text)


def test_retry_once_then_success(make_env):
    rev = ScriptedReviewer({"001": ["error", "approve"]})
    env = make_env(reviewer=rev)
    w = env.session("001", "s1")
    env.start()
    w.submit()
    evs = env.wait_for(lambda evs: count(evs, "merge") == 1)
    seq = [e["type"] for e in evs if e["type"].startswith("review")]
    assert seq == ["reviewer_busy", "review_start", "review_error", "review_start", "review_end", "reviewer_idle"]


def test_persistent_failure_requeues_and_voids_window(make_env):
    rev = ScriptedReviewer({"001": ["error"] * 6 + ["approve"]})
    env = make_env(reviewer=rev, reviewer_cfg={"retry_backoff_s": 400, "max_downtime_min": 10})
    w = env.session("001", "s1")
    env.start()
    w.submit()
    evs = env.wait_for(lambda evs: count(evs, "review_end") == 1, timeout=60)
    assert count(evs, "review_error") == 6
    assert any(e["text"].startswith("VOID: reviewer downtime") for e in evs if e["type"] == "note")
    assert env.orch.void_reasons


def _packet():
    return ReviewPacket(Task("001", "Add slugify()", "do it", ["a", "b"]), "abc123", "def456",
                        "diff --git a/x b/x\n+1\n", ["x"], True, "1 passed")


def test_command_reviewer_with_fake_command(tmp_path):
    fake = tmp_path / "fake_reviewer.py"
    fake.write_text(textwrap.dedent("""\
        import sys, json
        prompt = open(sys.argv[2]).read()
        assert "Task 001" in prompt and "Acceptance criteria" in prompt and "+1" in prompt
        print(json.dumps({"result": "REQUEST_CHANGES\\nneeds work",
                          "usage": {"input_tokens": 100, "output_tokens": 7}}))
        """))
    cfg = config_mod.from_dict({"reviewer": {
        "job": "diff", "prompt_on_stdin": False,
        "command": [sys.executable, str(fake), "{model}", "{prompt_file}"],
        "output_format": "json", "prompt_template": "prompts/reviewer-diff.md"}},
        base_dir=tmp_path.parent)
    cfg.reviewer.prompt_template = str(__import__("pathlib").Path(__file__).parent.parent / "prompts/reviewer-diff.md")
    r = CommandReviewer(cfg).review(_packet())
    assert (r.verdict, r.reason, r.tokens_in, r.tokens_out) == ("request_changes", "needs work", 100, 7)


def test_command_reviewer_failure_is_review_error(tmp_path):
    cfg = config_mod.from_dict({"reviewer": {"job": "diff",
                                             "command": [sys.executable, "-c", "import sys; sys.exit(3)"]}})
    cfg.reviewer.prompt_template = str(__import__("pathlib").Path(__file__).parent.parent / "prompts/reviewer.md")
    with pytest.raises(ReviewError):
        CommandReviewer(cfg).review(_packet())


def test_real_run_refuses_unverified_templates():
    from pathlib import Path
    cfg = config_mod.load(Path(__file__).parent.parent / "config.t2.toml")
    cfg.reviewer.verified = False   # the shipped reviewer is verified; the guard must still refuse an unverified one
    cfg.launcher.verified = False   # likewise the launcher (routine path verified at T0c); command mode must refuse
    with pytest.raises(NotVerified):
        require_verified(cfg)
    cfg.reviewer.verified = True
    cfg.launcher.mode = "manual"
    require_verified(cfg)          # manual launcher needs no product flags
    cfg.launcher.mode = "command"
    with pytest.raises(NotVerified):
        require_verified(cfg)
