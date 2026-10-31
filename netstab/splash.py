"""Controls the startup splash of the built exe.

The splash is drawn by PyInstaller's launcher while it unpacks the app, so the
user sees something within a second instead of a blank 10 s wait. The
`pyi_splash` module only exists inside that exe; from source these are no-ops.
"""

from __future__ import annotations

try:
    import pyi_splash  # type: ignore[import-not-found]
except ImportError:
    pyi_splash = None


def update(text: str) -> None:
    try:
        if pyi_splash and pyi_splash.is_alive():
            pyi_splash.update_text(text)
    except Exception:  # noqa: BLE001 - cosmetic; never block startup
        pass


def close() -> None:
    try:
        if pyi_splash and pyi_splash.is_alive():
            pyi_splash.close()
    except Exception:  # noqa: BLE001
        pass


def present() -> bool:
    return pyi_splash is not None
