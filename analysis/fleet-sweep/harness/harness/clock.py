"""Wall clock with an optional acceleration factor.

All times the harness logs are *virtual* times: ``start + (real elapsed) * scale``.
With scale = 1 this is the real UTC clock. A dry run at scale 90 plays a
90-minute window in one real minute, and the event log still reads as a
90-minute window, so the analysis cannot tell the difference.
"""
from __future__ import annotations

import datetime as dt
import threading
import time


def iso(t: dt.datetime) -> str:
    """UTC ISO 8601 with milliseconds and a trailing Z (the SCHEMA.md format)."""
    t = t.astimezone(dt.timezone.utc)
    return t.strftime("%Y-%m-%dT%H:%M:%S.") + f"{t.microsecond // 1000:03d}Z"


class Clock:
    def __init__(self, scale: float = 1.0, start: dt.datetime | None = None):
        if scale <= 0:
            raise ValueError("scale must be positive")
        self.scale = float(scale)
        self._real0 = time.monotonic()
        self._virt0 = start or dt.datetime.now(dt.timezone.utc)

    def now(self) -> dt.datetime:
        return self._virt0 + dt.timedelta(seconds=(time.monotonic() - self._real0) * self.scale)

    def elapsed(self) -> float:
        """Virtual seconds since the clock started."""
        return (time.monotonic() - self._real0) * self.scale

    def real(self, virtual_seconds: float) -> float:
        return virtual_seconds / self.scale

    def sleep(self, virtual_seconds: float, stop: threading.Event | None = None) -> bool:
        """Sleep for virtual seconds. Returns True if ``stop`` was set meanwhile."""
        secs = max(0.0, virtual_seconds / self.scale)
        if stop is None:
            time.sleep(secs)
            return False
        return stop.wait(secs)

    def sleep_until(self, when: dt.datetime, stop: threading.Event | None = None) -> bool:
        return self.sleep((when - self.now()).total_seconds(), stop)
