"""Ring chain microbenchmark — pcc port of BoCPy's ``examples/benchmark.py``.

Mirrors the structure of BoCPy's ring chain at small scale, with pcc's
``threading.Lock`` instead of ``@when`` decorators and BoCPy's Cown class.

Setup:
- A ring of RING_SIZE shared slots (``vals[]``), each protected by its
  own ``Lock`` (``locks[]``).
- N_CHAINS worker threads. Each chain holds a ``head`` index and steps
  through the ring, acquiring a window of GROUP_SIZE adjacent slots
  per step in canonical (ascending index) order — the BOC trick that
  makes deadlock impossible by construction.
- Inside the critical section, runs a small CPU loop (``cpu_work``)
  and increments ``vals[head]`` so the final-state invariant
  ``sum(vals) == N_CHAINS * ITERS`` proves correctness under
  contention.

Final invariant: ``sum(vals) == N_CHAINS * ITERS``. Same correctness
contract as BoCPy's ring (every chain-step must be observed in the
ring).

Known status (2026-05-08):
- The shared-receiver Lock path is fixed and locked by
  ``test_pthread_lock_serializes_shared_list_updates``.
- The ``locks[i].acquire()`` list-indexed receiver path used here
  loses ~5%–13% updates per run. Tracked at
  ``docs/investigations/threading-list-index-start-failure.md``.
  Fix this benchmark by fixing pcc, not by rewriting the benchmark
  to a non-BoC shape.
"""
from threading import Lock, Thread


RING_SIZE = 16
GROUP_SIZE = 2
STRIDE = 1
N_CHAINS = 4
# Keep total CPU work high enough to prove real parallel execution, but do not
# make lock handoff the benchmark's dominant signal. The older 100000 * 200
# shape spent most wall-clock in pcc's STW-safe Lock protocol on macOS.
ITERS = 20000
CPU_WORK_ROUNDS = 1000


def cpu_work(rounds: int) -> int:
    x = 1
    i = 0
    while i < rounds:
        x = (x * 1664525 + 1013904223) & 0xFFFFFFFF
        i = i + 1
    return x


def chain_step(locks: list[Lock], vals: list, head: int) -> int:
    a = head
    b = (head + STRIDE) % RING_SIZE
    if a < b:
        first = a
        second = b
    else:
        first = b
        second = a
    locks[first].acquire()
    locks[second].acquire()
    _ = cpu_work(CPU_WORK_ROUNDS)
    vals[a] = vals[a] + 1
    locks[second].release()
    locks[first].release()
    return (head + STRIDE) % RING_SIZE


def chain(
    locks: list[Lock],
    vals: list,
    chain_id: int,
    iters: int,
) -> None:
    head = chain_id * (RING_SIZE // N_CHAINS)
    i = 0
    while i < iters:
        head = chain_step(locks, vals, head)
        i = i + 1


def main() -> None:
    locks: list[Lock] = []
    vals: list = []
    i = 0
    while i < RING_SIZE:
        locks.append(Lock())
        vals.append(0)
        i = i + 1

    t0 = Thread(target=chain, args=(locks, vals, 0, ITERS))
    t1 = Thread(target=chain, args=(locks, vals, 1, ITERS))
    t2 = Thread(target=chain, args=(locks, vals, 2, ITERS))
    t3 = Thread(target=chain, args=(locks, vals, 3, ITERS))
    t0.start(); t1.start(); t2.start(); t3.start()
    t0.join(); t1.join(); t2.join(); t3.join()

    total = 0
    for v in vals:
        total = total + v
    expected = N_CHAINS * ITERS
    print("ring_size=" + str(RING_SIZE))
    print("chains=" + str(N_CHAINS))
    print("group_size=" + str(GROUP_SIZE))
    print("iters_per_chain=" + str(ITERS))
    print("total_steps=" + str(total))
    print("expected=" + str(expected))
    if total == expected:
        print("PASS")
    else:
        print("FAIL")


if __name__ == "__main__":
    main()
