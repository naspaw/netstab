"""Translate measurements into "what will playing feel like" and what to do.

Numbers alone don't tell a player much; this module turns them into short,
game-aware comments (e.g. peeker's advantage at high ping in CS2) and
concrete tips.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from .detailed import DetailedResult
from .profiles import GameProfile
from .stats import Summary

GOOD, OK, BAD = "good", "ok", "bad"


@dataclass
class Aspect:
    name: str
    level: str  # good | ok | bad
    headline: str
    comment: str


@dataclass
class Verdict:
    level: str
    headline: str
    aspects: list[Aspect] = field(default_factory=list)
    tips: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"level": self.level, "headline": self.headline,
                "aspects": [a.__dict__ for a in self.aspects], "tips": self.tips}


def _ping(p: GameProfile, ms: float) -> Aspect:
    cs = p.key == "cs2"
    if ms < 30:
        return Aspect("Ping", GOOD, f"Excellent ({ms:.0f} ms)",
                      "LAN-like. Shots and peeks register instantly."
                      if cs else "Feels instant.")
    if ms < 50:
        return Aspect("Ping", GOOD, f"Very good ({ms:.0f} ms)",
                      "Crisp and responsive; you won't feel any delay.")
    if ms < 80:
        return Aspect("Ping", GOOD, f"Good ({ms:.0f} ms)",
                      "Normal online feel. Fine for competitive play.")
    if ms < 110:
        return Aspect("Ping", OK, f"Playable ({ms:.0f} ms)",
                      "Slight delay. Enemies peeking you get a small head start, "
                      "so holding tight angles is harder." if cs else
                      "Slight but noticeable delay in fast moments.")
    if ms < 150:
        return Aspect("Ping", BAD, f"High ({ms:.0f} ms)",
                      "Noticeable delay: you'll die behind cover and lose "
                      "close peeks. Fine for casual, rough for Premier/Faceit."
                      if cs else "Noticeable delay; competitive play will suffer.")
    return Aspect("Ping", BAD, f"Too high ({ms:.0f} ms)",
                  "Matchmaking may refuse servers this far (CS2's max ping "
                  "limit is 150 ms by default)." if cs else
                  "Very laggy; pick a closer server if you can.")


def _stability(s: Summary) -> Aspect:
    j = s.jitter_ms
    spikes = (f" {s.spikes} spike{'s' if s.spikes != 1 else ''} above "
              f"{s.spike_threshold_ms:.0f} ms." if s.spikes else "")
    # Many spikes make a line unstable even when the average jitter looks fine.
    spike_pct = 100 * s.spikes / max(1, s.received)
    if spike_pct >= 5 and j >= 4:
        return Aspect("Stability", BAD, "Unstable",
                      f"{spike_pct:.0f}% of packets spiked above "
                      f"{s.spike_threshold_ms:.0f} ms ({j:.1f} ms jitter): expect "
                      "stutters and shots that feel delayed.")
    if spike_pct >= 2 and j < 10:
        return Aspect("Stability", OK, "Unsteady",
                      f"{spike_pct:.0f}% of packets spiked above "
                      f"{s.spike_threshold_ms:.0f} ms: mostly fine, with "
                      "noticeable hitches.")
    if j < 2 and s.spikes <= s.received * 0.002:
        return Aspect("Stability", GOOD, "Rock solid",
                      f"Ping barely moves ({j:.1f} ms jitter).{spikes}")
    if j < 5:
        return Aspect("Stability", GOOD, "Stable",
                      f"Small variation ({j:.1f} ms jitter) you won't notice.{spikes}")
    if j < 10:
        return Aspect("Stability", OK, "Slightly unstable",
                      f"{j:.1f} ms jitter: aim and sprays can feel a bit "
                      f"inconsistent from moment to moment.{spikes}")
    return Aspect("Stability", BAD, "Unstable",
                  f"{j:.1f} ms jitter: movement and sprays will feel random, "
                  f"enemies may stutter.{spikes}")


def _loss(p: GameProfile, s: Summary) -> Aspect:
    l = s.loss_pct
    if l == 0:
        return Aspect("Packet loss", GOOD, "None",
                      "Every packet arrived - every shot reaches the server.")
    if l < 0.5:
        return Aspect("Packet loss", GOOD if l < 0.2 else OK, f"Rare ({l:.2f}%)",
                      "Barely noticeable; maybe a rare hitch.")
    if l < 2:
        return Aspect("Packet loss", BAD, f"Noticeable ({l:.1f}%)",
                      "Expect occasional teleporting enemies and shots that "
                      "don't register.")
    return Aspect("Packet loss", BAD, f"Severe ({l:.1f}%)",
                  "Rubber-banding and frozen players"
                  + (", and CS2 will flash its connection warning." if p.key == "cs2"
                     else "."))


def _freezes(s: Summary, rate_hz: int) -> Aspect | None:
    gap_ms = s.longest_loss_burst * 1000 / rate_hz
    tail = s.p99_ms - s.median_ms
    if gap_ms >= 250:
        return Aspect("Freezes", BAD, f"Dropout of ~{gap_ms:.0f} ms",
                      "The connection stopped completely at least once - in "
                      "game that's a short freeze or lag spike.")
    if tail > 30:
        return Aspect("Freezes", OK, "Occasional lag spikes",
                      f"1 in 100 packets came {tail:.0f}+ ms late - you'll "
                      "feel a hitch now and then.")
    return Aspect("Freezes", GOOD, "None", "No dropouts or long lag spikes.")


def _under_load(d: DetailedResult) -> Aspect | None:
    if not (d.idle and d.idle.median_ms is not None):
        return None
    rises = [ph.median_ms - d.idle.median_ms
             for ph in (d.download, d.upload) if ph and ph.median_ms is not None]
    if not rises:
        return None
    worst = max(rises)
    if worst < 15:
        return Aspect("When the line is busy", GOOD, f"Holds up (+{worst:.0f} ms)",
                      "Downloads or streams on your network won't hurt your game.")
    if worst < 60:
        return Aspect("When the line is busy", OK, f"Ping rises +{worst:.0f} ms",
                      "If someone streams or Steam updates while you play, "
                      "you'll feel it.")
    return Aspect("When the line is busy", BAD, f"Ping jumps +{worst:.0f} ms",
                  "Any download or video on your network will make the game "
                  "lag badly (bufferbloat).")


def build(profile: GameProfile, s: Summary, server_name: str,
          detailed: DetailedResult | None = None,
          best_other_ping: float | None = None,
          metered: bool = False) -> Verdict:
    if s.received == 0:
        return Verdict(BAD, "No connection to the server.",
                       tips=["Check that you're online and that a firewall "
                             "isn't blocking ping (ICMP)."])

    aspects = [_ping(profile, s.median_ms), _stability(s), _loss(profile, s)]
    fz = _freezes(s, profile.rate_hz)
    if fz:
        aspects.append(fz)
    if detailed:
        ul = _under_load(detailed)
        if ul:
            aspects.append(ul)

    bad = sum(a.level == BAD for a in aspects)
    ok = sum(a.level == OK for a in aspects)
    level = BAD if bad else OK if ok else GOOD
    game = profile.name
    if level == GOOD:
        headline = f"Ready to play {game} on {server_name} - smooth and stable."
    elif level == OK:
        weak = ", ".join(a.name.lower() for a in aspects if a.level == OK)
        headline = f"Playable on {server_name}, but watch the {weak}."
    else:
        weak = ", ".join(a.name.lower() for a in aspects if a.level == BAD)
        headline = f"Expect problems on {server_name}: {weak}."

    tips = _tips(profile, s, detailed, best_other_ping, metered)
    return Verdict(level, headline, aspects, tips)


def _tips(profile: GameProfile, s: Summary, d: DetailedResult | None,
          best_other_ping: float | None, metered: bool) -> list[str]:
    cs = profile.key == "cs2"
    tips: list[str] = []
    wifi_suspect = False
    if d and d.hops and d.hops[0].avg is not None:
        first = d.hops[0]
        wifi_suspect = first.avg > 5 or (first.jitter or 0) > 3

    if s.jitter_ms >= 5 or s.loss_pct >= 0.5 or wifi_suspect:
        tips.append("Use an Ethernet cable instead of Wi-Fi if you can. On Wi-Fi, "
                    "use 5 GHz and sit closer to the router.")
    if s.loss_pct >= 0.5:
        tips.append("Restart your modem/router and check cables. If loss continues, "
                    "send the exported report to your ISP.")
        if cs:
            tips.append("In CS2: Settings > Game > 'Buffering to smooth over packet "
                        "loss' (cl_net_buffer_ticks 1-2) hides small losses at the "
                        "cost of a little delay.")
    elif cs and 5 <= s.jitter_ms < 15:
        tips.append("In CS2, 'Buffering to smooth over packet loss' set to 1 tick can "
                    "smooth out jitter like yours.")

    if d:
        for ph in (d.download, d.upload):
            if (ph and d.idle and ph.median_ms and d.idle.median_ms
                    and ph.median_ms - d.idle.median_ms >= 30):
                tips.append("Pause downloads, Steam updates and video streams on your "
                            "network while playing, or enable SQM / QoS on your router.")
                break

    if cs and not math.isnan(s.median_ms):
        limit = int(math.ceil((s.median_ms + 25) / 10) * 10)
        limit = max(50, min(limit, 150))
        tips.append(f"In CS2, set 'mm_dedicated_search_maxping {limit}' in the "
                    "console so matchmaking keeps you on servers like this one.")
        tips.append("To watch your connection in game: cl_hud_telemetry_ping_show 2 "
                    "and cl_hud_telemetry_net_misdelivery_show 2.")
    if best_other_ping is not None and best_other_ping + 15 < s.median_ms:
        tips.append(f"Another server answers in {best_other_ping:.0f} ms - try Auto "
                    "mode for a lower ping.")
    if metered:
        tips.append("Your connection is metered (mobile / data-capped). These are "
                    "often busier in the evening, so test again before you play.")
    return tips
