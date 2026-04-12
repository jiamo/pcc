"""pcc.py_stdlib.traceback — narrow ``traceback`` skeleton.

Hooks into the py_runtime exception-frame table (``py_exc_append_frame``)
to format an exception's stack. Real formatting mirrors CPython's
``Traceback (most recent call last):`` layout.
"""
from __future__ import annotations


def format_exception(etype, value, tb) -> list[str]:
    raise NotImplementedError(
        "format_exception awaits runtime frame-table marshalling"
    )


def format_exc() -> str:
    raise NotImplementedError("format_exc awaits frame-table marshalling")


def print_exc() -> None:
    raise NotImplementedError("print_exc awaits frame-table marshalling")


def print_exception(etype, value, tb) -> None:
    raise NotImplementedError(
        "print_exception awaits frame-table marshalling"
    )
