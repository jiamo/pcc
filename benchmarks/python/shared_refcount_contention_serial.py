"""Shared-object refcount contention benchmark (serial baseline).

Same total work as shared_refcount_contention.py (N_THREADS * ROUNDS
iterations of the same touch/read loop) on one thread. The parallel
speedup is wall-clock(serial) / wall-clock(parallel).
"""

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
    t = 0
    while t < N_THREADS:
        worker(t, ROUNDS)
        t = t + 1
    print("threads=1")
    print("rounds_total=" + str(N_THREADS * ROUNDS))
    print("DONE")


if __name__ == "__main__":
    main()
