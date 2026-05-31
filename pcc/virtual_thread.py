"""Compiler-recognized virtual-thread helpers.

The pcc Python frontend lowers this module natively.  The CPython
implementation intentionally raises so tests do not accidentally treat this as
a portable Python library.
"""

from __future__ import annotations

from typing import Any, Callable


def _trap(name: str) -> None:
    raise NotImplementedError(f"pcc.virtual_thread.{name}() requires pcc lowering")


def spawn(fn: Callable[..., Any], *args: Any) -> Any:
    _trap("spawn")


def run(carrier_count: int, max_steps: int) -> int:
    _trap("run")


def run_until_idle(max_steps: int) -> int:
    _trap("run_until_idle")


def carrier_pool_start(carrier_count: int) -> int:
    _trap("carrier_pool_start")


def carrier_pool_stop() -> int:
    _trap("carrier_pool_stop")


def current() -> Any:
    _trap("current")


def yield_now() -> None:
    _trap("yield_now")


def sleep_current(delay_ms: int) -> None:
    _trap("sleep_current")


def block_current_on_fd(fd: int, events: int, timeout_ms: int) -> None:
    _trap("block_current_on_fd")


def result(vthread: Any) -> Any:
    _trap("result")


def state(vthread: Any) -> int:
    _trap("state")


def sleep(vthread: Any, delay_ms: int) -> None:
    _trap("sleep")


def block_on_fd(vthread: Any, fd: int, events: int, timeout_ms: int) -> None:
    _trap("block_on_fd")


__all__ = [
    "spawn",
    "run",
    "run_until_idle",
    "carrier_pool_start",
    "carrier_pool_stop",
    "current",
    "yield_now",
    "sleep_current",
    "block_current_on_fd",
    "result",
    "state",
    "sleep",
    "block_on_fd",
]
