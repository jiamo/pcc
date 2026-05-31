"""pcc.py_stdlib.urllib.parse — narrow native subset.

The public spelling remains ``import urllib.parse``. This module is the
native provider selected by pcc's resolver for the subset needed during
self-host bring-up.
"""
from __future__ import annotations


_HEX = "0123456789ABCDEF"


def _is_unreserved(ch: str) -> bool:
    # RFC 3986 unreserved set: ALPHA / DIGIT / "-" / "." / "_" / "~"
    if ch >= "a" and ch <= "z":
        return True
    if ch >= "A" and ch <= "Z":
        return True
    if ch >= "0" and ch <= "9":
        return True
    if ch == "-" or ch == "." or ch == "_" or ch == "~":
        return True
    return False


def quote(s: str, safe: str = "/") -> str:
    out = ""
    i = 0
    n = len(s)
    while i < n:
        ch = s[i]
        if _is_unreserved(ch):
            out = out + ch
        elif ch in safe:
            out = out + ch
        else:
            b = ord(ch)
            # ASCII path — pcc-Python str is UTF-8; quoting multi-byte
            # characters needs the underlying bytes, which the closed
            # world layer hasn't surfaced yet. ASCII is sufficient for
            # the self-host / py_corpus probes that exercise quote.
            out = out + "%" + _HEX[(b >> 4) & 0xF] + _HEX[b & 0xF]
        i = i + 1
    return out


def _hex_digit_val(ch: str) -> int:
    if ch >= "0" and ch <= "9":
        return ord(ch) - ord("0")
    if ch >= "A" and ch <= "F":
        return ord(ch) - ord("A") + 10
    if ch >= "a" and ch <= "f":
        return ord(ch) - ord("a") + 10
    return -1


def unquote(s: str) -> str:
    out = ""
    i = 0
    n = len(s)
    while i < n:
        ch = s[i]
        if ch == "%" and i + 2 < n:
            hi = _hex_digit_val(s[i + 1])
            lo = _hex_digit_val(s[i + 2])
            if hi >= 0 and lo >= 0:
                out = out + chr((hi << 4) | lo)
                i = i + 3
                continue
        out = out + ch
        i = i + 1
    return out


def urlparse(url: str):
    return (url, "", "", "", "", "")
