"""pcc.py_stdlib.os — skeleton replacement for ``os`` / ``os.path``.

Minimal surface: env, getcwd, listdir, exists, the ``os.path``
helpers pcc uses (join, basename, dirname, exists). Heavy lifting
delegates to extern libc.
"""
from __future__ import annotations

from pcc.extern import extern, c_int, c_int64, c_str, c_ptr


_getenv: "extern" = extern("getenv", (c_str,), c_str)
_setenv: "extern" = extern("setenv", (c_str, c_str, c_int), c_int)
_getcwd: "extern" = extern("getcwd", (c_str, c_int64), c_str)
_access: "extern" = extern("access", (c_str, c_int), c_int)
_getpid: "extern" = extern("getpid", (), c_int)


# POSIX file-access constants.
F_OK: int = 0
R_OK: int = 4
W_OK: int = 2
X_OK: int = 1

sep: str = "/"
linesep: str = "\n"


def getpid() -> int:
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
    """True if ``path`` exists on disk, via ``access(path, F_OK)``."""
    return _access(path, F_OK) == 0


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
        return p[i + 1:]

    @staticmethod
    def dirname(p: str) -> str:
        i = len(p) - 1
        while i >= 0 and p[i] != "/":
            i = i - 1
        if i < 0:
            return ""
        if i == 0:
            return "/"
        return p[:i]

    @staticmethod
    def exists(p: str) -> bool:
        return exists(p)


path = _path()
