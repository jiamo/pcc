"""Shared-object refcount contention benchmark (parallel).

Every worker thread hammers the refcount of ONE shared instance: ``touch``
returns its borrowed parameter, so the callee retains it and the caller
releases the owned result — one incref/decref pair per iteration, all
landing on the same object header cache line.

This mirrors the free-threaded CPython bottleneck where every thread
increfs the same dtype/module singleton
(labs.quansight.org/blog/scaling-numpy-on-free-threaded-python).
No shared mutable state is written; the cross-thread traffic is purely
reference-count adjustments plus one attribute read per iteration.

The control benchmark is boc_bank_demo.py (independent per-thread data,
no shared-object traffic): the gap between its parallel speedup and this
file's speedup is the cost of shared refcount contention.
"""
from threading import Thread


N_THREADS = 4
ROUNDS = 5000000


class Shared:
    def __init__(self, v: int) -> None:
        self.v = v


SHARED = Shared(7)


def touch(o: Shared) -> Shared:
    return o


def worker(idx: int, rounds: int) -> None:
    acc = 0
    i = 0
    while i < rounds:
        s = touch(SHARED)
        acc = acc + s.v
        i = i + 1
    print("t" + str(idx) + " acc=" + str(acc))


def main() -> None:
    threads: list = []
    t = 0
    while t < N_THREADS:
        th = Thread(target=worker, args=(t, ROUNDS))
        threads.append(th)
        t = t + 1
    for th in threads:
        th.start()
    for th in threads:
        th.join()
    print("threads=" + str(N_THREADS))
    print("rounds_each=" + str(ROUNDS))
    print("DONE")


if __name__ == "__main__":
    main()
