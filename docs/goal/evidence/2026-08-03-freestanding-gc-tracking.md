# Freestanding pcc-Python GC tracking ownership

Date: 2026-08-03

Task: `LIBC-P2-FREESTANDING-GC` (partial slice; task remains `DONE_WEAK`)

Superseded for the current tracking closure by
`2026-08-03-freestanding-gc-backend0-slot-actions.md`: finalizer reentry added
native-TLS owner identification and deferred-node draining to this member.  The
results below remain the evidence snapshot for the original track/untrack
ownership migration, not the final current symbol count.

Source identity: Git `127ec488f026556c70aa20cea4e466257f93c597`, dirty
shared worktree. Slice fingerprints include
`freestanding_gc_tracking.py=b4cd2b5b2dc2633e8589910d64bd8b1aeeba96d2cfc262e3ed40341ceb9f64c8`,
`py_obj_gc.py=a4d5750f152077ae88486d4666a919fe1dc7e8ed95eb0ae866bd5feec8c8c0bf`,
`pipeline.py=ac88f8304d782b00949e037f5021eb8b7b4acef92737c9910b663dcaca84990c`,
and
`test_freestanding_gc_tracking.py=c077ff57e38d5d320060d8a4586e57bc08fd61b7d806598450e5c913e5c06e95`.

## Claim boundary

The production backend-0 tracking transition is now owned by strict
freestanding pcc-Python:

```text
py_gc_track
py_gc_untrack
pcc_gc_default_unlink_tracked_node
pcc_gc_default_table_lock / pcc_gc_default_table_unlock
```

The first two symbols and the shared unlink implementation moved out of the
managed `py_obj_gc.o` member. The lock ABI is deliberately shared with the
managed collector so track, untrack, and collection cannot drift into separate
graph synchronization rules. This slice does not claim the remaining collector
traversal/finalizer/weakref/root/relocation families are freestanding.

## Red evidence and semantic correction

The source-owner test first failed because the strict source did not exist.
The compiler contract then failed because the required raw cross-object calls
were not in the finite signature registry. After registering only exact
signatures, object compilation exposed two further fail-closed issues:

```text
freestanding module functions require @c_abi_export: _table_lock
freestanding module emitted managed-runtime reference: ... @py_gc_head
```

Private helpers were not used as an escape hatch: the final lock helpers are
explicit internal C ABI functions because `py_gc_collect` must share them. The
IR validator change only recognizes an already-registered symbol at the exact
end of an IR line; arbitrary `py_*`/`pcc_gc_*` references remain rejected.

The comparison also found a pre-existing semantic race. The retained C
collector retries stop-the-world after a safepoint and holds the GC table lock;
the pcc-Python collector returned immediately on the first STW miss and held no
table lock. The port now mirrors the retry and shares the same lock on every
post-lock exit path.

## Object, production, and concurrency proof

LLVM and self emitters define exactly the five functions above plus the
`pcc_py_gc_table_lock` byte. Their complete undefined closure is exactly
`malloc`, `free`, `pcc_gc_backend`, `pcc_threads_enabled`,
`pcc_thread_safepoint`, `py_gc_index_insert`, `py_gc_index_remove`,
`py_gc_head`, and `py_gc_tracked_count`. The emitted lock uses acquire
`atomicrmw xchg i8` and release atomic clear; there are no managed exception,
libpython, or undeclared runtime dependencies.

The production archive uniquely attributes all five functions to
`freestanding_gc_tracking.o`, never `py_obj_gc.o`. A deterministic harness
covers null/tagged objects, first/duplicate track, two-object list updates,
first/duplicate untrack, flags, and counts. Its output matches the retained C
runtime byte-for-byte under `PCC_GC_BACKEND=0..4`.

A second harness runs one collector thread concurrently with four tracking or
untracking workers over 1,024 objects. Both the C oracle and production
pcc-Python runtime finish with:

```text
tracked:1024,1024
untracked:0,0
```

Focused results:

```text
5 passed in 53.96s  # object, archive, GC0..4, pthread collector contention
56 passed in 9.09s  # strict module + tracking + GC fastpath/thread contracts
```

## Fresh pcc1 proof

The final current-source no-libpython/self stage1 completed its publish and
exec-smoke barrier:

```text
gtimeout 360s env -u LC_ALL \
  PCC_BOOTSTRAP_PROFILE_DIR=build/libc-gc-tracking-stage1-v2-profile \
  bash scripts/bootstrap.sh \
  --out-dir build/libc-gc-tracking-stage1-v2 --backend self --stage 1

PCC_BOOTSTRAP_STAGE_RESULT stage=1 elapsed_ms=29201 \
  output=build/libc-gc-tracking-stage1-v2/pcc1
```

The profile records 325 self-backend object-cache hits, zero misses. That
fresh pcc1 compiled the real strict module with `--ir-scaffold=on`,
`--backend self`, `--python-libpython off`, and `--python-library`. Clang plus
`nm` confirmed the same exact five-function/one-byte definitions and nine raw
imports listed above.

## Remaining task boundary

Continue splitting the collector/referent/root/relocation symbol families from
managed `py_obj_gc.py` and `py_gc_backend.py`. Prove weakref/finalizer/
resurrection, suspended-frame/scheduler/C-extension roots, relocation, and
concurrent collector behavior before claiming all-GC production ownership.
Run the full five-GC fixed-point matrix once only after those migrations are
complete.
