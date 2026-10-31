"""Thin ctypes wrapper around the Windows ICMP API (iphlpapi.dll).

IcmpSendEcho works without administrator rights, unlike raw sockets, so the
app can run as a normal user. Each thread gets its own ICMP handle.
"""

from __future__ import annotations

import ctypes
import socket
import struct
import threading
import time
from ctypes import wintypes
from dataclasses import dataclass

_iphlpapi = ctypes.WinDLL("iphlpapi.dll", use_last_error=True)

IP_SUCCESS = 0
IP_BUF_TOO_SMALL = 11001
IP_DEST_NET_UNREACHABLE = 11002
IP_DEST_HOST_UNREACHABLE = 11003
IP_PACKET_TOO_BIG = 11009
IP_REQ_TIMED_OUT = 11010
IP_TTL_EXPIRED_TRANSIT = 11013

IP_FLAG_DF = 0x02  # don't fragment


class IP_OPTION_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("Ttl", ctypes.c_ubyte),
        ("Tos", ctypes.c_ubyte),
        ("Flags", ctypes.c_ubyte),
        ("OptionsSize", ctypes.c_ubyte),
        ("OptionsData", ctypes.c_void_p),
    ]


_IcmpCreateFile = _iphlpapi.IcmpCreateFile
_IcmpCreateFile.restype = wintypes.HANDLE
_IcmpCreateFile.argtypes = []

_IcmpCloseHandle = _iphlpapi.IcmpCloseHandle
_IcmpCloseHandle.restype = wintypes.BOOL
_IcmpCloseHandle.argtypes = [wintypes.HANDLE]

_IcmpSendEcho = _iphlpapi.IcmpSendEcho
_IcmpSendEcho.restype = wintypes.DWORD
_IcmpSendEcho.argtypes = [
    wintypes.HANDLE,
    wintypes.ULONG,  # IPAddr, network byte order
    ctypes.c_void_p,
    wintypes.WORD,
    ctypes.POINTER(IP_OPTION_INFORMATION),
    ctypes.c_void_p,
    wintypes.DWORD,
    wintypes.DWORD,
]

# ICMP_ECHO_REPLY header is Address, Status, RoundTripTime (3 x ULONG) followed
# by fields whose layout differs between 32/64-bit; we only read the header.
_REPLY_HEADER = struct.Struct("<III")
_REPLY_OVERHEAD = 64  # generous: sizeof(ICMP_ECHO_REPLY) + 8 bytes ICMP error data


@dataclass(slots=True)
class EchoResult:
    status: int
    rtt_ms: float | None  # high-resolution measured RTT, None if no reply
    reply_from: str | None  # address that answered (a router for TTL expiry)

    @property
    def ok(self) -> bool:
        return self.status == IP_SUCCESS


class _HandleCache(threading.local):
    handle: int | None = None


_local = _HandleCache()


def _handle() -> int:
    if _local.handle is None:
        h = _IcmpCreateFile()
        if not h or h == wintypes.HANDLE(-1).value:
            raise OSError(ctypes.get_last_error(), "IcmpCreateFile failed")
        _local.handle = h
    return _local.handle


def resolve(host: str) -> str:
    return socket.gethostbyname(host)


def _ip_to_ulong(ip: str) -> int:
    # IPAddr is the address in network byte order stored in a ULONG.
    return struct.unpack("<I", socket.inet_aton(ip))[0]


def echo(
    ip: str,
    payload_size: int = 32,
    timeout_ms: int = 1000,
    ttl: int = 128,
    dont_fragment: bool = False,
) -> EchoResult:
    """Send one ICMP echo request and block until the reply or the timeout."""
    payload = (b"netstab-" * (payload_size // 8 + 1))[:payload_size]
    req = ctypes.create_string_buffer(payload, payload_size)
    reply_size = _REPLY_OVERHEAD + max(payload_size, 8) + 8
    reply = ctypes.create_string_buffer(reply_size)
    opts = IP_OPTION_INFORMATION(ttl, 0, IP_FLAG_DF if dont_fragment else 0, 0, None)

    t0 = time.perf_counter()
    n = _IcmpSendEcho(
        _handle(),
        _ip_to_ulong(ip),
        req,
        payload_size,
        ctypes.byref(opts),
        reply,
        reply_size,
        timeout_ms,
    )
    elapsed = (time.perf_counter() - t0) * 1000.0

    if n == 0:
        err = ctypes.get_last_error()
        return EchoResult(err or IP_REQ_TIMED_OUT, None, None)

    addr, status, rtt_coarse = _REPLY_HEADER.unpack_from(reply.raw, 0)
    reply_from = socket.inet_ntoa(struct.pack("<I", addr))
    if status in (IP_SUCCESS, IP_TTL_EXPIRED_TRANSIT):
        # The OS only reports whole milliseconds; our own timer is far finer.
        # Guard against scheduler hiccups inflating it beyond the OS figure.
        rtt = elapsed if elapsed <= rtt_coarse + 2 else float(rtt_coarse)
        return EchoResult(status, rtt, reply_from)
    return EchoResult(status, None, reply_from)
