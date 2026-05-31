# Investigation: Close remaining GC xfail debt

## Status
resolved

## Problem Description
The current full GC gate reports `181 passed, 14 xfailed in 154.32s`.
The final five-backend GC goal must not close while those 14 GC xfails
remain hidden. Each xfail must either become a normal passing test or be
removed/rewritten with evidence that the test is not a reasonable pcc
production contract.

## Repro
Run the xfailed GC nodes with xfail disabled:

```bash
/opt/homebrew/bin/timeout 900s env -u LC_ALL uv run pytest \
  tests/test_gc_api.py::test_callbacks_fire_on_collect \
  tests/test_gc_effectiveness.py::test_non_cyclic_rss_plateaus \
  tests/test_gc_finalizer_corner.py::test_del_can_create_new_objects \
  tests/test_gc_finalizer_corner.py::test_module_global_del_at_shutdown \
  tests/test_gc_finalizer_corner.py::test_long_running_del \
  tests/test_gc_g3_weakref.py::test_ref_lambda_callback_fires_on_collection \
  tests/test_gc_g3_weakref.py::test_weak_value_dict_auto_removes \
  tests/test_gc_g3_weakref.py::test_weak_key_dict_auto_removes \
  tests/test_gc_performance.py::test_tracing_backend_steady_state_matches_refcount \
  tests/test_gc_regression_bugs.py::test_bug_110_str_in_local_list_does_not_leak \
  tests/test_gc_resurrection.py::test_resurrection_only_happens_once_per_object \
  tests/test_gc_resurrection.py::test_resurrection_is_transitive \
  tests/test_gc_resurrection.py::test_resurrection_does_not_block_other_cleanup \
  tests/test_gc_trashcan.py::test_trashcan_with_del_no_overflow \
  -q -n0 --runxfail -ra
```

Expected current result: `14 failed`.

## Test [CONFIRMED]
The command above was run on 2026-05-08 and produced:

- `14 failed in 9.48s`
- `test_callbacks_fire_on_collect`: compile rejects libpython-off IR because `gc.callbacks` lowers through `py_cpy_*`.
- `test_non_cyclic_rss_plateaus`: peak RSS `942704 KiB`, above the `200000 KiB` contract.
- `test_del_can_create_new_objects`: prints `False`, expected `True`.
- `test_module_global_del_at_shutdown`: missing `module_del_ran` on stderr.
- `test_long_running_del`: prints `0`, expected `1`.
- `test_ref_lambda_callback_fires_on_collection`: compile rejects libpython-off IR because lambda callback lowers through `py_cpy_*`.
- `test_weak_value_dict_auto_removes`: compile rejects libpython-off IR because `WeakValueDictionary` lowers through `py_cpy_*`.
- `test_weak_key_dict_auto_removes`: compile rejects libpython-off IR because `WeakKeyDictionary` lowers through `py_cpy_*`.
- `test_tracing_backend_steady_state_matches_refcount`: explicit `NotImplementedError` placeholder.
- `test_bug_110_str_in_local_list_does_not_leak`: peak RSS `942704 KiB`, above the `200000 KiB` contract.
- `test_resurrection_only_happens_once_per_object`: compile rejects libpython-off IR because the scenario still lowers through `py_cpy_*`.
- `test_resurrection_is_transitive`: runtime `AttributeError: cargo`.
- `test_resurrection_does_not_block_other_cleanup`: second collect returns `1`, expected `0`.
- `test_trashcan_with_del_no_overflow`: prints `False`, expected `True`.

## Proposals
- No.1 Classify the 14 remaining GC xfails     [CONFIRMED]
- No.2 Implement `gc.callbacks` without libpython fallback     [CONFIRMED]
- No.3 Fix BUG #110 / function-scope string-list RSS leak     [CONFIRMED]
- No.4 Complete `__del__`, shutdown-finalizer, resurrection, and trashcan semantics     [CONFIRMED]
- No.5 Implement weakref lambda callbacks     [CONFIRMED]
- No.6 Implement weak dictionaries     [CONFIRMED]
- No.7 Replace the tracing-backend performance placeholder with a measured backend ratchet     [CONFIRMED]

## No.1 Classify the 14 remaining GC xfails
### Code Change
Add this investigation file and make the 14-xfail closure an explicit
goal gate instead of accepting `xfail` as a final state.

### CONFIRMED
All 14 xfailed GC tests still fail when run with `--runxfail`; none are
stale XPASS entries. They group into five implementation buckets:

- GC API surface: `gc.callbacks`.
- Leak/RSS: BUG #110 and `test_non_cyclic_rss_plateaus`.
- Finalization: local `__del__`, shutdown finalization, resurrection,
  and trashcan plus `__del__`.
- Weakref: lambda callbacks and weak dictionary containers.
- Backend performance contract: a placeholder test that must become a
  real backend comparison before the five-backend goal closes.

## No.2 Implement `gc.callbacks` without libpython fallback
### Code Change
Add native runtime support for a pinned `gc.callbacks` list:

- `py_gc_callbacks_list()`
- `py_gc_callbacks_append(callback)`
- `py_gc_callbacks_remove(callback)`

`pcc_gc_collect()` now fires registered callbacks with phase `"start"`
before collection and `"stop"` after collection. The Python frontend
keeps `import gc` on the native path while lowering
`gc.callbacks.append(cb)`, `gc.callbacks.remove(cb)`, and the
`gc.callbacks` attribute itself without libpython fallback.
`remove(cb)` matches freshly lowered pcc function objects by native entry
and captures so `append(cb); remove(cb)` stops future callback dispatch
instead of depending on pointer identity of two wrapper allocations.

### CONFIRMED
The xfail marker was removed from
`tests/test_gc_api.py::test_callbacks_fire_on_collect`.

Command:

```bash
/opt/homebrew/bin/timeout 300s env -u LC_ALL uv run pytest \
  tests/test_gc_api.py::test_callbacks_fire_on_collect \
  -q -n0 --runxfail -ra
```

Observed result:

```text
1 passed in 23.86s
```

After strengthening the test to run a second collection after
`remove(cb)`, the full GC API file was run:

```bash
/opt/homebrew/bin/timeout 300s env -u LC_ALL uv run pytest \
  tests/test_gc_api.py -q -n0 -ra
```

Observed result:

```text
16 passed in 9.28s
```

The original 14-node `--runxfail` list was rerun after No.2 and No.6:

```text
12 failed, 2 passed in 10.71s
```

## No.3 Fix BUG #110 / function-scope string-list RSS leak
### Code Change
Fix expression ownership for object-producing binary operations:

- string/list/tuple `BinOp` results are now treated as owned objects
  by `_expr_returns_owned_object()`
- after lowering a regular `BinOp`, owned temporary operands are
  released with `_gc_release_if_owned()`

This closes the `"v" + str(i)` leak pattern: `StrLit` and `str(i)` are
temporary owned operands consumed by `py_str_concat`, and the concat
result is an owned temporary consumed by `list.append`.

### CONFIRMED
Both RSS xfails use the same reproducer: create a local list, append 100
fresh strings, return `None`, repeat 100k times. The xfail markers were
removed from both RSS tests.

Command:

```bash
/opt/homebrew/bin/timeout 600s env -u LC_ALL uv run pytest \
  tests/test_gc_regression_bugs.py::test_bug_110_str_in_local_list_does_not_leak \
  tests/test_gc_effectiveness.py::test_non_cyclic_rss_plateaus \
  -q -n0 --runxfail -ra
```

Observed result:

```text
2 passed in 6.28s
```

The containing files were then run:

```text
29 passed in 24.06s
```

## No.4 Complete `__del__`, shutdown-finalizer, resurrection, and trashcan semantics
### Code Change
Partial update on 2026-05-08: frontend discard assignment now treats
function-local `_ = owned_object` as immediate release. This fixed the two
local finalizer timing xfails:

- `tests/test_gc_finalizer_corner.py::test_del_can_create_new_objects`
- `tests/test_gc_finalizer_corner.py::test_long_running_del`

### CONFIRMED
The affected tests all require production finalizer semantics, including
safe object creation inside `__del__`, module-global finalization during
runtime teardown, one-shot resurrection, transitive resurrection of
reachable objects, cleanup isolation between resurrecting and
non-resurrecting objects, and iterative trashcan processing for deep
chains with finalizers.

Current status after the discard fix:

```text
tests/test_gc_finalizer_corner.py: 9 passed, 1 xfailed in 5.99s
original 14-node GC xfail closure list: 5 failed, 9 passed in 13.24s
```

The remaining No.4 failures are shutdown finalizer ordering, resurrection
semantics, and trashcan plus `__del__`.

Additional update on 2026-05-08: `Lazarus.stash.clear()` in
`test_resurrection_only_happens_once_per_object` was a dyn-list method
fallback, not a resurrection runtime failure. Adding native dyn-list
`clear()` removed that xfail.

Current status:

```text
tests/test_gc_resurrection.py: 4 passed, 2 xfailed in 3.82s
original 14-node GC xfail closure list: 4 failed, 10 passed in 13.47s
```

The remaining No.4 failures are:

- shutdown finalizer ordering;
- transitive resurrection;
- resurrection cleanup isolation;
- trashcan plus `__del__`.

Additional update on 2026-05-08: backend #0 now runs unreachable user
finalizers before clearing referents and recomputes reachability if any
finalizer ran. This closed the two remaining resurrection xfails:

- `tests/test_gc_resurrection.py::test_resurrection_is_transitive`
- `tests/test_gc_resurrection.py::test_resurrection_does_not_block_other_cleanup`

The focused investigation is
`docs/investigations/gc-transitive-resurrection-clear-order.md`.

Current status:

```text
tests/test_gc_resurrection.py: 6 passed in 3.59s
original 14-node GC xfail closure list: 2 failed, 12 passed in 13.40s
tests/test_gc_*.py: 193 passed, 2 xfailed in 158.95s
```

The remaining No.4 failures are:

- shutdown finalizer ordering;
- trashcan plus `__del__`.

Additional update on 2026-05-08: frontend ownership lowering now treats
statically known instance field loads as owned, and owned locals use a runtime
per-local boolean flag before releasing overwritten/cleanup values. Class
method lowering also saves and resets owned-local flag/root state per method.
This closed the deep-chain trashcan finalizer xfail:

- `tests/test_gc_trashcan.py::test_trashcan_with_del_no_overflow`

The focused investigation is
`docs/investigations/gc-trashcan-finalizer-count.md`.

Current status:

```text
tests/test_gc_trashcan.py::test_trashcan_with_del_no_overflow plus cycle/refcount guards:
5 passed in 3.41s
tests/test_gc_store_ptr_balance.py tests/test_gc_trashcan.py tests/test_gc_resurrection.py:
22 passed in 34.70s
original 14-node GC xfail closure list: 1 failed, 13 passed in 13.18s
tests/test_gc_*.py: 194 passed, 1 xfailed in 163.38s
```

The only remaining No.4 failure is shutdown finalizer ordering:

- `tests/test_gc_finalizer_corner.py::test_module_global_del_at_shutdown`

Additional update on 2026-05-08: generated pcc-Python modules now emit
`_pcc_py_module_fini_<mod>()` teardown functions. Entry `main()` calls its own
teardown, then sibling teardowns in reverse initialization order. Each teardown
clears object-valued `.modvar.*` globals and releases the old value, giving
module-global objects a final release path at process shutdown. This closed the
last No.4 xfail:

- `tests/test_gc_finalizer_corner.py::test_module_global_del_at_shutdown`

The focused investigation is
`docs/investigations/gc-module-global-finalizer-shutdown.md`.

Final status:

```text
tests/test_gc_finalizer_corner.py: 10 passed in 5.83s
original 14-node GC xfail closure list: 14 passed in 13.12s
tests/test_gc_*.py: 195 passed in 156.71s
```

## No.5 Implement weakref lambda callbacks
### Code Change
Lower lambda callbacks passed to native `weakref.ref` / `gc.callbacks`
as pcc-native `py_func_new` function objects when the lambda has no
non-module free variables. The callback adapter receives the runtime
`args` tuple, binds lambda parameters as pcc `PyObject*` values, emits
the lambda body for side effects, and returns `None`.

### CONFIRMED
The xfail marker was removed from
`tests/test_gc_g3_weakref.py::test_ref_lambda_callback_fires_on_collection`.

Command:

```bash
/opt/homebrew/bin/timeout 300s env -u LC_ALL uv run pytest \
  tests/test_gc_g3_weakref.py::test_ref_lambda_callback_fires_on_collection \
  -q -n0 --runxfail -ra
```

Observed result:

```text
1 passed in 0.87s
```

At that point the full weakref G3 file was run:

```text
6 passed, 2 xfailed in 3.92s
```

The two remaining weakref xfails were `WeakValueDictionary` and
`WeakKeyDictionary`; No.6 closes that separate implementation.

## No.6 Implement weak dictionaries
### Code Change
Add native weak-dictionary helpers on top of the existing weakref runtime:

- `py_weak_value_dict_new/set/contains/len`
- `py_weak_key_dict_new/set/len`

`WeakValueDictionary` stores weakref values in the native dict runtime and
purges stale values during containment/length checks. `WeakKeyDictionary`
stores `(weakref(key), value)` entries in a native list and compacts stale
entries during length checks.

The Python frontend now keeps `weakref.WeakValueDictionary()` and
`weakref.WeakKeyDictionary()` on the native path, tracks assignments to
those constructors, and specializes `d[k] = v`, `k in d`, and `len(d)` for
tracked weak dictionaries. The matching pcc-Python runtime port exports the
same helper ABI for bootstrap/self-runtime paths.

### CONFIRMED
Focused repro before implementation:

```bash
/opt/homebrew/bin/timeout 300s env -u LC_ALL uv run pytest \
  tests/test_gc_g3_weakref.py::test_weak_value_dict_auto_removes \
  tests/test_gc_g3_weakref.py::test_weak_key_dict_auto_removes \
  -q -n0 --runxfail -ra
```

Observed on 2026-05-08:

```text
2 failed in 0.63s
```

Both failures occur during `compile_python(..., ir_scaffold_mode="on")`:
the generated IR still calls `py_cpy_*`, so the libpython-off gate rejects
the program before runtime behavior is tested.

After the implementation, the xfail markers were removed from:

- `tests/test_gc_g3_weakref.py::test_weak_value_dict_auto_removes`
- `tests/test_gc_g3_weakref.py::test_weak_key_dict_auto_removes`

Focused command:

```bash
/opt/homebrew/bin/timeout 300s env -u LC_ALL uv run pytest \
  tests/test_gc_g3_weakref.py::test_weak_value_dict_auto_removes \
  tests/test_gc_g3_weakref.py::test_weak_key_dict_auto_removes \
  -q -n0 --runxfail -ra
```

Observed result:

```text
2 passed in 25.42s
```

Full weakref G3 file:

```bash
/opt/homebrew/bin/timeout 300s env -u LC_ALL uv run pytest \
  tests/test_gc_g3_weakref.py -q -n0 -ra
```

Observed result:

```text
8 passed in 4.83s
```

The original 14-node GC xfail closure list now reports:

```text
7 failed, 7 passed in 13.19s
```

The remaining failures are the finalizer/resurrection/trashcan bucket
tracked by No.4; the weakref bucket no longer has hidden xfails.

Bootstrap/fallback gates after the change:

```text
tests/test_llvm_capi_ir_parity.py tests/test_llvm_capi_end_to_end.py: 23 passed in 0.15s
tests/test_fallback_baseline.py tests/test_ir_py_fallback_baseline.py: 11 passed in 49.23s
tests/test_py_multi_file_compile.py tests/test_py_multi_file_bootstrap_shim.py: 70 passed in 145.63s
```

## No.7 Replace the tracing-backend performance placeholder with a measured backend ratchet
### Code Change
Replace the explicit `NotImplementedError` placeholder in
`tests/test_gc_performance.py::test_tracing_backend_steady_state_matches_refcount`
with a real workload:

- compile one cycle-free pcc-Python program
- run it under `PCC_GC_BACKEND=0`
- run the same binary under `PCC_GC_BACKEND=1` with tuned debt settings
- assert both runs finish and backend #1 stays within a broad regression
  budget relative to backend #0

The stricter five-backend performance matrix remains in `goal.md`; this
test is only the per-commit ratchet that removes the unreasonable xfail
placeholder.

### CONFIRMED
Command:

```bash
/opt/homebrew/bin/timeout 360s env -u LC_ALL uv run pytest \
  tests/test_gc_performance.py::test_tracing_backend_steady_state_matches_refcount \
  -q -n0 -ra
```

Observed result:

```text
1 passed in 0.98s
```

## Report (only when the investigation is closing)
All 14 GC xfails from the starting gate are closed as normal passing tests.
No test was deleted or kept as an xfail. The closure landed across the seven
confirmed proposals above:

- native `gc.callbacks`;
- RSS leak fix for owned string/list temporaries;
- local finalizer timing, resurrection, trashcan, and module-global shutdown
  finalizer semantics;
- native weakref lambda callbacks and weak dictionaries;
- a real backend #1 performance ratchet replacing the placeholder xfail.

Final gates:

```text
original 14-node GC xfail closure list: 14 passed in 13.12s
tests/test_gc_*.py: 195 passed in 156.71s
tests/test_llvm_capi_ir_parity.py tests/test_llvm_capi_end_to_end.py: 23 passed in 0.13s
tests/test_fallback_baseline.py tests/test_ir_py_fallback_baseline.py: 11 passed in 59.03s
tests/test_py_multi_file_compile.py tests/test_py_multi_file_bootstrap_shim.py: 70 passed in 133.08s
```
