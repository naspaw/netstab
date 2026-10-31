import math
import time

import pytest

from netstab import advice, compare, detailed, engine, icmp, metered, profiles, servers
from netstab.detailed import DetailedOptions, DetailedResult, Hop, LoadPhase
from netstab.stats import summarize

CS2 = profiles.get("cs2")


# ------------------------------------------------------------------ stats --
def test_summary_perfect_connection():
    s = summarize([30.0] * 640, 64)
    assert s.loss_pct == 0 and s.jitter_ms == 0 and s.spikes == 0
    assert s.grade == "A" and s.score == 100
    assert s.findings == ["Stable connection - no problems found."]


def test_summary_loss_burst_and_spikes():
    rtts = [40.0] * 600
    rtts[100:120] = [None] * 20  # 20 lost in a row at 64 Hz = ~312 ms
    rtts[300] = 140.0
    s = summarize(rtts, 64)
    assert s.longest_loss_burst == 20
    assert s.spikes == 1
    assert s.loss_pct == pytest.approx(100 * 20 / 600)
    assert s.grade in "CDF"
    assert any("dropped" in f for f in s.findings)


def test_summary_no_replies():
    s = summarize([None] * 50, 64)
    assert s.received == 0 and s.loss_pct == 100 and s.grade == "F"
    assert math.isnan(s.median_ms)


def test_jitter_is_mean_consecutive_delta():
    s = summarize([10.0, 20.0, 10.0, 20.0], 64)
    assert s.jitter_ms == pytest.approx(10.0)


# ----------------------------------------------------------------- engine --
def test_load_test_sends_at_rate_and_records_in_order(monkeypatch):
    calls = []

    def fake_echo(ip, size, timeout_ms, **kw):
        calls.append(size)
        n = len(calls)
        return icmp.EchoResult(icmp.IP_REQ_TIMED_OUT if n % 10 == 0 else icmp.IP_SUCCESS,
                               None if n % 10 == 0 else 25.0, ip)

    monkeypatch.setattr(engine.icmp, "echo", fake_echo)
    t = engine.LoadTest("1.2.3.4", rate_hz=50, payload_bytes=120, duration_s=1,
                        timeout_ms=100)
    start = time.perf_counter()
    t.start()
    assert t.finished.wait(3)
    time.sleep(0.2)
    assert 0.9 < time.perf_counter() - start < 2.5
    snap = t.snapshot()
    assert len(snap) == 50 and set(calls) == {120}
    assert snap.count(None) == 5
    assert t.summary().loss_pct == pytest.approx(10.0)


def test_load_test_stop(monkeypatch):
    monkeypatch.setattr(engine.icmp, "echo",
                        lambda *a, **k: icmp.EchoResult(0, 10.0, "x"))
    t = engine.LoadTest("1.2.3.4", 64, 32, 30)
    t.start()
    time.sleep(0.3)
    t.stop()
    assert t.finished.wait(2)
    assert t.cancelled and t.sent < 64


# ---------------------------------------------------------------- servers --
SDR = {"pops": {
    "fra": {"desc": "Frankfurt", "relays": [{"ipv4": "1.1.1.1"}, {"ipv4": "1.1.1.2"}]},
    "sto": {"desc": "Stockholm", "relays": [{"ipv4": "2.2.2.2"}]},
    "ctu": {"desc": "Chengdu", "partners": 2, "relays": [{"ipv4": "3.3.3.3"}]},
    "iad": {"desc": "Backbone only"},
}}


def test_list_and_select_servers(monkeypatch):
    monkeypatch.setattr(servers, "_load_sdr_config", lambda appid: SDR)
    pings = {"1.1.1.1": 40.0, "1.1.1.2": 35.0, "2.2.2.2": 95.0}
    monkeypatch.setattr(servers, "_probe", lambda ip, count=3: pings.get(ip))
    ss = servers.list_servers(CS2)
    assert {s.code for s in ss} == {"fra", "sto"}  # China + relay-less POPs skipped
    servers.probe_servers(ss)
    assert ss[0].code == "fra" and ss[0].best_relay == "1.1.1.2" and ss[0].ping_ms == 35
    assert servers.auto_select(ss).code == "fra"
    assert servers.auto_select(ss, max_ping=30) is None
    assert [s.code for s in servers.within(ss, 100)] == ["fra", "sto"]


def test_unavailable_profile_has_no_servers():
    with pytest.raises(NotImplementedError):
        servers.list_servers(profiles.get("bf6"))


# --------------------------------------------------------------- detailed --
def test_estimate_and_cap():
    o = DetailedOptions(load="standard")
    assert detailed.estimate_mb(o, 80, 16) == pytest.approx((80 + 16) * 11.5 / 8)
    assert detailed.estimate_mb(DetailedOptions(load="off"), 80, 16) == 0
    assert detailed.estimate_mb(DetailedOptions(load="hard", max_mb=50), 500, 100) == 50
    t = detailed.DetailedTest("1.2.3.4", DetailedOptions(load="hard", max_mb=100))
    assert t.streams == 8 and t.load_seconds == 20 and t.phase_cap == 50_000_000


def test_budget_never_hands_out_more_than_the_cap():
    b = detailed._Budget(10_000_000, 4)
    got = []
    while n := b.take():
        got.append(n)
    allowed = 10_000_000 / detailed._Budget.OVERHEAD
    assert allowed * 0.95 < sum(got) <= allowed  # uses nearly all of it, never more
    assert len(got) >= 4  # every stream gets a share
    assert detailed._Budget(None, 4).take() == detailed.MAX_REQUEST_BYTES


class _FakeServer:
    """Stands in for requests.Session: downloads return exactly ?bytes=N."""

    sent = 0
    received = 0

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def get(self, url, **kw):
        n = int(url.rsplit("bytes=", 1)[1])

        class Resp:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def raise_for_status(self):
                pass

            def iter_content(self, size):
                left = n
                while left:
                    piece = min(size, left)
                    left -= piece
                    _FakeServer.sent += piece
                    yield b"\0" * piece

        return Resp()

    def post(self, url, data, **kw):
        for piece in data:
            _FakeServer.received += len(piece)


@pytest.mark.parametrize("upload", [False, True])
def test_load_phase_respects_data_cap(monkeypatch, upload):
    _FakeServer.sent = _FakeServer.received = 0
    monkeypatch.setattr(detailed.requests, "Session", _FakeServer)
    monkeypatch.setattr(detailed, "RAMP_SECONDS", 0.05)
    monkeypatch.setattr(detailed.icmp, "echo",
                        lambda *a, **k: icmp.EchoResult(0, 20.0, "x"))
    t = detailed.DetailedTest("1.2.3.4", DetailedOptions(load="hard", max_mb=8))
    start = time.perf_counter()
    t._saturate(upload=upload)
    wire = _FakeServer.received if upload else _FakeServer.sent
    assert 0 < wire <= 4_000_000  # half the 8 MB cap per direction
    assert t.result.data_mb * 1e6 <= 4_000_000
    assert time.perf_counter() - start < 5  # stops when the budget is spent


def test_run_skips_disabled_stages(monkeypatch):
    t = detailed.DetailedTest("1.2.3.4", DetailedOptions(route_seconds=0, mtu=False,
                                                         load="off"))
    for name in ("_trace", "_mtr", "_mtu", "_bufferbloat"):
        monkeypatch.setattr(t, name, lambda *a: pytest.fail(f"{name} ran"))
    r = t.run(lambda *a: None)
    assert r.findings == ["No problems found on the route."]


def _hop(ttl, ip, rtts, sent=20):
    h = Hop(ttl, ip, sent)
    h.rtts = rtts
    return h


def test_analyse_wifi_loss_and_bloat():
    t = detailed.DetailedTest("9.9.9.9")
    t.result.hops = [_hop(1, "192.168.1.1", [9.0, 2.0] * 5, sent=10),
                     _hop(2, "10.0.0.1", [20.0] * 18),
                     _hop(3, "9.9.9.9", [60.0] * 18)]
    t.result.idle = LoadPhase("Idle", 60, 0, 0)
    t.result.download = LoadPhase("Download", 200, 0, 90)
    t._analyse()
    text = " ".join(t.result.findings)
    assert "Wi-Fi" in text and "Bufferbloat" in text and "starts at hop 2" in text
    assert t.result.bloat_grade == "D"


# ----------------------------------------------------------------- advice --
def test_advice_good_connection():
    v = advice.build(CS2, summarize([25.0] * 640, 64), "Frankfurt")
    assert v.level == advice.GOOD
    assert "Ready to play" in v.headline
    assert any("mm_dedicated_search_maxping 50" in t for t in v.tips)


def test_advice_bad_connection_mentions_problems():
    rtts = [130.0 + (i % 7) * 6 for i in range(640)]
    for i in range(0, 640, 20):
        rtts[i] = None
    d = DetailedResult("x", hops=[_hop(1, "192.168.1.1", [12.0, 3.0] * 5)],
                       idle=LoadPhase("Idle", 130, 0, 0),
                       download=LoadPhase("Download", 300, 0, 50))
    v = advice.build(CS2, summarize(rtts, 64), "Far", d, best_other_ping=60,
                     metered=True)
    assert v.level == advice.BAD
    names = {a.name: a.level for a in v.aspects}
    assert names["Ping"] == advice.BAD and names["Packet loss"] == advice.BAD
    assert names["When the line is busy"] == advice.BAD
    tips = " ".join(v.tips)
    for needle in ("Ethernet", "cl_net_buffer_ticks", "SQM", "Auto mode", "metered"):
        assert needle in tips


def test_advice_many_spikes_is_unstable_even_with_moderate_jitter():
    # The real run that exposed this: 9.8 ms jitter but 12% of packets spiking.
    rtts = [80.0 + (i % 5) for i in range(1920)]
    for i in range(0, 1920, 8):
        rtts[i] = 160.0
    s = summarize(rtts, 64)
    assert s.jitter_ms < 30 and s.spikes / s.received > 0.1
    v = advice.build(CS2, s, "Amsterdam")
    stab = next(a for a in v.aspects if a.name == "Stability")
    assert stab.level == advice.BAD and "spiked" in stab.comment
    assert v.level == advice.BAD and v.headline.startswith("Expect problems")


# ---------------------------------------------------------------- compare --
def _run(label, score, median, jitter, loss, server="fra", detailed=None):
    s = summarize([median] * 640, 64).to_dict()
    s.update(score=score, median_ms=median, jitter_ms=jitter, loss_pct=loss)
    return compare.Run(label, label, "2026-10-30T12:00:00", "cs2", "Counter-Strike 2",
                       server, server, 60, s, detailed)


def test_compare_picks_better_network():
    home = _run("Home Wi-Fi", 72, 85, 7.0, 0.8)
    cable = _run("Cable", 96, 70, 1.2, 0.0)
    c = compare.compare([home, cable])
    assert c.winner == 1 and "Cable is the better network" in c.summary
    rows = {r.metric.name: r for r in c.rows}
    assert rows["Jitter"].best == [1] and rows["Ping (median)"].best == [1]
    assert "Download" not in rows  # nobody ran the load test
    assert any(x.startswith("Jitter") for x in c.reasons)
    assert c.notes == []


def test_compare_tie_and_server_note():
    a = _run("A", 90, 50, 2.0, 0.0, server="fra")
    b = _run("B", 91, 52, 2.1, 0.0, server="ams")
    c = compare.compare([a, b])
    assert c.winner is None and "tie" in c.summary
    assert any("different servers" in n for n in c.notes)


def test_compare_history_roundtrip(tmp_path):
    p = tmp_path / "h.json"
    runs = [_run(f"n{i}", 80, 50, 2, 0) for i in range(compare.MAX_RUNS + 5)]
    compare.save(runs, p)
    back = compare.load(p)
    assert len(back) == compare.MAX_RUNS and back[-1].label == runs[-1].label
    p.write_text("not json")
    assert compare.load(p) == []


# ---------------------------------------------------------------- metered --
@pytest.mark.parametrize("cost,is_metered", [
    (metered.ConnectionCost("Home", "Unrestricted"), False),
    (metered.ConnectionCost("Phone", "Fixed", approaching_limit=True), True),
    (metered.ConnectionCost("Roam", "Unrestricted", roaming=True), True),
    (None, False),  # offline
])
def test_metered_flags(monkeypatch, cost, is_metered):
    monkeypatch.setattr(metered, "_query", lambda: cost)
    c = metered.check()
    assert c.metered is is_metered
    if cost and cost.profile == "Phone":
        assert "close to its data limit" in c.description


def test_metered_never_raises(monkeypatch):
    def boom():
        raise OSError("WinRT unavailable")
    monkeypatch.setattr(metered, "_query", boom)
    assert metered.check().cost_type == "Unknown"


def test_metered_real_api_answers():
    # Runs the real in-process WinRT query (no PowerShell or other program).
    assert metered._query() is None or metered.check().cost_type != ""
