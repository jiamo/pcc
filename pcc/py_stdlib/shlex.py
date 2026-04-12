"""pcc.py_stdlib.shlex — narrow ``shlex`` skeleton."""
from __future__ import annotations


def split(s: str, comments: bool = False, posix: bool = True) -> list[str]:
    """Split a shell-style string respecting quotes. Tight POSIX subset
    covering pcc's callsites (build command parsing)."""
    out: list[str] = []
    cur: list[str] = []
    i = 0
    n = len(s)
    while i < n:
        ch = s[i]
        if ch in " \t\n\r":
            if cur:
                out.append("".join(cur))
                cur = []
            i += 1
            continue
        if comments and ch == "#":
            break
        if ch in ('"', "'"):
            quote = ch
            i += 1
            while i < n and s[i] != quote:
                cur.append(s[i])
                i += 1
            if i < n:
                i += 1
            continue
        if ch == "\\" and i + 1 < n:
            cur.append(s[i + 1])
            i += 2
            continue
        cur.append(ch)
        i += 1
    if cur:
        out.append("".join(cur))
    return out


def quote(s: str) -> str:
    """Return a POSIX-safe quoted form of ``s``."""
    if not s:
        return "''"
    for c in s:
        if not (c.isalnum() or c in "_-./:="):
            return "'" + s.replace("'", "'\"'\"'") + "'"
    return s


def join(parts) -> str:
    return " ".join(quote(p) for p in parts)
