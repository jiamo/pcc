"""pcc.py_stdlib.tempfile — narrow ``tempfile`` skeleton.

Plan: bind libc ``mkstemp`` / ``mkdtemp`` via :mod:`pcc.extern`. The
Python surface (``NamedTemporaryFile``, ``TemporaryDirectory``) is a
thin class-level wrapper over those calls.
"""
from __future__ import annotations


class NamedTemporaryFile:
    def __init__(self, mode: str = "w", delete: bool = True,
                 suffix: str = "", prefix: str = "tmp", dir=None) -> None:
        self.mode = mode
        self.delete = delete
        self.name: str = ""
        raise NotImplementedError(
            "NamedTemporaryFile awaits mkstemp extern binding"
        )


class TemporaryDirectory:
    def __init__(self, suffix: str = "", prefix: str = "tmp", dir=None) -> None:
        self.name: str = ""
        raise NotImplementedError(
            "TemporaryDirectory awaits mkdtemp extern binding"
        )

    def __enter__(self):
        return self.name

    def __exit__(self, *exc) -> None:
        self.cleanup()

    def cleanup(self) -> None:
        pass


def mkstemp(suffix: str = "", prefix: str = "tmp", dir=None, text: bool = False):
    raise NotImplementedError(
        "mkstemp awaits the libc extern binding"
    )


def mkdtemp(suffix: str = "", prefix: str = "tmp", dir=None) -> str:
    raise NotImplementedError(
        "mkdtemp awaits the libc extern binding"
    )


def gettempdir() -> str:
    return "/tmp"
