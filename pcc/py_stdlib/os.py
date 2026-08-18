"""pcc.py_stdlib.os — skeleton replacement for ``os`` / ``os.path``.

Minimal surface: env, getcwd, listdir, exists, the ``os.path``
helpers pcc uses (join, basename, dirname, exists). Heavy lifting
delegates to extern libc.
"""

from __future__ import annotations

from pcc.extern import extern, c_int, c_int64, c_str, c_ptr, c_rawptr

_getenv = extern("getenv", (c_str,), c_rawptr)
_setenv = extern("setenv", (c_str, c_str, c_int), c_int)
_getcwd = extern("getcwd", (c_str, c_int64), c_rawptr)
_access = extern("access", (c_str, c_int), c_int)
_getpid = extern("getpid", (), c_int)


# POSIX file-access constants.
F_OK: int = 0
R_OK: int = 4
W_OK: int = 2
X_OK: int = 1

sep: str = "/"
linesep: str = "\n"


def getpid() -> int:
    # ``_getpid`` lowers to a direct ``bl _getpid`` extern call (pure C
    # ABI). There is no Python-level failure path the ``try/except``
    # could catch — the previous host-CPython fallback was stale code
    # from before extern codegen landed, and would pull libpython back
    # into the self-host closure via the ``import os`` walker hit.
    return _getpid()


def getenv(key: str, default: str = "") -> str:
    # In the self-host runtime, ``_getenv`` returns either a valid
    # C-string pointer or NULL. The pcc→C-string marshalling
    # converts the Python str to a NUL-terminated buffer, and the
    # return value is marshalled back through py_str_new when the
    # pointer is non-NULL. Both conversions are P6C.1 FFI work.
    raise NotImplementedError(
        "os.getenv needs the P6C.1 extern string-return marshalling"
    )


def exists(path: str) -> bool:
    """True if ``path`` exists on disk, via ``access(path, F_OK)``.

    ``_access`` lowers to ``bl _access`` (pure C ABI). Callers that want
    a Python-style ``OSError`` on syscall failure should rely on errno
    inspection — the extern returns ``-1`` on error, never raises.
    """
    return _access(path, F_OK) == 0


class PathLike:
    """Base protocol for objects that provide a filesystem path."""

    def __fspath__(self):
        raise NotImplementedError("PathLike subclasses must define __fspath__")


def fspath(path):
    """Return the filesystem representation of a path-like object."""
    if isinstance(path, (str, bytes)):
        return path
    try:
        result = path.__fspath__()
    except AttributeError:
        raise TypeError("expected str, bytes or os.PathLike object")
    if not isinstance(result, (str, bytes)):
        raise TypeError("__fspath__() must return str or bytes")
    return result


class _path:
    """``os.path`` namespace."""

    @staticmethod
    def join(*parts: str) -> str:
        if not parts:
            return ""
        out = parts[0]
        for p in parts[1:]:
            if not p:
                continue
            if p.startswith("/"):
                out = p
                continue
            if out.endswith("/"):
                out = out + p
            else:
                out = out + "/" + p
        return out

    @staticmethod
    def basename(p: str) -> str:
        i = len(p) - 1
        while i >= 0 and p[i] != "/":
            i = i - 1
        return p[i + 1 :]

    @staticmethod
    def dirname(p: str) -> str:
        i = len(p) - 1
        while i >= 0 and p[i] != "/":
            i = i - 1
        if i < 0:
            return ""
        head = p[: i + 1]
        # CPython strips trailing slashes unless the head is all slashes, so
        # dirname("/a//b") is "/a" and dirname("//") stays "//".
        if head != "/" * len(head):
            head = head.rstrip("/")
        return head

    @staticmethod
    def exists(p: str) -> bool:
        return exists(p)

    @staticmethod
    def splitext(p: str):
        slash = -1
        dot = -1
        i = 0
        while i < len(p):
            if p[i] == "/":
                slash = i
                dot = -1
            elif p[i] == ".":
                dot = i
            i += 1
        if dot <= slash + 1:
            return (p, "")
        return (p[:dot], p[dot:])

    @staticmethod
    def normpath(p: str) -> str:
        absolute = p.startswith("/")
        parts = []
        for part in p.split("/"):
            if part == "" or part == ".":
                continue
            if part == "..":
                if parts and parts[-1] != "..":
                    parts.pop()
                elif not absolute:
                    parts.append(part)
                continue
            parts.append(part)
        out = "/".join(parts)
        if absolute:
            out = "/" + out
        if out == "":
            return "/" if absolute else "."
        return out

    @staticmethod
    def isabs(p: str) -> bool:
        return p.startswith("/")

    @staticmethod
    def commonpath(paths) -> str:
        if not paths:
            raise ValueError("commonpath() arg is an empty sequence")
        split_paths = [p.split("/") for p in paths]
        prefix = []
        i = 0
        while True:
            if i >= len(split_paths[0]):
                break
            part = split_paths[0][i]
            for pieces in split_paths[1:]:
                if i >= len(pieces) or pieces[i] != part:
                    return "/".join(prefix)
            prefix.append(part)
            i += 1
        return "/".join(prefix)


path = _path()
