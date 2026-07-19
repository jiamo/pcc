# 2026-07-16 M3 C-like value-array performance evidence

Task: `M3-C-LIKE-PERFORMANCE`

## Result

The pinned source
`benchmarks/python/scenarios/value_array_c_like.py` joins one fixed
`pcc.array[Sample, 2]` float-recurrence kernel with its negative-index,
out-of-bounds, bignum-index overflow, and element-to-`Any` escape oracle.  The
matching `benchmarks/c/value_array_c_like.c` implements only the hot numerical
kernel and is compiled with clang `-O3`; it does not pretend to implement
Python's slow-path semantics.

The checked manifest is
`benchmarks/results/m3_value_array_c_like.json`.  It binds the result to the
repository base commit recorded by the head-truth manifest, the exact Python
and C source SHA-256 hashes, and the full emitted frontend-IR SHA-256.  The
manifest explicitly records `worktree_dirty=true`; this is exact local-source
evidence, not a clean-commit, GitHub, or release claim.

The emitted `hot` signature is:

```text
define double @user_value_array_c_like_hot(
  { { double, double }, { double, double } } %values,
  ptr %rounds)
```

Its body has no `py_list_new`, `py_instance_new`, or `py_valuebox_new` call.
It contains 54 aggregate `extractvalue` operations and 16 each of `fadd`,
`fmul`, and `fsub`.  The Python-int `py_int_add` overflow slow path remains in
the function; the performance claim depends on the pinned loop counter staying
inside the tagged-small-int lane and does not weaken arbitrary-precision `int`.

A self/no-libpython allocation probe compiled otherwise identical 0- and
1000-round sources.  Both emitted exactly 89 `alloc_object` events with the
same type-tag histogram, so the additional hot-loop allocation delta is zero.
This does not claim that process startup, module initialization, or the
explicit element escape is allocation-free.

## Runtime result

Environment: Darwin 25.5.0 arm64, Python 3.13.2, Homebrew clang 20.1.8.  Each
mode used one warmup and seven measured process runs over 1,000,000 rounds with
16 dependent float recurrences per round.  All modes produced checksum
`-1.325`; host, LLVM, and self produced the exact same full slow-path output.

| Mode | Median | vs CPython | vs native C | Claim |
|---|---:|---:|---:|---|
| CPython-host | 776.144 ms | 1.000x | 20.380x | semantic host oracle |
| LLVM/no-libpython | 59.815 ms | 0.077x | 1.571x | inside pinned C-like band |
| self/no-libpython | 284.164 ms | 0.366x | 7.462x | measured owner path; no C-like ratio claim |
| native-C/clang-O3 | 38.082 ms | 0.049x | 1.000x | numerical oracle/baseline |

Both produced pcc binaries were inspected and do not link libpython.  The
LLVM C-like policy was fixed before the formal run at at most 2.0x native C and
at most 0.2x CPython.  The self result is deliberately reported separately;
its current 7.462x native-C ratio is evidence for the remaining self-backend
optimization distance, not something hidden by averaging the backends.

## Gates

Formal manifest generation:

```text
gtimeout 120s env -u LC_ALL uv run python \
  benchmarks/run_value_array_c_like.py \
  --runs 7 --warmups 1 --output /tmp/m3_value_array_c_like.json

wrote /tmp/m3_value_array_c_like.json
```

Checked manifest, IR/source binding, mode labels, allocation oracle, and live
host/LLVM/self parity:

```text
gtimeout 90s env -u LC_ALL uv run pytest -q -n0 \
  tests/benchmarks/test_value_array_c_like.py

4 passed in 2.29s
```

Typed-array and performance-intent adjacency:

```text
gtimeout 60s env -u LC_ALL uv run pytest -q -n0 \
  tests/benchmarks/test_value_array_c_like.py \
  tests/python/test_py_value_array_projection.py \
  tests/python/test_intent_constraints.py::TestObligation2PerformanceProven

9 passed in 3.40s
```

`py_compile` passed for the Python workload, harness, and test.  The native C
oracle also compiled under `clang -Wall -Wextra -Werror -O3 -std=c11` and
printed `-1.325`.  `ruff` was not installed in this environment, so it was not
used as a claimed gate.

## Claim boundary

This proves one fixed, source-hash-bound value-array float kernel has a direct
aggregate ABI, zero additional hot-loop object allocations, exact
Python-semantic dynamic/overflow/escape behavior in CPython-host,
LLVM/no-libpython, and self/no-libpython modes, and a measured LLVM result
within 1.571x of the native-C numerical oracle on the recorded machine.  It
does not claim arbitrary dynamic Python is C-speed, that self already has
C-like parity, that compilation/startup is allocation-free, or any
long-running GC pause/RSS/fragmentation result.
