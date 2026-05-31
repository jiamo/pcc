"""Single-threaded baseline matching ``boc_bank_demo.py``.

Same total CPU work as the 4-thread parallel demo
(N_THREADS * ROUNDS iterations of cpu_work), executed serially on
one thread. The proof harness compares wall-clock time against the
parallel binary; a real free-threaded runtime should give a speedup
roughly proportional to the parallel-thread count.

Constants intentionally mirror ``boc_bank_demo.py`` so the two
binaries are comparable: change one, change the other.
"""

N_THREADS = 4
ROUNDS = 200000000


def cpu_work(rounds: int) -> int:
    x = 1
    i = 0
    while i < rounds:
        x = (x * 1664525 + 1013904223) & 0xFFFFFFFF
        i = i + 1
    return x


def main() -> None:
    i = 0
    while i < N_THREADS:
        cpu_work(ROUNDS)
        i = i + 1
    print("threads=1")
    print("rounds_total=" + str(N_THREADS * ROUNDS))
    print("DONE")


if __name__ == "__main__":
    main()
