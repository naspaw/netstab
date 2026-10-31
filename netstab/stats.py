"""Turn raw RTT samples into the numbers a player cares about."""

from __future__ import annotations

import math
import statistics
from dataclasses import asdict, dataclass, field


@dataclass
class Summary:
    sent: int
    received: int
    loss_pct: float
    min_ms: float
    avg_ms: float
    median_ms: float
    p95_ms: float
    p99_ms: float
    max_ms: float
    stddev_ms: float
    jitter_ms: float  # mean |delta| between consecutive replies (RFC 3550 style)
    spikes: int  # replies far above the median
    spike_threshold_ms: float
    longest_loss_burst: int
    score: int  # 0-100
    grade: str
    findings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


def _pct(sorted_vals: list[float], p: float) -> float:
    if not sorted_vals:
        return math.nan
    k = (len(sorted_vals) - 1) * p
    lo, hi = math.floor(k), math.ceil(k)
    return sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * (k - lo)


def summarize(rtts: list[float | None], rate_hz: int) -> Summary:
    """`rtts` is in send order; None means the packet was lost."""
    got = [r for r in rtts if r is not None]
    sent = len(rtts)
    if not got:
        return Summary(sent, 0, 100.0, *([math.nan] * 8), 0, math.nan,
                       sent, 0, "F", ["No replies at all - server unreachable."])

    s = sorted(got)
    median = statistics.median(s)
    deltas = [abs(b - a) for a, b in zip(got, got[1:])]
    jitter = statistics.fmean(deltas) if deltas else 0.0
    mad = statistics.median([abs(x - median) for x in s])
    spike_thr = median + max(20.0, 6 * mad)
    spikes = sum(1 for r in got if r > spike_thr)

    burst = longest = 0
    for r in rtts:
        burst = burst + 1 if r is None else 0
        longest = max(longest, burst)

    loss = 100.0 * (sent - len(got)) / sent
    p99 = _pct(s, 0.99)
    score, grade, findings = _grade(loss, jitter, median, p99, spikes, len(got),
                                    longest, rate_hz)
    return Summary(
        sent, len(got), loss, s[0], statistics.fmean(s), median, _pct(s, 0.95),
        p99, s[-1], statistics.pstdev(s), jitter, spikes, spike_thr, longest,
        score, grade, findings,
    )


def _grade(loss, jitter, median, p99, spikes, received, burst, rate_hz):
    findings = []
    score = 100.0

    score -= min(50, loss * 12)
    if loss >= 2:
        findings.append(f"Heavy packet loss ({loss:.1f}%): expect rubber-banding "
                        "and shots that don't register.")
    elif loss >= 0.5:
        findings.append(f"Noticeable packet loss ({loss:.1f}%): occasional "
                        "teleporting players / missed inputs.")
    elif loss > 0:
        findings.append(f"Minor packet loss ({loss:.2f}%).")

    if burst * 1000 / rate_hz >= 250:
        score -= 10
        findings.append(f"Connection dropped for ~{burst * 1000 / rate_hz:.0f} ms "
                        "in a row at least once (lag spike / freeze).")

    score -= min(25, max(0.0, jitter - 2) * 2.5)
    if jitter >= 10:
        findings.append(f"High jitter ({jitter:.1f} ms): aim and movement will "
                        "feel inconsistent.")
    elif jitter >= 4:
        findings.append(f"Moderate jitter ({jitter:.1f} ms).")

    spike_pct = 100 * spikes / max(1, received)
    score -= min(15, spike_pct * 3)
    if spike_pct >= 1:
        findings.append(f"{spikes} latency spikes ({spike_pct:.1f}% of packets).")

    tail = p99 - median
    score -= min(10, max(0.0, tail - 15) * 0.3)
    if tail > 30:
        findings.append(f"Unstable tail latency: 1% of packets were "
                        f"{tail:.0f}+ ms slower than usual.")

    if median > 90:
        score -= min(10, (median - 90) * 0.2)
        findings.append(f"High base ping ({median:.0f} ms) - consider a closer server.")

    score = max(0, round(score))
    grade = ("A" if score >= 90 else "B" if score >= 75 else "C" if score >= 60
             else "D" if score >= 40 else "F")
    if not findings:
        findings.append("Stable connection - no problems found.")
    return score, grade, findings
