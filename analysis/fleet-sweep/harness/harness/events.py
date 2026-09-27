"""Append-only JSONL event log (runs/<run_id>/events.jsonl)."""
from __future__ import annotations

import json
import os
import threading
from pathlib import Path

from .clock import Clock, iso
from .schema import check_event


class SchemaError(ValueError):
    pass


class EventLog:
    def __init__(self, path: Path | str, clock: Clock, echo: bool = False):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.clock = clock
        self.echo = echo
        self._lock = threading.Lock()

    def emit(self, type_: str, **fields) -> dict:
        # The timestamp is taken inside the lock so the file is time-ordered.
        with self._lock:
            ev = {"t": iso(self.clock.now()), "type": type_, **fields}
            errs = check_event(ev)
            if errs:
                raise SchemaError("; ".join(errs))
            line = json.dumps(ev, separators=(",", ":")) + "\n"
            # O_APPEND so operator commands (`harness log`) can add lines safely.
            fd = os.open(self.path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
            try:
                os.write(fd, line.encode())
            finally:
                os.close(fd)
            if self.echo:
                print(line, end="", flush=True)
            return ev

    def note(self, text: str) -> dict:
        return self.emit("note", text=text)


def read_events(path: Path | str) -> list[dict]:
    return [json.loads(l) for l in Path(path).read_text().splitlines() if l.strip()]
