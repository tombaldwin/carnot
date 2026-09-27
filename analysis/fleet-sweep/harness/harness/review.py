"""The reviewer: one change in, one verdict out.

Review job (PLAN-v4 section 7, frozen before T1; `[reviewer] job = "checkout"`): the reviewer
is handed a fresh temporary checkout of the exact submitted head (the call's working
directory), plus a packet with the task text, acceptance criteria, the files changed and the
diff against the change's merge base. It may read files and run the visible test suite in that
checkout (the tool allow-list permits nothing else) and answers APPROVE or REQUEST_CHANGES with
a reason. The checkout is an export of the head's tree (no .git, no other refs), with
`strip_paths` (.claude/, CLAUDE.md) removed so a change cannot alter the reviewer's settings or
instructions. It never sees the queue, other changes, or hidden tests. The prompt template is
the same for every review.

`job = "diff"` is the SUPERSEDED diff-only job (packet with the harness's visible-test result,
run in an empty temp dir, prompts/reviewer-diff.md), kept for reproducing earlier dry runs.

The same `CommandReviewer` serves live windows (orchestrator) and offline calibration
(`python -m harness calibrate`), so the calibrated V is measured on the live job.
"""
from __future__ import annotations

import dataclasses as dc
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from .config import Config
from .tasks import Task


class ReviewError(RuntimeError):
    pass


@dc.dataclass
class ReviewPacket:
    task: Task
    head: str
    base: str
    diff: str
    files: list[str]
    visible_passed: bool
    visible_output: str
    visible_cmd: str = "python -m pytest -q"   # how the reviewer runs the visible suite in the checkout

    def render(self, template: str, max_diff_chars: int = 60000) -> str:
        diff = self.diff
        if len(diff) > max_diff_chars:
            diff = diff[:max_diff_chars] + f"\n... [diff truncated at {max_diff_chars} chars]\n"
        crit = "\n".join(f"- {c}" for c in self.task.acceptance) or "- (none given)"
        vis = "PASSED" if self.visible_passed else "FAILED"
        tail = "\n".join(self.visible_output.strip().splitlines()[-40:])
        return template.format(
            task_id=self.task.id, task_title=self.task.title, task_text=self.task.text,
            acceptance=crit, visible_result=vis, visible_output=tail, diff=diff,
            head=self.head, files="\n".join(self.files) or "(none)", visible_cmd=self.visible_cmd,
        )


@dc.dataclass
class ReviewResult:
    verdict: str            # approve | request_changes
    reason: str
    tokens_in: int | None = None
    tokens_out: int | None = None


_APPROVE = re.compile(r"^(approve|approved|lgtm)\b", re.I)
_REQUEST = re.compile(r"^(request[\s_-]*changes|changes[\s_-]*requested|reject(ed)?)\b", re.I)


def _clean(line: str) -> str:
    line = line.strip()
    line = re.sub(r"^[#>*`\s\-]+", "", line)          # markdown heading/bullet/bold/code
    line = re.sub(r"^(verdict|decision)\s*[:\-]\s*", "", line, flags=re.I)
    line = re.sub(r"^[*`\"']+", "", line)
    return line


def parse_verdict(text: str) -> tuple[str, str]:
    """First non-empty line must start with APPROVE or REQUEST_CHANGES (case,
    markdown and a 'Verdict:' prefix tolerated). The rest is the reason. If the
    first line does not say, the first line that *starts* with a verdict word is
    used. Anything else is an error: ambiguous output is never read as approval."""
    lines = [l for l in text.strip().splitlines() if l.strip()]
    if not lines:
        raise ReviewError("empty reviewer output")

    def classify(line: str):
        c = _clean(line)
        if _REQUEST.match(c):
            return "request_changes", _REQUEST.sub("", c, count=1)
        if _APPROVE.match(c):
            return "approve", _APPROVE.sub("", c, count=1)
        return None, None

    for idx, line in enumerate(lines):
        verdict, rest = classify(line)
        if verdict:
            rest = re.sub(r"^[*`\"'\s:.\-—]+", "", rest)
            reason = "\n".join(([rest] if rest else []) + lines[idx + 1:]).strip()
            return verdict, reason[:4000]
    raise ReviewError(f"no verdict in reviewer output: {lines[0][:200]!r}")


class Reviewer:
    def review(self, packet: ReviewPacket) -> ReviewResult:  # pragma: no cover - interface
        raise NotImplementedError


JOBS = ("checkout", "diff")
FEEDBACK_FILE = "FEEDBACK.md"


def make_packet(cfg: Config, repo, task: Task, head: str, base: str, visible_passed: bool,
                visible_output: str) -> ReviewPacket:
    """The one packet builder used by live windows and by calibration: the diff of ``head``
    against ``base`` (FEEDBACK.md excluded), the files it touches, and the task text."""
    spec = [base, head, "--", ".", f":(exclude){FEEDBACK_FILE}"]
    diff = repo.out("diff", "--no-color", *spec)
    files = sorted(repo.out("diff", "--name-only", "--no-renames", *spec).split("\n")) if diff else []
    files = [f for f in files if f]
    return ReviewPacket(task, head, base, diff, files, visible_passed, visible_output, visible_cmd_text(cfg))


def visible_cmd_text(cfg: Config) -> str:
    """The visible-suite command as the reviewer should type it in the checkout."""
    py = cfg.tests.python or sys.executable
    return " ".join(c.format(python=py) for c in cfg.tests.visible_cmd)


class CommandReviewer(Reviewer):
    """Runs ``cfg.reviewer.command`` (UNVERIFIED template) once per review:
    a fresh process with no memory of earlier reviews. For the checkout job it needs
    ``repo`` (a local clone that has the head) to export the checkout."""

    def __init__(self, cfg: Config, repo=None):
        rc = cfg.reviewer
        if rc.job not in JOBS:
            raise ValueError(f"[reviewer] job must be one of {JOBS}, not {rc.job!r}")
        if rc.job == "checkout" and repo is None:
            raise ValueError("the checkout review job needs the local clone (repo)")
        self.cfg, self.repo = cfg, repo
        self.template = cfg.path(rc.prompt_template).read_text()

    def _checkout(self, head: str, dest: Path) -> None:
        self.repo.export(head, dest)
        for rel in self.cfg.reviewer.strip_paths:
            q = dest / rel
            if q.is_dir() and not q.is_symlink():
                shutil.rmtree(q)
            elif q.exists() or q.is_symlink():
                q.unlink()

    def command_for(self, prompt_file: str, workdir: str, checkout: str) -> tuple[list[str], str]:
        """The rendered command line and working directory for one call."""
        rc = self.cfg.reviewer
        py = self.cfg.tests.python or sys.executable
        tools = rc.allowed_tools_sep.join(t.format(python=py, checkout=checkout) for t in rc.allowed_tools)
        kw = dict(model=self.cfg.run.reviewer_model, prompt_file=prompt_file, workdir=workdir,
                  checkout=checkout, allowed_tools=tools, python=py)
        cmd = [c.format(**kw) for c in rc.command]
        cwd_tpl = rc.cwd if rc.job == "checkout" else "{workdir}"   # the diff job runs in an empty temp dir
        return cmd, cwd_tpl.format(**kw)

    def review(self, packet: ReviewPacket) -> ReviewResult:
        rc = self.cfg.reviewer
        with tempfile.TemporaryDirectory(prefix="fleet-review-") as td:
            pf = Path(td) / "prompt.md"
            checkout = ""
            if rc.job == "checkout":
                co = Path(td) / "checkout"
                try:
                    self._checkout(packet.head, co)
                except Exception as e:
                    raise ReviewError(f"could not export the checkout of {packet.head[:12]}: {e}")
                checkout = str(co)
            pf.write_text(packet.render(self.template, rc.max_diff_chars))
            cmd, cwd = self.command_for(str(pf), td, checkout)
            try:
                with open(pf) as stdin:
                    p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=rc.timeout_s,
                                       stdin=stdin if rc.prompt_on_stdin else subprocess.DEVNULL)
            except subprocess.TimeoutExpired:
                raise ReviewError(f"reviewer timed out after {rc.timeout_s}s")
            except OSError as e:
                raise ReviewError(f"reviewer did not start: {e}")
        if p.returncode != 0:
            raise ReviewError(f"reviewer exit {p.returncode}: {(p.stderr or p.stdout).strip()[:500]}")
        text, tin, tout = p.stdout, None, None
        if rc.output_format == "json":
            try:
                obj = json.loads(p.stdout)
            except json.JSONDecodeError as e:
                raise ReviewError(f"reviewer output is not JSON: {e}")
            text = obj.get("result") or ""
            usage = obj.get("usage") or {}
            tin = usage.get("input_tokens")
            tout = usage.get("output_tokens")
            if isinstance(tin, int):
                tin += int(usage.get("cache_read_input_tokens") or 0) + int(usage.get("cache_creation_input_tokens") or 0)
        verdict, reason = parse_verdict(text)
        return ReviewResult(verdict, reason, tin if isinstance(tin, int) else None,
                            tout if isinstance(tout, int) else None)
