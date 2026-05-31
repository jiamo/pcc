# Investigation: pcc-Python backend 1 aborts after allocation churn and collect

## Status
resolved

## Problem Description
During the bootstrap five-GC audit, `PCC_GC_BACKEND=1` accepted by all compiler
stages (`pcc0`, `pcc1`, `pcc2`) but generated programs linked against the
bootstrap-default pcc-Python runtime aborted after container allocation churn
followed by `gc.collect()`.

This is distinct from the C-runtime backend #1 gates, which still pass.

## Repro
Compile and run a Python program equivalent to:

```python
from pcc.extern import extern, c_int64

pcc_gc_backend = extern("pcc_gc_backend", (), c_int64)
pcc_gc_telemetry = extern("pcc_gc_telemetry", (c_int64,), c_int64)

def churn(n: int) -> int:
    total: int = 0
    i: int = 0
    while i < n:
        xs = [i, i + 1, i + 2]
        ys = {"x": xs, "i": i}
        if ys["x"][1] == i + 1:
            total = total + ys["i"]
        i = i + 1
    return total

def main() -> None:
    import gc
    print("backend", pcc_gc_backend())
    print("sum", churn(20000))
    print("collect", gc.collect() >= 0)
    print("allocs", pcc_gc_telemetry(0) > 0)

if __name__ == "__main__":
    main()
```

The program:

- imports `gc`
- reads `pcc_gc_backend()`
- allocates many short-lived lists and dicts
- calls `gc.collect()`

Observed in the audit with a temporary copy at
`build/bootstrap-self-gc-audit/matrix/gc_probe.py`:

```bash
env -u LC_ALL PCC_GC_BACKEND=1 PCC_RUNTIME_CC=pcc PCC_RUNTIME_HIGH=py \
  /opt/homebrew/bin/timeout 120s uv run python -m pcc \
  --backend self --python-libpython off \
  build/bootstrap-self-gc-audit/matrix/gc_probe.py \
  -o build/bootstrap-self-gc-audit/matrix/pcc0_gc1.out

env -u LC_ALL PCC_GC_BACKEND=1 \
  /opt/homebrew/bin/timeout 30s \
  build/bootstrap-self-gc-audit/matrix/pcc0_gc1.out
```

Expected failure: process exits with `-6` / SIGABRT after printing the backend
and allocation sum, before printing the `gc.collect()` result.

## Test [CONFIRMED]
The failure was observed for all three compiler stages in the matrix:

- `pcc0`, backend 1: compile `0`, run `-6`
- `pcc1`, backend 1: compile `0`, run `-6`
- `pcc2`, backend 1: compile `0`, run `-6`

Control gates passed:

```bash
env -u LC_ALL PCC_GC_BACKEND=1 /opt/homebrew/bin/timeout 240s \
  uv run pytest tests/test_gc_backend_incremental.py -q -n0 -rxX

env -u LC_ALL PCC_GC_BACKEND=1 /opt/homebrew/bin/timeout 240s \
  uv run pytest tests/test_gc_g1_cycle_collector.py tests/test_gc_g2_finalizers.py -q -n0 -rxX
```

Observed results: `2 passed` and `15 passed`.

Focused pcc-Python runtime regression added:

```bash
env -u LC_ALL /opt/homebrew/bin/timeout 240s \
  uv run pytest \
  'tests/test_gc_backend_incremental.py::test_incremental_backend_collects_container_churn_under_pcc_python_runtime' \
  -q -n0
```

Observed pre-fix result: `1 failed`; the generated binary exits with
`SIGABRT` (`returncode=-6`) after printing `backend 1` and
`sum 199990000`.

## Proposals
- No.1 Diff pcc-Python backend #1 trace/collect path against C runtime     [CONFIRMED]

## No.1 Diff pcc-Python backend #1 trace/collect path against C runtime
### Code Change
Remove the pcc-Python-only extra object-list registration in
`py_gc_track()`.

The C runtime registers every pluggable-GC object from `pcc_gc_alloc()` via
`pcc_gc_note_object_allocated()`. The pcc-Python port did the same in
`pcc_gc_alloc()`, but also called `pcc_gc_note_object_allocated(o)` again from
`py_gc_track()` for backend 1/2/3/4. Tracked containers therefore had duplicate
`pcc_gc_object_head` entries. During backend 1 sweep, finalization removed only
one entry before freeing the object; a later duplicate entry still pointed at
the freed address, where the object header could now read as `type_tag=0`.
That stale entry fell through to `py_dealloc_generic()` and attempted to free
the same invalid pointer again.

### CONFIRMED
The failing regression now passes:

```bash
env -u LC_ALL /opt/homebrew/bin/timeout 240s \
  uv run pytest \
  'tests/test_gc_backend_incremental.py::test_incremental_backend_collects_container_churn_under_pcc_python_runtime' \
  -q -n0
```

Observed result: `1 passed in 31.54s`.

`lldb` confirmed the pre-fix crash path stopped in `py_dealloc_generic()` from
`user_py_gc_backend__finalize_unreachable`, with the argument object's header
showing `type_tag=0` and `flags=0x408` (`GC_WHITE |
GC_SWEEP_CANDIDATE`) after a previous free. This matches a stale duplicate
object-list entry, not a missing type-specific deallocator.

Additional gates:

```bash
env -u LC_ALL /opt/homebrew/bin/timeout 300s \
  uv run pytest tests/test_gc_backend_incremental.py -q -n0
```

Observed result: `3 passed in 31.47s`.

```bash
env -u LC_ALL /opt/homebrew/bin/timeout 300s \
  make -B -C pcc/py_runtime PCC='uv run pcc' \
  PYTHON=/Users/jiamo/my/pcc/.venv/bin/python3 \
  libpy_runtime_pcc_py.a
```

Observed result: archive rebuild passed.

```bash
env -u LC_ALL PCC_GC_BACKEND=1 /opt/homebrew/bin/timeout 300s \
  uv run pytest tests/test_gc_backend_incremental.py \
  tests/test_gc_g1_cycle_collector.py tests/test_gc_g2_finalizers.py \
  -q -n0 -rxX
```

Observed result: `18 passed in 16.93s`.

```bash
env -u LC_ALL /opt/homebrew/bin/timeout 700s \
  uv run pytest tests/test_gc_*.py -q -n0 -rxX
```

Observed result: `172 passed, 14 xfailed in 165.31s`.

## Report (only when the investigation is closing)
Landed No.1. The root cause was duplicate object-list registration in the
pcc-Python `py_gc_track()` port. `pcc_gc_alloc()` already registers every
object with the pluggable GC backend; `py_gc_track()` registered tracked
containers a second time. Backend 1 sweep removed one node and freed the
object, then later encountered the duplicate stale node and attempted a second
free through `py_dealloc_generic()`.

The fix removes that pcc-Python-only duplicate registration and leaves
container tracking to the ordinary `py_gc_head` side table, matching the C
runtime split between allocation tracking and cycle-GC container tracking.

The original bootstrap matrix failure is closed by the follow-up audit in
`docs/investigations/bootstrap-five-gc-matrix-after-backend1-fix.md`.
