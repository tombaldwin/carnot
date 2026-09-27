"""The reviewer: one change in, one verdict out.

The reviewer is handed a single-change packet (diff against the change's merge
base, task text and acceptance criteria, visible test result on that head). It
never sees the queue, other changes, or hidden tests.
"""
from __future__ import annotations

import dataclasses as dc
import json
import re
import subprocess
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
            head=self.head, files="\n".join(self.files),
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


class CommandReviewer(Reviewer):
    """Runs ``cfg.reviewer.command`` (UNVERIFIED template) once per review:
    a fresh process with no memory of earlier reviews."""

    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.template = cfg.path(cfg.reviewer.prompt_template).read_text()

    def review(self, packet: ReviewPacket) -> ReviewResult:
        rc = self.cfg.reviewer
        with tempfile.TemporaryDirectory(prefix="fleet-review-") as td:
            pf = Path(td) / "prompt.md"
            pf.write_text(packet.render(self.template, rc.max_diff_chars))
            cmd = [c.format(model=self.cfg.run.reviewer_model, prompt_file=str(pf), workdir=td)
                   for c in rc.command]
            try:
                p = subprocess.run(cmd, cwd=td, capture_output=True, text=True, timeout=rc.timeout_s)
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
