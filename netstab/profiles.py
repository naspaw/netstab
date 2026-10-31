"""Game profiles: how each game's traffic looks and where its servers are.

`rate_hz` and `payload_bytes` shape the load the test applies so it resembles
the game's own packet stream (CS2 exchanges ~64 packets/s each way).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class GameProfile:
    key: str
    name: str
    rate_hz: int  # packets per second sent during the test
    payload_bytes: int  # ICMP payload size, close to a typical game packet
    provider: str  # how servers are discovered: "valve_sdr" or "" (not yet)
    steam_appid: int | None = None
    default_max_ping: int = 90
    note: str = ""

    @property
    def available(self) -> bool:
        return bool(self.provider)


PROFILES: list[GameProfile] = [
    GameProfile(
        "cs2", "Counter-Strike 2", rate_hz=64, payload_bytes=120,
        provider="valve_sdr", steam_appid=730, default_max_ping=90,
        note="64 packets/s through Valve's SDR relays, like a matchmaking game.",
    ),
    GameProfile(
        "steam", "Steam (Valve network)", rate_hz=30, payload_bytes=96,
        provider="valve_sdr", steam_appid=570, default_max_ping=120,
        note="Valve relay network used by Steam Datagram Relay games.",
    ),
    GameProfile("bf6", "Battlefield 6", 60, 160, "", note="Coming soon"),
    GameProfile("gta", "GTA Online", 30, 140, "", note="Coming soon"),
    GameProfile("lol", "League of Legends", 30, 100, "", note="Coming soon"),
    GameProfile("delta", "Delta Force", 60, 140, "", note="Coming soon"),
    GameProfile("rust", "Rust", 30, 140, "", note="Coming soon"),
]

DURATIONS = [30, 60, 120, 300]


def get(key: str) -> GameProfile:
    for p in PROFILES:
        if p.key == key:
            return p
    raise KeyError(key)
