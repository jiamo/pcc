"""pcc.py_stdlib.zlib - native compile surface for zlib."""

from __future__ import annotations


class error(Exception):
    pass


def crc32(data, value: int = 0) -> int:
    import binascii

    return binascii.crc32(data, value)


def compress(data, level: int = -1):
    raise NotImplementedError("zlib.compress is not implemented")


def decompress(data, wbits: int = 15, bufsize: int = 16384):
    raise NotImplementedError("zlib.decompress is not implemented")
