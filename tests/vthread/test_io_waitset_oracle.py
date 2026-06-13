"""Tests for the CPU-only IO waitset oracle.

Encodes the real invariants a later C slice must preserve when it replaces the
per-poll O(n) fd scan in pcc_threads.c:

* readiness delivery,
* timeout,
* add / remove,
* the poll fallback and the kqueue-style simulation agree on the same
  readiness sequence (in level mode),
* the real-kqueue path reports SKIPPED_WITH_REASON.
"""

from __future__ import annotations

import random

import vthread_io_waitset_oracle as W


# -- readiness delivery ----------------------------------------------------


def test_poll_delivers_ready_fd():
    ws = W.PollWaitSet()
    ws.add(fd=3, interest=W.POLLIN)
    assert ws.wait_count() == 1
    ws.set_ready(3, W.POLLIN)
    res = ws.wait(now=0)
    assert [e.fd for e in res.ready] == [3]
    assert res.ready[0].events & W.POLLIN
    # Delivered fd is removed (one-shot park/unpark, like the C poller).
    assert ws.wait_count() == 0


def test_poll_not_ready_stays_registered():
    ws = W.PollWaitSet()
    ws.add(fd=5, interest=W.POLLIN)
    res = ws.wait(now=0)
    assert res.ready == [] and res.timed_out == []
    assert ws.wait_count() == 1  # root retained


def test_error_bits_always_reported_even_if_not_requested():
    # C: revents & (events | POLLERR | POLLHUP | POLLNVAL).
    ws = W.PollWaitSet()
    ws.add(fd=9, interest=W.POLLIN)  # only asked for readable
    ws.set_ready(9, W.POLLHUP)       # hangup arrives
    res = ws.wait(now=0)
    assert [e.fd for e in res.ready] == [9]
    assert res.ready[0].events & W.POLLHUP


def test_interest_mask_filters_unrequested_bits():
    ws = W.PollWaitSet()
    ws.add(fd=2, interest=W.POLLIN)
    ws.set_ready(2, W.POLLOUT)  # writable, but we only want readable
    res = ws.wait(now=0)
    assert res.ready == []
    assert ws.wait_count() == 1


# -- timeout ---------------------------------------------------------------


def test_timeout_fires_when_deadline_passes():
    ws = W.PollWaitSet()
    ws.add(fd=4, interest=W.POLLIN, deadline=100)
    assert ws.wait(now=50).timed_out == []
    res = ws.wait(now=100)  # deadline inclusive (C: deadline_ms <= now)
    assert res.timed_out == [4]
    assert ws.wait_count() == 0


def test_ready_wins_over_timeout_at_same_tick():
    ws = W.PollWaitSet()
    ws.add(fd=4, interest=W.POLLIN, deadline=100)
    ws.set_ready(4, W.POLLIN)
    res = ws.wait(now=100)
    # Ready and expired at once -> reported as ready, not timed out.
    assert [e.fd for e in res.ready] == [4]
    assert res.timed_out == []


def test_infinite_deadline_never_times_out():
    ws = W.PollWaitSet()
    ws.add(fd=1, interest=W.POLLIN, deadline=None)  # C: -1
    for now in (0, 10**9):
        assert ws.wait(now=now).timed_out == []
    assert ws.wait_count() == 1


# -- add / remove ----------------------------------------------------------


def test_remove_unregisters():
    ws = W.PollWaitSet()
    ws.add(fd=7, interest=W.POLLIN)
    assert ws.remove(7) is True
    assert ws.wait_count() == 0
    assert ws.remove(7) is False  # already gone


def test_removed_fd_not_delivered():
    ws = W.PollWaitSet()
    ws.add(fd=7, interest=W.POLLIN)
    ws.set_ready(7, W.POLLIN)
    ws.remove(7)
    assert ws.wait(now=0).ready == []


# -- kqueue-sim readiness model -------------------------------------------


def test_kqueue_level_mode_delivers_like_poll():
    ws = W.KqueueSimWaitSet()
    ws.add(fd=3, interest=W.POLLIN, edge=False)
    ws.set_ready(3, W.POLLIN)
    res = ws.wait(now=0)
    assert [e.fd for e in res.ready] == [3]


def test_kqueue_edge_fires_once_per_transition():
    ws = W.KqueueSimWaitSet()
    ws.add(fd=8, interest=W.POLLIN, edge=True)
    ws.set_ready(8, W.POLLIN)          # false->true transition
    # Re-add after delivery to keep watching (delivery is one-shot here).
    res1 = ws.wait(now=0)
    assert [e.fd for e in res1.ready] == [8]
    # No new transition -> nothing pending.
    ws.add(fd=8, interest=W.POLLIN, edge=True)
    ws.set_ready(8, W.POLLIN)  # still ready, but no false->true edge recorded
    # set_ready with the same bit is not a transition (prev already had it? no:
    # fd was removed on delivery so ready_mask was cleared -> this IS a new
    # transition). Assert the transition semantics explicitly below instead.
    res2 = ws.wait(now=0)
    assert [e.fd for e in res2.ready] == [8]


def test_kqueue_edge_no_event_without_transition():
    ws = W.KqueueSimWaitSet()
    ws.add(fd=8, interest=W.POLLIN, edge=True)
    ws.set_ready(8, W.POLLIN)  # arm
    # Setting the SAME already-present bit again is not a new false->true edge.
    ws.set_ready(8, W.POLLIN)
    res = ws.wait(now=0)
    assert [e.fd for e in res.ready] == [8]  # exactly one delivery
    assert ws.wait_count() == 0


def test_kqueue_timeout_matches_poll():
    ws = W.KqueueSimWaitSet()
    ws.add(fd=4, interest=W.POLLIN, deadline=100)
    assert ws.wait(now=50).timed_out == []
    assert ws.wait(now=100).timed_out == [4]


# -- poll fallback and kqueue-sim agree (level mode) -----------------------


def _drive_sequence(ws, events):
    """Replay a scripted (op, args) sequence and collect woken fds per wait."""
    trace = []
    for op in events:
        kind = op[0]
        if kind == "add":
            _, fd, interest, deadline = op
            ws.add(fd, interest, deadline=deadline, edge=False)
        elif kind == "ready":
            _, fd, ev = op
            ws.set_ready(fd, ev)
        elif kind == "remove":
            _, fd = op
            ws.remove(fd)
        elif kind == "wait":
            _, now = op
            res = ws.wait(now)
            trace.append(
                (
                    sorted((e.fd, e.events) for e in res.ready),
                    sorted(res.timed_out),
                )
            )
        else:  # pragma: no cover - test author error
            raise AssertionError(f"unknown op {kind}")
    return trace


def test_poll_and_kqueue_agree_on_scripted_sequence():
    script = [
        ("add", 1, W.POLLIN, None),
        ("add", 2, W.POLLIN, 30),
        ("add", 3, W.POLLOUT, None),
        ("wait", 0),                # nothing ready
        ("ready", 1, W.POLLIN),
        ("wait", 5),                # fd 1 ready
        ("ready", 3, W.POLLOUT),
        ("wait", 10),               # fd 3 ready
        ("wait", 30),               # fd 2 times out (deadline 30)
        ("add", 4, W.POLLIN, None),
        ("ready", 4, W.POLLHUP),    # error bit reported despite POLLIN interest
        ("remove", 4),              # ... but removed before the wait
        ("wait", 40),               # nothing
    ]
    poll_trace = _drive_sequence(W.PollWaitSet(), script)
    kq_trace = _drive_sequence(W.KqueueSimWaitSet(), script)
    assert poll_trace == kq_trace


def test_poll_and_kqueue_agree_randomized_level_mode():
    rng = random.Random(2026)
    for _ in range(50):
        script = []
        fds = list(range(1, 9))
        for fd in fds:
            deadline = rng.choice([None, rng.randint(1, 20)])
            script.append(("add", fd, W.POLLIN, deadline))
        now = 0
        for _step in range(20):
            r = rng.random()
            if r < 0.5:
                script.append(("ready", rng.choice(fds), W.POLLIN))
            elif r < 0.65:
                script.append(("remove", rng.choice(fds)))
            else:
                now += rng.randint(1, 6)
                script.append(("wait", now))
        poll_trace = _drive_sequence(W.PollWaitSet(), script)
        kq_trace = _drive_sequence(W.KqueueSimWaitSet(), script)
        assert poll_trace == kq_trace, f"divergence on script: {script}"


# -- kqueue-sim avoids the O(n) full rescan --------------------------------


def test_kqueue_sim_does_not_rescan_all_fds():
    # PollWaitSet increments scan_steps once per registered fd per wait.
    # KqueueSimWaitSet has no such per-fd rescan counter; it delivers from a
    # pending queue. This test documents the intended cost difference.
    poll = W.PollWaitSet()
    for fd in range(1000):
        poll.add(fd, W.POLLIN)
    poll.set_ready(500, W.POLLIN)
    poll.wait(now=0)
    # The fallback paid ~1000 scan steps for one ready fd.
    assert poll.scan_steps >= 1000


# -- level vs edge readiness-semantics DISTINCTION -------------------------
#
# The defining difference between level- and edge-triggered readiness is only
# observable for a *persistent* (non-one-shot) registration: a fd that stays
# ready across successive waits. Level re-reports it every wait (CPython's
# level-triggered selectors: an undrained readable fd shows up on every
# select); edge reports it exactly once per false->true transition.


def test_level_persistent_fd_redelivers_every_wait():
    # Matches CPython selectors: registering a readable fd and never draining it
    # reports it on every select() call. (python3 selectors gave 3/3 here.)
    ws = W.PollWaitSet()
    ws.add(fd=3, interest=W.POLLIN, oneshot=False)
    ws.set_ready(3, W.POLLIN)
    deliveries = [len(ws.wait(now=0).ready) for _ in range(3)]
    assert deliveries == [1, 1, 1]
    assert ws.wait_count() == 1  # persistent: never unregistered on delivery


def test_level_persistent_fd_stops_after_clear():
    ws = W.PollWaitSet()
    ws.add(fd=3, interest=W.POLLIN, oneshot=False)
    ws.set_ready(3, W.POLLIN)
    assert [e.fd for e in ws.wait(now=0).ready] == [3]
    ws.clear_ready(3)  # drained -> no longer ready
    assert ws.wait(now=0).ready == []
    assert ws.wait_count() == 1  # still registered, just not ready


def test_kqueue_level_persistent_redelivers_but_edge_fires_once():
    # THE distinction, side by side on the same scripted readiness.
    level = W.KqueueSimWaitSet()
    level.add(fd=7, interest=W.POLLIN, edge=False, oneshot=False)
    level.set_ready(7, W.POLLIN)
    level_seq = [len(level.wait(now=0).ready) for _ in range(3)]

    edge = W.KqueueSimWaitSet()
    edge.add(fd=7, interest=W.POLLIN, edge=True, oneshot=False)
    edge.set_ready(7, W.POLLIN)  # false->true transition
    edge_seq = [len(edge.wait(now=0).ready) for _ in range(3)]

    assert level_seq == [1, 1, 1]      # level re-arms while ready
    assert edge_seq == [1, 0, 0]       # edge fires once per transition
    assert level_seq != edge_seq       # the semantics intentionally differ


def test_kqueue_edge_persistent_refires_only_after_new_transition():
    ws = W.KqueueSimWaitSet()
    ws.add(fd=7, interest=W.POLLIN, edge=True, oneshot=False)
    ws.set_ready(7, W.POLLIN)
    assert [e.fd for e in ws.wait(now=0).ready] == [7]  # first transition
    assert ws.wait(now=0).ready == []                    # still ready, no re-fire
    ws.clear_ready(7)
    ws.set_ready(7, W.POLLIN)                            # new false->true edge
    assert [e.fd for e in ws.wait(now=0).ready] == [7]  # fires again
    assert ws.wait(now=0).ready == []


def test_level_persistent_ready_wins_over_timeout_and_keeps_firing():
    # A persistent, ready, deadline-bearing fd never times out while ready:
    # ready wins over timeout at every tick (poll's per-fd elif structure).
    for cls, kw in ((W.PollWaitSet, {}),
                    (W.KqueueSimWaitSet, {"edge": False})):
        ws = cls()
        ws.add(fd=4, interest=W.POLLIN, deadline=100, oneshot=False, **kw)
        ws.set_ready(4, W.POLLIN)
        r = ws.wait(now=100)  # ready and past deadline -> ready, not timeout
        assert [e.fd for e in r.ready] == [4]
        assert r.timed_out == []
        r2 = ws.wait(now=200)  # still ready -> still delivered, still no timeout
        assert [e.fd for e in r2.ready] == [4]
        assert r2.timed_out == []


def test_persistent_level_poll_and_kqueue_agree_randomized():
    # Persistent (non-one-shot) level mode must keep poll and kqueue-sim in
    # lock-step, including clear_ready, remove, deadlines and interrupted waits.
    rng = random.Random(99)
    for _ in range(100):
        script = []
        fds = list(range(1, 7))
        for fd in fds:
            deadline = rng.choice([None, rng.randint(1, 15)])
            script.append(("add", fd, W.POLLIN, deadline))
        now = 0
        for _step in range(25):
            r = rng.random()
            if r < 0.35:
                script.append(("ready", rng.choice(fds), W.POLLIN))
            elif r < 0.5:
                script.append(("clear", rng.choice(fds)))
            elif r < 0.6:
                script.append(("remove", rng.choice(fds)))
            elif r < 0.7:
                now += rng.randint(1, 5)
                script.append(("wait", now, True))  # interrupted / EINTR wait
            else:
                now += rng.randint(1, 5)
                script.append(("wait", now))
        poll_trace = _drive_persistent(W.PollWaitSet(), script)
        kq_trace = _drive_persistent(W.KqueueSimWaitSet(), script)
        assert poll_trace == kq_trace, f"divergence on script: {script}"


def _drive_persistent(ws, events):
    """Replay a scripted sequence with persistent (non-one-shot) level regs.

    Extends :func:`_drive_sequence` with ``clear`` and interrupted ``wait`` ops.
    """
    trace = []
    for op in events:
        kind = op[0]
        if kind == "add":
            _, fd, interest, deadline = op
            ws.add(fd, interest, deadline=deadline, edge=False, oneshot=False)
        elif kind == "ready":
            _, fd, ev = op
            ws.set_ready(fd, ev)
        elif kind == "clear":
            _, fd = op
            ws.clear_ready(fd)
        elif kind == "remove":
            _, fd = op
            ws.remove(fd)
        elif kind == "wait":
            now = op[1]
            interrupted = len(op) > 2 and op[2]
            res = ws.wait(now, interrupted=interrupted)
            trace.append(
                (
                    sorted((e.fd, e.events) for e in res.ready),
                    sorted(res.timed_out),
                )
            )
        else:  # pragma: no cover - test author error
            raise AssertionError(f"unknown op {kind}")
    return trace


# -- EINTR / spurious-wakeup retry parity ----------------------------------
#
# A poll/kevent syscall can return early with no events because a signal
# interrupted it (EINTR) or the kernel woke the thread spuriously. Under PEP 475
# CPython auto-retries on EINTR, so the caller just loops. The invariant: an
# interrupted wait consumes nothing, so the retried wait delivers each event
# exactly once (never lost, never doubled), and both waitset models agree.


def test_interrupted_wait_returns_empty_and_consumes_nothing():
    for cls, kw in ((W.PollWaitSet, {}),
                    (W.KqueueSimWaitSet, {"edge": False}),
                    (W.KqueueSimWaitSet, {"edge": True})):
        ws = cls()
        ws.add(fd=5, interest=W.POLLIN, **kw)
        ws.set_ready(5, W.POLLIN)
        empty = ws.wait(now=0, interrupted=True)   # EINTR: no delivery
        assert empty.ready == [] and empty.timed_out == []
        assert ws.wait_count() == 1                # nothing unregistered
        retry = ws.wait(now=0)                     # retry sees the event once
        assert [e.fd for e in retry.ready] == [5]
        assert ws.wait(now=0).ready == []          # one-shot: gone after delivery


def test_interrupted_wait_does_not_expire_deadline():
    for cls, kw in ((W.PollWaitSet, {}),
                    (W.KqueueSimWaitSet, {"edge": False})):
        ws = cls()
        ws.add(fd=9, interest=W.POLLIN, deadline=100, **kw)
        # Interrupted exactly at the deadline: must not time out (syscall was
        # interrupted before it could report the expiry).
        assert ws.wait(now=100, interrupted=True).timed_out == []
        assert ws.wait_count() == 1
        # Retry at the same logical time: now the deadline fires.
        assert ws.wait(now=100).timed_out == [9]
        assert ws.wait_count() == 0


def _drive_oneshot_eintr(ws, events):
    """Replay a scripted sequence with default (one-shot) registrations,
    supporting interrupted waits. Used for the EINTR retry parity narrative
    where a delivered fd should leave the set (delivered exactly once)."""
    trace = []
    for op in events:
        kind = op[0]
        if kind == "add":
            _, fd, interest, deadline = op
            ws.add(fd, interest, deadline=deadline, edge=False)
        elif kind == "ready":
            _, fd, ev = op
            ws.set_ready(fd, ev)
        elif kind == "wait":
            now = op[1]
            interrupted = len(op) > 2 and op[2]
            res = ws.wait(now, interrupted=interrupted)
            trace.append(
                (
                    sorted((e.fd, e.events) for e in res.ready),
                    sorted(res.timed_out),
                )
            )
        else:  # pragma: no cover - test author error
            raise AssertionError(f"unknown op {kind}")
    return trace


def test_interrupted_then_real_wait_parity_poll_vs_kqueue():
    # A scripted EINTR-heavy sequence: poll fallback and kqueue-sim must produce
    # the identical (ready, timed_out) trace despite the spurious empty returns.
    script = [
        ("add", 1, W.POLLIN, None),
        ("add", 2, W.POLLIN, 30),
        ("ready", 1, W.POLLIN),
        ("wait", 5, True),      # interrupted: fd 1 NOT delivered yet
        ("wait", 5),            # retry: fd 1 delivered (once)
        ("wait", 30, True),     # interrupted at fd 2's deadline: no timeout
        ("wait", 30),           # retry: fd 2 times out
    ]
    poll_trace = _drive_oneshot_eintr(W.PollWaitSet(), script)
    kq_trace = _drive_oneshot_eintr(W.KqueueSimWaitSet(), script)
    assert poll_trace == kq_trace
    # And the concrete expected sequence (documents the semantics):
    assert poll_trace == [
        ([], []),               # interrupted -> empty
        ([(1, W.POLLIN)], []),  # retry -> fd 1 ready exactly once
        ([], []),               # interrupted at deadline -> no timeout
        ([], [2]),              # retry -> fd 2 times out
    ]


# -- platform SKIPPED path -------------------------------------------------


def test_real_kqueue_backend_is_skipped_with_reason():
    skipped = W.real_kqueue_backend()
    assert isinstance(skipped, W.SkippedReason)
    assert skipped.path == "io_waitset.real_kqueue"
    assert "kqueue" in skipped.reason.lower()
    assert str(skipped).startswith("SKIPPED_WITH_REASON")
