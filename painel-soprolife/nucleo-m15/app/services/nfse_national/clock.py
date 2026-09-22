"""Host clock synchronization status — read-only, offline, no subprocess.

M55 measured the host clock 14.3 minutes BEHIND the SEFIN clock and could
only prove it after the fact, by comparing a stored response's ``Date``
header against the local ``mtime`` of the file written when it arrived.
That is forensics, not a gate: it needs a real government response to
exist first. A production issuance must be refused BEFORE the request,
from local state alone.

``dhEmi`` is stamped from this host's clock (``dps_builder.EMISSION_TIMEZONE``
converts, it does not correct), and SEFIN rejects a DPS whose emission
instant sits outside its tolerance — rule E0008, the rejection that killed
DPS #9. A clock running fast reproduces it immediately; a clock running slow
merely hid it. Neither is acceptable for a real fiscal document.

This module reads the kernel's own NTP discipline state through
``adjtimex(2)`` with ``modes=0`` (a pure read: the struct's ``modes`` field
is zero, so the call adjusts nothing). That is the same state ``timedatectl``
reports as ``System clock synchronized`` and the same one ``chronyd``
maintains — but read directly, with no subprocess, no parsing of localized
command output and no network. Fails closed: any platform without the call,
any error, any inability to determine the state yields ``synchronized=False``
with a named reason, never an optimistic default.
"""
from __future__ import annotations

import ctypes
import ctypes.util
from dataclasses import dataclass

# <sys/timex.h>. STA_UNSYNC is set by the kernel when no time source is
# disciplining the clock; chronyd/systemd-timesyncd clear it once they hold
# a lock on a source. TIME_ERROR (5) is adjtimex's own "clock not
# synchronized" return code.
STA_UNSYNC = 0x0040
TIME_ERROR = 5

# ``maxerror`` is the kernel's own upper bound on the current clock error, in
# microseconds. It grows continuously between NTP updates and is reset on
# each one, so a small value is positive evidence that a source is actively
# disciplining the clock — not merely that STA_UNSYNC happens to be clear.
# 16 s is the kernel's own "unsynchronized" watermark (NTP_PHASE_LIMIT);
# this default is two orders of magnitude tighter, while staying far above
# the tens of milliseconds a healthy chronyd reports.
DEFAULT_MAX_ERROR_SECONDS = 2.0


@dataclass(frozen=True)
class ClockStatus:
    """Safe to log: three numbers and a reason code. Never a hostname, never
    a time source address, never anything derived from a person."""
    synchronized: bool
    reason: str
    max_error_seconds: float | None = None
    estimated_error_seconds: float | None = None
    status_flags: int | None = None

    def as_dict(self) -> dict:
        return {
            "synchronized": self.synchronized,
            "reason": self.reason,
            "max_error_seconds": self.max_error_seconds,
            "estimated_error_seconds": self.estimated_error_seconds,
            "status_flags": self.status_flags,
        }


class _Timex(ctypes.Structure):
    """``struct timex`` as the Linux kernel defines it (``<sys/timex.h>``).

    Field order and types must match exactly; the trailing padding is part
    of the ABI and is what keeps this struct forward-compatible with kernels
    that grow new fields.
    """
    _fields_ = [
        ("modes", ctypes.c_uint),
        ("offset", ctypes.c_long),
        ("freq", ctypes.c_long),
        ("maxerror", ctypes.c_long),
        ("esterror", ctypes.c_long),
        ("status", ctypes.c_int),
        ("constant", ctypes.c_long),
        ("precision", ctypes.c_long),
        ("tolerance", ctypes.c_long),
        ("time_tv_sec", ctypes.c_long),
        ("time_tv_usec", ctypes.c_long),
        ("tick", ctypes.c_long),
        ("ppsfreq", ctypes.c_long),
        ("jitter", ctypes.c_long),
        ("shift", ctypes.c_int),
        ("stabil", ctypes.c_long),
        ("jitcnt", ctypes.c_long),
        ("calcnt", ctypes.c_long),
        ("errcnt", ctypes.c_long),
        ("stbcnt", ctypes.c_long),
        ("tai", ctypes.c_int),
        ("_padding", ctypes.c_int * 11),
    ]


def _read_timex() -> tuple[int, _Timex]:
    """Raw ``adjtimex(2)`` read. Raises OSError/AttributeError on any
    platform that cannot answer — the caller turns that into a blocker."""
    libc = ctypes.CDLL(ctypes.util.find_library("c"), use_errno=True)
    buffer = _Timex()
    buffer.modes = 0  # read-only: adjust nothing
    result = libc.adjtimex(ctypes.byref(buffer))
    if result < 0:
        raise OSError(ctypes.get_errno(), "adjtimex falhou")
    return result, buffer


def read_clock_status(*, max_error_seconds: float = DEFAULT_MAX_ERROR_SECONDS) -> ClockStatus:
    """The host's NTP discipline state. Total: never raises."""
    try:
        code, buffer = _read_timex()
    except Exception:
        # No adjtimex (non-Linux, restricted sandbox, missing libc): we
        # cannot prove the clock is disciplined, so it is not.
        return ClockStatus(False, "clock_status_unavailable")

    flags = int(buffer.status)
    max_error = buffer.maxerror / 1_000_000
    est_error = buffer.esterror / 1_000_000
    if flags & STA_UNSYNC:
        return ClockStatus(False, "clock_unsynchronized", max_error, est_error, flags)
    if code == TIME_ERROR:
        return ClockStatus(False, "clock_in_error_state", max_error, est_error, flags)
    if max_error > max_error_seconds:
        # STA_UNSYNC clear but the kernel's own error bound has drifted past
        # tolerance: a source was locked at some point and has since gone
        # quiet. Treated as unsynchronized, which is the safe direction.
        return ClockStatus(False, "clock_max_error_exceeded", max_error, est_error, flags)
    return ClockStatus(True, "clock_synchronized", max_error, est_error, flags)
