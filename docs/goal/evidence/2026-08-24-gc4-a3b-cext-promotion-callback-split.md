# GC4 A3b C-extension promotion callback split

Date: 2026-08-24

Task: `GC-P0-GC4-RELOCATION-MUTATOR-QUIESCENCE`

Status: finite callback-holder boundary confirmed; parent remains
`IN_PROGRESS`.

## Claim boundary

GC3/GC4 owner promotion no longer invokes an external C-extension
`tp_traverse` callback under the GC graph lock. The worklist tenure validates
and temporarily retains the non-moving C-extension owner, detaches it and
unlocks. The callback runs unlocked; each synchronous `Py_VISIT` slot performs
one short graph-locked promotion transaction. The temporary owner is released
after callback return. C and strict pcc-Python implement the same ordering.

## RED

The final true-pthread probe was observed red before the split:

```text
gtimeout 120s env -u LC_ALL uv run pytest -vv -x -n0 --tb=short \
  'tests/python/test_gc_threading_substrate.py::test_generational_cext_traverse_runs_outside_graph_lock[c]'

c C-extension traverse probe returned 11
```

Inside `tp_traverse`, a contender whose next operation takes the real graph
lock could not acquire it before the callback's bounded wait expired. This was
the exact locked-callback boundary, not a guessed source assertion.

## Gates

Final callback/closure/ownership/shared-slot packet:

```text
gtimeout 270s zsh -o pipefail -c "gtimeout 240s env -u LC_ALL \
  uv run pytest -vv -x -n0 --tb=short \
  tests/python/test_freestanding_gc_generational_promotion.py \
  tests/python/test_freestanding_gc_production_link_map.py \
  tests/python/test_gc_threading_substrate.py::test_generational_owner_referent_promotion_uses_bounded_logical_slot_worklist \
  tests/python/test_gc_threading_substrate.py::test_generational_cext_traverse_runs_outside_graph_lock \
  tests/python/test_gc_update_referents.py \
  2>&1 | tee build/gc3-cext-promotion-callback-final.log"

46 passed in 149.19s
```

Full owner-worklist pthread neighbors:

```text
20 passed in 5.27s
```

The 20-node packet includes C/strict built-in 16-slot unlock, GC4 owner-wide
logical slots, C-extension callback split, registered roots, GC4 finalizer/
maintenance and GC3 list/dict/set/instance/valuebox rewrite behavior. Durable
output: `build/gc3-owner-worklist-runtime-final.log`.

Task-card payload/retirement neighbors:

```text
gtimeout 630s zsh -o pipefail -c "gtimeout 600s env -u LC_ALL \
  uv run pytest -vv -x -n0 --tb=short \
  tests/python/test_freestanding_gc_relocation_payload.py \
  tests/python/test_freestanding_gc_forwarding_retirement.py \
  2>&1 | tee build/gc4-relocation-mutator-quiescence.log"

24 passed in 6.55s
```

C syntax with `PCC_WITH_THREADS=0/1`, strict self/no-libpython closure,
Python syntax and `git diff --check` passed. One earlier 120-second combined
command expired during a cold nonthreaded archive build without a final
summary; no child survived, and the measured 240-second replacement above is
the only claimed result.

## Frozen identities

```text
658ce46564c4415ac228eefe933c3646c05f89d050f4fe3500647dbb43f97d54  pcc/py_runtime/src/py_gc_backend.c
620d01fad9f260cb518e915ce475ac0ec729402270a6f39b55011e2c8ff19331  pcc/py_runtime/py/freestanding_gc_generational_promotion.py
4feea5c998e4c0fccdb6787101966dc994ed037ad3d7f8fd1a45ee5fe81ce555  tests/python/test_gc_threading_substrate.py
cca906c9734849b99f507b28e5c2576826c29a8d6edf2ecc18f95db8f1f0e176  build/gc3-cext-promotion-callback-final.log
321194dc09cd084e3808376495a73e29d7917deb5eba9e173d90074c67650612  build/gc3-owner-worklist-runtime-final.log
e8b683df9bd94166e7ceda3864d54fd51c8081b962fe2ed220e5e6c96b0e3a99  build/gc4-relocation-mutator-quiescence.log
b7dc6aabca35c67957dee688e23b35910e07f1b99043b66ad41e6b98f844f84b  nonthreaded cache provenance
b3186a632737674453b25a57beaded81db001749dd3ab0f221a3b46c0dfb222c  threaded cache provenance
```

Cache roots for the final provenance receipts:

```text
/Users/jiamo/.cache/pcc/test-artifacts/runtime-builds/3ee9561e945be4fc10b587db-pcc-py
/Users/jiamo/.cache/pcc/test-artifacts/runtime-builds/3ee9561e945be4fc10b587db-threaded-pcc-py
```

## Classified remaining callback edges

- C-extension tags cannot enter relocation-copy or forwarded-source payload
  copy/retirement visitors; the supported-tag gates reject them first.
- Trace/mark can still invoke C-extension `tp_traverse` under the graph lock.
  Initial/incremental tracing needs a cycle/object claim; final tracing must
  preserve gray-count/color commit while it retains STW.
- Backend-4 remap can visit a non-moving C-extension owner to heal moved child
  slots. Splitting that registry walk depends on the still-open collector-owned
  STW, object/source lifetime and revision protocol.
- Generic clear/deallocation callbacks remain under their collector/finalizer
  contracts and are not claimed by this slice.

## Nonclaims

No trace/remap C-extension callback closure, A3c graph-lock/no-park connection,
raw container transaction, collector-owned STW phase, source/page lifetime,
ABA/backend-switch proof, constructor publication, C-API raw-view lease,
callback-root, resurrection, stage2 performance, fixed point or broad five-GC
claim follows.
