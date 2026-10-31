"""Command-line runner (handy for scripting and debugging the engine).

    python -m netstab.cli --game cs2 --duration 30 --max-ping 90 [--detailed]
"""

from __future__ import annotations

import argparse
import json
import sys
import time

from . import profiles, servers
from .detailed import DetailedTest
from .engine import LoadTest


def main() -> int:
    ap = argparse.ArgumentParser(prog="netstab")
    ap.add_argument("--game", default="cs2", choices=[p.key for p in profiles.PROFILES])
    ap.add_argument("--duration", type=int, default=30)
    ap.add_argument("--max-ping", type=float, default=None)
    ap.add_argument("--server", help="server code, e.g. fra (default: auto)")
    ap.add_argument("--detailed", action="store_true")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()

    prof = profiles.get(a.game)
    ss = servers.probe_servers(servers.list_servers(prof))
    if a.server:
        srv = next((s for s in ss if s.code == a.server), None)
    else:
        srv = servers.auto_select(ss, a.max_ping)
    if srv is None:
        print("No server matches.", file=sys.stderr)
        return 1
    print(f"Server: {srv.label} via {srv.best_relay}", file=sys.stderr)

    test = LoadTest(srv.best_relay, prof.rate_hz, prof.payload_bytes, a.duration)
    test.start()
    while not test.finished.wait(1):
        print(f"\r{test.elapsed():5.1f}s  sent {test.sent}", end="", file=sys.stderr)
    time.sleep(1.1)  # let the last replies arrive
    summ = test.summary()
    print(file=sys.stderr)
    out = {"server": srv.code, "summary": summ.to_dict()}

    if a.detailed:
        det = DetailedTest(srv.best_relay)
        res = det.run(lambda st, f, m: print(f"\r[{st}] {m:60}", end="", file=sys.stderr))
        print(file=sys.stderr)
        out["detailed"] = res.to_dict()

    print(json.dumps(out, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
