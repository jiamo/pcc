# GC4 A3b relocation-reset physical finish

Date: 2026-08-23

Task: `GC-P0-GC4-RELOCATION-MUTATOR-QUIESCENCE`

Status: finite A3b sub-boundary confirmed; parent task remains `IN_PROGRESS`.

## Claim boundary

Backend 4 relocation-set reset in the C and strict pcc-Python runtime roots now
detaches the relocation and evacuation-page node chains while holding the GC
graph lock, preserves candidate/target/page flag clearing and counter reset
under that lock, then releases the detached physical nodes only after graph
unlock.  Relocation-epoch reseed follows the same ownership split for the
evacuation-page chain it replaces: old page membership is detached and cleared
under lock, the current membership and counters are rebuilt under lock, and the
old nodes are freed afterward.

This is a physical-retirement slice, not the complete relocation-reset holder
split.  The relocation/object/page metadata scans deliberately remain under
the graph lock because their nodes contain raw, non-owning object/page pointers;
moving those reads outside the lock without a lifetime protocol would create a
UAF/ABA path.  Reseed also still allocates new evacuation nodes under the graph
lock.  Nested/concurrent reset stress, allocation failure, the remaining GC3
and callback holders, A3c, raw container transactions and the collector-owned
stopped-world phase remain open.

## Genuine RED

`test_relocation_reset_retires_detached_nodes_after_graph_unlock` was added
before implementation.  It failed because neither runtime root had a reset
finish call:

```text
1 failed in 0.32s
ValueError: substring not found
```

The now-green contract slices the reset and reseed bodies in both roots.  It
requires detach before unlock, rejects `free` before unlock, and requires the
matching physical finish call after unlock.

## Implementation

- C reset retains detached relocation and evacuation heads, scans and clears
  their metadata under the graph lock, then calls
  `pcc_gc_relocation_reset_finish` after unlock.
- Strict reset mirrors the same ownership transfer through
  `_relocation_reset_finish`.
- C and strict evacuation-page clearing were split into allocation-free detach
  and post-unlock finish helpers.
- C and strict relocation-epoch reseed retain the detached old evacuation
  chain across the locked rebuild and finish it only after unlock.

## Focused evidence

Every pytest invocation stopped at the first failure.  The strict archive run
used visible node IDs, a short traceback and a durable live log.

1. The source-order RED turned green:

   ```text
   gtimeout 60s env -u LC_ALL uv run pytest -q -x -n0 tests/python/test_gc_backend4_production.py::test_relocation_reset_retires_detached_nodes_after_graph_unlock
   1 passed in 0.08s
   ```

2. Strict `py_gc_backend.py` compiled under
   `--backend self --python-libpython=off --ir-scaffold=on --python-library`.
   Python syntax and C syntax with `PCC_WITH_THREADS=0/1` passed.

3. The first C/strict phase-reset differential completed in 124.21 seconds
   with `2 passed`; it covers reset flag lifecycle and telemetry reseed in both
   runtime archives.  The final cache-hot packet combined that differential
   with the source contract, C page-policy reset and relocation-entry
   consumption:

   ```text
   gtimeout 180s sh -c 'set -o pipefail; env -u LC_ALL uv run pytest -vv -x -n0 --tb=short tests/python/test_gc_backend4_production.py::test_relocation_reset_retires_detached_nodes_after_graph_unlock tests/python/test_gc_backend4_production.py::test_backend4_genzgc_reset_relocation_set_clears_page_policy_shape tests/python/test_gc_backend_relocating.py::test_colored_relocating_targets_wait_for_phase_reset tests/python/test_gc_backend_relocating.py::test_pcc_python_colored_relocating_targets_wait_for_phase_reset tests/python/test_gc_backend_relocating.py::test_colored_relocating_copy_consumes_relocation_entry 2>&1 | tee build/gc4-a3b-reset-physical-finish-final.log'
   5 passed in 1.98s
   ```

4. `git diff --check` passed for the two runtime roots and the focused source
   contract.

## Frozen identities

```text
512f8ea7d5eb76672c0caef4a9ea10b4557ba1c2b7a98a3f51e482282996925c  pcc/py_runtime/src/py_gc_backend.c
815cf127e41c8da58dc831efbb72a166a77df04c7d988195475b4fea4ca07440  pcc/py_runtime/py/py_gc_backend.py
020c355c50811c17ed870275300ffaa4e77da3425d4296e98ea87d0984c7751f  tests/python/test_gc_backend4_production.py
126ab2012a9fc58eb042435d26fedb26dd3dbfb97bcda6375993da9d07540800  tests/python/test_gc_backend_relocating.py
85f5ee9c58fc7438a151e9de3582adbffd22583cf018c67be0b6dc61c2da449f  build/gc4-a3b-reset-physical-finish-final.log
```

## Next boundary

Design a lifetime-safe way to move or bound the remaining relocation/object/
page scans without reading raw non-owning pointers after unlock, and prepare
reseed evacuation nodes outside graph-lock ownership while preserving page
membership, counters, races and allocation failure.  Only after that source and
pthread evidence is green should the inventory proceed to GC3 promotion,
remembered-owner and callback holders.  A3c remains blocked.
