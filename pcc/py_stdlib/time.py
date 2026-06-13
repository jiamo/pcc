"""pcc.py_stdlib.time — libc-backed ``time`` surface.

Scope: what pcc's own source (and the self-host benchmark harness)
actually calls.
"""

from __future__ import annotations

import time as _native_time

from pcc.extern import c_int64, extern


def time() -> float:
    """Seconds since the epoch, as a float."""
    return float(_native_time.time())


def monotonic() -> float:
    return float(_native_time.monotonic())


def perf_counter() -> float:
    return float(_native_time.perf_counter())


def strftime(fmt: str) -> str:
    return str(_native_time.strftime(fmt))


_nanosleep: "extern" = extern(
    "usleep",
    (c_int64,),
    c_int64,
)


def sleep(seconds: float) -> None:
    """Suspend the current thread for ``seconds`` via ``usleep``.
    Rounds to the nearest microsecond; millisecond-scale accuracy."""
    us = int(seconds * 1_000_000.0)
    if us < 0:
        us = 0
    _nanosleep(us)
