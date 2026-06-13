"""pcc-Python port of py_io_waitset.c (scalable vthread IO waitset structure).

Mirror of the C runtime structure ``pcc/py_runtime/src/py_io_waitset.c`` and of
the CPU-only oracle ``pcc/vthread/io_waitset_oracle.py`` (``PollWaitSet`` +
``KqueueSimWaitSet``). The dependency-free C twin is embedded by production
``pcc_threads.c``: it owns Darwin kqueue or the unique-fd live-poll fallback,
while stable scheduler nodes retain per-vthread GC roots. See
``docs/design/pcc-vthread-oracles.md``.

Two readiness backends, one abstraction (mirroring the oracle and the C file):

* POLL FALLBACK -- the level-triggered fallback. Readiness is FED explicitly
  through :meth:`set_ready` (standing in for ``poll(2)``'s revents), so it is
  deterministic and diffable against the oracle's ``PollWaitSet`` without live
  fds. On each :meth:`wait` every registered fd is rescanned against its fed
  readiness mask, exactly like the C ``while (*cur != NULL)`` loop. Available
  everywhere.

* DARWIN KQUEUE -- the real ``kqueue``/``kevent(2)`` notifier over LIVE fds.
  That is a C-only syscall path: it cannot be expressed in the pcc-Python subset
  (no live-fd syscalls), so this port does NOT implement it. Requesting it
  reports a machine-readable skip, mirroring how the oracle's
  ``real_kqueue_backend()`` returns ``SkippedReason`` and how the standalone C
  ``pcc_io_waitset_real_kqueue_skip`` reports the same path when kqueue is
  unavailable. The pcc-Python archive links the C scheduler/waitset kernel, so
  its production scheduler can own kqueue even though this pure Python mirror
  deliberately cannot issue live-fd syscalls itself.

Faithfulness note
-----------------
This port is written in the pcc-Python subset (classes + list + dict + int),
also valid CPython, so a host test can exercise it directly against the oracle,
exactly like ``tests/vthread/test_timer_oracle.py`` exercises the oracle. It
preserves the same observable semantics the C poll fallback guarantees:

  * interest-mask filtering (only requested bits + always-reported error bits);
  * inclusive deadline timeout (``deadline <= now``; ``deadline < 0`` infinite);
  * ready wins over timeout at the same tick;
  * one-shot delivery: a delivered / timed-out fd is unregistered.
"""

# POSIX poll event bits, matching pcc_vthread_fd_ready's use of poll(2) and the
# oracle's constants. Inlined at use sites where needed (pcc-Python module-level
# int constants can be zeroed in stripped library .o builds).
PCC_IO_POLLIN = 0x0001
PCC_IO_POLLOUT = 0x0004
PCC_IO_POLLERR = 0x0008
PCC_IO_POLLHUP = 0x0010
PCC_IO_POLLNVAL = 0x0020

# Bits reported even when not requested (C: revents & (events | POLLERR |
# POLLHUP | POLLNVAL)).
PCC_IO_ALWAYS_REPORTED = 0x0008 | 0x0010 | 0x0020

PCC_IO_WAITSET_BACKEND_POLL = 0
PCC_IO_WAITSET_BACKEND_KQUEUE = 1


class IoReadyEvent:
    """One delivered readiness event (mirrors oracle ReadyEvent / C
    PccIoReadyEvent)."""

    def __init__(self, fd: int, events: int) -> None:
        self.fd = fd
        self.events = events


class IoWaitResult:
    """Result of one :meth:`PollIoWaitSet.wait` drain.

    ``ready`` is a list of :class:`IoReadyEvent`; ``timed_out`` is a list of
    fds. They are disjoint per fd, matching the C poller and the oracle.
    """

    def __init__(self) -> None:
        self.ready = []  # type: list
        self.timed_out = []  # type: list


class PollIoWaitSet:
    """Level-triggered poll fallback (mirrors the C poll backend + oracle
    PollWaitSet).

    Rescans every registered fd on each :meth:`wait`, exactly like the C
    ``while (*cur != NULL)`` loop calling ``pcc_vthread_fd_ready`` per entry.
    An fd that stays ready and registered is reported on every wait.
    """

    def __init__(self) -> None:
        # fd -> [interest, deadline, ready_mask]. deadline < 0 == infinite.
        self._regs = {}  # type: dict

    def add(self, fd: int, interest: int, deadline: int, edge: int) -> int:
        # Poll fallback is inherently level-triggered; edge is ignored here
        # (matches the oracle and the C poll backend). Re-adding updates.
        existing = 0
        if fd in self._regs:
            existing = self._regs[fd][2]
        self._regs[fd] = [interest, deadline, existing]
        return 0

    def remove(self, fd: int) -> int:
        if fd in self._regs:
            del self._regs[fd]
            return 1
        return 0

    def count(self) -> int:
        return len(self._regs)

    def set_ready(self, fd: int, events: int) -> None:
        if fd in self._regs:
            reg = self._regs[fd]
            reg[2] = reg[2] | events

    def clear_ready(self, fd: int) -> None:
        if fd in self._regs:
            self._regs[fd][2] = 0

    def wait(self, now: int) -> IoWaitResult:
        result = IoWaitResult()
        # Snapshot keys so removal during the scan is safe.
        fds = []  # type: list
        for fd in self._regs:
            fds.append(fd)
        for fd in fds:
            if fd not in self._regs:
                continue
            reg = self._regs[fd]
            interest = reg[0]
            deadline = reg[1]
            ready_mask = reg[2]
            hit = ready_mask & (interest | (0x0008 | 0x0010 | 0x0020))
            expired = deadline >= 0 and deadline <= now
            if hit != 0:
                result.ready.append(IoReadyEvent(fd, hit))
                del self._regs[fd]
            elif expired:
                result.timed_out.append(fd)
                del self._regs[fd]
        return result


def kqueue_available() -> int:
    """Whether the real kqueue backend is available in THIS runtime tier.

    The pcc-Python port never provides the real kqueue syscall path (that is a
    C-only capability in ``py_io_waitset.c``), so this always returns 0 here.
    The C mirror's ``pcc_io_waitset_kqueue_available()`` returns 1 on
    Darwin/BSD. Callers that get 0 must use :class:`PollIoWaitSet`.
    """
    return 0


def real_kqueue_skip():
    """Machine-readable skip marker for the real-kqueue path in this port.

    Mirrors the oracle's ``real_kqueue_backend()`` SkippedReason and the C
    ``pcc_io_waitset_real_kqueue_skip``. Returns ``[path, reason]``.
    """
    return [
        "io_waitset.real_kqueue",
        (
            "real kqueue/kevent requires live-fd syscalls; the pcc-Python "
            "runtime port has no kqueue backend. The C runtime slice owns "
            "EVFILT_READ/EVFILT_WRITE via kevent(2) on Darwin/BSD."
        ),
    ]
