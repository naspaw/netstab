"""Detailed diagnostics for one chosen server.

1. Route + per-hop stats (MTR style): where on the path latency/loss begins.
2. Path MTU: the largest packet that passes without fragmentation.
3. Bufferbloat: how much ping rises while your line is busy downloading or
   uploading (e.g. someone streaming, Steam updating in the background).
"""

from __future__ import annotations

import statistics
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from typing import Callable

import requests

from . import icmp

Progress = Callable[[str, float, str], None]  # stage, 0..1, message

DOWNLOAD_BASE = "https://speed.cloudflare.com/__down"  # ?bytes=N
UPLOAD_URL = "https://speed.cloudflare.com/__up"
MAX_REQUEST_BYTES = 25_000_000  # the endpoint refuses larger downloads


@dataclass
class Hop:
    ttl: int
    ip: str | None
    sent: int = 0
    rtts: list[float] = field(default_factory=list)

    @property
    def loss_pct(self) -> float:
        return 100.0 * (self.sent - len(self.rtts)) / self.sent if self.sent else 0.0

    @property
    def avg(self) -> float | None:
        return statistics.fmean(self.rtts) if self.rtts else None

    @property
    def best(self) -> float | None:
        return min(self.rtts) if self.rtts else None

    @property
    def worst(self) -> float | None:
        return max(self.rtts) if self.rtts else None

    @property
    def jitter(self) -> float | None:
        if len(self.rtts) < 2:
            return None
        return statistics.fmean(abs(b - a) for a, b in zip(self.rtts, self.rtts[1:]))

    def to_dict(self) -> dict:
        return {"ttl": self.ttl, "ip": self.ip, "sent": self.sent,
                "loss_pct": self.loss_pct, "avg": self.avg, "best": self.best,
                "worst": self.worst, "jitter": self.jitter}


@dataclass
class LoadPhase:
    name: str
    median_ms: float | None
    loss_pct: float
    mbps: float

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class DetailedResult:
    target: str
    hops: list[Hop] = field(default_factory=list)
    mtu: int | None = None
    idle: LoadPhase | None = None
    download: LoadPhase | None = None
    upload: LoadPhase | None = None
    bloat_grade: str | None = None
    data_mb: float = 0.0  # data used by the load phases
    findings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "target": self.target,
            "hops": [h.to_dict() for h in self.hops],
            "mtu": self.mtu,
            "idle": self.idle and self.idle.to_dict(),
            "download": self.download and self.download.to_dict(),
            "upload": self.upload and self.upload.to_dict(),
            "bloat_grade": self.bloat_grade,
            "data_mb": self.data_mb,
            "findings": self.findings,
        }


@dataclass(frozen=True)
class LoadMode:
    key: str
    label: str
    seconds: int  # measured seconds per direction (plus a short ramp-up)
    streams: int


LOAD_MODES = {
    "off": LoadMode("off", "Off", 0, 0),
    "standard": LoadMode("standard", "Standard - 10 s each way, 4 streams", 10, 4),
    "hard": LoadMode("hard", "Hard - 20 s each way, 8 streams (max speed)", 20, 8),
}
RAMP_SECONDS = 1.5


@dataclass
class DetailedOptions:
    route_seconds: int = 30  # 0 = skip route / per-hop test
    mtu: bool = True
    load: str = "standard"  # key of LOAD_MODES
    max_mb: int | None = None  # total data cap for the load phases

    @property
    def uses_data(self) -> bool:
        return self.load != "off"


def estimate_mb(opts: DetailedOptions, down_mbps: float, up_mbps: float) -> float:
    """Rough data usage of the load phases at the given line speeds."""
    mode = LOAD_MODES[opts.load]
    if not mode.seconds:
        return 0.0
    secs = mode.seconds + RAMP_SECONDS
    mb = (down_mbps + up_mbps) * secs / 8
    if opts.max_mb is not None:
        mb = min(mb, opts.max_mb)
    return mb


class _Budget:
    """Hands out byte allowances to the transfer streams of one load phase.

    Without a cap every request gets the endpoint's maximum. With a cap, the
    total handed out never exceeds it, minus room for TCP/IP/TLS overhead, so
    the data really used on the line stays under the user's limit.
    """

    # Wire bytes per payload byte: headers, TLS, TCP acks, handshakes. Measured
    # 1.083 on the Wi-Fi adapter for a capped standard test; keep a margin.
    OVERHEAD = 1.12
    MIN_REQUEST = 256_000

    def __init__(self, cap_bytes: int | None, streams: int):
        self.left = None if cap_bytes is None else int(cap_bytes / self.OVERHEAD)
        self.streams = max(1, streams)
        self.lock = threading.Lock()

    def take(self) -> int:
        with self.lock:
            if self.left is None:
                return MAX_REQUEST_BYTES
            # Share what's left between the streams so all of them keep loading.
            n = min(MAX_REQUEST_BYTES, self.left,
                    max(self.MIN_REQUEST, self.left // self.streams))
            self.left -= n
            return n


class DetailedTest:
    def __init__(self, ip: str, options: DetailedOptions | None = None):
        self.ip = ip
        self.opts = options or DetailedOptions()
        self.mtr_seconds = self.opts.route_seconds
        mode = LOAD_MODES[self.opts.load]
        self.load_seconds = mode.seconds
        self.streams = mode.streams
        # Split the data cap evenly between download and upload.
        self.phase_cap = (self.opts.max_mb * 1_000_000 // 2
                          if self.opts.max_mb is not None else None)
        self.result = DetailedResult(ip)
        self._stop = threading.Event()

    def stop(self) -> None:
        self._stop.set()

    @property
    def cancelled(self) -> bool:
        return self._stop.is_set()

    def run(self, progress: Progress) -> DetailedResult:
        if self.mtr_seconds:
            progress("route", 0, "Tracing route...")
            self._trace(progress)
            if not self.cancelled:
                self._mtr(progress)
        if self.opts.mtu and not self.cancelled:
            progress("mtu", 0, "Finding path MTU...")
            self.result.mtu = self._mtu()
        if self.load_seconds and not self.cancelled:
            self._bufferbloat(progress)
        self._analyse()
        progress("done", 1, "Done")
        return self.result

    # -- route -------------------------------------------------------------
    def _trace(self, progress: Progress, max_hops: int = 30) -> None:
        hops: list[Hop] = []
        for ttl in range(1, max_hops + 1):
            if self.cancelled:
                return
            hop = Hop(ttl, None)
            for _ in range(2):
                r = icmp.echo(self.ip, 32, 1000, ttl=ttl)
                if r.reply_from and r.status in (icmp.IP_SUCCESS,
                                                 icmp.IP_TTL_EXPIRED_TRANSIT):
                    hop.ip = r.reply_from
                    break
            hops.append(hop)
            progress("route", ttl / max_hops, f"Hop {ttl}: {hop.ip or '*'}")
            if hop.ip == self.ip:
                break
        # Drop trailing silent hops if the destination never answered.
        while hops and hops[-1].ip is None:
            hops.pop()
        self.result.hops = hops

    def _mtr(self, progress: Progress, rounds_per_s: int = 2) -> None:
        hops = self.result.hops
        if not hops:
            return
        rounds = self.mtr_seconds * rounds_per_s
        with ThreadPoolExecutor(max_workers=len(hops) + 2) as ex:
            for n in range(rounds):
                if self.cancelled:
                    return
                t = time.perf_counter()
                futs = [(h, ex.submit(icmp.echo, self.ip, 32, 1000, h.ttl))
                        for h in hops]
                for h, f in futs:
                    r = f.result()
                    h.sent += 1
                    if r.rtt_ms is not None:
                        h.rtts.append(r.rtt_ms)
                        h.ip = h.ip or r.reply_from
                progress("mtr", (n + 1) / rounds,
                         f"Measuring each hop ({n // rounds_per_s + 1}/{self.mtr_seconds}s)")
                time.sleep(max(0.0, 1 / rounds_per_s - (time.perf_counter() - t)))

    # -- MTU ---------------------------------------------------------------
    def _mtu(self) -> int | None:
        def passes(payload: int) -> bool:
            for _ in range(2):
                if icmp.echo(self.ip, payload, 1000, dont_fragment=True).ok:
                    return True
            return False

        if not passes(548):
            return None  # host doesn't answer big pings; can't tell
        lo, hi = 548, 1472  # 1472 + 28 header bytes = 1500 (Ethernet)
        if passes(hi):
            return hi + 28
        while hi - lo > 1:
            mid = (lo + hi) // 2
            lo, hi = (mid, hi) if passes(mid) else (lo, mid)
        return lo + 28

    # -- bufferbloat -------------------------------------------------------
    def _ping_while(self, seconds: float, rate_hz: int = 10,
                    halt: Callable[[], bool] = lambda: False) -> tuple[list, int]:
        rtts, sent = [], 0
        end = time.perf_counter() + seconds
        while time.perf_counter() < end and not self.cancelled and not halt():
            t = time.perf_counter()
            r = icmp.echo(self.ip, 64, 1000)
            sent += 1
            if r.ok:
                rtts.append(r.rtt_ms)
            time.sleep(max(0.0, 1 / rate_hz - (time.perf_counter() - t)))
        return rtts, sent

    @staticmethod
    def _phase(name: str, rtts: list, sent: int, nbytes: int, secs: float) -> LoadPhase:
        return LoadPhase(name, statistics.median(rtts) if rtts else None,
                         100.0 * (sent - len(rtts)) / max(1, sent),
                         nbytes * 8 / secs / 1e6 if secs else 0.0)

    def _saturate(self, upload: bool) -> tuple[list, int, int, float]:
        stop = threading.Event()
        counter = [0]
        lock = threading.Lock()
        budget = _Budget(self.phase_cap, self.streams)

        def down():
            # Each request asks for no more than the budget allows, so the server
            # can't send past the cap even with data already in flight.
            with requests.Session() as s:
                while not stop.is_set():
                    n = budget.take()
                    if not n:
                        return
                    try:
                        with s.get(f"{DOWNLOAD_BASE}?bytes={n}", stream=True,
                                   timeout=10) as r:
                            r.raise_for_status()
                            for chunk in r.iter_content(65536):
                                with lock:
                                    counter[0] += len(chunk)
                                if stop.is_set():
                                    return
                    except requests.RequestException:
                        time.sleep(0.5)

        def up():
            block = b"\0" * 65536

            def gen(limit):
                left = limit
                while not stop.is_set() and left > 0:
                    piece = block[:min(len(block), left)]
                    with lock:
                        counter[0] += len(piece)
                    left -= len(piece)
                    yield piece

            with requests.Session() as s:
                while not stop.is_set():
                    n = budget.take()
                    if not n:
                        return
                    try:
                        s.post(UPLOAD_URL, data=gen(n), timeout=self.load_seconds + 10)
                    except requests.RequestException:
                        time.sleep(0.5)

        threads = [threading.Thread(target=up if upload else down, daemon=True)
                   for _ in range(self.streams)]
        for th in threads:
            th.start()
        time.sleep(RAMP_SECONDS)  # let TCP ramp up so queues are actually full
        with lock:
            start_bytes = counter[0]
        t = time.perf_counter()
        rtts, sent = self._ping_while(
            self.load_seconds,
            halt=lambda: not any(th.is_alive() for th in threads))  # budget used up
        secs = time.perf_counter() - t
        with lock:
            nbytes = counter[0] - start_bytes
        stop.set()
        self.result.data_mb += counter[0] / 1e6
        return rtts, sent, nbytes, secs

    def _bufferbloat(self, progress: Progress) -> None:
        progress("bloat", 0.0, "Measuring idle latency...")
        rtts, sent = self._ping_while(5)
        self.result.idle = self._phase("Idle", rtts, sent, 0, 0)
        if self.cancelled:
            return
        progress("bloat", 0.25, "Measuring latency during download...")
        rtts, sent, nb, secs = self._saturate(upload=False)
        self.result.download = self._phase("Download", rtts, sent, nb, secs)
        if self.cancelled:
            return
        progress("bloat", 0.65, "Measuring latency during upload...")
        rtts, sent, nb, secs = self._saturate(upload=True)
        self.result.upload = self._phase("Upload", rtts, sent, nb, secs)

    # -- analysis ----------------------------------------------------------
    def _analyse(self) -> None:
        r, f = self.result, self.result.findings
        hops = [h for h in r.hops if h.sent]
        if hops:
            dest = hops[-1]
            if dest.loss_pct >= 1:
                # Loss that starts at a hop and persists to the end is real;
                # loss only on a middle router is usually ICMP rate limiting.
                origin = dest
                for h in reversed(hops):
                    if h.ip and h.loss_pct >= dest.loss_pct * 0.5:
                        origin = h
                    else:
                        break
                where = ("your router / Wi-Fi" if origin.ttl == 1 else
                         "your ISP" if origin.ttl <= 3 else
                         "the wider internet / server side")
                f.append(f"Packet loss ({dest.loss_pct:.1f}%) starts at hop "
                         f"{origin.ttl} ({origin.ip}) - likely {where}.")
            first = hops[0]
            if first.avg is not None and (first.avg > 5 or (first.jitter or 0) > 3):
                f.append(f"Your first hop (router) answers in {first.avg:.1f} ms "
                         f"with {first.jitter or 0:.1f} ms jitter - typical of Wi-Fi "
                         "or a busy router. A cable usually fixes this.")
            prev = None
            for h in hops:
                if h.avg is None:
                    continue
                if prev is not None and h.avg - prev > 30:
                    f.append(f"Latency jumps by {h.avg - prev:.0f} ms at hop "
                             f"{h.ttl} ({h.ip}) - long-distance link or congestion.")
                prev = h.avg

        if r.mtu is not None and r.mtu < 1500:
            f.append(f"Path MTU is {r.mtu} (below 1500) - normal for PPPoE/VPN, "
                     "harmless for games.")

        if r.idle and r.idle.median_ms is not None:
            worst = 0.0
            for ph in (r.download, r.upload):
                if ph and ph.median_ms is not None:
                    worst = max(worst, ph.median_ms - r.idle.median_ms)
            r.bloat_grade = ("A" if worst < 5 else "B" if worst < 30 else
                             "C" if worst < 60 else "D" if worst < 200 else "F")
            if worst >= 30:
                f.append(f"Bufferbloat: ping rises by {worst:.0f} ms when the line "
                         "is busy. Pause downloads/streams while playing, or enable "
                         "SQM / QoS on your router.")
        if not f:
            f.append("No problems found on the route.")
