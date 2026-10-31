"""Test history and side-by-side comparison of networks.

Each finished test is saved as a Run labelled with the network it ran on
(the Wi-Fi/Ethernet profile name), so a player can test at home, switch to a
phone hotspot or another Wi-Fi, test again and see which is better.
"""

from __future__ import annotations

import json
import math
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable

from .servers import CACHE_DIR

HISTORY_PATH = CACHE_DIR / "history.json"
MAX_RUNS = 50


@dataclass
class Run:
    label: str  # user-editable, defaults to the network name
    network: str
    time: str  # ISO timestamp
    game: str  # profile key
    game_name: str
    server_code: str
    server_name: str
    duration_s: int
    summary: dict  # stats.Summary.to_dict()
    detailed: dict | None = None  # detailed.DetailedResult.to_dict()
    rate_hz: int = 64
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])

    def to_dict(self) -> dict:
        return asdict(self)


def load(path: Path | None = None) -> list[Run]:
    path = path or HISTORY_PATH
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    runs = []
    for d in data:
        try:
            runs.append(Run(**d))
        except TypeError:
            continue  # written by an incompatible version; skip it
    return runs


def save(runs: list[Run], path: Path | None = None) -> None:
    path = path or HISTORY_PATH
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps([r.to_dict() for r in runs[-MAX_RUNS:]], indent=1),
                        encoding="utf-8")
    except OSError:
        pass


# --------------------------------------------------------------- comparison --
@dataclass
class Metric:
    name: str
    unit: str
    better: str  # "low" or "high"
    get: Callable[[Run], float | None]
    digits: int = 0
    margin: float = 0.0  # differences smaller than this count as a tie


def _s(key: str) -> Callable[[Run], float | None]:
    def get(r: Run):
        v = r.summary.get(key)
        return None if v is None or (isinstance(v, float) and math.isnan(v)) else v
    return get


def _spikes_per_min(r: Run) -> float | None:
    n, secs = r.summary.get("spikes"), r.duration_s
    return n * 60 / secs if n is not None and secs else None


def _dropout_ms(r: Run) -> float | None:
    burst = r.summary.get("longest_loss_burst")
    return None if burst is None else burst * 1000 / r.rate_hz


def _loaded(r: Run) -> float | None:
    d = r.detailed or {}
    vals = [p["median_ms"] for p in (d.get("download"), d.get("upload"))
            if p and p.get("median_ms") is not None]
    return max(vals) if vals else None


def _mbps(direction: str) -> Callable[[Run], float | None]:
    def get(r: Run):
        p = (r.detailed or {}).get(direction)
        return p.get("mbps") or None if p else None
    return get


METRICS = [
    Metric("Stability score", "/100", "high", _s("score"), margin=2),
    Metric("Ping (median)", " ms", "low", _s("median_ms"), margin=3),
    Metric("Worst 1% ping", " ms", "low", _s("p99_ms"), margin=5),
    Metric("Jitter", " ms", "low", _s("jitter_ms"), 1, margin=0.5),
    Metric("Packet loss", "%", "low", _s("loss_pct"), 2, margin=0.05),
    Metric("Spikes per minute", "", "low", _spikes_per_min, 1, margin=0.5),
    Metric("Longest dropout", " ms", "low", _dropout_ms, margin=20),
    Metric("Ping while line is busy", " ms", "low", _loaded, margin=10),
    Metric("Download", " Mbps", "high", _mbps("download"), margin=2),
    Metric("Upload", " Mbps", "high", _mbps("upload"), margin=2),
]


@dataclass
class Row:
    metric: Metric
    values: list[float | None]
    best: list[int]  # indexes of the winning run(s); empty if not comparable

    def fmt(self, i: int) -> str:
        v = self.values[i]
        return "-" if v is None else f"{v:.{self.metric.digits}f}{self.metric.unit}"


@dataclass
class Comparison:
    runs: list[Run]
    rows: list[Row]
    winner: int | None  # index into runs, None if too close to call
    summary: str
    reasons: list[str]
    notes: list[str]


def compare(runs: list[Run]) -> Comparison:
    rows = []
    for m in METRICS:
        vals = [m.get(r) for r in runs]
        present = [(v, i) for i, v in enumerate(vals) if v is not None]
        if not present:
            continue  # nobody measured this; leave the row out
        best = []
        if len(present) >= 2:
            target = (min if m.better == "low" else max)(v for v, _ in present)
            best = [i for v, i in present if abs(v - target) <= m.margin]
            if len(best) == len(present):
                best = []  # all within the margin: a tie, highlight nobody
        rows.append(Row(m, vals, best))

    notes = []
    if len({r.server_code for r in runs}) > 1:
        notes.append("These tests used different servers, so ping partly reflects "
                     "server distance, not only the network.")
    if len({r.game for r in runs}) > 1:
        notes.append("These tests are for different games (different packet rates).")

    if len(runs) < 2:
        return Comparison(runs, rows, None, "Test another network to compare.", [], notes)

    # Overall: stability score decides; very close scores fall back to ping.
    scored = sorted(range(len(runs)),
                    key=lambda i: (-(runs[i].summary.get("score") or 0),
                                   _s("median_ms")(runs[i]) or 1e9))
    first, second = scored[0], scored[1]
    s1 = runs[first].summary.get("score") or 0
    s2 = runs[second].summary.get("score") or 0
    p1 = _s("median_ms")(runs[first]) or 0
    p2 = _s("median_ms")(runs[second]) or 0
    if abs(s1 - s2) <= 2 and abs(p1 - p2) <= 5:
        return Comparison(runs, rows, None,
                          f"It's a tie - {runs[first].label} and {runs[second].label} "
                          "performed about the same.", [], notes)

    reasons = []
    for row in rows:
        if row.best == [first]:
            other = [v for i, v in enumerate(row.values) if i != first and v is not None]
            if other:
                ref = (min if row.metric.better == "low" else max)(other)
                diff = abs(row.values[first] - ref)
                reasons.append(f"{row.metric.name}: {row.fmt(first)} "
                               f"({diff:.{row.metric.digits}f}{row.metric.unit} "
                               f"{'better' if row.metric.name != 'Stability score' else 'higher'})")
    lead = runs[first]
    return Comparison(runs, rows, first,
                      f"{lead.label} is the better network for {lead.game_name}.",
                      reasons, notes)
