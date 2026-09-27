"""Thin, thread-safe wrappers around the git CLI."""
from __future__ import annotations

import os
import subprocess
import threading
from pathlib import Path


class GitError(RuntimeError):
    def __init__(self, args, rc, out, err):
        super().__init__(f"git {' '.join(args)} -> {rc}: {err.strip() or out.strip()}")
        self.rc, self.out, self.err = rc, out, err


_ENV = {
    "GIT_TERMINAL_PROMPT": "0",
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "commit.gpgsign", "GIT_CONFIG_VALUE_0": "false",
    "GIT_AUTHOR_NAME": "fleet-harness", "GIT_AUTHOR_EMAIL": "harness@localhost",
    "GIT_COMMITTER_NAME": "fleet-harness", "GIT_COMMITTER_EMAIL": "harness@localhost",
}


class Repo:
    """A local clone. All commands on one Repo are serialised by a lock, so
    watcher, merge queue and bounce writer can share it."""

    def __init__(self, path: Path | str, user: tuple[str, str] | None = None):
        self.path = Path(path)
        self.lock = threading.RLock()
        self.env = dict(os.environ, **_ENV)
        if user:
            self.env.update(GIT_AUTHOR_NAME=user[0], GIT_AUTHOR_EMAIL=user[1],
                            GIT_COMMITTER_NAME=user[0], GIT_COMMITTER_EMAIL=user[1])

    def run(self, *args: str, check: bool = True, input: str | bytes | None = None,
            binary: bool = False) -> subprocess.CompletedProcess:
        with self.lock:
            p = subprocess.run(["git", *args], cwd=self.path, env=self.env, capture_output=True,
                               input=input, text=not binary)
        if check and p.returncode != 0:
            err = p.stderr if not binary else p.stderr.decode(errors="replace")
            out = p.stdout if not binary else ""
            raise GitError(args, p.returncode, out, err)
        return p

    def out(self, *args: str, **kw) -> str:
        return self.run(*args, **kw).stdout.strip()

    def sha(self, ref: str) -> str:
        return self.out("rev-parse", "--verify", ref + "^{commit}")

    def try_sha(self, ref: str) -> str | None:
        p = self.run("rev-parse", "--verify", "-q", ref + "^{commit}", check=False)
        return p.stdout.strip() or None

    def message(self, sha: str) -> str:
        return self.out("log", "-1", "--format=%B", sha)

    def refs(self, pattern: str) -> dict[str, str]:
        """{refname: sha} for refs matching a for-each-ref pattern."""
        o = self.out("for-each-ref", "--format=%(objectname) %(refname)", pattern)
        res = {}
        for line in o.splitlines():
            sha, name = line.split(" ", 1)
            res[name] = sha
        return res

    def export(self, sha: str, dest: Path) -> None:
        """Write the tree of ``sha`` into ``dest`` (no .git), for test runs."""
        dest.mkdir(parents=True, exist_ok=True)
        data = self.run("archive", "--format=tar", sha, binary=True).stdout
        subprocess.run(["tar", "-x", "-C", str(dest)], input=data, check=True, capture_output=True)

    def commit_tree(self, tree: str, parents: list[str], msg: str) -> str:
        args = ["commit-tree", tree]
        for p in parents:
            args += ["-p", p]
        return self.out(*args, "-m", msg)


def clone(url: str, dest: Path, user: tuple[str, str] | None = None) -> Repo:
    dest = Path(dest)
    if not (dest / ".git").exists():
        dest.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(["git", "clone", "-q", url, str(dest)], check=True, capture_output=True,
                       env=dict(os.environ, **_ENV))
    r = Repo(dest, user)
    r.run("remote", "set-url", "origin", url)
    return r


def init_bare(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(path)], check=True,
                   capture_output=True)
