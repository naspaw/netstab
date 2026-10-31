"""The load test: a steady game-like packet stream for a fixed duration."""

from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor

from . import icmp
from .stats import Summary, summarize

PENDING = -1.0  # sentinel in the rtt array while a probe is in flight


class LoadTest:
    """Sends `rate_hz` echo requests per second to `ip` for `duration_s`.

    Requests are fired on a fixed clock regardless of replies (like game
    ticks), so a slow reply never delays the next packet. Results are written
    by index; the UI polls `snapshot()`.
    """

    def __init__(self, ip: str, rate_hz: int, payload_bytes: int,
                 duration_s: int, timeout_ms: int = 1000):
        self.ip = ip
        self.rate_hz = rate_hz
        self.payload = payload_bytes
        self.duration = duration_s
        self.timeout_ms = timeout_ms
        self.total = rate_hz * duration_s
        self.rtts: list[float | None] = [PENDING] * self.total
        self.sent = 0
        self.started_at: float | None = None
        self.finished = threading.Event()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    @property
    def cancelled(self) -> bool:
        return self._stop.is_set()

    def _one(self, i: int) -> None:
        r = icmp.echo(self.ip, self.payload, self.timeout_ms)
        self.rtts[i] = r.rtt_ms if r.ok else None

    def _run(self) -> None:
        # Enough workers to cover every packet that can be in flight at once.
        workers = max(8, int(self.rate_hz * self.timeout_ms / 1000 * 1.5))
        with ThreadPoolExecutor(max_workers=workers) as ex:
            self.started_at = t0 = time.perf_counter()
            for i in range(self.total):
                if self._stop.is_set():
                    break
                delay = t0 + i / self.rate_hz - time.perf_counter()
                if delay > 0:
                    time.sleep(delay)
                ex.submit(self._one, i)
                self.sent = i + 1
        self.finished.set()

    def elapsed(self) -> float:
        return 0.0 if self.started_at is None else time.perf_counter() - self.started_at

    def snapshot(self) -> list[float | None]:
        """Completed results in send order (stops at the first in-flight one)."""
        out = []
        for r in self.rtts[: self.sent]:
            if r == PENDING:
                break
            out.append(r)
        return out

    def summary(self) -> Summary:
        return summarize(self.snapshot(), self.rate_hz)
