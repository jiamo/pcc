"""pcc.py_stdlib.time — libc-backed ``time`` surface.

Scope: what pcc's own source (and the self-host benchmark harness)
actually calls.
"""
from __future__ import annotations

from pcc.extern import extern, c_int64, c_double


_clock_gettime_realtime: "extern" = extern(
    "clock_gettime", (c_int64, c_int64), c_int64,
)


def time() -> float:
    """Seconds since the epoch, as a float."""
    raise NotImplementedError(
        "time.time() awaits the struct timespec marshalling path in P6C.1"
    )


def monotonic() -> float:
    """Monotonic clock seconds. Same marshalling requirement as time.time."""
    raise NotImplementedError(
        "time.monotonic() awaits struct-timespec marshalling"
    )


def perf_counter() -> float:
    return monotonic()


_nanosleep: "extern" = extern(
    "usleep", (c_int64,), c_int64,
)


def sleep(seconds: float) -> None:
    """Suspend the current thread for ``seconds`` via ``usleep``.
    Rounds to the nearest microsecond; millisecond-scale accuracy."""
    us = int(seconds * 1_000_000.0)
    if us < 0:
        us = 0
    _nanosleep(us)
