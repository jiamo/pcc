# Investigation: Backend #1 object registry performance gates

## Status
resolved

## Problem Description
After the Backend #1 correctness fixes, the full `PCC_GC_BACKEND=1`
`tests/test_gc_*.py` gate has only three remaining failures. All three are
performance/release-throughput failures:

- `tests/test_gc_performance.py::test_long_chain_decref_no_stack_overflow`
- `tests/test_gc_performance.py::test_gc_collect_cycle_throughput`
- `tests/test_gc_trashcan.py::test_million_entry_list_release`

The shared suspicion is an O(n^2) tracing-object registry path rather than a
semantic resurrection/rooting bug.

## Repro
Run the full gate:

```bash
env -u LC_ALL PCC_GC_BACKEND=1 /opt/homebrew/bin/timeout 700s uv run pytest tests/test_gc_*.py -q -n0 -rxX
```

Expected current failure before the fix:

```text
3 failed, 195 passed
```

Focused nodes:

```bash
env -u LC_ALL PCC_GC_BACKEND=1 /opt/homebrew/bin/timeout 240s uv run pytest tests/test_gc_performance.py::test_long_chain_decref_no_stack_overflow -q -n0 -rxX
env -u LC_ALL PCC_GC_BACKEND=1 /opt/homebrew/bin/timeout 240s uv run pytest tests/test_gc_performance.py::test_gc_collect_cycle_throughput -q -n0 -rxX
env -u LC_ALL PCC_GC_BACKEND=1 /opt/homebrew/bin/timeout 180s uv run pytest tests/test_gc_trashcan.py::test_million_entry_list_release -q -n0 -rxX
```

## Test [CONFIRMED]
The full Backend #1 gate was observed with:

```bash
env -u LC_ALL PCC_GC_BACKEND=1 /opt/homebrew/bin/timeout 700s uv run pytest tests/test_gc_*.py -q -n0 -rxX
```

Observed result:

```text
3 failed, 195 passed in 418.34s
```

Failure details:

- 100k chain release: `18.58s`, expected `<3s`.
- 10k 2-node cycle collect: `6.53s`, expected `<2s`.
- 1M list release: timed out after `120s`.

## Proposals
- No.1 Audit Backend #1 object-node lookup/removal complexity     [CONFIRMED]
- No.2 Add an O(1) object-node index and unlink path for pcc-Python Backend #1     [CONFIRMED]

## No.1 Audit Backend #1 object-node lookup/removal complexity
### Code Change
No source change. Run the three focused failures and the
`test_gc_performance.py + test_gc_trashcan.py` Backend #1 group.

### CONFIRMED
The original three failures shared the same shape: object-heavy workloads
were semantically correct but too slow. The first attempted object index made
the original three focused nodes pass, but exposed a steady-state regression:
dead object-list nodes were left as tombstones, so long-running workloads still
paid for scanning old nodes.

## No.2 Add an O(1) object-node index and unlink path for pcc-Python Backend #1
### Code Change
Add `pcc_gc_object_index_*` helpers in `pcc/py_runtime/src/py_gc_index_table.c`
and declare them in `pcc/py_runtime/src/py_internal.h`.

Update `pcc/py_runtime/py/py_gc_backend.py` so Backend #1 object nodes are
inserted into the hash index at allocation time. Hot paths now use index
lookup instead of scanning:

- `_is_known_object`
- `_mark_gray_if_known`
- `_promote_young_if_known`
- `_object_known_size`
- `pcc_gc_note_object_freeing`
- `pcc_gc_free_object_memory`

The pcc-Python object node also gained a `prev` link, so the index can find
the node and unlink it from the object list in O(1). This avoids both the
old O(n^2) release path and the tombstone-list steady-state regression.

### CONFIRMED
After rebuilding `libpy_runtime_pcc_py.a`, the focused Backend #1 performance
nodes passed:

```bash
env -u LC_ALL PCC_GC_BACKEND=1 /opt/homebrew/bin/timeout 240s uv run pytest tests/test_gc_performance.py::test_long_chain_decref_no_stack_overflow -q -n0 -rxX
env -u LC_ALL PCC_GC_BACKEND=1 /opt/homebrew/bin/timeout 240s uv run pytest tests/test_gc_performance.py::test_gc_collect_cycle_throughput -q -n0 -rxX
env -u LC_ALL PCC_GC_BACKEND=1 /opt/homebrew/bin/timeout 240s uv run pytest tests/test_gc_trashcan.py::test_million_entry_list_release -q -n0 -rxX
```

Observed result: all three passed.

The wider performance group passed:

```bash
env -u LC_ALL PCC_GC_BACKEND=1 /opt/homebrew/bin/timeout 360s uv run pytest tests/test_gc_performance.py tests/test_gc_trashcan.py -q -n0 -rxX
```

Observed result: `20 passed in 21.74s`.

The full Backend #1 GC gate passed:

```bash
env -u LC_ALL PCC_GC_BACKEND=1 /opt/homebrew/bin/timeout 700s uv run pytest tests/test_gc_*.py -q -n0 -rxX
```

Observed result: `198 passed in 163.63s`.

The default backend GC gate remained green:

```bash
env -u LC_ALL /opt/homebrew/bin/timeout 500s uv run pytest tests/test_gc_*.py -q -n0
```

Observed result: `198 passed in 156.74s`.

Bootstrap-adjacent gates remained green:

```bash
env -u LC_ALL /opt/homebrew/bin/timeout 360s uv run pytest tests/test_fallback_baseline.py tests/test_ir_py_fallback_baseline.py tests/test_llvm_capi_ir_parity.py tests/test_llvm_capi_end_to_end.py -q -n0
env -u LC_ALL /opt/homebrew/bin/timeout 420s uv run pytest tests/test_py_multi_file_compile.py tests/test_py_multi_file_bootstrap_shim.py -q -n0
```

Observed results: `34 passed` and `70 passed`.

## Report (only when the investigation is closing)
The landed fix is No.2. Backend #1's pcc-Python object registry had two
production blockers:

- child marking/known checks scanned the whole object list;
- object freeing also scanned the whole list, which was worst-case O(n^2)
  when releasing objects in allocation order.

Adding a hash index fixed lookup, and adding a `prev` pointer fixed deletion
without leaving tombstones behind. This closes the remaining Backend #1 GC
suite failures while keeping the default backend GC suite green.
