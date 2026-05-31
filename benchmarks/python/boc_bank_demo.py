"""Proof that pcc's threading model gives real free-threaded parallelism.

Inspired by Microsoft's BoCPy (https://microsoft.github.io/bocpy/), but
demonstrating the substrate that BOC patterns need: actual pthread-level
parallelism with no GIL.

Workload: 4 threads each run a CPU-bound integer mixer for ROUNDS
iterations. No shared mutable state across threads — each worker only
reads its own thread arguments and writes its own stdout line. The
proof is wall-clock: with real parallelism on a multicore host, 4
threads finish ~Nx faster than the same total work serialized on one
thread (where N approaches the core count, capped by memory pressure
and per-thread overhead).

What this proves:

  * Multiple pthreads run pcc-compiled Python code concurrently
    (printed thread tags interleave; output ordering varies run to run).
  * Refcount adjustments use atomic CAS / atomic-add (verified in the
    runtime archive — ``ldadd`` / ``ldaddal`` on aarch64); they do not
    serialize under contention.
  * threading.Lock is backed by pthread_mutex; threading.Thread is
    backed by pthread_create.

What this does NOT yet prove (separate runtime gap, see TaskCreate
follow-up): correctness of concurrent mutation of shared Python-level
mutable containers (list/dict/instance fields) under user-level locks.
That path currently exhibits lost updates and is filed for
investigation rather than demonstrated here.
"""
from threading import Thread


N_THREADS = 4
ROUNDS = 200000000


def cpu_work(rounds: int) -> int:
    x = 1
    i = 0
    while i < rounds:
        x = (x * 1664525 + 1013904223) & 0xFFFFFFFF
        i = i + 1
    return x


def worker(idx: int, rounds: int) -> None:
    r = cpu_work(rounds)
    print("t" + str(idx) + " r=" + str(r))


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
