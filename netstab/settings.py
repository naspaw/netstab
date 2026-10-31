"""Small persistent settings file (last measured speeds, chosen options)."""

from __future__ import annotations

import json

from .servers import CACHE_DIR

_PATH = CACHE_DIR / "settings.json"


def load() -> dict:
    try:
        return json.loads(_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save(data: dict) -> None:
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        _PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")
    except OSError:
        pass


def update(**kw) -> None:
    d = load()
    d.update(kw)
    save(d)
