"""Detect whether Windows treats the current connection as metered.

Reads the same flag Windows Update and the Store respect (Settings > Network >
"Metered connection") straight from the WinRT NetworkInformation API, in
process, so no external program is needed.
"""

from __future__ import annotations

from dataclasses import dataclass

_COST_NAMES = {0: "Unknown", 1: "Unrestricted", 2: "Fixed", 3: "Variable"}


@dataclass
class ConnectionCost:
    profile: str = ""
    cost_type: str = "Unknown"  # Unrestricted | Fixed | Variable | Unknown
    roaming: bool = False
    approaching_limit: bool = False
    over_limit: bool = False

    @property
    def metered(self) -> bool:
        return self.cost_type in ("Fixed", "Variable") or self.roaming

    @property
    def description(self) -> str:
        if not self.metered:
            return f"{self.profile or 'Connection'}: unmetered"
        bits = [f"{self.profile or 'Connection'} is metered"]
        if self.roaming:
            bits.append("roaming")
        if self.over_limit:
            bits.append("over its data limit")
        elif self.approaching_limit:
            bits.append("close to its data limit")
        return ", ".join(bits)


def _query() -> ConnectionCost | None:
    from winrt.windows.networking.connectivity import NetworkInformation

    p = NetworkInformation.get_internet_connection_profile()
    if p is None:
        return None  # offline
    c = p.get_connection_cost()
    return ConnectionCost(p.profile_name or "", _COST_NAMES.get(int(c.network_cost_type),
                                                                "Unknown"),
                          bool(c.roaming), bool(c.approaching_data_limit),
                          bool(c.over_data_limit))


def check() -> ConnectionCost:
    """Returns cost_type 'Unknown' if it can't tell (never raises)."""
    try:
        return _query() or ConnectionCost()
    except Exception:  # noqa: BLE001 - WinRT errors vary; treat as unknown
        return ConnectionCost()


if __name__ == "__main__":
    c = check()
    print(c, "->", c.description)
