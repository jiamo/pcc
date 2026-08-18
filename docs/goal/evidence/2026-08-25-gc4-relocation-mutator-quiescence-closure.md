# GC4 relocation/mutator quiescence parent closure — 2026-08-25

## Claim

`GC-P0-GC4-RELOCATION-MUTATOR-QUIESCENCE` is complete at its original
copy/remap/retire phase boundary.  C and strict pcc-Python share the same
graph-lock/no-park/STW ordering; true-pthread list/dict/set raw-access probes
prove a collector cannot copy or retire storage while a pre-existing mutator
access is live; forwarding target-death and normal retirement defer cleanup
decrefs until structural metadata is invisible and graph/world locks are
released.

The task's focused relocation-payload/forwarding-retirement gate passes 24/24
on current source.  The investigation records the complete finite evidence
chain: A1/A2/A3 graph-lock and thread admission, C-extension trace/remap callback
splits, generic slot/list/dict/set raw transactions, constructor publication,
C-API borrowed/raw leases, and callback-root corrections through sorted,
min/max, enumerate, internal iterators, tuple scans and read lookup.

## Routed successors — not claimed complete

- `GC-P0-FORWARDED-SOURCE-PAYLOAD-RETIREMENT` already owns source payload
  release.  The set-remove probe adds exact evidence that strict forwarding
  retirement leaves a managed C-extension stored-key dealloc count at zero
  after two epochs while C reports one; direct strict bit62 dealloc control
  passes, so flag/hook hypotheses are denied.
- `GC-P0-LAST-DECREF-RESURRECTION-METADATA-RESTORE` owns GC3/GC4 resurrection
  metadata repair.
- `GC-P0-CONTAINER-CALLBACK-MUTATION-COMMIT` newly owns dict set/delete and set
  add/update callback restart plus commit/finalizer ordering.
- FUNC/ITER and suspended-execution fresh-admission remain blocked by the
  recorded strict allocator/recursive-MemoryError boundary.

No Stage1/Stage2, fixed-point, five-GC broad parity or performance claim is
made by this correctness closure.

## Current-source gates

- strict source closures for all latest touched runtime/C-API owners: pass.
- strict archive owner: `1 passed in 0.97s`.
- runtime ABI chunk plus GC abstraction: `17 passed in 11.20s`.
- relocation payload/forwarding retirement: `24 passed in 8.23s`.
- task-board validation and `git diff --check`: pass.

## Status

`DONE_STRONG` for the parent quiescence boundary.  Routed successor tasks
remain unfinished and are required before stage/five-GC claims.
