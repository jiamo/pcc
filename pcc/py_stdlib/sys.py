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


# Populated from main's argc/argv by the startup glue (P6C.5 work).
argv: "list[str]" = []


def exit(code: int = 0) -> None:
    """``sys.exit(code)`` — calls ``libc exit(code)``, does not return."""
    exit_c(code)


class _VersionInfo:
    def __init__(self, major: int, minor: int, micro: int) -> None:
        self.major = major
        self.minor = minor
        self.micro = micro


# pcc's self-host binary reports itself as CPython-3.13-compatible so
# that user code probing ``sys.version_info.major`` works the same
# way.
version_info: _VersionInfo = _VersionInfo(3, 13, 0)
version: str = "3.13.0 (pcc self-host)"


# pcc only ships darwin/linux at the moment. The real platform is
# stamped in by the build via an environment variable or a generated
# constant file; this default is the development fallback.
platform: str = "darwin"
