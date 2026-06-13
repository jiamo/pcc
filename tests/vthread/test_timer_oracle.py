"""Tests for the CPU-only scalable timer oracle.

Encodes the real invariants a later C slice must preserve when it replaces the
O(n)-insert sorted linked-list timer queue in pcc_threads.c:

* correct nondecreasing-deadline expiry order (FIFO among equal deadlines),
* cancellation,
* done/cancelled skip on expiry,
* root retention (an id stays registered until expired or cancelled),
* a large-N ordering stress,
* an operation-count proof that the min-heap beats the naive sorted list.

The second structure, ``HashedTimingWheelTimerQueue`` (a hierarchical timing
wheel), is validated in the same oracle-diff style: it must produce
byte-identical expiry sequences to the min-heap and the naive baseline across
all tick/slot geometries (equal-order / cancel / reschedule / root-retention /
done-skip parity), while its insert stays O(1)-amortized vs the naive list's
O(n^2) worst case.
"""

from __future__ import annotations

import random

import pytest

import vthread_timer_oracle as T


def _fresh():
    return T.MinHeapTimerQueue()


# Tick/slot geometries the wheel must be correct under. Includes tick==1
# (finest), coarse ticks that quantize multiple deadlines into one bucket, and
# tiny slot counts (2, 3) that force frequent hierarchical cascades.
WHEEL_GEOMETRIES = [
    (1, 256),
    (1, 2),
    (2, 4),
    (3, 3),
    (4, 4),
    (16, 2),
    (64, 8),
]


# -- correct expiry order --------------------------------------------------


def test_expire_order_nondecreasing_deadline():
    q = _fresh()
    # Insert out of deadline order.
    q.insert(50, timer_id=1)
    q.insert(10, timer_id=2)
    q.insert(30, timer_id=3)
    q.insert(20, timer_id=4)
    # now beyond all deadlines -> all expire in nondecreasing deadline order.
    got = q.expire_due(now=100)
    assert got == [2, 4, 3, 1]
    assert q.pending_count() == 0


def test_expire_partial_leaves_future_registered():
    q = _fresh()
    q.insert(10, 1)
    q.insert(20, 2)
    q.insert(30, 3)
    assert q.expire_due(now=20) == [1, 2]
    # id 3 is future: still registered (root retained).
    assert q.pending_count() == 1
    assert q.is_registered(3)
    assert q.expire_due(now=30) == [3]
    assert q.pending_count() == 0


def test_expire_boundary_inclusive():
    # C loop breaks on deadline_ms > now, i.e. deadline == now IS due.
    q = _fresh()
    q.insert(100, 1)
    assert q.expire_due(now=99) == []
    assert q.expire_due(now=100) == [1]


def test_fifo_among_equal_deadlines():
    q = _fresh()
    # Same deadline, insertion order 5,6,7,8 -> that is the expiry order,
    # mirroring the C `<= deadline` list walk (stable insert).
    for tid in (5, 6, 7, 8):
        q.insert(42, tid)
    assert q.expire_due(now=42) == [5, 6, 7, 8]


# -- cancellation ----------------------------------------------------------


def test_cancel_removes_from_expiry():
    q = _fresh()
    q.insert(10, 1)
    q.insert(20, 2)
    q.insert(30, 3)
    assert q.cancel(2) is True
    assert q.pending_count() == 2
    assert q.expire_due(now=100) == [1, 3]


def test_cancel_unknown_id_is_false():
    q = _fresh()
    q.insert(10, 1)
    assert q.cancel(999) is False
    assert q.cancel(1) is True
    assert q.cancel(1) is False  # already cancelled


def test_cancel_then_reinsert_same_id():
    q = _fresh()
    q.insert(10, 1)
    assert q.cancel(1) is True
    q.insert(50, 1)
    assert q.expire_due(now=10) == []  # old deadline is dead
    assert q.expire_due(now=50) == [1]


def test_reschedule_supersedes_old_deadline():
    # Re-inserting a live id reschedules it; the stale heap slot is skipped.
    q = _fresh()
    q.insert(10, 1)
    q.insert(90, 1)  # reschedule later without explicit cancel
    assert q.pending_count() == 1
    assert q.expire_due(now=10) == []
    assert q.expire_due(now=90) == [1]


# -- done / cancelled skip -------------------------------------------------


def test_done_skip_does_not_return_cancelled_entry():
    q = _fresh()
    q.insert(5, 1)
    q.insert(5, 2)
    q.cancel(1)
    # Both have the same due deadline; only the live one comes back.
    assert q.expire_due(now=5) == [2]


# -- root retention semantics ---------------------------------------------


def test_root_retained_until_expired_or_cancelled():
    q = _fresh()
    q.insert(100, 1)
    # Not yet due and not cancelled -> stays registered across polls.
    for now in (0, 10, 50, 99):
        assert q.expire_due(now=now) == []
        assert q.is_registered(1)
        assert q.pending_count() == 1
    # Expiry ends retention.
    assert q.expire_due(now=100) == [1]
    assert not q.is_registered(1)


def test_root_retention_ends_on_cancel():
    q = _fresh()
    q.insert(100, 7)
    assert q.is_registered(7)
    q.cancel(7)
    assert not q.is_registered(7)
    assert q.pending_count() == 0


# -- large-N ordering stress ----------------------------------------------


def test_large_n_expiry_matches_sorted_order():
    rng = random.Random(1234)
    n = 5000
    q = _fresh()
    deadlines = {}
    for tid in range(n):
        d = rng.randint(0, 10_000)
        deadlines[tid] = d
        q.insert(d, tid)
    assert q.pending_count() == n
    got = q.expire_due(now=10_000)
    # Nondecreasing deadline order.
    got_deadlines = [deadlines[t] for t in got]
    assert got_deadlines == sorted(got_deadlines)
    assert len(got) == n
    assert q.pending_count() == 0


def test_large_n_matches_naive_baseline_result():
    rng = random.Random(99)
    n = 2000
    heap = T.MinHeapTimerQueue()
    naive = T.NaiveSortedListTimerQueue()
    for tid in range(n):
        d = rng.randint(0, 5000)
        heap.insert(d, tid)
        naive.insert(d, tid)
    # Cancel a random subset in both.
    for tid in rng.sample(range(n), 300):
        heap.cancel(tid)
        naive.cancel(tid)
    # Expire in ascending time steps; the returned id *sets* must match at each
    # step (heap FIFO tiebreak equals the naive stable-insert tiebreak).
    for now in (1000, 2500, 4000, 5000):
        h = heap.expire_due(now)
        nv = naive.expire_due(now)
        assert h == nv, f"mismatch at now={now}: heap={h} naive={nv}"
    assert heap.pending_count() == naive.pending_count() == 0


# -- operation-count proof: heap beats naive sorted list -------------------


def test_op_count_heap_insert_is_logarithmic_vs_naive_linear():
    """Insert-cost oracle: worst-case for the naive list is ascending insert.

    Inserting in ascending deadline order forces the C-style list walk to the
    tail every time -> O(n^2) total comparisons. The heap sifts up in
    O(log n) per insert. This is the core justification for the replacement.
    """
    n = 4000
    heap = T.MinHeapTimerQueue()
    naive = T.NaiveSortedListTimerQueue()
    for tid in range(n):
        heap.insert(tid, tid)      # ascending -> naive worst case
        naive.insert(tid, tid)
    # Naive comparisons ~ n^2/2; heap comparisons ~ n*log2(n).
    assert naive.counts.comparisons > heap.counts.comparisons * 10
    # Sanity: heap total stays within a comfortable n*log2(n) envelope.
    import math

    envelope = n * (math.floor(math.log2(n)) + 2)
    assert heap.counts.comparisons <= envelope


def test_op_count_scales_sublinearly_with_n():
    """Doubling N should roughly double heap insert cost (n log n), while the
    naive list quadruples (n^2). Confirms asymptotic separation, not a constant.
    """

    def measure(n):
        heap = T.MinHeapTimerQueue()
        naive = T.NaiveSortedListTimerQueue()
        for tid in range(n):
            heap.insert(tid, tid)
            naive.insert(tid, tid)
        return heap.counts.comparisons, naive.counts.comparisons

    h1, nv1 = measure(1000)
    h2, nv2 = measure(2000)
    # Heap ratio ~2.x (n log n), naive ratio ~4x (n^2).
    heap_ratio = h2 / max(h1, 1)
    naive_ratio = nv2 / max(nv1, 1)
    assert heap_ratio < 2.5
    assert naive_ratio > 3.5


# ==========================================================================
# Hierarchical timing wheel (HashedTimingWheelTimerQueue) parity + op-count
# ==========================================================================
#
# The wheel is the second structure the C slice may adopt. Every functional
# invariant proven for the min-heap above must hold identically for the wheel
# under every tick/slot geometry, so these tests parametrize over
# WHEEL_GEOMETRIES and assert *byte-identical* results, not just "also correct".


def _wheel(tick, slots):
    return T.HashedTimingWheelTimerQueue(tick=tick, slots=slots)


def test_wheel_rejects_degenerate_geometry():
    with pytest.raises(ValueError):
        T.HashedTimingWheelTimerQueue(tick=0)
    with pytest.raises(ValueError):
        T.HashedTimingWheelTimerQueue(slots=1)


@pytest.mark.parametrize("tick,slots", WHEEL_GEOMETRIES)
def test_wheel_expire_order_nondecreasing_deadline(tick, slots):
    q = _wheel(tick, slots)
    q.insert(50, timer_id=1)
    q.insert(10, timer_id=2)
    q.insert(30, timer_id=3)
    q.insert(20, timer_id=4)
    assert q.expire_due(now=100) == [2, 4, 3, 1]
    assert q.pending_count() == 0


@pytest.mark.parametrize("tick,slots", WHEEL_GEOMETRIES)
def test_wheel_expire_partial_leaves_future_registered(tick, slots):
    q = _wheel(tick, slots)
    q.insert(10, 1)
    q.insert(20, 2)
    q.insert(30, 3)
    assert q.expire_due(now=20) == [1, 2]
    # id 3 is future: still registered (root retained).
    assert q.pending_count() == 1
    assert q.is_registered(3)
    assert q.expire_due(now=30) == [3]
    assert q.pending_count() == 0


@pytest.mark.parametrize("tick,slots", WHEEL_GEOMETRIES)
def test_wheel_expire_boundary_inclusive(tick, slots):
    # deadline == now IS due; deadline within an unfinished tick is NOT due
    # until now actually reaches it (exact deadline, not tick-rounded).
    q = _wheel(tick, slots)
    q.insert(100, 1)
    assert q.expire_due(now=99) == []
    assert q.expire_due(now=100) == [1]


@pytest.mark.parametrize("tick,slots", WHEEL_GEOMETRIES)
def test_wheel_fifo_among_equal_deadlines(tick, slots):
    q = _wheel(tick, slots)
    for tid in (5, 6, 7, 8):
        q.insert(42, tid)
    assert q.expire_due(now=42) == [5, 6, 7, 8]


@pytest.mark.parametrize("tick,slots", WHEEL_GEOMETRIES)
def test_wheel_cancel_removes_from_expiry(tick, slots):
    q = _wheel(tick, slots)
    q.insert(10, 1)
    q.insert(20, 2)
    q.insert(30, 3)
    assert q.cancel(2) is True
    assert q.pending_count() == 2
    assert q.expire_due(now=100) == [1, 3]


@pytest.mark.parametrize("tick,slots", WHEEL_GEOMETRIES)
def test_wheel_cancel_unknown_id_is_false(tick, slots):
    q = _wheel(tick, slots)
    q.insert(10, 1)
    assert q.cancel(999) is False
    assert q.cancel(1) is True
    assert q.cancel(1) is False


@pytest.mark.parametrize("tick,slots", WHEEL_GEOMETRIES)
def test_wheel_cancel_then_reinsert_same_id(tick, slots):
    q = _wheel(tick, slots)
    q.insert(10, 1)
    assert q.cancel(1) is True
    q.insert(50, 1)
    assert q.expire_due(now=10) == []  # old deadline is dead
    assert q.expire_due(now=50) == [1]


@pytest.mark.parametrize("tick,slots", WHEEL_GEOMETRIES)
def test_wheel_reschedule_supersedes_old_deadline(tick, slots):
    q = _wheel(tick, slots)
    q.insert(10, 1)
    q.insert(90, 1)  # reschedule later without explicit cancel
    assert q.pending_count() == 1
    assert q.expire_due(now=10) == []
    assert q.expire_due(now=90) == [1]


@pytest.mark.parametrize("tick,slots", WHEEL_GEOMETRIES)
def test_wheel_reschedule_earlier_deadline(tick, slots):
    # Reschedule to an EARLIER deadline: the new (sooner) fire wins, the stale
    # far bucket slot is skipped. Exercises a live entry moving down the wheel.
    q = _wheel(tick, slots)
    q.insert(900, 1)
    q.insert(10, 1)
    assert q.pending_count() == 1
    assert q.expire_due(now=10) == [1]
    assert q.expire_due(now=900) == []
    assert q.pending_count() == 0


@pytest.mark.parametrize("tick,slots", WHEEL_GEOMETRIES)
def test_wheel_done_skip_does_not_return_cancelled_entry(tick, slots):
    q = _wheel(tick, slots)
    q.insert(5, 1)
    q.insert(5, 2)
    q.cancel(1)
    assert q.expire_due(now=5) == [2]


@pytest.mark.parametrize("tick,slots", WHEEL_GEOMETRIES)
def test_wheel_root_retained_until_expired_or_cancelled(tick, slots):
    q = _wheel(tick, slots)
    q.insert(100, 1)
    for now in (0, 10, 50, 99):
        assert q.expire_due(now=now) == []
        assert q.is_registered(1)
        assert q.pending_count() == 1
    assert q.expire_due(now=100) == [1]
    assert not q.is_registered(1)


@pytest.mark.parametrize("tick,slots", WHEEL_GEOMETRIES)
def test_wheel_root_retention_ends_on_cancel(tick, slots):
    q = _wheel(tick, slots)
    q.insert(100, 7)
    assert q.is_registered(7)
    q.cancel(7)
    assert not q.is_registered(7)
    assert q.pending_count() == 0


@pytest.mark.parametrize("tick,slots", WHEEL_GEOMETRIES)
def test_wheel_far_future_deadline_uses_lazy_hierarchy(tick, slots):
    # A deadline far beyond level 0's window forces lazy higher levels + cascade
    # down as the clock advances; it must still fire at exactly its deadline.
    q = _wheel(tick, slots)
    far = tick * (slots ** 3) + 7  # comfortably several levels up
    q.insert(far, 1)
    q.insert(3, 2)
    assert q.expire_due(now=3) == [2]
    assert q.is_registered(1)  # still parked, cascading down
    assert q.expire_due(now=far - 1) == []
    assert q.expire_due(now=far) == [1]


# -- three-way parity: wheel == heap == naive -----------------------------


@pytest.mark.parametrize("tick,slots", WHEEL_GEOMETRIES)
def test_wheel_large_n_expiry_matches_sorted_order(tick, slots):
    rng = random.Random(1234)
    n = 3000
    q = _wheel(tick, slots)
    deadlines = {}
    for tid in range(n):
        d = rng.randint(0, 10_000)
        deadlines[tid] = d
        q.insert(d, tid)
    assert q.pending_count() == n
    got = q.expire_due(now=10_000)
    got_deadlines = [deadlines[t] for t in got]
    assert got_deadlines == sorted(got_deadlines)
    assert len(got) == n
    assert q.pending_count() == 0


@pytest.mark.parametrize("tick,slots", WHEEL_GEOMETRIES)
def test_wheel_matches_heap_and_naive_step_sequence(tick, slots):
    """The wheel must return the SAME id set/order as the heap and the naive
    baseline at every ascending expiry step, with interleaved cancels and
    reschedules. Byte-identical, not just correct.
    """
    rng = random.Random(2026)
    n = 1500
    heap = T.MinHeapTimerQueue()
    naive = T.NaiveSortedListTimerQueue()
    wheel = _wheel(tick, slots)
    for tid in range(n):
        d = rng.randint(0, 6000)
        heap.insert(d, tid)
        naive.insert(d, tid)
        wheel.insert(d, tid)
    # Cancel + reschedule a random subset in all three.
    for tid in rng.sample(range(n), 300):
        heap.cancel(tid)
        naive.cancel(tid)
        wheel.cancel(tid)
    for tid in rng.sample(range(n), 200):
        d = rng.randint(0, 6000)
        heap.insert(d, tid)
        naive.insert(d, tid)
        wheel.insert(d, tid)
    for now in (500, 1500, 3000, 4500, 6000):
        h = heap.expire_due(now)
        nv = naive.expire_due(now)
        w = wheel.expire_due(now)
        assert h == nv == w, f"mismatch at now={now}: heap={h} naive={nv} wheel={w}"
    assert heap.pending_count() == naive.pending_count() == wheel.pending_count() == 0


def test_wheel_randomized_parity_across_geometries():
    """Fuzz: random op streams (insert/cancel/reschedule/monotonic expire) must
    give byte-identical results for wheel vs heap vs naive across geometries.
    """

    def run(factory, ops):
        q = factory()
        log = []
        for op in ops:
            if op[0] == "ins":
                q.insert(op[1], op[2])
            elif op[0] == "can":
                log.append(("can", q.cancel(op[1])))
            elif op[0] == "exp":
                log.append(("exp", tuple(q.expire_due(op[1]))))
        log.append(("pend", q.pending_count()))
        return log

    geometries = WHEEL_GEOMETRIES
    for trial in range(60):
        rng = random.Random(trial)
        n = rng.randint(1, 40)
        ops = []
        now = 0
        for tid in range(n):
            ops.append(("ins", rng.randint(0, 1500), tid))
        for _ in range(rng.randint(0, 30)):
            k = rng.random()
            if k < 0.25:
                ops.append(("can", rng.randint(0, n - 1)))
            elif k < 0.45:
                ops.append(("ins", rng.randint(0, 1500), rng.randint(0, n - 1)))
            else:
                now += rng.randint(0, 150)
                ops.append(("exp", now))
        ops.append(("exp", 10_000_000))
        heap = run(T.MinHeapTimerQueue, ops)
        naive = run(T.NaiveSortedListTimerQueue, ops)
        tick, slots = geometries[trial % len(geometries)]
        wheel = run(lambda: _wheel(tick, slots), ops)
        assert heap == naive == wheel, (
            f"trial={trial} tick={tick} slots={slots}\n"
            f"heap ={heap}\nnaive={naive}\nwheel={wheel}"
        )


# -- op-count proof: wheel insert is O(1)-amortized vs naive linear --------


def test_wheel_op_count_insert_is_constant_amortized_vs_naive_linear():
    """Ascending inserts are the naive list's O(n^2) worst case; the wheel places
    each entry in O(1) amortized (a bounded number of level hops), so its insert
    comparison count is linear in n while the naive baseline is quadratic.
    """
    n = 4000
    wheel = T.HashedTimingWheelTimerQueue()
    naive = T.NaiveSortedListTimerQueue()
    for tid in range(n):
        wheel.insert(tid, tid)  # ascending -> naive worst case
        naive.insert(tid, tid)
    # Naive comparisons ~ n^2/2; wheel ~ n (one placement per insert).
    assert naive.counts.comparisons > wheel.counts.comparisons * 10
    # Wheel insert cost is exactly one placement charge per insert (O(1)).
    assert wheel.counts.comparisons == n


def test_wheel_op_count_scales_linearly_with_n():
    """Doubling N doubles wheel insert cost (O(1) each) but quadruples the naive
    list (O(n^2)). Confirms asymptotic separation, not a constant factor.
    """

    def measure(n):
        wheel = T.HashedTimingWheelTimerQueue()
        naive = T.NaiveSortedListTimerQueue()
        for tid in range(n):
            wheel.insert(tid, tid)
            naive.insert(tid, tid)
        return wheel.counts.comparisons, naive.counts.comparisons

    w1, nv1 = measure(1000)
    w2, nv2 = measure(2000)
    wheel_ratio = w2 / max(w1, 1)
    naive_ratio = nv2 / max(nv1, 1)
    assert wheel_ratio < 2.5  # ~2x (linear)
    assert naive_ratio > 3.5  # ~4x (quadratic)
