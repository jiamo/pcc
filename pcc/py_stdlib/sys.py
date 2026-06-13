"""pcc.py_stdlib.sys — skeleton replacement for ``sys``.

Surface covers what pcc's own source uses: ``argv``, ``exit``,
``stderr.write``, ``platform``, ``byteorder``, ``version_info.major``. Everything
else is out of scope for P6C.4.
"""

from __future__ import annotations

import sys as _native_sys

from pcc.extern import c_int, extern

# libc glue.
exit_c: "extern" = extern("exit", (c_int,))  # noreturn; treat as void

# Use the compiler's kernel-backed argv projection to construct real pcc
# strings. Publishing this module must not expose raw extern ``char *`` values
# as Python objects.
argv = _native_sys.argv


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
        if self.name == "stdout":
            return _native_sys.stdout.write(text)
        return _native_sys.stderr.write(text)

    def flush(self) -> None:
        if self.name == "stdout":
            _native_sys.stdout.flush()
        else:
            _native_sys.stderr.flush()


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
byteorder: str = "little"


def getdefaultencoding() -> str:
    return "utf-8"


def intern(value: str) -> str:
    return value
