"""pcc.py_stdlib.warnings — narrow ``warnings`` skeleton."""

from __future__ import annotations

from pcc.extern import extern, c_int, c_str

_fprintf: "extern" = extern("fprintf", (c_str, c_str), c_int, variadic=True)
_stderr_ptr: "extern" = extern("__stderrp", (), c_str)  # darwin symbol
_active_recorders = []


class WarningMessage:
    def __init__(self, message, category) -> None:
        self.message = message
        self.category = category


class catch_warnings:
    """Native subset of ``warnings.catch_warnings``.

    The record path preserves the part package initializers commonly rely on:
    warnings raised through this module are appended as ``WarningMessage``
    objects and nested contexts restore the outer recorder on exit.
    """

    def __init__(self, *, record: bool = False, module=None) -> None:
        self._record = record
        self._module = module
        self._log = []

    def __enter__(self):
        if self._record:
            _active_recorders.append(self._log)
            return self._log
        return None

    def __exit__(self, exc_type, exc_value, traceback) -> bool:
        if self._record and len(_active_recorders) > 0:
            _active_recorders.pop()
        return False


def warn(message: str, category=None, stacklevel: int = 1) -> None:
    """Write ``message`` to stderr prefixed with ``Warning: ``."""
    if len(_active_recorders) > 0:
        warning_category = category
        if warning_category is None:
            warning_category = UserWarning
        _active_recorders[-1].append(WarningMessage(message, warning_category))
        return None
    # The extern layer isn't wired for varargs strings yet; for the
    # pcc-hosted path we go through sys.stderr once that exists.
    raise NotImplementedError("warnings.warn awaits stderr / varargs extern wiring")


def filterwarnings(
    action: str,
    message: str = "",
    category=None,
    module: str = "",
    lineno: int = 0,
    append: bool = False,
) -> None:
    """No-op filter — pcc's self-host path doesn't maintain a
    warning-filter table yet."""
    return None


def simplefilter(
    action: str, category=None, lineno: int = 0, append: bool = False
) -> None:
    return None


class UserWarning(Warning):
    pass


class DeprecationWarning(Warning):
    pass
