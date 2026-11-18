import time

import pytest

from netstab import icmp, monitor
from netstab.monitor import AlertTracker, PingMonitor, summarize


def test_summarize_basic_and_losses_at_end():
    s = summarize([(0, 40.0), (0.5, 44.0), (1, None), (1.5, 42.0), (2, None), (2.5, None)])
    assert s.count == 6 and s.loss_pct == pytest.approx(50)
    assert s.last_ms is None and s.lost_in_row == 2
    assert s.min_ms == 40 and s.max_ms == 44 and s.jitter_ms == pytest.approx(3.0)


def test_summarize_empty_and_all_lost():
    assert summarize([]).loss_pct == 0 and summarize([]).count == 0
    s = summarize([(0, None), (1, None)])
    assert s.loss_pct == 100 and s.avg_ms is None and s.lost_in_row == 2


def test_alert_high_ping_then_cooldown():
    a = AlertTracker(limit_ms=50, hold_s=3, cooldown=60)
    high = summarize([(i * 0.5, 80.0) for i in range(6)])
    assert "High ping" in a.check(high, now=100, interval=0.5)
    assert a.check(high, now=130, interval=0.5) is None  # still in cooldown
    assert a.check(high, now=161, interval=0.5) is not None


def test_alert_needs_enough_samples_and_detects_drops():
    a = AlertTracker(limit_ms=50, hold_s=3)
    assert a.check(summarize([(0, 90.0)]), now=0, interval=0.5) is None  # one blip
    drop = summarize([(i * 0.5, None) for i in range(6)])
    assert "dropping" in a.check(drop, now=10, interval=0.5)


def test_resolve_accepts_urls_and_ips():
    assert monitor.resolve("1.1.1.1") == "1.1.1.1"
    assert monitor.resolve("https://1.1.1.1/path?q=1") == "1.1.1.1"
    with pytest.raises(monitor.TargetError):
        monitor.resolve("   ")


def test_monitor_keeps_send_order_with_out_of_order_replies(monkeypatch):
    calls = [0]

    def fake_echo(ip, size, timeout_ms, **kw):
        calls[0] += 1
        n = calls[0]
        time.sleep(0.15 if n % 2 else 0.0)  # odd pings answer later than even ones
        return icmp.EchoResult(icmp.IP_SUCCESS, float(n), ip)

    monkeypatch.setattr(monitor.icmp, "echo", fake_echo)
    m = PingMonitor("1.2.3.4", interval=0.05, timeout_ms=300)
    m.start()
    time.sleep(0.8)
    m.stop()
    time.sleep(0.3)
    hist = m.history()
    assert len(hist) >= 8
    times = [t for t, _ in hist]
    assert times == sorted(times)  # published strictly in send order


def test_monitor_pause_and_errors(monkeypatch):
    def boom(*a, **k):
        raise OSError("no ICMP handle")

    monkeypatch.setattr(monitor.icmp, "echo", boom)
    m = PingMonitor("1.2.3.4", interval=0.05, timeout_ms=100)
    m.start()
    time.sleep(0.3)
    assert m.history() and all(r is None for _, r in m.history())  # counted as lost
    m.pause()
    time.sleep(0.15)  # let in-flight pings land
    n = len(m.history())
    time.sleep(0.3)
    assert len(m.history()) == n  # nothing sent while paused
    m.resume()
    time.sleep(0.3)
    assert len(m.history()) > n
    m.stop()
