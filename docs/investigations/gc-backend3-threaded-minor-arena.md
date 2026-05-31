# Investigation: Backend 3 threaded minor arena ownership

## Status
resolved

## Problem Description
Continue the Backend #3 production work from `goal.md`.  Backend #3 already has
single-domain minor bump arenas, refill-time promotion, remembered-set
promotion, and pcc-Python runtime parity.  It still lacks the threaded
domain-local heap ownership called out in `tasksV2.md`: multiple mutators can
allocate from the same global `pcc_gc_minor_current` block and update minor
arena counters without a tested synchronization/ownership model.

## Repro
Run the focused Backend #3 TSan gate:

```bash
env -u LC_ALL CC=clang /opt/homebrew/bin/timeout 300s uv run pytest 'tests/test_gc_concurrent_collection.py::test_generational_minor_threadsanitizer_alloc_or_skip' -q -n0 -rxX
```

Expected after the fix: the test passes, or skips only when a TSan-capable
clang runtime is unavailable.

## Test [CONFIRMED]
Observed before the fix:

```bash
env -u LC_ALL CC=clang /opt/homebrew/bin/timeout 300s uv run pytest 'tests/test_gc_concurrent_collection.py::test_generational_minor_threadsanitizer_alloc_or_skip' -q -n0 -rxX
```

Result: `FAILED`.  TSan reported a data race on the global
`pcc_gc_minor_current` in `pcc_gc_try_minor_alloc()`:

```text
WARNING: ThreadSanitizer: data race
Read ... pcc_gc_try_minor_alloc py_gc_backend.c:964
Previous write ... pcc_gc_try_minor_alloc py_gc_backend.c:970
Location is global 'pcc_gc_minor_current'
```

## Proposals
- No.1 Make Backend #3 C minor arenas thread-owned     [CONFIRMED]

## No.1 Make Backend #3 C minor arenas thread-owned
### Code Change
Give each native mutator thread its own current minor block by making
`pcc_gc_minor_current` thread-local and tagging each block with its owner thread
id.  Keep global minor-block registration synchronized under the GC graph lock
so object-node ownership lookup can still find minor-arena objects, and make
shared minor counters plus block `live_objects` updates atomic.  The release
path now resets only the current thread's empty current block, retains empty
blocks owned by other threads, and unlinks/frees only same-owner non-current
blocks.

The same ThreadSanitizer run exposed two adjacent threaded-release races.  The
C runtime trashcan deallocation depth and pending queue are now thread-local,
and Backend #3 promotion scans the global object list under the GC graph lock
while using atomic header flag helpers.  This is still not full OCaml-style
copying oldification, pointer rewriting, or cross-domain remembered-set
sharing, but it closes the first C-runtime `PCC_WITH_THREADS=1` minor-arena
ownership hole.

### CONFIRMED
Focused TSan gate:

```bash
env -u LC_ALL CC=clang /opt/homebrew/bin/timeout 300s uv run pytest 'tests/test_gc_concurrent_collection.py::test_generational_minor_threadsanitizer_alloc_or_skip' -q -n0 -rxX
```

Result after the change: `1 passed in 4.24s`.

Broader confirmation:

```bash
env -u LC_ALL CC=clang /opt/homebrew/bin/timeout 360s uv run pytest tests/test_gc_concurrent_collection.py -q -n0 -rxX
env -u LC_ALL /opt/homebrew/bin/timeout 420s uv run pytest tests/test_gc_backend_generational.py -q -n0
env -u LC_ALL PCC_GC_BACKEND=3 /opt/homebrew/bin/timeout 700s uv run pytest tests/test_gc_*.py -q -n0 -rxX
env -u LC_ALL /opt/homebrew/bin/timeout 700s uv run pytest tests/test_gc_*.py -q -n0 -rxX
env -u LC_ALL /opt/homebrew/bin/timeout 300s make -B -C pcc/py_runtime libpy_runtime.a
env -u LC_ALL PATH="$PWD/.venv/bin:$PATH" /opt/homebrew/bin/timeout 900s make -B -C pcc/py_runtime libpy_runtime_pcc_py.a
```

Observed results:

- `tests/test_gc_concurrent_collection.py`: `4 passed in 12.06s`
- `tests/test_gc_backend_generational.py`: `8 passed in 33.38s`
- `PCC_GC_BACKEND=3 tests/test_gc_*.py`: `203 passed in 186.41s`
- default `tests/test_gc_*.py`: `203 passed in 179.97s`
- both C runtime and pcc-Python runtime archive rebuilds passed

## Report (only when the investigation is closing)
No.1 landed.  Backend #3's C runtime minor arena no longer shares a global
current block across mutator threads, and the TSan gate that originally caught
the `pcc_gc_minor_current` race is green.  This is a production-safety slice,
not the full Backend #3 production close: OCaml-style copying oldification,
pointer/reference updating, richer remembered-set behavior, and pcc-Python
threaded-domain parity remain tracked in `goal.md` No.8.
