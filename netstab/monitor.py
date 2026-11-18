"""Live monitor: a light, continuous ping to one target, for running in the
background (or in a small always-on-top window) while you play.

Unlike the stability test it sends only 1-4 pings per second, so it can run for
hours without adding noticeable load, and keeps a rolling history.
"""

from __future__ import annotations

import socket
import statistics
import threading
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

from . import icmp

INTERVALS = [1.0, 0.5, 0.25]  # seconds between pings: 1, 2 or 4 per second
KEEP_SECONDS = 600  # history kept for the chart and stats
ROUTER = "router"  # special target: the first hop (your router)
PRESET_HOSTS = [
    ("google.com", "Google"),
    ("1.1.1.1", "Cloudflare DNS"),
    ("8.8.8.8", "Google DNS"),
    (ROUTER, "Your router"),
]


class TargetError(ValueError):
    pass


def first_hop(probe_ip: str = "1.1.1.1") -> str:
    """Address of your router: whoever answers a ping that may only go one hop."""
    for _ in range(3):
        r = icmp.echo(probe_ip, 32, 1000, ttl=1)
        if r.reply_from and r.status in (icmp.IP_TTL_EXPIRED_TRANSIT, icmp.IP_SUCCESS):
            return r.reply_from
    raise TargetError("Couldn't find your router (no reply from the first hop).")


def resolve(target: str) -> str:
    """IPv4 address for a host name, an IP, or ROUTER."""
    target = target.strip()
    if not target:
        raise TargetError("Enter a website or IP address.")
    if target.lower() == ROUTER:
        return first_hop()
    # Accept pasted URLs like https://google.com/search.
    host = target.split("://", 1)[-1].split("/", 1)[0].split("@")[-1]
    host = host.rsplit(":", 1)[0] if host.count(":") == 1 else host
    try:
        infos = socket.getaddrinfo(host, None, socket.AF_INET, socket.SOCK_DGRAM)
    except (socket.gaierror, UnicodeError):
        raise TargetError(f"Couldn't find '{host}'. Check the spelling, or that "
                          "you're online.") from None
    return infos[0][4][0]


@dataclass
class LiveStats:
    count: int  # pings in the window (answered or lost)
    last_ms: float | None  # most recent reply, None if it was lost
    avg_ms: float | None
    median_ms: float | None
    min_ms: float | None
    max_ms: float | None
    jitter_ms: float | None
    loss_pct: float
    lost_in_row: int  # consecutive losses at the end (connection dropping now)


class PingMonitor:
    """Pings `ip` every `interval` seconds on a fixed clock until stopped.

    Results are kept in send order for KEEP_SECONDS. Thread-safe; the UI reads
    them with history() and stats().
    """

    def __init__(self, ip: str, interval: float = 0.5, timeout_ms: int = 1000,
                 payload: int = 32):
        self.ip = ip
        self.interval = interval
        self.timeout_ms = timeout_ms
        self.payload = payload
        self.started = time.monotonic()
        self._samples: deque[tuple[float, float | None]] = deque()
        self._pending: dict[int, tuple[float, float | None | bool]] = {}
        self._next_seq = 0  # next sequence number to publish, in order
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._paused = threading.Event()
        self._thread: threading.Thread | None = None

    # -- control ---------------------------------------------------------------
    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, daemon=True,
                                        name="netstab-monitor")
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive() and \
            not self._stop.is_set()

    def pause(self) -> None:
        self._paused.set()

    def resume(self) -> None:
        self._paused.clear()

    @property
    def paused(self) -> bool:
        return self._paused.is_set()

    # -- worker ----------------------------------------------------------------
    def _one(self, seq: int, t: float) -> None:
        try:
            r = icmp.echo(self.ip, self.payload, self.timeout_ms)
            rtt = r.rtt_ms if r.ok else None
        except OSError:
            rtt = None
        with self._lock:
            self._pending[seq] = (t, rtt)
            # Publish every result that is now complete, in send order.
            while self._next_seq in self._pending:
                self._samples.append(self._pending.pop(self._next_seq))
                self._next_seq += 1
            cutoff = t - KEEP_SECONDS
            while self._samples and self._samples[0][0] < cutoff:
                self._samples.popleft()

    def _run(self) -> None:
        seq = 0
        # Enough workers for every ping that can still be waiting for a reply,
        # at the fastest rate (the interval can be changed while running).
        workers = int(self.timeout_ms / 1000 / min(INTERVALS + [self.interval])) + 2
        with ThreadPoolExecutor(max_workers=workers) as ex:
            next_at = time.monotonic()
            while not self._stop.is_set():
                if self._paused.is_set():
                    next_at = time.monotonic() + self.interval
                    self._stop.wait(0.1)
                    continue
                delay = next_at - time.monotonic()
                if delay > 0 and self._stop.wait(delay):
                    break
                ex.submit(self._one, seq, time.monotonic() - self.started)
                seq += 1
                next_at += self.interval
                if next_at < time.monotonic() - self.interval:
                    next_at = time.monotonic()  # fell behind (PC asleep): don't burst

    # -- reading ---------------------------------------------------------------
    def history(self, seconds: float | None = None) -> list[tuple[float, float | None]]:
        """(seconds since start, rtt or None if lost), oldest first."""
        with self._lock:
            data = list(self._samples)
        if seconds is not None and data:
            cutoff = data[-1][0] - seconds
            data = [s for s in data if s[0] >= cutoff]
        return data

    def stats(self, seconds: float = 60) -> LiveStats:
        return summarize(self.history(seconds))


def summarize(samples: list[tuple[float, float | None]]) -> LiveStats:
    rtts = [r for _, r in samples if r is not None]
    lost_in_row = 0
    for _, r in reversed(samples):
        if r is not None:
            break
        lost_in_row += 1
    n = len(samples)
    if not rtts:
        return LiveStats(n, None, None, None, None, None, None,
                         100.0 if n else 0.0, lost_in_row)
    deltas = [abs(b - a) for a, b in zip(rtts, rtts[1:])]
    return LiveStats(
        n, samples[-1][1], statistics.fmean(rtts), statistics.median(rtts),
        min(rtts), max(rtts), statistics.fmean(deltas) if deltas else 0.0,
        100.0 * (n - len(rtts)) / n, lost_in_row)


class AlertTracker:
    """Decides when to notify: ping above the limit for a few seconds, or the
    connection dropping. At most one alert per `cooldown` seconds."""

    def __init__(self, limit_ms: float, hold_s: float = 3.0, cooldown: float = 60.0):
        self.limit_ms = limit_ms
        self.hold_s = hold_s
        self.cooldown = cooldown
        self._last_alert = -1e9

    def check(self, recent: LiveStats, now: float, interval: float) -> str | None:
        """`recent` = stats over the last `hold_s` seconds."""
        if now - self._last_alert < self.cooldown or recent.count == 0:
            return None
        msg = None
        if recent.lost_in_row * interval >= self.hold_s:
            msg = "Connection dropping: no replies for " \
                  f"{recent.lost_in_row * interval:.0f} s."
        elif recent.median_ms is not None and recent.median_ms > self.limit_ms \
                and recent.count * interval >= self.hold_s * 0.9:
            msg = (f"High ping: {recent.median_ms:.0f} ms for the last "
                   f"{self.hold_s:.0f} s (your limit is {self.limit_ms:.0f} ms).")
        if msg:
            self._last_alert = now
        return msg
