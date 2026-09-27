"""Hidden tests that rely on the sandbox's own tests/conftest.py fixtures and on either
import style for a shared helper module (`from helpers import` / `from tests.helpers import`)
must run under --import-mode=importlib when hidden_subdir sits under tests/ and
hidden_pythonpath puts tests/ and the root on PYTHONPATH."""
import subprocess
import sys
from pathlib import Path

from harness import config as config_mod
from harness.gitops import Repo
from harness.tasks import TestRunner as Runner

FILES = {
    "pyproject.toml": '[tool.pytest.ini_options]\ntestpaths = ["tests"]\npythonpath = ["."]\n',
    "pkg/__init__.py": "VALUE = 3\n",
    "tests/helpers.py": "START = 3\n",
    "tests/conftest.py": ("import pytest\nfrom helpers import START\n\n"
                          "@pytest.fixture\ndef start():\n    return START\n"),
    "tests/test_visible.py": "from helpers import START\n\ndef test_v():\n    assert START == 3\n",
}
HIDDEN = {
    "A": "from helpers import START\nfrom pkg import VALUE\n\ndef test_a(start):\n    assert start == START == VALUE\n",
    "B": "from tests.helpers import START\n\ndef test_b(start):\n    assert start == START\n",
}


def _repo(tmp: Path) -> tuple[Repo, str]:
    root = tmp / "repo"
    for rel, text in FILES.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text)
    subprocess.run(["git", "init", "-q", "-b", "main", str(root)], check=True)
    r = Repo(root)
    r.run("add", "-A")
    r.run("commit", "-q", "-m", "base")
    return r, r.sha("HEAD")


def _cfg(tmp: Path, **tests) -> config_mod.Config:
    hidden = tmp / "hidden"
    for tid, text in HIDDEN.items():
        (hidden / tid).mkdir(parents=True)
        (hidden / tid / f"test_hidden_{tid}.py").write_text(text)
    return config_mod.from_dict({"tasks": {"hidden_tests_dir": str(hidden)},
                                 "tests": {"python": sys.executable, **tests}}, tmp)


def test_hidden_tests_see_conftest_and_both_helper_imports(tmp_path):
    repo, sha = _repo(tmp_path)
    cfg = _cfg(tmp_path, hidden_subdir="tests/_hidden_acceptance", hidden_pythonpath=["tests", "."])
    timing = {}
    vis, hid = Runner(cfg, repo).run(sha, visible=True, hidden_tasks=["A", "B"], timing=timing)
    assert vis.passed, vis.output
    assert hid.passed, hid.output
    assert timing["n_hidden_tasks"] == 2 and timing["hidden_s"] > 0 and timing["visible_s"] > 0


def test_default_layout_misses_the_conftest(tmp_path):
    """Documents why the options exist: the default (hidden tests beside tests/, no extra
    PYTHONPATH) cannot see the fixtures."""
    repo, sha = _repo(tmp_path)
    _, hid = Runner(_cfg(tmp_path), repo).run(sha, visible=False, hidden_tasks=["A"])
    assert not hid.passed
