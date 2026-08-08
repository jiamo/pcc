"""Explicit Windows-CRT boundary for recursive build-tool closure.

Meson's platform package contains a Windows-only module which imports
``msvcrt``.  The recursive source walker sees that import even on Darwin and
Linux, so a pcc-owned provider is required to keep the closed-world compile
free of libpython fallbacks.  Importing the provider on a non-Windows target
matches CPython by raising ``ImportError``.  On Windows, the CRT descriptor and
locking operations remain unowned and fail closed instead of pretending to
provide process-global locking semantics.
"""
from __future__ import annotations

import sys


if sys.platform != "win32":
    raise ImportError("No module named 'msvcrt'")


LK_UNLCK = 0
LK_LOCK = 1
LK_NBLCK = 2
LK_RLCK = 3
LK_NBRLCK = 4


def _unowned():
    raise NotImplementedError(
        "msvcrt descriptor, console and locking operations are not runtime-owned"
    )


def locking(fd, mode, nbytes):
    _unowned()


def setmode(fd, flags):
    _unowned()


def open_osfhandle(handle, flags):
    _unowned()


def get_osfhandle(fd):
    _unowned()


def kbhit():
    _unowned()


def getch():
    _unowned()


def getwch():
    _unowned()


def putch(char):
    _unowned()


def putwch(char):
    _unowned()


__all__ = [
    "LK_UNLCK",
    "LK_LOCK",
    "LK_NBLCK",
    "LK_RLCK",
    "LK_NBRLCK",
    "locking",
    "setmode",
    "open_osfhandle",
    "get_osfhandle",
    "kbhit",
    "getch",
    "getwch",
    "putch",
    "putwch",
]
