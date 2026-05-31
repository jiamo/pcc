# Investigation: backend 3 pcc-Python arena deallocator abort

## Status
resolved

## Problem Description
Current self-bootstrap audit found that `pcc1` and `pcc2` start under
`PCC_GC_BACKEND=3`, but abort when compiling even a minimal Python source:

```text
PCC_GC_BACKEND=3 build/bootstrap-self-gc-backend3-py-arena-1778165501/pcc1 \
  --backend self --python-libpython off hello.py -o hello.out
```

Reduced problem: backend 3's pcc-Python runtime minor arena can return object
pointers that must not be passed directly to `free()`. Some pcc-Python
deallocators for `pcc_gc_alloc()`-backed object types still call `free(o)`
instead of `pcc_gc_free_object_memory(o)`.

## Repro
Smallest deterministic source:

```python
from pcc.extern import extern, c_int64
pcc_gc_backend = extern("pcc_gc_backend", (), c_int64)

class A:
    pass

def make() -> None:
    a = A()

def main() -> None:
    print("backend", pcc_gc_backend())
    i: int = 0
    while i < 100:
        make()
        i = i + 1
    print("ok")

if __name__ == "__main__":
    main()
```

Command:

```bash
env -u LC_ALL PCC_RUNTIME_CC=pcc PCC_RUNTIME_HIGH=py PCC_GC_BACKEND=3 \
  /opt/homebrew/bin/timeout 180s uv run pcc --backend self \
  --python-libpython off class_probe.py -o class_probe.out
env -u LC_ALL PCC_GC_BACKEND=3 \
  /opt/homebrew/bin/timeout 30s ./class_probe.out
```

Observed current result: compile returns `0`, run aborts with exit code `134`
after printing `backend 3`.

Bootstrap symptom:

```bash
env -u LC_ALL PCC_RUNTIME_CC=pcc PCC_RUNTIME_HIGH=py PCC_GC_BACKEND=3 \
  /opt/homebrew/bin/timeout 120s \
  build/bootstrap-self-gc-backend3-py-arena-1778165501/pcc1 \
  --backend self --python-libpython off hello.py -o hello.out
```

Observed current result: `pcc1` exits `134` with empty stdout/stderr.

## Test [CONFIRMED]
`tests/test_gc_backend_generational.py::test_generational_backend_pcc_python_runtime_class_instances_deallocate_from_minor_arena`

Observed pre-fix result:

```text
assert result.returncode == 0
E assert -6 == 0
E  +  where -6 = CompletedProcess(..., stdout='backend 3\n', stderr='').returncode
```

## Proposals
- No.1 Route all `pcc_gc_alloc()`-backed pcc-Python deallocators through `pcc_gc_free_object_memory()`     [CONFIRMED]

## No.1 Route all `pcc_gc_alloc()`-backed pcc-Python deallocators through `pcc_gc_free_object_memory()`
### Code Change
Updated pcc-Python runtime deallocators whose object body is allocated by
`pcc_gc_alloc()`:

- `pcc/py_runtime/py/py_class.py`
  - `py_class_dealloc`
  - `py_instance_dealloc`
- `pcc/py_runtime/py/py_exc_objects.py`
  - `py_dealloc_exc`
- `pcc/py_runtime/py/py_obj_stubs.py`
  - `py_dealloc_memoryview`
- `pcc/py_runtime/py/py_weakref.py`
  - `py_dealloc_weakref`

These deallocators still free their internal malloc-owned arrays/buffers
directly, but now release the object body via `pcc_gc_free_object_memory()`.
Malloc-backed runtime object families such as functions, iterators,
generators, coroutines, and thread helper objects were left unchanged.

### CONFIRMED
Focused regression:

```bash
env -u LC_ALL /opt/homebrew/bin/timeout 420s uv run pytest \
  tests/test_gc_backend_generational.py::test_generational_backend_pcc_python_runtime_class_instances_deallocate_from_minor_arena \
  -q -n0
```

Observed result: `1 passed in 24.83s`.

Backend 3 focused file:

```bash
env -u LC_ALL /opt/homebrew/bin/timeout 600s uv run pytest \
  tests/test_gc_backend_generational.py -q -n0
```

Observed result: `6 passed in 9.08s`.

Fresh self-bootstrap:

```bash
env -u LC_ALL /opt/homebrew/bin/timeout 1800s bash scripts/bootstrap.sh \
  --out-dir build/bootstrap-self-gc-arena-dealloc-1778166124 \
  --backend self --stage 2
```

Observed result:

- stage1 elapsed marker: `9420ms`
- stage2 elapsed marker: `11420ms`
- `pcc1` and `pcc2` link only `/usr/lib/libSystem.B.dylib`
- `pcc1` and `pcc2` `--help` return `0` under `PCC_GC_BACKEND=0..4`

Bootstrap matrix after rebuilding:

| stage | backend 0 | backend 1 | backend 2 | backend 3 | backend 4 |
| --- | --- | --- | --- | --- | --- |
| `pcc0` | pass | pass | pass | pass | pass |
| `pcc1` | pass | pass | pass | pass | pass |
| `pcc2` | pass | pass | pass | pass | pass |

Runtime oracle subset:

```bash
env -u LC_ALL /opt/homebrew/bin/timeout 600s uv run pytest \
  'tests/test_runtime_oracle_diff.py::test_corpus_cc_vs_pcc_py_equivalence' \
  -q -n0
```

Observed result: `7 passed, 6 skipped in 9.55s`.

Full GC suite:

```bash
env -u LC_ALL /opt/homebrew/bin/timeout 700s uv run pytest \
  tests/test_gc_*.py -q -n0 -rxX
```

Observed result: `176 passed, 14 xfailed in 154.51s`.

## Report (only when the investigation is closing)
No.1 landed. The root cause was incomplete arena ownership routing in the
pcc-Python runtime-high deallocator surface: backend 3's minor arena can return
interior arena pointers, so any `pcc_gc_alloc()`-backed object body must be
released through the GC memory helper instead of raw `free()`.

This fixed both the minimized class-instance abort and the current
`pcc1`/`pcc2` backend-3 compile abort after fresh bootstrap rebuild.
