"""Smoke test of a built netstab.exe: `netstab.exe --selftest <out_dir>`.

Exercises every capability from inside the packaged app (so missing bundled
files show up) and writes selftest.json plus a screenshot to out_dir. Exit
code 0 means every check passed. Uses well under 1 MB of data.

`--selftest <out_dir> --no-ping` is for cloud build machines (e.g. GitHub
Actions), which block ICMP: the ping-based checks are skipped and the rest use
a sample result.
"""

from __future__ import annotations

import json
import sys
import time
import traceback
from pathlib import Path


def run(out_dir: str, no_ping: bool = False) -> int:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    results: list[dict] = []

    def check(name, fn):
        t = time.perf_counter()
        try:
            info = fn()
            results.append({"check": name, "ok": True, "info": str(info)})
        except Exception as e:  # noqa: BLE001 - recorded, not raised
            results.append({"check": name, "ok": False, "info": repr(e),
                            "trace": traceback.format_exc()})
        results[-1]["seconds"] = round(time.perf_counter() - t, 2)

    ctx: dict = {}

    def startup_splash():
        from . import splash
        if getattr(sys, "frozen", False):
            assert splash.present(), "exe was built without its splash screen"
        splash.update("Running self-test...")
        splash.close()
        return "bundled and closed" if splash.present() else "not frozen; no splash"

    def imports():
        import importlib
        for mod in ("requests", "certifi",
                    "winrt.windows.networking.connectivity", "netstab.gui"):
            importlib.import_module(mod)
        return f"frozen={getattr(sys, 'frozen', False)}"

    def metered_check():
        from . import metered
        c = metered.check()
        if not no_ping:  # build VMs often have no connection profile to report
            assert c.cost_type != "Unknown", "WinRT metered query returned Unknown"
        return c.description

    def server_list():
        from . import profiles, servers
        ss = servers.list_servers(profiles.get("cs2"))
        if no_ping:  # list only (HTTPS); stand in a sample result for later checks
            from .stats import summarize
            assert ss, "empty server list"
            ctx["server"] = ss[0]
            ctx["summary"] = summarize([40.0 + (i % 7) for i in range(320)], 64)
            return f"{len(ss)} servers listed (not pinged: --no-ping)"
        ss = servers.probe_servers(ss)
        best = servers.auto_select(ss)
        assert best is not None, "no reachable CS2 server"
        ctx["server"] = best
        return f"{len(ss)} servers, best {best.label}"

    def load_test():
        from .engine import LoadTest
        t = LoadTest(ctx["server"].best_relay, 64, 120, 5)
        t.start()
        t.finished.wait(10)
        time.sleep(1.1)
        s = t.summary()
        assert s.received > 0, "no replies"
        ctx["summary"] = s
        return f"{s.received}/{s.sent} replies, median {s.median_ms:.0f} ms, grade {s.grade}"

    def route_and_mtu():
        from .detailed import DetailedOptions, DetailedTest
        d = DetailedTest(ctx["server"].best_relay,
                         DetailedOptions(route_seconds=3, mtu=True, load="off"))
        r = d.run(lambda *a: None)
        assert r.hops, "no hops traced"
        return f"{len(r.hops)} hops, MTU {r.mtu}"

    def https_speedtest_endpoint():
        import requests

        from .detailed import DOWNLOAD_BASE
        url = DOWNLOAD_BASE + "?bytes=1000"
        r = requests.get(url, timeout=10)  # proves bundled TLS certificates work
        r.raise_for_status()
        return f"{len(r.content)} bytes over HTTPS"

    def advice_and_compare():
        from . import advice, compare, profiles
        v = advice.build(profiles.get("cs2"), ctx["summary"], ctx["server"].name)
        run_ = compare.Run("a", "a", "2026-01-01T00:00:00", "cs2", "CS2", "x", "x", 5,
                           ctx["summary"].to_dict())
        compare.compare([run_, run_])
        return v.headline

    def window():
        from PySide6.QtWidgets import QApplication

        from . import gui
        app = QApplication.instance() or QApplication(sys.argv[:1])
        from PySide6.QtGui import QIcon
        icon = QIcon(str(gui.ICON_PATH))
        assert not icon.isNull(), f"icon missing at {gui.ICON_PATH}"
        app.setWindowIcon(icon)
        app.setStyleSheet(gui.make_style())
        w = gui.MainWindow()
        w.resize(1280, 860)
        w.show()
        for _ in range(20):
            app.processEvents()
            time.sleep(0.05)
        for i, name in enumerate(("stability", "detailed", "compare")):
            w.tabs.setCurrentIndex(i)
            app.processEvents()
            w.grab().save(str(out / f"selftest_{name}.png"))
        dlg = gui.AboutDialog(w)
        dlg.show()
        app.processEvents()
        dlg.grab().save(str(out / "selftest_privacy.png"))
        dlg.close()
        w.close()
        return "window, tabs and privacy dialog rendered"

    for name, fn in [("splash screen", startup_splash), ("imports", imports),
                     ("metered check (WinRT)", metered_check),
                     ("server list" if no_ping else "server list + ping", server_list),
                     ("64 Hz load test", load_test),
                     ("route + MTU", route_and_mtu),
                     ("HTTPS to speed test", https_speedtest_endpoint),
                     ("advice + compare", advice_and_compare), ("UI", window)]:
        if no_ping and name in ("64 Hz load test", "route + MTU"):
            results.append({"check": name, "ok": True, "info": "skipped: --no-ping"})
            continue
        if name in ("64 Hz load test", "route + MTU", "advice + compare") \
                and "server" not in ctx:
            results.append({"check": name, "ok": False, "info": "skipped: no server"})
            continue
        if name == "advice + compare" and "summary" not in ctx:
            results.append({"check": name, "ok": False, "info": "skipped: no summary"})
            continue
        check(name, fn)

    ok = all(r["ok"] for r in results)
    (out / "selftest.json").write_text(json.dumps({"ok": ok, "results": results},
                                                  indent=2), encoding="utf-8")
    return 0 if ok else 1
