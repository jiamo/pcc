# Investigation: backend 3 pcc-Python constructors clobber GC header flags

## Status
resolved

## Problem Description
Backend #3 pcc-Python runtime-high now allocates small objects from a minor
bump arena, but several pcc-Python constructors still rewrite the object
header after `pcc_gc_alloc()` returns. That can erase GC metadata set by
`pcc_gc_note_object_allocated()`, including `PY_FLAG_GC_YOUNG` and
`PY_FLAG_GC_MINOR_ARENA`.

Reduced target: prove that a pcc-Python runtime constructor backed by
`pcc_gc_alloc()` preserves the backend #3 young/minor flags visible in the
object header after construction.

## Repro
Run the focused regression:

```bash
env -u LC_ALL /opt/homebrew/bin/timeout 420s uv run pytest \
  tests/test_gc_backend_generational.py::test_generational_backend_pcc_python_runtime_string_constructor_preserves_minor_flags \
  -q -n0
```

Expected pre-fix result: failure because `py_str.py::_str_alloc()` calls
`store_i32(s, 12, 0)` after `pcc_gc_alloc()` has set the backend #3 GC flags.

## Test [CONFIRMED]
Focused regression:

```bash
env -u LC_ALL /opt/homebrew/bin/timeout 420s uv run pytest \
  tests/test_gc_backend_generational.py::test_generational_backend_pcc_python_runtime_string_constructor_preserves_minor_flags \
  -q -n0
```

Observed pre-fix result: `1 failed in 0.81s`.

The probe printed:

```text
3
0
0
```

Expected:

```text
3
128
4096
```

This confirms that the pcc-Python string constructor erases backend #3
`PY_FLAG_GC_YOUNG` and `PY_FLAG_GC_MINOR_ARENA` bits after `pcc_gc_alloc()`
returns.

## Proposals
- No.1 Stop pcc-Python `pcc_gc_alloc()` constructors from resetting GC flags     [CONFIRMED]

## No.1 Stop pcc-Python `pcc_gc_alloc()` constructors from resetting GC flags
### Code Change
Removed the redundant `store_i32(obj, 12, 0)` flag reset from pcc-Python
constructors whose object body is allocated by `pcc_gc_alloc()`:

- `pcc/py_runtime/py/py_str.py`
- `pcc/py_runtime/py/py_exc_objects.py`
- `pcc/py_runtime/py/py_weakref.py`
- `pcc/py_runtime/py/py_obj_stubs.py`
- `pcc/py_runtime/py/py_class.py`

The constructors still initialize payload fields, refcount, and type tags as
before. Malloc-backed constructors were intentionally left unchanged because
they must initialize their own header flags.

### CONFIRMED
Focused regression:

```bash
env -u LC_ALL /opt/homebrew/bin/timeout 420s uv run pytest \
  tests/test_gc_backend_generational.py::test_generational_backend_pcc_python_runtime_string_constructor_preserves_minor_flags \
  -q -n0
```

Observed result: `1 passed in 24.48s`.

Backend #3 focused file:

```bash
env -u LC_ALL /opt/homebrew/bin/timeout 600s uv run pytest \
  tests/test_gc_backend_generational.py -q -n0
```

Observed result: `7 passed in 9.51s`.

pcc-Python runtime archive rebuild:

```bash
env -u LC_ALL /opt/homebrew/bin/timeout 420s make -B -C pcc/py_runtime \
  PCC='uv run pcc' PYTHON='/Users/jiamo/my/pcc/.venv/bin/python3' \
  libpy_runtime_pcc_py.a
```

Observed result: success.

Runtime oracle subset:

```bash
env -u LC_ALL /opt/homebrew/bin/timeout 600s uv run pytest \
  'tests/test_runtime_oracle_diff.py::test_corpus_cc_vs_pcc_py_equivalence' \
  -q -n0
```

Observed result: `7 passed, 6 skipped in 9.67s`.

Full GC suite:

```bash
env -u LC_ALL /opt/homebrew/bin/timeout 700s uv run pytest \
  tests/test_gc_*.py -q -n0 -rxX
```

Observed result: `177 passed, 14 xfailed in 156.09s`.

Default self-bootstrap stage2:

```bash
env -u LC_ALL /opt/homebrew/bin/timeout 1800s bash scripts/bootstrap.sh \
  --out-dir build/bootstrap-self-gc-header-flags-1778167027 \
  --backend self --stage 2
```

Observed result:

- stage1 elapsed marker: `9271ms`
- stage2 elapsed marker: `10931ms`
- `pcc1` and `pcc2` link only `/usr/lib/libSystem.B.dylib`
- `pcc1` and `pcc2` `--help` return `0` under `PCC_GC_BACKEND=3`

## Report (only when the investigation is closing)
No.1 landed. The root cause was constructor-side header reinitialization after
`pcc_gc_alloc()` returned. Backend #3 sets young/minor-arena metadata during
`pcc_gc_note_object_allocated()`, so later stores to the header flags field
silently downgraded pcc-Python runtime-high objects to metadata-less objects.

The fix preserves the allocator-owned flags for all currently identified
`pcc_gc_alloc()`-backed pcc-Python constructors, while leaving malloc-backed
constructors untouched.
