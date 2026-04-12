"""pcc.py_stdlib.fcntl — no-op ``fcntl`` skeleton.

pcc only uses ``fcntl.flock`` around the PLY table build, and the
existing call site already tolerates ``fcntl is None`` on non-POSIX
hosts. This stub keeps the same contract for the self-host compile:
``flock`` is a no-op, ``LOCK_EX`` / ``LOCK_UN`` / ``LOCK_SH`` are
integer sentinels, and anything else raises NotImplementedError so
callers that need real file locking fail loudly.
"""
from __future__ import annotations


LOCK_SH = 1
LOCK_EX = 2
LOCK_NB = 4
LOCK_UN = 8


def flock(fd, operation: int) -> None:
    """No-op file lock. The self-host build serializes PLY table
    generation via a different mechanism (single-writer cache dir);
    process-level locking is not part of the self-host invariant."""
    return None


def fcntl(fd, cmd: int, arg=0):
    raise NotImplementedError("fcntl.fcntl awaits an F_* extern binding")


def ioctl(fd, request: int, arg=0, mutate_flag: bool = True):
    raise NotImplementedError("fcntl.ioctl awaits a TIOC* extern binding")
