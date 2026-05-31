# Investigation: backend3 pcc-Python runtime minor arena parity

## Status
resolved

## Problem Description
Backend #3 has a C-runtime single-domain minor bump arena, but the
pcc-Python runtime mirror still appears to account minor allocations without
using the same arena allocation path. The goal is to confirm whether
`PCC_RUNTIME_CC=pcc PCC_RUNTIME_HIGH=py` exposes the same backend3 minor arena
surface as the C runtime:

- small `pcc_gc_alloc()` calls use a minor bump allocation path
- allocated object flags include `PY_FLAG_GC_MINOR_ARENA`
- telemetry reports arena refills, bumps, and fallbacks
- release/free handling does not call `free()` on arena-backed object pointers

## Repro
Run the focused pcc-Python runtime-high regression:

```bash
env -u LC_ALL /opt/homebrew/bin/timeout 420s \
  uv run pytest \
  'tests/test_gc_backend_generational.py::test_generational_backend_pcc_python_runtime_uses_minor_bump_arena' \
  -q -n0
```

Expected pre-fix result: failure because the pcc-Python runtime mirror does
not mark backend3 small allocations as `PY_FLAG_GC_MINOR_ARENA` and does not
report arena refill/bump telemetry.

## Test [CONFIRMED]
Focused regression:

```bash
env -u LC_ALL /opt/homebrew/bin/timeout 420s \
  uv run pytest \
  'tests/test_gc_backend_generational.py::test_generational_backend_pcc_python_runtime_uses_minor_bump_arena' \
  -q -n0
```

Observed pre-fix result: `1 failed in 0.81s`.

The probe printed:

```text
3
0
8
0
0
0
```

Expected:

```text
3
4096
8
1
8
0
```

This confirms that the pcc-Python runtime mirror tracks minor allocations but
does not allocate through the backend3 minor arena and does not set
`PY_FLAG_GC_MINOR_ARENA`.

## Proposals
- No.1 Mirror the C-runtime minor arena in pcc-Python runtime     [CONFIRMED]

## No.1 Mirror the C-runtime minor arena in pcc-Python runtime
### Code Change
The implementation mirrors the C runtime shape in the pcc-Python runtime:

- add pcc-Python globals for `pcc_gc_minor_blocks`,
  `pcc_gc_minor_current`, and `pcc_gc_pending_minor_block`;
- add `pcc_gc_try_minor_alloc()` to `py_gc_backend.py`;
- route `py_obj.py::pcc_gc_alloc()` through that minor allocator before
  falling back to `malloc()`;
- extend pcc-Python GC object nodes from 24 to 40 bytes so they can retain the
  minor block pointer and a freeing marker;
- make `pcc_gc_free_object_memory()` use object-node `minor_block` ownership
  as the source of truth, because several pcc-Python constructors rewrite the
  header flags after `pcc_gc_alloc()`;
- route pcc-Python type-specific deallocators through
  `pcc_gc_free_object_memory()` instead of directly calling `free(o)`;
- adjust the existing minor-pressure regression so it keeps young allocations
  live long enough to create actual minor heap pressure.

### CONFIRMED
The focused pcc-Python runtime-high gate now passes:

```bash
env -u LC_ALL /opt/homebrew/bin/timeout 420s \
  uv run pytest \
  'tests/test_gc_backend_generational.py::test_generational_backend_pcc_python_runtime_uses_minor_bump_arena' \
  -q -n0
```

Observed result: `1 passed in 24.98s`.

The broader backend3 file also passes:

```bash
env -u LC_ALL /opt/homebrew/bin/timeout 420s \
  uv run pytest tests/test_gc_backend_generational.py -q -n0
```

Observed result after fixing the minor-pressure test: `5 passed in 8.52s`.

The implementation initially exposed a real abort in the mixed-cycle
collection test because constructor flag rewrites erased
`PY_FLAG_GC_MINOR_ARENA`. The fix was to use the object node's `minor_block`
field as the authoritative arena ownership marker. The regression now passes:

```bash
env -u LC_ALL /opt/homebrew/bin/timeout 420s \
  uv run pytest \
  'tests/test_gc_effectiveness.py::test_non_default_backends_collect_cross_type_cycle[3]' \
  -q -n0
```

Observed result: `1 passed in 24.74s`.

Runtime archive rebuild:

```bash
env -u LC_ALL /opt/homebrew/bin/timeout 420s make -B -C pcc/py_runtime \
  PCC='uv run pcc' PYTHON='/Users/jiamo/my/pcc/.venv/bin/python3' \
  libpy_runtime_pcc_py.a
```

Observed result: success.

Focused GC backend gate:

```bash
env -u LC_ALL /opt/homebrew/bin/timeout 600s \
  uv run pytest \
  tests/test_gc_backend_generational.py \
  tests/test_gc_backend_incremental.py \
  tests/test_gc_backend_concurrent.py \
  -q -n0
```

Observed result: `13 passed in 23.41s`.

pcc-Python runtime oracle subset:

```bash
env -u LC_ALL /opt/homebrew/bin/timeout 600s \
  uv run pytest \
  'tests/test_runtime_oracle_diff.py::test_corpus_cc_vs_pcc_py_equivalence' \
  -q -n0
```

Observed result: `7 passed, 6 skipped in 9.50s`.

Full GC suite:

```bash
env -u LC_ALL /opt/homebrew/bin/timeout 700s \
  uv run pytest tests/test_gc_*.py -q -n0 -rxX
```

Observed result: `175 passed, 14 xfailed in 154.96s`.

Default self-bootstrap stage2:

```bash
env -u LC_ALL /opt/homebrew/bin/timeout 1800s bash scripts/bootstrap.sh \
  --out-dir build/bootstrap-self-gc-backend3-py-arena-1778165501 \
  --backend self --stage 2
```

Observed result: stage1 `8.978s`, stage2 `11.199s`. `otool -L` for both
`pcc1` and `pcc2` listed only `/usr/lib/libSystem.B.dylib`.

Formatting note: `env -u LC_ALL uv run black ...` could not run because the
current pyenv/uv environment does not provide `black`.

## Report (only when the investigation is closing)
Backend3 pcc-Python runtime-high now has the same minor-arena allocation
surface that was already present in the C runtime: small backend3
`pcc_gc_alloc()` calls go through a bump arena, report refill/bump/fallback
telemetry, and release arena-backed objects through GC block ownership rather
than direct `free(o)`.

This does not make backend3 production. The remaining backend3 gaps in
`tasksV2.md` still apply: full copying oldification, pointer rewriting, and
domain-local threaded heap ownership are not implemented by this slice.
