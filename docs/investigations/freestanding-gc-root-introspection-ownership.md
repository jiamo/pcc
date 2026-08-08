# Investigation: move GC root introspection into strict freestanding pcc-Python

## Status
resolved

## Problem Description
`LIBC-P2-FREESTANDING-GC` still obtains five public root-introspection ABI
functions from the managed `py_gc_backend.py` archive member. The retained C
oracle holds the object-graph lock while reading scheduler, frame, and
continuation root lists. The current pcc-Python implementations of the three
count functions traverse those concurrently mutated lists without the lock;
only `pcc_gc_slot_is_runtime_root` is locked. Confirm the ownership and
synchronization gap, then move exactly this read-only five-function family to
one strict freestanding object.

## Repro

```bash
gtimeout 180s env -u LC_ALL uv run pytest -q -n0 \
  tests/python/test_freestanding_gc_root_introspection.py
```

Expected before the slice: the source-owner test fails because
`freestanding_gc_root_introspection.py` does not exist and the five exports
remain in `py_gc_backend.py`.

## Test [CONFIRMED]
The focused test pins one strict source owner, exact LLVM/self raw closure,
unique production archive ownership, deterministic C-oracle behavior across
GC0..4, and pthread register/unregister/count contention.

The initial focused run failed all three collected cases because the strict
source did not exist; the source-owner failure also confirms that the managed
module still owns the family. This is the intended red boundary.

## Proposals
- No.1 Split the five read-only root-introspection exports and share the existing graph lock [CONFIRMED]

## No.1 Split the five read-only root-introspection exports and share the existing graph lock

### Code Change
Create `freestanding_gc_root_introspection.py`, export only the three counts,
their aggregate score, and runtime-root membership query, and call the existing
low-level graph lock ABI around every shared-list traversal. Remove those five
definitions from managed `py_gc_backend.py`; retain all mutation and collector
logic there for later slices.

### CONFIRMED
The five public functions and one internal slot-span helper now compile as one
strict freestanding object. The three count functions now match the C oracle's
graph-lock discipline; membership already had a lock and retains it. LLVM and
self objects have the exact six-definition/six-import closure pinned by the
test. The production archive attributes every symbol uniquely to the new
object, deterministic root counts/membership match the C runtime under GC0..4,
and a four-mutator/one-observer pthread harness finishes at zero roots for both
thread-enabled archives under all five backends.

The first concurrent harness used the default archives, whose graph lock is
intentionally compiled out under `PCC_WITH_THREADS=0`; its timeout was a test
configuration error, not implementation evidence. Re-running the same harness
with both archives built using `PCC_WITH_THREADS=1` passed.

## Report
No.1 landed as the smallest ownership slice. It removes five exports from the
managed backend member, fixes the unlocked pcc-Python count traversal, and
does not move root mutation, tracing, rewriting, or collector policy. Focused
and adjacent tests are green, and a fresh current-source no-libpython/self
stage1 built and then compiled the strict source. Full fixed-point/five-GC
acceptance remains on the parent task and was deliberately not run here.
