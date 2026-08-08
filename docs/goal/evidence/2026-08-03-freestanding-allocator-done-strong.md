# Freestanding pcc-Python allocator production closure

Date: 2026-08-03

Task: `LIBC-P2-ALLOCATOR`

Source identity: Git `127ec488f026556c70aa20cea4e466257f93c597`, dirty
shared worktree. New measurement/gate fingerprints:

```text
freestanding_allocator_churn.c             a49da05eea2ad24854363e11b0c2d2479804b1c3725d4248205ca582bd350630
run_freestanding_allocator.py              9d766a0d18bb6acbf4bf637053272009b1306f7b771a8072a184b5959baf25f1
test_freestanding_allocator_benchmark.py   4281486508b8605c4a9a0c5271578659269517ec6333a21f30e65040b3eadc53
test_self_backend_x86_64_linux.py          7bbd8a37f476e93ccc99d7f58932363fa6293eea2622bde3cd5d129d1f48d42d
test_freestanding_allocator.py             21a43125ef09ccb7801cf0f698f4baee2f2f3dec1ba97f9ebd567a61a85aba8c
```

## Claim

The production pcc-Python runtime owns its malloc-family implementation with
the strict self-compilable page allocator described in
`2026-08-02-freestanding-pcc-python-allocator.md`. Current LLVM/self ABI,
threaded churn, page-provider, archive ownership, Darwin import ratchet,
Linux x86_64 raw-syscall execution, bounded allocator metrics, representative
five-GC long-running workloads, and the no-libpython/self fixed point are
green.

This is an ownership and correctness claim, not a speed claim. The current
simple global-lock/slab policy is materially slower than the host allocator on
the measured churn shape; the result is reported below without weakening the
gate or relabeling it as a win.

## Source-bound host comparison

`benchmarks/run_freestanding_allocator.py` compiles one deterministic C churn
shape twice: once with the host allocator and once with the allocator object
emitted by the self backend from strict freestanding pcc-Python. The manifest
is source-bound (`47eaf7d06471132f4591935192ea2cb69502b129feb74cc70aeb83e2432452d4`),
uses 2,048 live slots, 200,000 replacement rounds, and five fresh processes
per mode on the same Darwin arm64 host.

```text
allocator   median throughput   median peak RSS   retained capacity   live deltas
host        19,540,791 ops/s     6,897,664 B      12,582,912 B        n/a
pcc          1,805,820 ops/s     5,832,704 B       3,342,336 B        requested=0 usable=0
delta      -17,734,971 ops/s    -1,064,960 B      -9,240,576 B
```

The checksum is identical (`51049616`) in all ten processes. The pcc allocator
is about 10.8x slower on this shape while using about 1.0 MiB less peak RSS and
retaining about 8.8 MiB less capacity. These are single-machine observations,
not universal rankings and not correctness thresholds. The two benchmark
contract tests pass in 1.17s.

## Five-GC bounded long run

The current strict no-libpython/self churn binary is source-bound by
`71c4482ba35b6f8b191f9376c88c558b5f51416f95d479602f1f3683a12e87f3`
and binary hash
`99890c59536eff71439d97d8fa45ba87b8ca4243738e53237912d54918f163b2`.
Twenty thousand rounds execute 1,280,000 operations per backend:

```text
GC   wall ms   ops/s    peak RSS   tail RSS drift   pauses   pause sum/max
0      1,985   644,836   3,850,240 B       0 B          0       0 / 0 us
1      2,975   430,252   5,390,336 B       0 B        155  33,484 / 700 us
2      3,001   426,524   5,390,336 B       0 B        262  99,936 / 713 us
3      3,096   413,436   4,997,120 B       0 B          0       0 / 0 us
4     14,519    88,160   9,093,120 B  65,536 B          0       0 / 0 us
```

The common malloc-axis sample ends at 11,792 bytes in use and 12,582,912
bytes capacity; GC4 additionally records a 504,992-byte zpage retained gap.
Empty pause columns mean no instrumented pause window triggered under this
shape, not that the collector has no pauses.

The complete representative workload smoke (`churn`, `growshrink`,
`finalizers`, and `pointer_mutator` across GC0..4) passes 20/20 in 5.12s,
including telemetry sanity, bounded exit, graph integrity, and the finalizer
canary.

## Linux x86_64 execution and focused gates

The new Docker/amd64 integration gate runs the allocator's own self-backend
ABI and raw-syscall tests inside the target environment. The generated object
has zero undefined symbols, contains `syscall`, contains no mmap/munmap call,
and its malloc/calloc/realloc/free/alignment/counter harness executes:

```text
1 passed in 7.83s   # outer Linux x86_64 Docker gate; two inner tests
10 passed in 5.29s  # allocator + unsafe page-provider suites
2 passed in 0.33s   # Darwin import ratchet/platform-label checks
```

The explicit host triple in the allocator self-object test removes an
accidental dependency on LLVM filling `unknown-unknown-unknown`; both Darwin
and Linux now select their target deterministically. Python IR passes are off
inside this focused Docker gate because that image has no discoverable
LLVM-C shared library and pass behavior is outside the allocator claim.

## Fixed point and boundary disposition

No `pcc/` source changed after the current-source five-GC acceptance recorded
in `2026-08-03-freestanding-primitives-five-gc-fixed-point.md`:

```text
5 passed in 778.18s (0:12:58)
GC0..4; backend=self; python-libpython=off; normalized pcc2/pcc3 equal
```

Together with the original implementation/archive/ratchet evidence, the
measurements and gates above exhaust the allocator task's finite open boundary.
The observed throughput deficit is explicitly outside this ownership claim;
no C allocator fallback was reintroduced to hide it.
