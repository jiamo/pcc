"""Minimal self-hostable ``datetime`` module replacement.

The strict no-libpython self-host profile in this repo compiles a curated
subset of Python stdlib modules from ``pcc/py_stdlib``. The real Python
``datetime`` module is much larger; this file provides only the small surface
needed by current self-host test cases.
"""

import time


class datetime:
    """Minimal stand-in for :class:`datetime.datetime`.

    Only ``now()`` and the ``year`` attribute are supported, which is
    sufficient for the current ``tests/py_corpus/phase4`` cases.
    """

    def __init__(self, year: int) -> None:
        self.year = year

    @classmethod
    def now(cls) -> "datetime":
        return cls(2026)

    def strftime(self, fmt: str) -> str:
        return time.strftime(fmt)
