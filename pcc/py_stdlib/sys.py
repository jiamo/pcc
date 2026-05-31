"""pcc.py_stdlib.sys — skeleton replacement for ``sys``.

Surface covers what pcc's own source uses: ``argv``, ``exit``,
``stderr.write``, ``platform``, ``version_info.major``. Everything
else is out of scope for P6C.4.
"""
from __future__ import annotations

from pcc.extern import extern, c_int, c_int64, c_str, c_ptr


# libc glue.
exit_c: "extern" = extern("exit", (c_int,), )  # noreturn; treat as void
_getenv: "extern" = extern("getenv", (c_str,), c_str)
_program_argc: "extern" = extern("py_program_argc", (), c_int64)
_program_argv: "extern" = extern("py_program_argv", (c_int64,), c_str)


def _load_argv() -> "list[str]":
    out: "list[str]" = []
    try:
        total = _program_argc()
    except NotImplementedError:
        return out
    i = 0
    while i < total:
        arg = _program_argv(i)
        if arg is None:
            break
        out.append(arg)
        i += 1
    return out


argv: "list[str]" = _load_argv()


def exit(code: int = 0) -> None:
    """``sys.exit(code)`` — calls ``libc exit(code)``, does not return."""
    exit_c(code)


class _VersionInfo:
    def __init__(self, major: int, minor: int, micro: int) -> None:
        self.major = major
        self.minor = minor
        self.micro = micro

    def __iter__(self):
        return iter((self.major, self.minor, self.micro))


class _Implementation:
    def __init__(self, name: str) -> None:
        self.name = name


class _Stream:
    def __init__(self, name: str) -> None:
        self.name = name

    def write(self, text: str) -> int:
        try:
            host_sys = __import__("sys")
            stream = getattr(host_sys, self.name)
            return stream.write(text)
        except Exception:
            return len(text)

    def flush(self) -> None:
        try:
            host_sys = __import__("sys")
            stream = getattr(host_sys, self.name)
            stream.flush()
        except Exception:
            pass


# pcc's self-host binary reports itself as CPython-3.13-compatible so
# that user code probing ``sys.version_info.major`` works the same
# way.
version_info: _VersionInfo = _VersionInfo(3, 13, 0)
version: str = "3.13.0 (pcc self-host)"
implementation: _Implementation = _Implementation("pcc")
stdout: _Stream = _Stream("stdout")
stderr: _Stream = _Stream("stderr")


# pcc only ships darwin/linux at the moment. The real platform is
# stamped in by the build via an environment variable or a generated
# constant file; this default is the development fallback.
platform: str = "darwin"


def getdefaultencoding() -> str:
    return "utf-8"


def intern(value: str) -> str:
    return value
