"""pcc.py_stdlib.platform — narrow ``platform`` skeleton."""
from __future__ import annotations

import sys as _sys


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
    if _sys.platform.startswith("darwin"):
        return "arm64"
    if _sys.platform.startswith("linux"):
        return "x86_64"
    return ""


def python_version() -> str:
    return _sys.version


def platform() -> str:
    return system()


def node() -> str:
    return ""


def python_implementation() -> str:
    return _sys.implementation.name


def python_version_tuple():
    return ("3", "13", "0")


def uname():
    sys_name = system()
    return (sys_name, node(), "", "", machine(), "")
