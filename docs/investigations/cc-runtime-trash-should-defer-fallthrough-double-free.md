# cc-runtime `pcc_trash_should_defer` switch fall-through → trash-node double-free

## Symptom

Two cc-tier (`PCC_RUNTIME_CC=cc`) tests crashed with SIGTRAP (rc 133), while
their `port` (default pcc-Python runtime) parametrization passed:

- `tests/python/test_native_sorted_merge.py::test_sorted_merge_releases_all_elements[cc]`
- `tests/python/test_python_generator_parity.py::test_generator_overwrite_releases_suspended_frame_local_backend0`
  (cc tier, `PCC_GC_BACKEND=0`)

The failure was first mis-attributed to `py_obj_sorted`'s merge sort (the
sorted test was the visible one). It is **not** a sorted bug.

## Repro (minimal, [CONFIRMED])

```python
class T:
    def __init__(self, v): self.v = v
    def __del__(self): print("del", self.v)
def main():
    items = [T(0), T(1)]   # >= 2 finalizable instances in a list
    items = []             # clear -> cascade dealloc
    print("end")
main()
```

```
PCC_RUNTIME_CC=cc uv run pcc --backend self --python-libpython=off \
  --ir-scaffold=on repro.py -o repro.out && ./repro.out
```

Result: prints `del 0`, `del 1`, then SIGTRAP. Bisection findings:

- `[(0,T(0))]` (1 instance) → **passes**.
- `[T(0),T(1)]` (2 bare instances, no tuple) → **crashes**.
- `[(0,1),(2,3)]` (2 tuples, no instance) → **passes**.
- `sorted([(0,5),(1,3)])` (sorted, no instances) → **passes**.

So the trigger is *a list holding ≥ 2 finalizable objects, then released* —
independent of `sorted` and of tuple nesting. lldb backtrace:

```
frame #2: pcc_trash_drain + 96
frame #1: mfm_free + 1340          <- libsystem_malloc double-free
frame #0: mfm_free.cold.4
```

The double-freed pointer is the **trash node itself** (`free(node)` in
`pcc_trash_drain`, `py_obj.c`), reached directly from `pcc_trash_drain` (no
intervening dispatch frame).

## Root cause [CONFIRMED]

`pcc/py_runtime/src/py_obj.c::pcc_trash_should_defer` was a `switch` whose
every `case` label fell through to `default` with no per-case `return`:

```c
switch (type_tag) {
    case PY_TYPE_LIST:
    ...
    case PY_TYPE_VIRTUAL_THREAD:
    default:
        return type_tag >= PY_TYPE_USER;   // <- all cases land here
}
```

The listed container/instance tags were therefore **dead code**: LIST(5),
DICT(6), TUPLE(7), SET(8), INSTANCE(11), EXC(12), ITER(14), GEN(15), … all
returned `type_tag >= PY_TYPE_USER` (false), i.e. they were **not deferred**.

The pcc-Python port (`pcc/py_runtime/py/py_obj_dealloc.py::_dealloc_should_defer`)
returns `True` for each of those tags. So in cc mode an instance-dict / nested
container cascade dealloc'd **recursively** (immediate dispatch, `depth++`)
instead of being pushed onto the trash queue and drained iteratively. That
re-entrant immediate dealloc, running while an outer `pcc_trash_drain` was
walking the thread-local trash list, corrupted the head/tail walk and freed a
trash node twice.

This is the C↔port runtime mirror drift class called out in AGENTS.md: the C
kernel and the pcc-Python semantic runtime must agree on the slot/trace/defer
contract; here they disagreed on which tags defer.

## Fix

`pcc_trash_should_defer` now gives the container/instance tags an explicit
`return 1`, matching the port exactly (and adds the missing `PY_TYPE_TASK`):

```c
switch (type_tag) {
    case PY_TYPE_LIST: ... case PY_TYPE_TASK:
    case PY_TYPE_VIRTUAL_THREAD:
        return 1;
    default:
        return type_tag >= PY_TYPE_USER;
}
```

Editing `py_obj.c` requires wiping `libpy_runtime.a` so the archive is
re-`ar`'d (stale-archive trap), then rebuilding (a cc-mode `pcc` compile
rebuilds it).

## Test [CONFIRMED]

- New focused regression: `tests/python/test_gc_trashcan.py::test_list_of_finalizable_instances_cleared`
  parametrized `[port, cc]`. The whole `test_gc_trashcan.py` file previously
  only ran the **port** tier (its `_compile_and_run` never set
  `PCC_RUNTIME_CC=cc`), which is why this cc-only bug was uncovered.
- `tests/python/test_native_sorted_merge.py` — 6 passed (incl. `[cc]`).
- `tests/python/test_python_generator_parity.py::test_generator_overwrite_releases_suspended_frame_local_backend0` — passed.
- `tests/python/test_gc_trashcan.py` + cc-mode `test_native_re_match` runtime — 11 passed.
- `tests/python/test_fallback_baseline.py tests/python/test_ir_py_fallback_baseline.py` — 18 passed (default/port path unaffected).

## Status

FIXED. The fix is confined to the cc-tier C runtime; the default/no-libpython
(port) path and the bootstrap gate use the pcc-Python runtime, which already
deferred correctly, so they are unaffected.
