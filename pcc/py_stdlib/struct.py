"""pcc.py_stdlib.struct — narrow ``struct`` skeleton.

Scope: pack / unpack for the format characters pcc's own source uses.
Real implementation will read one format char at a time and walk
bytes; the fallback here keeps the surface shape for audit + runtime
experimentation.
"""
from __future__ import annotations


class error(Exception):
    pass


def calcsize(fmt: str) -> int:
    """Return the byte size of the given format string."""
    total = 0
    # Endian prefix byte — one of ``<``, ``>``, ``!``, ``=``, ``@``.
    i = 0
    if i < len(fmt) and fmt[i] in "<>!=@":
        i += 1
    repeat = 1
    while i < len(fmt):
        ch = fmt[i]
        if ch.isdigit():
            repeat = (repeat if repeat > 1 else 0) * 10 + int(ch)
            i += 1
            continue
        size = {
            "b": 1, "B": 1, "?": 1,
            "h": 2, "H": 2,
            "i": 4, "I": 4, "l": 4, "L": 4, "f": 4,
            "q": 8, "Q": 8, "d": 8,
            "s": 1,
        }.get(ch)
        if size is None:
            raise error(f"bad format char: {ch!r}")
        total += size * (repeat if repeat > 0 else 1)
        repeat = 1
        i += 1
    return total


def pack(fmt: str, *values) -> bytes:
    raise NotImplementedError(
        "struct.pack awaits the byte-packing lowering pass"
    )


def unpack(fmt: str, data: bytes) -> tuple:
    raise NotImplementedError(
        "struct.unpack awaits the byte-unpacking lowering pass"
    )


def pack_into(fmt: str, buffer, offset: int, *values) -> None:
    raise NotImplementedError("struct.pack_into awaits byte-buffer support")


def unpack_from(fmt: str, buffer, offset: int = 0) -> tuple:
    raise NotImplementedError("struct.unpack_from awaits byte-buffer support")
