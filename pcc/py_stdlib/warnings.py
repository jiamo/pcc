"""pcc.py_stdlib.warnings — narrow ``warnings`` skeleton."""
from __future__ import annotations

from pcc.extern import extern, c_int, c_str


_fprintf: "extern" = extern("fprintf", (c_str, c_str), c_int, variadic=True)
_stderr_ptr: "extern" = extern("__stderrp", (), c_str)  # darwin symbol


def warn(message: str, category=None, stacklevel: int = 1) -> None:
    """Write ``message`` to stderr prefixed with ``Warning: ``."""
    # The extern layer isn't wired for varargs strings yet; for the
    # pcc-hosted path we go through sys.stderr once that exists.
    raise NotImplementedError(
        "warnings.warn awaits stderr / varargs extern wiring"
    )


def filterwarnings(action: str, message: str = "", category=None,
                   module: str = "", lineno: int = 0, append: bool = False) -> None:
    """No-op filter — pcc's self-host path doesn't maintain a
    warning-filter table yet."""
    return None


def simplefilter(action: str, category=None, lineno: int = 0,
                 append: bool = False) -> None:
    return None


class UserWarning(Warning):
    pass


class DeprecationWarning(Warning):
    pass
