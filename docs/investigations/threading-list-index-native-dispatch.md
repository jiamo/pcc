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
- No.5 Split BOC ring speedup from lock correctness     [pending]

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

The BOC ring benchmark no longer fails the sum invariant; the pytest node
still fails on its speedup floor (`serial=0.02s`, `parallel=0.05s`,
`speedup=0.47x`, floor `2.0x`). That is now a benchmark/performance-threshold
question, not the original list-indexed Lock correctness bug.

## No.5 Split BOC ring speedup from lock correctness
### Code Change
Pending. `tests/test_boc_benchmarks.py::test_boc_ring_correctness_and_speedup`
currently combines a correctness assertion with an aggressive speedup floor.
After the lock fix, correctness passes and only the speedup floor fails.
### pending
Decide whether to split the BOC ring correctness gate from the performance
gate, tune the workload so the speedup floor is meaningful, or move the
speedup assertion under the broader performance matrix goal.
