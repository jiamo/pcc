# Investigation: Backend 3 pcc-Python threaded minor current

## Status
resolved

## Problem Description
Continue Backend #3 production work from `goal.md` No.8.  The C runtime now
uses a thread-local current minor block for `PCC_WITH_THREADS=1`, but the
pcc-Python runtime-high mirror still stores `pcc_gc_minor_current` as a normal
global in `pcc/py_runtime/py/py_substrate.py`.  Under native threads, a second
mutator can reuse the first mutator's current minor block instead of owning a
domain-local current block.

Reduced target: when `libpy_runtime_pcc_py.a` is built with
`PCC_WITH_THREADS=1`, two native threads that allocate one Backend #3 minor
object each should produce two minor arena refills, not one shared refill.

## Repro
Run the focused pcc-Python runtime-high threaded minor-block gate:

```bash
env -u LC_ALL /opt/homebrew/bin/timeout 900s uv run pytest \
  'tests/test_gc_backend_generational.py::test_generational_backend_pcc_python_runtime_threaded_minor_blocks' \
  -q -n0
```

Expected after the fix: the probe prints `1` then `2` for
`PCC_GC_COUNTER_MINOR_ARENA_REFILLS`.

## Test [CONFIRMED]
Observed before the fix:

```bash
env -u LC_ALL /opt/homebrew/bin/timeout 900s uv run pytest \
  'tests/test_gc_backend_generational.py::test_generational_backend_pcc_python_runtime_threaded_minor_blocks' \
  -q -n0
```

Result: `FAILED` in `25.83s`.  The probe printed `['1', '1']` for
`PCC_GC_COUNTER_MINOR_ARENA_REFILLS`, proving that the second native thread
reused the first thread's global pcc-Python current minor block.  The expected
thread-owned result is `['1', '2']`.

## Proposals
- No.1 Move pcc-Python Backend #3 current minor block to C-hosted TLS     [CONFIRMED]

## No.1 Move pcc-Python Backend #3 current minor block to C-hosted TLS
### Code Change
Use a standalone C helper object, `py_runtime_high_substrate.c`, to host the
pcc-Python runtime-high current minor block in native TLS.  The helper is linked
into `libpy_runtime_pcc_py.a` without pulling in the C `py_substrate.o` symbols
that `py_substrate.py` replaces.  `py_gc_backend.py` now uses those helpers
instead of the global `pcc_gc_minor_current`.

The same slice tags each pcc-Python minor block with `pcc_current_thread_id()`
so a non-current block is only unlinked/freed by its owner thread.  Minor arena
counters and block `live_objects` use exported atomic substrate helpers where
the pcc-Python frontend has no direct atomic intrinsic.  The pcc-Python runtime
archive still defaults to `PCC_WITH_THREADS=0`; the threaded regression builds a
temporary `PCC_WITH_THREADS=1` archive so it does not contaminate repository
default archive selection.

This is not full Backend #3 production: copying oldification,
pointer/reference updating, cross-domain remembered-set sharing, and the
broader pcc-Python object-index/list synchronization story remain separate.

### CONFIRMED
Focused pcc-Python runtime-high threaded gate:

```bash
env -u LC_ALL /opt/homebrew/bin/timeout 900s uv run pytest \
  'tests/test_gc_backend_generational.py::test_generational_backend_pcc_python_runtime_threaded_minor_blocks' \
  -q -n0
```

Observed result after the change: `1 passed in 24.02s`.

Backend #3 focused file:

```bash
env -u LC_ALL /opt/homebrew/bin/timeout 900s uv run pytest \
  tests/test_gc_backend_generational.py -q -n0
```

Observed result: `9 passed in 58.82s`.

Runtime archive rebuilds:

```bash
env -u LC_ALL /opt/homebrew/bin/timeout 300s make -B -C pcc/py_runtime libpy_runtime.a
env -u LC_ALL PATH="$PWD/.venv/bin:$PATH" /opt/homebrew/bin/timeout 900s make -B -C pcc/py_runtime libpy_runtime_pcc_py.a
env -u LC_ALL PATH="$PWD/.venv/bin:$PATH" /opt/homebrew/bin/timeout 900s make -B -C pcc/py_runtime PCC_WITH_THREADS=1 libpy_runtime_pcc_py.a
env -u LC_ALL PATH="$PWD/.venv/bin:$PATH" /opt/homebrew/bin/timeout 900s make -B -C pcc/py_runtime libpy_runtime_pcc_py.a
```

Observed result: all succeeded.  The final non-threaded rebuild restores the
repository default pcc-Python runtime archive after the explicit threaded
archive check.

pcc-Python runtime oracle subset:

```bash
env -u LC_ALL /opt/homebrew/bin/timeout 600s uv run pytest \
  'tests/test_runtime_oracle_diff.py::test_corpus_cc_vs_pcc_py_equivalence' \
  -q -n0
```

Observed result: `7 passed, 6 skipped in 19.15s`.

Full GC gates:

```bash
env -u LC_ALL PCC_GC_BACKEND=3 /opt/homebrew/bin/timeout 900s uv run pytest tests/test_gc_*.py -q -n0 -rxX
env -u LC_ALL /opt/homebrew/bin/timeout 900s uv run pytest tests/test_gc_*.py -q -n0 -rxX
```

Observed results:

- `PCC_GC_BACKEND=3 tests/test_gc_*.py`: `204 passed in 208.35s`
- default `tests/test_gc_*.py`: `204 passed in 204.93s`

## Report (only when the investigation is closing)
No.1 landed.  Backend #3's pcc-Python runtime-high minor allocator no longer
uses one process-global current minor block across native mutator threads.  The
new threaded regression first observed `['1', '1']` refills and now observes
`['1', '2']`, matching per-thread current-block ownership.

This closes the pcc-Python current-minor-block parity slice only.  Backend #3
still is not production-complete: copying oldification, pointer/reference
updating, cross-domain remembered-set sharing, and the broader pcc-Python
threaded object-index/object-list synchronization story remain tracked in
`goal.md` No.8.
