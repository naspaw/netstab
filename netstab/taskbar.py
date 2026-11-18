"""netstab's own taskbar button: a coloured strip under it (Windows' progress
indicator) for the live monitor. Talks to the shell's ITaskbarList3 COM
interface directly through ctypes, so nothing extra is bundled.

Windows 11 lets apps change only their own button (icon, strip, overlay), not
add text to the taskbar, so the ping number goes in the button's icon (set by
the caller) and the quality colour in this strip.
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes

# ITaskbarList3 progress states and their colours on the taskbar.
NOPROGRESS, NORMAL, ERROR, PAUSED = 0x0, 0x2, 0x4, 0x8  # none, green, red, yellow

_CLSID_TaskbarList = "{56FDF344-FD6D-11d0-958A-006097C9A090}"
_IID_ITaskbarList3 = "{ea1afb91-9e28-4b86-90e9-9e9f8a5eefaf}"
# vtable slots: IUnknown 0-2, ITaskbarList 3-7, ITaskbarList2 8, ITaskbarList3 9+
_HRINIT, _SET_PROGRESS_VALUE, _SET_PROGRESS_STATE = 3, 9, 10


class _GUID(ctypes.Structure):
    _fields_ = [("a", wintypes.DWORD), ("b", wintypes.WORD), ("c", wintypes.WORD),
                ("d", ctypes.c_ubyte * 8)]


def _guid(text: str) -> _GUID:
    g = _GUID()
    ctypes.oledll.ole32.CLSIDFromString(ctypes.c_wchar_p(text), ctypes.byref(g))
    return g


class TaskbarButton:
    """Colours the taskbar button of one window. Silently does nothing where
    the shell interface isn't available (it never breaks the app)."""

    def __init__(self, hwnd: int):
        self.hwnd = hwnd
        self._ptr = None
        self._state = None
        try:
            ole32 = ctypes.oledll.ole32
            ole32.CoInitialize(None)  # Qt's main thread is already STA; harmless
            ptr = ctypes.c_void_p()
            ole32.CoCreateInstance(ctypes.byref(_guid(_CLSID_TaskbarList)), None,
                                   1 | 4,  # CLSCTX_INPROC_SERVER | LOCAL_SERVER
                                   ctypes.byref(_guid(_IID_ITaskbarList3)),
                                   ctypes.byref(ptr))
            self._ptr = ptr
            self._call(_HRINIT, ctypes.HRESULT)
        except OSError:
            self._ptr = None

    @property
    def available(self) -> bool:
        return bool(self._ptr)

    def _call(self, slot: int, restype, *args):
        vtable = ctypes.cast(self._ptr, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p)))[0]
        argtypes = [ctypes.c_void_p] + [type(a) for a in args]
        fn = ctypes.WINFUNCTYPE(restype, *argtypes)(vtable[slot])
        return fn(self._ptr, *args)

    def set_state(self, state: int) -> None:
        """NORMAL (green), PAUSED (yellow), ERROR (red) as a full strip; or NOPROGRESS."""
        if not self._ptr or state == self._state:
            return
        try:
            hwnd = wintypes.HWND(self.hwnd)
            if state != NOPROGRESS:
                self._call(_SET_PROGRESS_VALUE, ctypes.HRESULT, hwnd,
                           ctypes.c_ulonglong(100), ctypes.c_ulonglong(100))
            self._call(_SET_PROGRESS_STATE, ctypes.HRESULT, hwnd, ctypes.c_int(state))
            self._state = state
        except OSError:
            pass

    def clear(self) -> None:
        self.set_state(NOPROGRESS)
