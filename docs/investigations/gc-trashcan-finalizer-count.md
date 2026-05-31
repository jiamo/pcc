# Investigation: trashcan dealloc skips finalizers in deep chains

## Status
resolved

## Problem Description
`tests/test_gc_trashcan.py::test_trashcan_with_del_no_overflow` still xfails
after resurrection was fixed. The compiled program builds a 100,000-node
linked list where every node has `__del__`; dropping the head should release
the whole chain without overflowing the C stack and should run every finalizer.

Current behavior returns normally but prints `False`, meaning the runtime avoids
stack overflow but does not increment the finalizer counter for all nodes.

## Repro
Focused trashcan gate:

```bash
env -u LC_ALL /opt/homebrew/bin/timeout 300s uv run pytest \
  tests/test_gc_trashcan.py::test_trashcan_with_del_no_overflow \
  -q -n0 --runxfail -ra
```

Expected current result before the fix: the node fails because stdout is
`False` instead of `True`.

## Test [CONFIRMED]
Observed locally on 2026-05-08 as part of the 14-node GC closure rerun
before the fix:

- `test_trashcan_with_del_no_overflow` fails with `False != True`.
- Full GC gate still reports this as one of the two remaining xfails:
  `193 passed, 2 xfailed in 158.95s`.

After the fix, the focused and containing gates pass:

```text
tests/test_gc_trashcan.py::test_trashcan_with_del_no_overflow
tests/test_gc_effectiveness.py::test_cycle_collect_finds_simple_cycles
tests/test_gc_g1_cycle_collector.py::test_gc_disable_enable
tests/test_gc_performance.py::test_gc_collect_cycle_throughput
tests/test_gc_semantics.py::test_concurrent_refcount_no_drift:
5 passed in 3.41s

tests/test_gc_store_ptr_balance.py tests/test_gc_trashcan.py
tests/test_gc_resurrection.py:
22 passed in 34.70s

tests/test_gc_*.py:
194 passed, 1 xfailed in 163.38s
```

## Proposals
- No.1 Confirm trashcan finalizer failure shape     [CONFIRMED]
- No.2 Fix owned field traversal lifetime in frontend lowering     [CONFIRMED]

## No.1 Confirm trashcan finalizer failure shape
### Code Change
No code change. Keep the existing pytest node as the gate and confirm it fails
under `--runxfail`.
### CONFIRMED
The failure is not a crash or timeout. The executable returns `0`, so the
current trashcan queue prevents stack overflow, but `counter[0] == n` is false.

## No.2 Fix owned field traversal lifetime in frontend lowering
### Code Change
Before the code change, shrinking the program showed the counter was always
`1` for `n=2`, `n=3`, `n=10`, `n=1000`, and `n=100000`. Refcount logging for
`n=3` showed only the head finalizer ran; the child was decref'd from `2` to
`1`, so the bug was an ownership leak in the traversal lowering, not a missing
runtime trashcan queue.

The fix has two parts:

- `_expr_returns_owned_object()` now treats statically known instance field
  loads as owned when the load lowers through `py_instance_get_field`.
- function and class-method lowering now track owned locals with a runtime
  per-local boolean flag. This makes `cur = cur.next` release only values
  that were actually acquired as owned field references, instead of relying on
  path-insensitive compile-time state.

Class method lowering also saves/resets/restores `_owned_local_flag_slots` and
`_gc_rooted_local_names`; without that, `threading.local.get/delete` could
reuse the flag alloca emitted for `threading.local._dict`, producing invalid
IR such as `store i1 0, ptr %d.owned.23` in a different function.

### CONFIRMED
The deep-list probe now reports exact finalizer counts:

```text
1 1
2 2
3 3
10 10
1000 1000
100000 100000
```

The focused pytest gate with `--runxfail` now passes, the surrounding
trashcan/store-ptr/resurrection tests pass, and the full GC gate reports
`194 passed, 1 xfailed`; the remaining xfail is unrelated shutdown finalizer
ordering.

## Report (only when the investigation is closing)
Proposal No.2 landed. The confirmed root cause was frontend ownership
lowering for field traversal plus missing class-method owned-local state
isolation, not runtime trashcan recursion. The xfail marker was removed from
`tests/test_gc_trashcan.py::test_trashcan_with_del_no_overflow`.

Bootstrap/fallback gates after the change:

```text
tests/test_llvm_capi_ir_parity.py tests/test_llvm_capi_end_to_end.py: 23 passed in 0.22s
tests/test_fallback_baseline.py tests/test_ir_py_fallback_baseline.py: 11 passed in 59.25s
tests/test_py_multi_file_compile.py tests/test_py_multi_file_bootstrap_shim.py: 70 passed in 134.89s
```
