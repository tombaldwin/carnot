"""Generate a tiny sandbox repo and toy tasks with hidden tests, for dry runs.

Layout written under ``dest``:
  remote.git/                 bare repo standing in for the GitHub remote; main + tag study2-base
  tasks/tasks.json            public task text and acceptance criteria
  tasks/hidden/<id>/          hidden acceptance tests (never enter the repo)
  tasks/reference/<id>.patch  reference solution (used by SimWorker only)

Ten task templates. With n_tasks > 10 the templates repeat under new module
names (task 011 is template 1 again as toylib/slugify_2.py, and so on).
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import textwrap
from pathlib import Path

from .gitops import init_bare

BASE_TAG = "study2-base"

SEED_FILES = {
    "README.md": "# toylib\n\nA toy sandbox for harness dry runs.\n",
    "pyproject.toml": textwrap.dedent("""\
        [project]
        name = "toylib"
        version = "0.0.1"

        [tool.pytest.ini_options]
        testpaths = ["tests"]
        """),
    "toylib/__init__.py": '"""toylib: small string and list helpers."""\n',
    "toylib/shared.py": textwrap.dedent('''\
        """Settings shared by several modules."""
        DEFAULT_WIDTH = 10
        SEPARATOR = ","
        '''),
    "toylib/core.py": textwrap.dedent('''\
        """Core helpers covered by the visible test suite."""


        def clamp(x, lo, hi):
            return max(lo, min(hi, x))


        def dedupe(items):
            seen, out = set(), []
            for x in items:
                if x not in seen:
                    seen.add(x)
                    out.append(x)
            return out
        '''),
    "tests/test_core.py": textwrap.dedent('''\
        from toylib.core import clamp, dedupe


        def test_clamp():
            assert clamp(5, 0, 3) == 3
            assert clamp(-1, 0, 3) == 0
            assert clamp(2, 0, 3) == 2


        def test_dedupe():
            assert dedupe([1, 2, 1, 3, 2]) == [1, 2, 3]
        '''),
    ".claude/settings.json": '{\n  "permissions": {"allow": ["Bash(git *)", "Bash(python -m pytest*)"]}\n}\n',
}

# (module, title, text, acceptance, implementation, hidden test body)
TEMPLATES = [
    ("slugify", "Add slugify()",
     "Add toylib/{mod}.py with slugify(s): lowercase, runs of non-alphanumerics become one '-', no leading or trailing '-'.",
     ["slugify('Hello, World!') == 'hello-world'", "slugify('  a  b ') == 'a-b'", "slugify('') == ''"],
     '''import re


def slugify(s):
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")
''',
     '''def test_slugify():
    assert slugify("Hello, World!") == "hello-world"
    assert slugify("  a  b ") == "a-b"
    assert slugify("") == ""
'''),
    ("pad", "Add pad_left()",
     "Add toylib/{mod}.py with pad_left(s, width=None): right-justify s with spaces to width; width defaults to toylib.shared.DEFAULT_WIDTH, read at call time.",
     ["pad_left('ab') has length DEFAULT_WIDTH", "pad_left('ab', 4) == '  ab'", "longer strings are unchanged"],
     '''from toylib import shared


def pad_left(s, width=None):
    w = shared.DEFAULT_WIDTH if width is None else width
    return s.rjust(w)
''',
     '''def test_pad_left():
    assert pad_left("ab") == "        ab"
    assert pad_left("ab", 4) == "  ab"
    assert pad_left("abcdef", 3) == "abcdef"
'''),
    ("center", "Add center_text()",
     "Add toylib/{mod}.py with center_text(s): centre s in toylib.shared.DEFAULT_WIDTH columns using '*' as fill.",
     ["center_text('ab') == '****ab****' with the default width", "result length is DEFAULT_WIDTH for short input"],
     '''from toylib import shared


def center_text(s):
    return s.center(shared.DEFAULT_WIDTH, "*")
''',
     '''def test_center_text():
    assert center_text("ab") == "****ab****"
    assert len(center_text("x")) == 10
'''),
    ("palindrome", "Add is_palindrome()",
     "Add toylib/{mod}.py with is_palindrome(s): True if s reads the same backwards, ignoring case and non-alphanumerics.",
     ["'A man, a plan, a canal: Panama' is a palindrome", "'abc' is not", "'' is"],
     '''def is_palindrome(s):
    t = [c.lower() for c in s if c.isalnum()]
    return t == t[::-1]
''',
     '''def test_is_palindrome():
    assert is_palindrome("A man, a plan, a canal: Panama")
    assert not is_palindrome("abc")
    assert is_palindrome("")
'''),
    ("wordcount", "Add word_count()",
     "Add toylib/{mod}.py with word_count(s): dict of lowercase word -> count, words split on whitespace.",
     ["word_count('a b A') == {'a': 2, 'b': 1}", "word_count('') == {}"],
     '''def word_count(s):
    out = {}
    for w in s.lower().split():
        out[w] = out.get(w, 0) + 1
    return out
''',
     '''def test_word_count():
    assert word_count("a b A") == {"a": 2, "b": 1}
    assert word_count("") == {}
'''),
    ("chunk", "Add chunk()",
     "Add toylib/{mod}.py with chunk(items, n): split a list into lists of length n (last may be shorter); n < 1 raises ValueError.",
     ["chunk([1,2,3,4,5], 2) == [[1,2],[3,4],[5]]", "chunk([], 3) == []", "chunk([1], 0) raises ValueError"],
     '''def chunk(items, n):
    if n < 1:
        raise ValueError("n must be >= 1")
    return [items[i:i + n] for i in range(0, len(items), n)]
''',
     '''import pytest


def test_chunk():
    assert chunk([1, 2, 3, 4, 5], 2) == [[1, 2], [3, 4], [5]]
    assert chunk([], 3) == []
    with pytest.raises(ValueError):
        chunk([1], 0)
'''),
    ("joinfields", "Add join_fields()",
     "Add toylib/{mod}.py with join_fields(items): str() each item and join with toylib.shared.SEPARATOR.",
     ["join_fields([1, 'a', 2.5]) == '1,a,2.5'", "join_fields([]) == ''"],
     '''from toylib import shared


def join_fields(items):
    return shared.SEPARATOR.join(str(x) for x in items)
''',
     '''def test_join_fields():
    assert join_fields([1, "a", 2.5]) == "1,a,2.5"
    assert join_fields([]) == ""
'''),
    ("roman", "Add to_roman()",
     "Add toylib/{mod}.py with to_roman(n) for 1 <= n <= 3999; other n raise ValueError.",
     ["to_roman(1994) == 'MCMXCIV'", "to_roman(4) == 'IV'", "to_roman(0) raises ValueError"],
     '''_R = [(1000, "M"), (900, "CM"), (500, "D"), (400, "CD"), (100, "C"), (90, "XC"),
      (50, "L"), (40, "XL"), (10, "X"), (9, "IX"), (5, "V"), (4, "IV"), (1, "I")]


def to_roman(n):
    if not 1 <= n <= 3999:
        raise ValueError(n)
    out = []
    for v, s in _R:
        while n >= v:
            out.append(s)
            n -= v
    return "".join(out)
''',
     '''import pytest


def test_to_roman():
    assert to_roman(1994) == "MCMXCIV"
    assert to_roman(4) == "IV"
    with pytest.raises(ValueError):
        to_roman(0)
'''),
    ("flatten", "Add flatten()",
     "Add toylib/{mod}.py with flatten(x): flatten arbitrarily nested lists and tuples into one list.",
     ["flatten([1, [2, (3, [4])], 5]) == [1, 2, 3, 4, 5]", "flatten([]) == []"],
     '''def flatten(x):
    out = []
    for i in x:
        if isinstance(i, (list, tuple)):
            out.extend(flatten(i))
        else:
            out.append(i)
    return out
''',
     '''def test_flatten():
    assert flatten([1, [2, (3, [4])], 5]) == [1, 2, 3, 4, 5]
    assert flatten([]) == []
'''),
    ("rle", "Add rle_encode()",
     "Add toylib/{mod}.py with rle_encode(s): run-length encode as count followed by character.",
     ["rle_encode('aaabcc') == '3a1b2c'", "rle_encode('') == ''"],
     '''def rle_encode(s):
    out, i = [], 0
    while i < len(s):
        j = i
        while j < len(s) and s[j] == s[i]:
            j += 1
        out.append(f"{j - i}{s[i]}")
        i = j
    return "".join(out)
''',
     '''def test_rle_encode():
    assert rle_encode("aaabcc") == "3a1b2c"
    assert rle_encode("") == ""
'''),
]

FUNC_OF = {"slugify": "slugify", "pad": "pad_left", "center": "center_text", "palindrome": "is_palindrome",
           "wordcount": "word_count", "chunk": "chunk", "joinfields": "join_fields", "roman": "to_roman",
           "flatten": "flatten", "rle": "rle_encode"}

# The knobs SimWorker uses on this sandbox (see config.dryrun.toml).
SEMANTIC_EDIT = {"file": "toylib/shared.py", "find": "DEFAULT_WIDTH = 10", "replace": "DEFAULT_WIDTH = 12"}
VISIBLE_BREAK_EDIT = {"file": "toylib/core.py", "find": "return max(lo, min(hi, x))",
                      "replace": "return min(lo, max(hi, x))"}


def _new_file_patch(path: str, content: str) -> str:
    lines = content.splitlines()
    body = "".join(f"+{l}\n" for l in lines)
    return (f"diff --git a/{path} b/{path}\nnew file mode 100644\n--- /dev/null\n+++ b/{path}\n"
            f"@@ -0,0 +1,{len(lines)} @@\n{body}")


def _git(cwd: Path, *args: str) -> str:
    env = dict(os.environ, GIT_AUTHOR_NAME="toygen", GIT_AUTHOR_EMAIL="toygen@localhost",
               GIT_COMMITTER_NAME="toygen", GIT_COMMITTER_EMAIL="toygen@localhost",
               GIT_CONFIG_COUNT="1", GIT_CONFIG_KEY_0="commit.gpgsign", GIT_CONFIG_VALUE_0="false")
    return subprocess.run(["git", *args], cwd=cwd, env=env, check=True, capture_output=True,
                          text=True).stdout.strip()


def generate(dest: Path, n_tasks: int = 10, force: bool = False) -> dict:
    dest = Path(dest)
    if dest.exists():
        if not force:
            raise FileExistsError(f"{dest} exists (use force)")
        shutil.rmtree(dest)
    seed = dest / "seed"
    seed.mkdir(parents=True)
    for rel, content in SEED_FILES.items():
        p = seed / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)
    _git(seed, "init", "-q", "-b", "main")
    _git(seed, "add", "-A")
    _git(seed, "commit", "-q", "-m", "toy sandbox base")
    _git(seed, "tag", BASE_TAG)
    remote = dest / "remote.git"
    init_bare(remote)
    _git(seed, "push", "-q", str(remote), "main", f"refs/tags/{BASE_TAG}")
    base_sha = _git(seed, "rev-parse", "HEAD")

    tasks = []
    hidden = dest / "tasks" / "hidden"
    ref = dest / "tasks" / "reference"
    ref.mkdir(parents=True)
    for i in range(n_tasks):
        tid = f"{i + 1:03d}"
        mod, title, text, acc, impl, test = TEMPLATES[i % len(TEMPLATES)]
        rep = i // len(TEMPLATES)
        modname = mod if rep == 0 else f"{mod}_{rep + 1}"
        path = f"toylib/{modname}.py"
        tasks.append({"id": tid, "title": f"{title} ({modname})", "text": text.format(mod=modname),
                      "acceptance": acc})
        (ref / f"{tid}.patch").write_text(_new_file_patch(path, impl))
        hd = hidden / tid
        hd.mkdir(parents=True)
        (hd / f"test_task_{tid}.py").write_text(
            f"from toylib.{modname} import {FUNC_OF[mod]}\n\n\n" + test)
    (dest / "tasks" / "tasks.json").write_text(json.dumps({"tasks": tasks}, indent=2) + "\n")
    shutil.rmtree(seed)
    return {"remote": str(remote), "base_sha": base_sha, "n_tasks": n_tasks,
            "task_file": str(dest / "tasks" / "tasks.json"), "hidden_tests_dir": str(hidden),
            "reference_dir": str(ref)}
