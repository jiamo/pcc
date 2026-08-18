# GC4 A3b GC3 registered-root walk bounded

Date: 2026-08-23

Task: `GC-P0-GC4-RELOCATION-MUTATOR-QUIESCENCE`

Status: finite A3b GC3 holder sub-boundary confirmed; parent remains
`IN_PROGRESS`.

## Claim boundary

The C and strict pcc-Python Backend-3 generational schedulers no longer walk
all registered frame, continuation, scheduler and builtin-exception-cache
slots in the main generational graph-lock tenure. Frame/continuation roots and
scheduler/cache roots now have separate resumable walkers. Each walker owns
one graph-lock transaction and examines at most its supplied budget; the
production scheduler supplies `min(remaining_budget, 16)`.

Structural removals repair a retained cursor before the node can be freed.
Continuation relocation retargeting resets the current slot offset. A shared
registry revision detects mutation that reenters during a slot promotion, but
ordinary head insertion does not restart a partially completed walk and
therefore cannot starve deeper roots merely through frame churn.

This proves a bound on registry-slot enumeration per root-walker lock tenure.
It does **not** bound the promotion work reachable from one slot: owner
referent traversal remains a separate open A3b boundary. It also does not
close trace-cycle extension traversal, caller-provided runtime-root visitors,
remaining allocation/tripwire/log holders, A3c graph-lock/no-park connection,
raw container access, collector STW ownership, broad parity, performance or
fixed point.

## RED and denied paths

The initial source contract was genuinely RED:

```text
tests/python/test_freestanding_gc_generational_scheduler.py::
test_generational_registered_root_walks_bound_each_graph_lock_tenure

IndexError: pcc_gc_generational_promote_scheduler_roots was absent
1 failed in 0.09s
```

The following proposals or probe assumptions were then denied:

- `[DENIED]` unexported strict reset helpers: direct strict closure failed
  closed because every function in a freestanding module requires an explicit
  `@c_abi_export` owner.
- `[DENIED]` treating pointer replacement as promotion evidence: the C probe
  reported `changed=0` while flags showed the first slot OLD (`0x100`) and the
  last slot YOUNG (`0x80`). Lists can promote in place, so the final probe
  measures generation flags.
- `[DENIED]` restarting every cursor on every registry revision: after a
  16-slot batch followed by one head insertion, the C probe reported
  `frame batch2 old=16 inserted_old=1`. That policy rescanned the head and can
  starve deep frames under normal frame churn. The accepted implementation
  repairs cursors exactly on removal/retarget and uses revision only to detect
  reentrant mutation during a visitor.
- `[DENIED]` separate C-only helper names: the strict probe failed to link
  `_pcc_gc_promote_frame_roots` / `_pcc_gc_promote_scheduler_roots`. C and
  strict now share the existing
  `pcc_gc_generational_promote_{frame,scheduler}_roots` ABI.
- One strict runtime run hit its 90-second outer watchdog without a final
  pytest summary. No child survived. It was rerun with 180 seconds, justified
  by the measured 126-139 second cold archive build envelope; the rerun and
  final combined gate passed.

## Implementation

- `pcc/py_runtime/src/py_gc_backend.c`
  - adds separate frame/continuation and scheduler/cache cursors;
  - moves both bounded root walkers before the main GC3 lock region;
  - repairs cursors in frame, continuation and scheduler unlink paths;
  - resets a continuation slot offset on relocation retarget;
  - advances a graph-locked registry revision for reentrant mutation
    detection.
- Strict pcc-Python mirrors the same ownership in
  `freestanding_gc_generational_scheduler.py`,
  `freestanding_gc_root_registry.py`,
  `freestanding_gc_frame_registry.py`,
  `freestanding_gc_relocation_payload.py` and
  `freestanding_gc_state.py`.
- `runtime_abi.py` records the exact cross-object signatures and raw global
  storage types.
- The dynamic C/strict test uses 40 frame roots to prove `16/16/8` progress,
  inserts a new head between batches to prove no restart starvation, removes
  the scheduler node held by the retained cursor, and proves safe progress to
  all 19 surviving scheduler roots.

## Gates

Direct strict self/no-libpython closures passed for the scheduler, root
registry, frame registry and relocation payload, producing:

```text
/tmp/gc3_root_scan_scheduler.ll
/tmp/gc3_root_scan_root_registry.ll
/tmp/gc3_root_scan_frame_registry.ll
/tmp/gc3_root_scan_relocation_payload.ll
```

Final source/ABI/LLVM+self closure packet:

```text
gtimeout 90s sh -c 'env -u LC_ALL uv run pytest -vv -x -n0 --tb=short ... \
  2>&1 | tee build/gc3-root-scan-source-final.log'

22 passed in 7.94s
```

Final production/pthread packet:

```text
gtimeout 180s sh -c 'env -u LC_ALL uv run pytest -vv -x -n0 --tb=short \
  tests/python/test_freestanding_gc_generational_scheduler.py \
  tests/python/test_freestanding_gc_root_registry.py::test_production_archive_uniquely_owns_registry_and_matches_c_oracle \
  tests/python/test_freestanding_gc_root_registry.py::test_root_registry_survives_pthread_mutation_and_observation \
  tests/python/test_freestanding_gc_frame_registry.py::test_archive_owns_frame_registry_and_matches_gc0_to_gc4_oracle \
  tests/python/test_freestanding_gc_frame_registry.py::test_frame_registry_survives_threaded_mutation_and_observation \
  tests/python/test_freestanding_gc_relocation_payload.py::test_production_archive_has_one_relocation_payload_owner \
  tests/python/test_gc_threading_substrate.py::test_generational_registered_root_promotion_resumes_in_bounded_batches \
  2>&1 | tee build/gc3-root-scan-final.log'

15 passed in 152.94s
```

The packet includes one-owner archive checks, C/strict LLVM+self object
closures, five-backend C-oracle parity for root/frame registries, real pthread
registry mutation/observation, and both C and strict GC3 bounded-batch probes.

Python syntax, C11 syntax with `PCC_WITH_THREADS=0` and `=1`, and
`git diff --check` also exited zero.

## Frozen identities

```text
85ec89dd9a65d5d62deb387d246e91d03f880df0cdb941a0105cf587277a0091  pcc/py_runtime/src/py_gc_backend.c
9467265bbc878f3c469d84f2f73c61abc42c8f4269bcc5fd55962553b6ef5b96  pcc/py_runtime/py/freestanding_gc_state.py
ab752bfd72dcb08768dca01804ca80518218cfb99756852e34899c9fbad13e22  pcc/py_runtime/py/freestanding_gc_root_registry.py
ea173258e08b6b5d9c462deab4f37f2e9df82407ca94279fb162e8af868294df  pcc/py_runtime/py/freestanding_gc_frame_registry.py
711075e51e4f99e2c77e6cd657ac653ea5afcc17dc2fce67833dcd84507b63e4  pcc/py_runtime/py/freestanding_gc_generational_scheduler.py
35a996abcbaae33b7c63498140df11b3ef5a058f9cf1c296d3b7ad41ee4e8dea  pcc/py_runtime/py/freestanding_gc_relocation_payload.py
fb78897bf56a92bf6f6d41c860888a3f7f4f87acb2db97b1001426085dd6e1c5  pcc/py_frontend/codegen/runtime_abi.py
311c4b74a4d9d52eec5aa571f821f3633f170587b5d460a0369400b61116c80b  tests/python/test_freestanding_gc_generational_scheduler.py
cebff65ec7c509a9ced1ca7a1c874f615461643a45f2edc00a390722095d8785  tests/python/test_gc_backend_generational.py
cfea39de6784f06f0bc1b116761c3133b94783a2c329d1bb463a17167a18dc0f  tests/python/test_gc_update_referents.py
860cb02ec7d83428e906dcec4cdec173fd4b45205c16a50348debd81b98b1f03  tests/python/test_gc_threading_substrate.py
4b1c8286fb75b20c1607ef431536e4a07388893fab61f4b1eefa83de59a1ca0c  tests/python/test_freestanding_gc_root_registry.py
287cdbc390e92683286af41576b0542526d7d448f0f1f8e0cee5c4f7d94e34f6  tests/python/test_freestanding_gc_frame_registry.py
8af55e3ce3f5d18a695d5b1ac240b7f3a6f9f337e33a5a713de0315351d867ff  tests/python/test_freestanding_gc_relocation_payload.py
1443b4bf8e063ef051d5ebcaf13254ddc351b827c87a351bfdd47fb48d2f6389  tests/python/test_freestanding_gc_state.py
42c1dd6d44cec8233e5ebf6fa2c5ebbed5a0ce87d5700e186252531459fdcb23  build/gc3-root-scan-source-final.log
f41e93a824437ecb1baf82c979829b30248829a416b1e6458c275e3071d724cf  build/gc3-root-scan-final.log
c6bb28e919097fa81a77537cfe7bedbc167f9ebd234f0657e462ccf1e39efd3f  /tmp/gc3_root_scan_scheduler.ll
f84e532cbaa1d0702ee688692056e957af58637c9416199241fed5ca3ee2ccbc  /tmp/gc3_root_scan_root_registry.ll
11a866352503f88841e6e09cdd0216c2f939279e63a7801bd1a4ce39ea1b2aa0  /tmp/gc3_root_scan_frame_registry.ll
7b293735d5b927ddea5503cc7f4fbd60cad825a6fc059c97007665958b7f12d6  /tmp/gc3_root_scan_relocation_payload.ll
```

## Next boundary

Do not connect A3c. Inventory and split or bound owner-referent promotion,
trace-cycle extension traversal, caller-provided runtime-root visitors, and
remaining allocation/tripwire/log or unbounded holder paths. The registered
root **enumeration** bound is closed; no broader root-promotion closure claim
is made.
