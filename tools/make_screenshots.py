"""Makes the README screenshots from the real app window, filled with SAMPLE data.

    python tools/make_screenshots.py [out_dir]   (default: docs/screenshots)

Nothing is measured and nothing goes over the network, so no personal data
can end up in the images: network names are invented, and every IP address
is from the ranges reserved for documentation (192.0.2.0/24, 198.51.100.0/24,
203.0.113.0/24, RFC 5737), which never belong to anyone.
"""

from __future__ import annotations

import random
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

from netstab import compare, gui, metered, profiles, servers, settings  # noqa: E402
from netstab.detailed import (DetailedOptions, DetailedTest, Hop,  # noqa: E402
                              LoadPhase)
from netstab.stats import summarize  # noqa: E402

OUT = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "docs" / "screenshots"
OUT.mkdir(parents=True, exist_ok=True)
TMP = Path(tempfile.mkdtemp(prefix="netstab-shots-"))
compare.HISTORY_PATH = TMP / "history.json"  # never touch the user's own files
settings._PATH = TMP / "settings.json"

SAMPLE_NET = "Home Wi-Fi"
metered.check = lambda: metered.ConnectionCost(SAMPLE_NET, "Unrestricted")
QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.No)

RELAY = "203.0.113.24"  # documentation address standing in for a Valve relay
SERVERS = [  # (code, name, ping ms)
    ("fra", "Frankfurt (Germany)", 38), ("vie", "Vienna (Austria)", 41),
    ("ams", "Amsterdam (Netherlands)", 44), ("par", "Paris (France)", 47),
    ("waw", "Warsaw (Poland)", 49), ("lhr", "London (England)", 52),
    ("sto", "Stockholm (Sweden)", 61), ("mad", "Madrid (Spain)", 68),
]


def sample_rtts(seconds: int, rate: int, seed: int, base: float, wobble: float,
                spikes: list[tuple[float, float, float]] = (),
                losses: list[tuple[float, int]] = ()) -> list[float | None]:
    """A believable ping trace: base +- wobble, with spike windows
    (start s, end s, extra ms) and loss bursts (at s, packets)."""
    rnd = random.Random(seed)
    out: list[float | None] = []
    for i in range(seconds * rate):
        t = i / rate
        v = base + abs(rnd.gauss(0, wobble))
        for start, end, extra in spikes:
            if start <= t < end:
                v += extra * (0.5 + rnd.random())
        out.append(v)
    for at, n in losses:
        k = int(at * rate)
        out[k:k + n] = [None] * n
    return out


class SampleTest:
    """Stands in for engine.LoadTest with a finished, pre-made trace."""

    timeout_ms = 1000
    cancelled = False

    def __init__(self, rtts, rate_hz, duration):
        self.rtts, self.rate_hz, self.duration = rtts, rate_hz, duration
        self.sent = len(rtts)
        self.finished = threading.Event()
        self.finished.set()

    def snapshot(self):
        return self.rtts

    def elapsed(self):
        return self.duration

    def summary(self):
        return summarize(self.rtts, self.rate_hz)

    def stop(self):
        pass


def seed_history() -> None:
    """Two earlier tests on other networks, for the Compare tab."""
    def run(label, when, rtts):
        s = summarize(rtts, 64).to_dict()
        return compare.Run(label, label, when, "cs2", "Counter-Strike 2", "fra",
                           "Frankfurt (Germany)", 60, s)
    compare.save([
        run("Phone hotspot", "2026-10-30T18:12:00",
            sample_rtts(60, 64, 7, 58, 9, spikes=[(12, 16, 70), (41, 43, 120)],
                        losses=[(20, 14), (33, 9), (50, 11)])),
        run("Ethernet cable", "2026-10-30T18:25:00",
            sample_rtts(60, 64, 8, 36.5, 0.8)),
    ])


def hop(ttl, ip, base, jitter, seed, lost=0.0, sent=60):
    rnd = random.Random(seed)
    h = Hop(ttl, ip, sent)
    h.rtts = [] if ip is None else [base + abs(rnd.gauss(0, jitter))
                                    for _ in range(sent) if rnd.random() >= lost]
    return h


def sample_detail():
    d = DetailedTest(RELAY, DetailedOptions(route_seconds=30, mtu=True, load="standard"))
    r = d.result
    r.hops = [
        hop(1, "192.168.1.1", 3.1, 3.6, 1),           # home router over Wi-Fi
        hop(2, "198.51.100.1", 11.0, 2.2, 2),
        hop(3, "198.51.100.14", 12.4, 2.0, 3),
        hop(4, None, 0, 0, 4),                          # router that ignores pings
        hop(5, "203.0.113.9", 21.8, 2.1, 5, lost=0.4),  # deprioritises ICMP; harmless
        hop(6, "203.0.113.70", 34.9, 2.4, 6),
        hop(7, RELAY, 38.6, 2.6, 7),
    ]
    for h in r.hops:
        if h.ip is None:
            h.sent = 60
    r.mtu = 1500
    r.idle = LoadPhase("Idle", 39.2, 0.0, 0.0)
    r.download = LoadPhase("Download", 91.0, 0.0, 186.0)
    r.upload = LoadPhase("Upload", 50.5, 0.0, 38.0)
    d._analyse()
    return d


def main() -> int:
    seed_history()
    settings.update(theme="dark", down_mbps=186.0, up_mbps=38.0)
    app = QApplication(sys.argv)
    app.setStyleSheet(gui.make_style())
    w = gui.MainWindow()
    w.resize(1400, 900)
    w.show()
    app.processEvents()

    prof = profiles.get("cs2")
    sample = [servers.Server(code, name, [RELAY], ms, RELAY) for code, name, ms in SERVERS]
    w._on_scan_done((prof.key, sample), None)

    # A finished 60 s CS2 test on "Home Wi-Fi": mostly steady, one rough patch.
    rtts = sample_rtts(60, 64, 3, 37.5, 2.4, spikes=[(33.5, 36.0, 55)],
                       losses=[(35.0, 7)])
    t = SampleTest(rtts, prof.rate_hz, 60)
    w.server, w.tested, w.test = sample[0], prof, t
    w.summary = w.detail_result = w.verdict_data = None
    w._xs, w._ys, w._loss_x, w._sec_sum = [], [], [], {}
    w._consumed, w._ticks, w._live = 0, 0, None
    w.plot.reset(60)
    w.progress.setRange(0, 60_000)
    w.dur_group.button(60).setChecked(True)
    w.verdict.message("", prof.name)  # what starting a real test sets
    w._tick()
    w.test_done_at = time.perf_counter()
    w._finish_test()

    # The detailed test for the same server.
    d = sample_detail()
    w.detail, w.detail_opts = d, d.opts
    w._on_detail_done(d.result)
    app.processEvents()

    def shot(name, widget):
        app.processEvents()
        widget.grab().save(str(OUT / f"{name}.png"))
        print("wrote", OUT / f"{name}.png")

    for i, name in enumerate(("stability", "detailed", "compare")):
        w.tabs.setCurrentIndex(i)
        shot(f"{name}-dark", w)
    w.theme_combo.setCurrentIndex(w.theme_combo.findData("light"))
    w.tabs.setCurrentIndex(0)
    shot("stability-light", w)
    w.theme_combo.setCurrentIndex(w.theme_combo.findData("dark"))

    dlg = gui.DetailOptionsDialog(w, "Frankfurt (Germany)",
                                  metered.ConnectionCost("Phone hotspot", "Fixed"),
                                  (186.0, 38.0), True, {})
    for b in dlg.load_group.buttons():
        b.setChecked(b.property("key") == "standard")
    dlg.cap.setValue(100)
    dlg.show()
    shot("metered-approval", dlg)
    dlg.close()

    about = gui.AboutDialog(w)
    about.show()
    shot("privacy", about)
    about.close()
    w.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
