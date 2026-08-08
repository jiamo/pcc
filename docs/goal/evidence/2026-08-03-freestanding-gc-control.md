# Freestanding pcc-Python GC control ownership

Date: 2026-08-03

Task: `LIBC-P2-FREESTANDING-GC` (partial slice; task remains `DONE_WEAK`)

Source identity: Git `127ec488f026556c70aa20cea4e466257f93c597`, dirty
shared worktree.  Slice fingerprints include
`freestanding_gc_control.py=669952b523ea82b871ca316321319361fe04d98381a1d6d67d31515c8b7ebd10`,
`py_obj_gc.py=07a40a9806f6d54fe56524a986067827b71b216d2e0f8d82c92e14615487a00c`,
and
`test_freestanding_gc_control.py=ca0653f5bfc9201d8a9e46be00dc6513c2efc98f41250bb118f1bc807915b4c8`.

## Claim boundary

Eleven state/object-header-only public GC control exports moved unchanged from
managed `py_obj_gc.py` into strict
`pcc/py_runtime/py/freestanding_gc_control.py`:

```text
py_gc_init
py_gc_enable / py_gc_disable / py_gc_is_enabled
py_gc_is_tracked / py_gc_get_count
py_gc_get_threshold / py_gc_set_threshold
py_gc_freeze / py_gc_unfreeze / py_gc_get_freeze_count
```

This slice does not claim collector graph, finalizer, weakref, root, relocation,
or concurrent-GC closure.

## TDD and object closure

The ownership test first failed because the strict source did not exist:

```text
FileNotFoundError: .../freestanding_gc_control.py
1 failed in 0.08s
```

After the move, LLVM and self objects each define exactly the eleven symbols.
Their complete undefined set is exactly the six raw state globals
`py_gc_enabled`, `py_gc_tracked_count`, `py_gc_threshold{0,1,2}`, and
`py_gc_freeze_count`; there are no managed runtime, exception, libpython, or
libc imports.

## C-oracle, production, and GC0..4 proof

The production test links the same deterministic C harness separately against
the retained C runtime and the current pcc-Python runtime.  It covers init,
enable/disable, default/set/negative-preserving thresholds, generation count,
freeze/unfreeze, and null/tagged/untracked/tracked object-header cases.

Archive
`~/.cache/pcc/test-artifacts/runtime-builds/57f736058ff787027e031f60-pcc-py/libpy_runtime_pcc_py.a`
contains `freestanding_gc_control.o`; `nm -A -g` attributes every public symbol
exactly once to that member and none to `py_obj_gc.o`.  The pcc-Python harness
matches the C oracle byte-for-byte under `PCC_GC_BACKEND=0..4`.

```text
4 passed in 57.42s
```

Adjacent fast gates:

```text
3 passed in 5.49s
  C-extension source guard + native gc module

1 passed in 1.27s
  managed backend no-libpython compile gate

4 passed in 0.60s
  public GC ABI, naming, refcount surface, no-libpython selector
```

The fresh current-compiler self/no-libpython pcc1 produced by the preceding
telemetry slice compiled the real strict module in 0.35 seconds.  Compiling
that IR to an object again showed exactly eleven definitions and six raw state
imports.

## Remaining task boundary

The production pcc-Python archive still has managed `py_obj_gc.o` and
`py_gc_backend.o` symbol families.  Continue with a closed algorithm/ABI slice
that preserves the one graph contract, then prove finalizer/weakref/
resurrection, suspended roots, concurrent synchronization, relocation, full
archive ownership, one final five-GC fixed point, and long-running metrics.
