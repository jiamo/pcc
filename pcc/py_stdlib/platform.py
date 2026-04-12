"""pcc.py_stdlib.platform — narrow ``platform`` skeleton."""
from __future__ import annotations

from pcc.extern import extern, c_str, c_int
from . import sys as _sys


def system() -> str:
    p = _sys.platform
    if p.startswith("darwin"):
        return "Darwin"
    if p.startswith("linux"):
        return "Linux"
    if p.startswith("win"):
        return "Windows"
    return p


def machine() -> str:
    """Return the machine architecture — pcc builds pin this at compile
    time; the runtime query awaits ``uname(2)`` extern wiring."""
    raise NotImplementedError(
        "platform.machine awaits uname(2) extern binding"
    )


def python_version() -> str:
    return _sys.version


def platform() -> str:
    return system()


def node() -> str:
    raise NotImplementedError("platform.node awaits gethostname extern")
