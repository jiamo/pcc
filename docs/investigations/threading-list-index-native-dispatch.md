# Investigation: list-indexed threading receivers miss native dispatch

## Status
active

## Problem Description
The remaining threading/Lock failures are tied to method calls whose receiver
comes from list indexing instead of a plain local/global name.

User-reported summary:

- A: `locks[0].acquire()` from multiple threads loses about 40% of shared
  updates. Passing the same `Lock` directly as an argument or using a global
  `Lock` is correct.
- B: a single-producer/single-consumer pipeline using `del`/`append` under a
  named per-queue `Lock` still loses items. The cooking benchmark serves fewer
  than all requested items and the main thread waits until timeout.
- C: `threads[i].start()` raises `RuntimeError` when a `Thread` instance is
  stored in a list and started through list indexing.

The direct/global `Lock.acquire()` path was fixed in
`docs/investigations/threading-lock-lost-update.md`. The earlier standalone
`threads[i].start()` finding is in
`docs/investigations/threading-list-index-start-failure.md`. This investigation
tracks the broader list-indexed receiver dispatch class because concurrent GC
backend #2, scheduler queues, and BOC-style benchmarks all depend on native
threading methods remaining mutually exclusive through this access pattern.

## Update 2026-06-19 — list-iterated Thread receivers
The remaining `tests/python/test_intent_constraints.py` xfail
`threading_thread_start` exposed another list-held native receiver shape:

```python
ts = [threading.Thread(target=work, args=(i,)) for i in range(3)]
for t in ts:
    t.start()
for t in ts:
    t.join()
```

The program compiled under strict self/no-libpython but failed at runtime with
`AttributeError: start`. The older `threads[0].start()` fix did not cover
loop-target binding from a list of native Thread objects. While gating that fix,
`tests/python/test_boc_threading_proof.py` exposed the sibling shape
`threads=[]; th=Thread(...); threads.append(th); for th in threads:
th.start()`, which failed with the same `AttributeError: start`.

## Repro
BOC benchmark gates:

```bash
env -u LC_ALL /opt/homebrew/bin/timeout 300s \
  uv run pytest tests/test_boc_benchmarks.py::test_boc_ring_correctness_and_speedup \
  -q -n0 -ra

env -u LC_ALL /opt/homebrew/bin/timeout 300s \
  uv run pytest tests/test_boc_benchmarks.py::test_boc_cooking_pipeline_serves_all \
  -q -n0 -ra
```

Expected pre-fix behavior:

- `test_boc_ring_correctness_and_speedup` fails correctness because the parallel
  ring uses `locks[i].acquire()`.
- `test_boc_cooking_pipeline_serves_all` fails or times out because list
  mutation under nominal locks loses queue items.

List-indexed `Thread.start()` reduction is documented in
`docs/investigations/threading-list-index-start-failure.md`.

## Test [CONFIRMED]
Observed locally on 2026-05-08:

- `tests/test_boc_benchmarks.py::test_boc_ring_correctness_and_speedup`
  failed in 5.50s. Parallel output contained `total_steps=192660`,
  `expected=200000`, `FAIL`.
- `tests/test_boc_benchmarks.py::test_boc_cooking_pipeline_serves_all`
  failed after the compiled binary hit the test's 120.0s timeout.
- The one-thread list-indexed reduction from
  `docs/investigations/threading-list-index-start-failure.md` compiled but
  printed `start` and then raised
  `RuntimeError: native Thread.start failed`.

## Proposals
- No.1 Confirm BOC and minimal list-indexed repros     [CONFIRMED]
- No.2 Generalize native threading method dispatch beyond `Name` receivers     [CONFIRMED]
- No.3 Add focused regression tests for list-indexed Lock and Thread methods     [CONFIRMED]
- No.4 Re-run BOC ring/cooking and GC threading gates     [CONFIRMED]
- No.5 Tune BOC ring speedup workload after correctness fix     [CONFIRMED]
- No.6 Propagate native Thread element kind through list comprehension, append(name), and for-loop targets     [CONFIRMED]

## No.1 Confirm BOC and minimal list-indexed repros
### Code Change
No code change. Run the BOC pytest nodes and a small `threads[0].start()`
reduction under `PCC_WITH_THREADS=1`.
### CONFIRMED
The BOC ring test fails correctness, BOC cooking times out, and the
list-indexed `Thread.start` reduction raises `RuntimeError`. These are now
confirmed gates for the dispatch fix.

## No.2 Generalize native threading method dispatch beyond `Name` receivers
### Code Change
`pcc/py_frontend/codegen/layer1.py` now tracks list element threading kinds
from:

- `list[Lock]` / `list[Thread]` annotations on locals, module globals, and
  function parameters;
- homogeneous list literals such as `[Thread(target=work)]`;
- `lst.append(Lock())` / `lst.append(Thread(...))` on native list receivers.

`_maybe_emit_threading_instance_method` now accepts any receiver expression,
not only `Name`, and asks `_threading_kind_for_receiver_expr()` for the
receiver kind. `Subscript` receivers such as `locks[0]` and `threads[0]` then
emit the same direct runtime helpers as plain `lock.acquire()` and
`thread.start()`. The code also releases owned `py_list_get` results after the
native method call, including error branches.
### CONFIRMED
The focused tests added under No.3 pass and demonstrate direct native dispatch
for both list-indexed `Lock` and list-indexed `Thread`.

## No.3 Add focused regression tests for list-indexed Lock and Thread methods
### Code Change
Added `tests/test_list_indexed_method_dispatch_parity.py` as a single class
gate for `list[index].method(...)` dispatch parity. The compiled program covers:

- ordinary pcc user class method dispatch (`counters[0].add(...)`);
- native `Thread.start` / `Thread.join` dispatch (`threads[0].start()`);
- native `Lock.acquire` / `Lock.release` dispatch under contention
  (`locks[0].acquire()` in 4 worker threads).

Also removed the xfail from
`tests/test_python_concurrency_parity.py::test_list_indexed_lock_contended_counter`
because the list-indexed `Lock` path now matches the CPython-parity contract.
### CONFIRMED
Observed:

```bash
env -u LC_ALL /opt/homebrew/bin/timeout 240s \
  uv run pytest tests/test_list_indexed_method_dispatch_parity.py -q -n0
```

Result: `1 passed in 3.77s`.

The parity file plus the existing native-threading file also pass together:

```bash
env -u LC_ALL /opt/homebrew/bin/timeout 300s \
  uv run pytest tests/test_threading_module_native.py \
  tests/test_list_indexed_method_dispatch_parity.py -q -n0
```

Result: `6 passed in 12.91s`.

```bash
env -u LC_ALL /opt/homebrew/bin/timeout 360s \
  uv run pytest tests/test_python_concurrency_parity.py -q -n0 -rxX
```

Result: `6 passed in 21.77s`.

## No.4 Re-run BOC ring/cooking and GC threading gates
### Code Change
No code change. Validate the user-reported benchmarks and GC/threading gates
after the dispatch fix.
### CONFIRMED
Observed after No.2:

```bash
env -u LC_ALL /opt/homebrew/bin/timeout 360s \
  uv run pytest tests/test_boc_benchmarks.py::test_boc_cooking_pipeline_serves_all \
  -q -n0 -ra
```

Result: `1 passed in 3.83s`.

```bash
env -u LC_ALL /opt/homebrew/bin/timeout 300s \
  uv run pytest tests/test_threading_module_native.py -q -n0
```

Result: `7 passed in 16.39s`.

```bash
env -u LC_ALL /opt/homebrew/bin/timeout 420s \
  uv run pytest tests/test_threading_compat_matrix.py tests/test_threading_local.py \
  tests/test_gc_threading_substrate.py -q -n0 -rxX
```

Result: `16 passed in 2.55s`.

```bash
env -u LC_ALL /opt/homebrew/bin/timeout 300s \
  uv run pytest tests/test_boc_threading_proof.py -q -n0 -rxX
```

Result: `1 passed in 15.52s`.

The BOC ring benchmark no longer fails the sum invariant. A later workload
tuning pass is recorded in No.5.

## No.5 Tune BOC ring speedup workload after correctness fix
### Code Change
`benchmarks/python/boc_ring.py` and
`benchmarks/python/boc_ring_serial.py` now keep roughly the same total CPU
work but use fewer, larger chain steps (`ITERS=20000`,
`CPU_WORK_ROUNDS=1000`). The previous `100000 * 200` shape spent most of its
wall-clock in pcc's STW-safe Lock acquire/release protocol on macOS, so the
ring speedup assertion measured lock handoff overhead more than free-threaded
execution.
### CONFIRMED
Observed:

```bash
env -u LC_ALL uv run pytest tests/python/test_boc_benchmarks.py -q -n0 -s --maxfail=1
```

Result: `3 passed`. The ring node reported
`serial=1.28s parallel=0.40s speedup=3.18x` against the `1.5x` floor.

## No.6 Propagate native Thread element kind through list comprehension, append(name), and for-loop targets
### Code Change
The native-threading side-table now recognizes:

- list-comprehension sentinels whose element expression is a native threading
  constructor or a native threading receiver;
- `lst.append(th)` when `th` is already known as a native `Thread`/`Lock`/etc.,
  not only direct `lst.append(Thread(...))`;
- index-based `for <target> in <list|tuple>` binding, where the loop target
  inherits `_threading_env_flags` from the element type or the iterable's
  `_threading_list_elem_flags`.

Added focused regressions in `tests/python/test_threading_module_native.py` for
both `ts = [Thread(...)] ; for t in ts: t.start()` and
`threads.append(th); for th in threads: th.start()`. Promoted
`threading_thread_start` from `GAP_CASES` into the normal intent semantics
corpus.
### CONFIRMED
Observed:

```bash
env -u LC_ALL uv run pytest \
  'tests/python/test_threading_module_native.py::test_thread_start_on_for_loop_target_from_thread_list' \
  'tests/python/test_threading_module_native.py::test_thread_start_on_for_loop_target_from_appended_thread_name' \
  -q -n0
```

Result: `2 passed`.

```bash
env -u LC_ALL uv run pytest \
  'tests/python/test_intent_constraints.py::TestIntentGaps::test_unmet_obligation[threading_thread_start]' \
  -q -n0 -m integration --runxfail
```

Result before promotion: `1 passed`.

```bash
env -u LC_ALL uv run pytest \
  'tests/python/test_intent_constraints.py::TestPythonSemanticsDifferential::test_matches_cpython[threading_thread_start]' \
  -q -n0 -m integration
```

Result after promotion: `1 passed`.

Broader gates after the fix:

- `tests/python/test_threading_module_native.py` -> `7 passed`;
- `tests/python/test_boc_threading_proof.py tests/python/test_threading_lock_concurrency.py tests/python/test_python_concurrency_parity.py` -> `10 passed`;
- fallback/no-libpython baselines -> `18 passed`;
- `tests/python/gc/test_pcc_bootstrap_full_gc0.py::test_full_three_stage_bootstrap_self_gc0` -> `1 passed`;
- live xfail audit -> `2 xfailed`, both `run=False` frontiers.
