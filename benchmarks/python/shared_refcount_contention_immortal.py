"""Shared-object refcount contention benchmark — immortalized singleton.

Identical to shared_refcount_contention.py except the shared instance is
immortalized before the threads start: py_incref/py_decref early-return on
PY_FLAG_IMMORTAL, so the per-iteration refcount pair stops generating
cross-thread cache-line traffic on the shared object.

This is pcc's native equivalent of the fix free-threaded CPython 3.14
exposes to NumPy as PyUnstable_Object_SetImmortal
(labs.quansight.org/blog/scaling-numpy-on-free-threaded-python).
"""
import gc
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
    gc.immortalize(SHARED)
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
