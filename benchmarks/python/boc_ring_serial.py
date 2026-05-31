"""Single-threaded baseline for ``boc_ring.py``.

Same total work (N_CHAINS * ITERS chain steps) as the parallel ring
benchmark, executed serially on one thread. The proof harness compares
wall-clock time against the parallel binary; with a fixed list-indexed
Lock dispatch, the parallel run should finish meaningfully faster than
this baseline.

Constants intentionally mirror ``boc_ring.py`` so the two binaries
are comparable.
"""

RING_SIZE = 16
GROUP_SIZE = 2
STRIDE = 1
N_CHAINS = 4
ITERS = 100000


def cpu_work(rounds: int) -> int:
    x = 1
    i = 0
    while i < rounds:
        x = (x * 1664525 + 1013904223) & 0xFFFFFFFF
        i = i + 1
    return x


def chain_step_serial(vals: list, head: int) -> int:
    _ = cpu_work(200)
    vals[head] = vals[head] + 1
    return (head + STRIDE) % RING_SIZE


def main() -> None:
    vals: list = []
    i = 0
    while i < RING_SIZE:
        vals.append(0)
        i = i + 1

    c = 0
    while c < N_CHAINS:
        head = c * (RING_SIZE // N_CHAINS)
        i = 0
        while i < ITERS:
            head = chain_step_serial(vals, head)
            i = i + 1
        c = c + 1

    total = 0
    for v in vals:
        total = total + v
    expected = N_CHAINS * ITERS
    print("threads=1")
    print("total_steps=" + str(total))
    print("expected=" + str(expected))
    if total == expected:
        print("PASS")
    else:
        print("FAIL")


if __name__ == "__main__":
    main()
