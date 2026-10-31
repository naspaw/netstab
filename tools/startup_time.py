"""Measures how long dist/netstab.exe takes to show its splash and its window.

    python tools/startup_time.py [runs] [screenshot_dir] [exe]

Launches the exe like a double-click, watches for its windows, saves a
screenshot of the splash, and closes the app.
"""

from __future__ import annotations

import ctypes
import subprocess
import sys
import time
from ctypes import wintypes
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXE = ROOT / "dist" / "netstab.exe"
user32 = ctypes.windll.user32
# Real pixel coordinates from GetWindowRect, matching what the screen grab uses.
user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))  # per-monitor v2


def _exe_pids() -> set[int]:
    out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq netstab.exe", "/FO", "CSV", "/NH"],
                         capture_output=True, text=True).stdout
    return {int(line.split('","')[1]) for line in out.splitlines() if '","' in line}


def _windows(pids: set[int]) -> list[tuple[str, str, tuple[int, int, int, int]]]:
    found = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def cb(hwnd, _):
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value in pids and user32.IsWindowVisible(hwnd):
            title = ctypes.create_unicode_buffer(256)
            cls = ctypes.create_unicode_buffer(256)
            user32.GetWindowTextW(hwnd, title, 256)
            user32.GetClassNameW(hwnd, cls, 256)
            r = wintypes.RECT()
            user32.GetWindowRect(hwnd, ctypes.byref(r))
            if r.right - r.left > 100:
                found.append((title.value, cls.value, (r.left, r.top, r.right, r.bottom)))
        return True

    user32.EnumWindows(cb, 0)
    return found


def measure(shot_dir: Path | None) -> tuple[float | None, float | None]:
    proc = subprocess.Popen([str(EXE)], cwd=EXE.parent)
    t0 = time.perf_counter()
    splash_at = window_at = None
    try:
        while time.perf_counter() - t0 < 60 and window_at is None:
            wins = _windows(_exe_pids())
            now = time.perf_counter() - t0
            for title, cls, rect in wins:
                if title.startswith("netstab ") and window_at is None:
                    window_at = now
                elif not title.startswith("netstab ") and splash_at is None:
                    splash_at = now
                    if shot_dir:
                        time.sleep(1.0)  # let the status text show
                        _grab(rect, shot_dir / "splash_on_screen.png")
            time.sleep(0.05)
    finally:
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)], capture_output=True)
    return splash_at, window_at


def _grab(rect, path: Path) -> None:
    from PySide6.QtGui import QGuiApplication
    app = QGuiApplication.instance() or QGuiApplication(sys.argv[:1])
    screen = app.primaryScreen()
    ratio = screen.devicePixelRatio()  # grabWindow takes logical coordinates
    x, y, r, b = (round(v / ratio) for v in rect)
    screen.grabWindow(0, x, y, r - x, b - y).save(str(path))


def main() -> int:
    global EXE
    runs = int(sys.argv[1]) if len(sys.argv) > 1 else 3
    shots = Path(sys.argv[2]) if len(sys.argv) > 2 and sys.argv[2] != "-" else None
    if len(sys.argv) > 3:
        EXE = Path(sys.argv[3]).resolve()
    if shots:
        shots.mkdir(parents=True, exist_ok=True)
    for i in range(runs):
        s, w = measure(shots if i == 0 else None)
        fmt = lambda v: "never" if v is None else f"{v:.1f} s"  # noqa: E731
        print(f"run {i + 1}: splash after {fmt(s)}, main window after {fmt(w)}", flush=True)
        time.sleep(1)
    return 0


if __name__ == "__main__":
    sys.exit(main())
