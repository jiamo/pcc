# LIBC-P1-PRIMITIVES — the atomics half, measured and ratcheted

Mode: host measurement over `pcc/py_runtime/src/*.c` and
`pcc/py_runtime/py/*.py`. No intrinsic was added; this sizes the work and
stops the gap widening.

## What the C runtime actually uses

```text
__atomic_load_n              140      __ATOMIC_RELAXED   256
__atomic_store_n             122      __ATOMIC_ACQUIRE    59
__atomic_add_fetch            77      __ATOMIC_RELEASE    48
__atomic_compare_exchange_n   20      __ATOMIC_ACQ_REL    43
__atomic_sub_fetch            12
__atomic_fetch_add             4
__atomic_test_and_set          3
__atomic_clear                 3
__atomic_exchange_n            2
__atomic_thread_fence          1
__atomic_or_fetch              1
__atomic_and_fetch             1
```

Twelve operation kinds, four orderings, 406 ordering annotations.

## What a pcc-Python port can reach

`pcc.unsafe` has **no** atomic intrinsic — the surface is pointer/memory,
libc wrappers, and tagged-int helpers, and nothing else. A port therefore
reaches atomics only by `extern`-calling seven fixed C helpers in
`py_runtime_high_substrate.c`:

```text
pcc_py_atomic_i32_load / i32_store / i32_add_fetch
pcc_py_atomic_i64_load / i64_store / i64_add_fetch
pcc_py_atomic_i64_dec_if_positive
```

So compare-exchange (20 C sites), exchange, test_and_set, clear,
thread_fence, sub_fetch, fetch_add and the bitwise fetch-ops have **no**
mirror expression at all.

## The part that is a correctness problem, not just coverage

The helpers pick their memory ordering **by operand width, not by use**:

```c
pcc_py_atomic_i32_load     -> __ATOMIC_RELAXED
pcc_py_atomic_i32_store    -> __ATOMIC_RELAXED
pcc_py_atomic_i32_add_fetch-> __ATOMIC_RELAXED
pcc_py_atomic_i64_load     -> __ATOMIC_ACQUIRE
pcc_py_atomic_i64_store    -> __ATOMIC_RELEASE
pcc_py_atomic_i64_add_fetch-> __ATOMIC_ACQ_REL
```

A port needing an acquire-ordered i32 load gets a relaxed one — silent
under-synchronization. A port needing a relaxed i64 counter bump pays a full
acq_rel barrier — silent over-synchronization. The width picks the barrier,
which is not something the mirrored C code ever said.

This matters for two standing rules: the 5-GC production-equality rule (all
five backends and both runtime tiers consume one slot-trace contract) and the
C-kernel layering rule (atomics legitimately *stay* in the C kernel — but the
pcc-Python side must still be able to state which ordering it needs when it
calls in).

## What landed

`tests/python/test_atomic_mirror_gap.py` (4 passed) pins the measured surface
so the gap cannot widen unnoticed:

- a new `__atomic_*` operation kind in the C runtime fails the test
- a fifth memory ordering fails the test
- helper-set drift between the C definitions and the port declarations fails
  the test (this repository produced two independent hand-maintained-mirror
  drifts today; this is the third mirror)
- the ratchet asserts its own premise — if `pcc.unsafe` gains atomic
  intrinsics the test fails, with instructions to re-point the ports and
  rewrite the ratchet rather than delete it

## What is not done

No intrinsic, no ordering parameter, no CAS, no fence, and nothing on the
self backend. The syscall6 intrinsic and the freestanding module discipline
in this row are untouched. The numbers above are what the atomics half has to
cover.
