"""Server discovery and selection.

For Valve games the server list comes from Steam's public SDR config, which
lists every relay point of presence (POP) with relay IPs. CS2 matchmaking
traffic goes through these relays, and they answer ICMP echo, so pinging them
measures the same path the game uses.
"""

from __future__ import annotations

import json
import os
import statistics
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import requests

from . import icmp
from .profiles import GameProfile

SDR_URL = "https://api.steampowered.com/ISteamApps/GetSDRConfig/v1/?appid={appid}"
CACHE_DIR = Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "netstab"
CACHE_MAX_AGE = 24 * 3600


@dataclass
class Server:
    code: str
    name: str
    relays: list[str]
    ping_ms: float | None = None  # median probe RTT, None if unreachable
    best_relay: str | None = None
    extra: dict = field(default_factory=dict)

    @property
    def label(self) -> str:
        p = f"{self.ping_ms:.0f} ms" if self.ping_ms is not None else "no reply"
        return f"{self.name} [{self.code}] - {p}"


def _load_sdr_config(appid: int) -> dict:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache = CACHE_DIR / f"sdr_{appid}.json"
    try:
        r = requests.get(SDR_URL.format(appid=appid), timeout=8)
        r.raise_for_status()
        data = r.json()
        cache.write_text(json.dumps(data), encoding="utf-8")
        return data
    except (requests.RequestException, ValueError):
        if cache.exists():
            return json.loads(cache.read_text(encoding="utf-8"))
        raise


def list_servers(profile: GameProfile) -> list[Server]:
    if profile.provider != "valve_sdr":
        raise NotImplementedError(f"No server list for {profile.name} yet")
    data = _load_sdr_config(profile.steam_appid)
    servers = []
    for code, pop in data.get("pops", {}).items():
        relays = [r["ipv4"] for r in pop.get("relays", []) if r.get("ipv4")]
        if not relays:
            continue  # backbone-only POP, no game servers behind it
        if pop.get("partners", 0) == 2:
            continue  # Perfect World (China) POPs, not reachable from global MM
        servers.append(Server(code, pop.get("desc", code), relays))
    return servers


def _probe(ip: str, count: int = 3) -> float | None:
    rtts = []
    for _ in range(count):
        r = icmp.echo(ip, 32, timeout_ms=800)
        if r.ok:
            rtts.append(r.rtt_ms)
    return statistics.median(rtts) if rtts else None


def probe_servers(
    servers: list[Server],
    progress: Callable[[int, int], None] | None = None,
) -> list[Server]:
    """Ping every server's relays (in parallel) and fill ping_ms/best_relay."""
    jobs = [(s, ip) for s in servers for ip in s.relays[:2]]
    done = 0
    with ThreadPoolExecutor(max_workers=48) as ex:
        futs = {ex.submit(_probe, ip): (s, ip) for s, ip in jobs}
        for fut in futs:
            s, ip = futs[fut]
            rtt = fut.result()
            if rtt is not None and (s.ping_ms is None or rtt < s.ping_ms):
                s.ping_ms, s.best_relay = rtt, ip
            done += 1
            if progress:
                progress(done, len(jobs))
    servers.sort(key=lambda s: (s.ping_ms is None, s.ping_ms or 0))
    return servers


def auto_select(servers: list[Server], max_ping: float | None = None) -> Server | None:
    """Pick the server matchmaking would most likely put you on: the lowest
    ping relay, optionally restricted to ones under max_ping
    (like `mm_dedicated_search_maxping`)."""
    reachable = [s for s in servers if s.ping_ms is not None]
    if max_ping is not None:
        reachable = [s for s in reachable if s.ping_ms <= max_ping]
    return min(reachable, key=lambda s: s.ping_ms) if reachable else None


def within(servers: list[Server], max_ping: float) -> list[Server]:
    return [s for s in servers if s.ping_ms is not None and s.ping_ms <= max_ping]


if __name__ == "__main__":
    from .profiles import get

    t = time.perf_counter()
    ss = probe_servers(list_servers(get("cs2")))
    for s in ss[:10]:
        print(s.label, s.best_relay)
    print(f"{len(ss)} servers in {time.perf_counter() - t:.1f}s")
