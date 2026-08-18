# GC4 A3b owner-referent logical-slot worklist

Date: 2026-08-24

Task: `GC-P0-GC4-RELOCATION-MUTATOR-QUIESCENCE`

Status: finite A3b holder boundary confirmed; parent remains `IN_PROGRESS`.

## Claim boundary

Built-in GC3/GC4 owner-referent promotion no longer performs a recursive,
unbounded object-slot closure in one GC graph-lock tenure. C and strict
pcc-Python share one canonical logical-slot slice contract, examine at most 16
slots per tenure, re-resolve current out-of-line payload bases after every
unlock, validate the current object-index node after registry revision, and
poll only after graph unlock.

The queue adds no allocation and no permanent object-node footprint. It reuses
the existing 80-byte object's `young_next`, `young_prev` and `gc_refs` fields
only after the owner is OLD and has left the young list. Object unlink repairs
the queue before node recycling. Pending GC4 promotion work drains before an
explicit trace can reuse `gc_refs`.

The investigation's original raw byte-offset entry was denied: externally
allocated list/dict/set/class/continuation slots cannot safely survive an
unlock as owner-relative byte offsets. A temporary 120-byte object-node design
was also rejected before final evidence because its 40-byte tax would scale
with the complete stage2 object graph.

## RED

Before implementation:

```text
gtimeout 30s env -u LC_ALL uv run pytest -q -x -n0 \
  tests/python/test_gc_threading_substrate.py::test_generational_owner_referent_promotion_uses_bounded_logical_slot_worklist

FAILED ... IndexError: no pcc_gc_backend3_drain_promotion_worklist definition
1 failed in 0.32s
```

## Gates

Canonical strict closure and production ownership:

```text
gtimeout 120s env -u LC_ALL uv run pytest -q -x -n0 \
  tests/python/test_freestanding_gc_barrier_dispatcher.py \
  tests/python/test_freestanding_gc_object_slots.py \
  tests/python/test_freestanding_gc_object_nodes.py \
  tests/python/test_freestanding_gc_generational_promotion.py \
  tests/python/test_freestanding_gc_generational_scheduler.py \
  tests/python/test_freestanding_gc_production_link_map.py

38 passed in 12.98s
```

Complete shared-slot contract:

```text
gtimeout 120s env -u LC_ALL uv run pytest -q -x -n0 \
  tests/python/test_freestanding_gc_object_slots.py \
  tests/python/test_gc_update_referents.py

43 passed in 5.85s
```

C/strict pthread, bounded-batch and adjacent GC3/GC4 behavior:

```text
gtimeout 180s zsh -o pipefail -c "gtimeout 150s env -u LC_ALL \
  uv run pytest -vv -x -n0 --tb=short \
  tests/python/test_gc_threading_substrate.py::test_generational_owner_referent_promotion_uses_bounded_logical_slot_worklist \
  tests/python/test_gc_threading_substrate.py::test_generational_owner_referent_worklist_unlocks_between_slot_batches \
  tests/python/test_gc_threading_substrate.py::test_colored_owner_wide_barrier_drains_through_logical_slot_worklist \
  tests/python/test_gc_threading_substrate.py::test_generational_registered_root_promotion_resumes_in_bounded_batches \
  tests/python/test_gc_threading_substrate.py::test_colored_remembered_root_finalizer_runs_after_graph_unlock \
  tests/python/test_gc_threading_substrate.py::test_colored_remembered_root_drain_counts_maintenance_work \
  tests/python/test_gc_threading_substrate.py::test_colored_remembered_root_drain_defers_blocking_tail_and_medium_flush \
  tests/python/test_gc_backend_generational.py::test_generational_budgeted_young_worklist_advances_without_rescan \
  tests/python/test_gc_backend_generational.py::test_generational_pcc_python_budgeted_young_worklist_advances_without_rescan \
  tests/python/test_gc_backend_generational.py::test_generational_backend_young_owner_promotion_rewrites_list_referent_to_oldified_copy \
  tests/python/test_gc_backend_generational.py::test_generational_backend_minor_refill_rewrites_non_list_owned_slots_to_oldified_copy \
  tests/python/test_gc_backend_generational.py::test_generational_backend_pcc_python_runtime_young_owner_promotion_rewrites_list_referent_to_oldified_copy \
  tests/python/test_gc_backend_generational.py::test_generational_backend_pcc_python_runtime_minor_refill_rewrites_non_list_owned_slots_to_oldified_copy \
  2>&1 | tee build/gc3-owner-worklist-runtime-final.log"

18 passed in 149.52s
```

Task-card payload/retirement neighbors:

```text
gtimeout 630s zsh -o pipefail -c "gtimeout 600s env -u LC_ALL \
  uv run pytest -vv -x -n0 --tb=short \
  tests/python/test_freestanding_gc_relocation_payload.py \
  tests/python/test_freestanding_gc_forwarding_retirement.py \
  2>&1 | tee build/gc4-relocation-mutator-quiescence.log"

24 passed in 6.30s
```

C syntax with `PCC_WITH_THREADS=0/1`, Python `py_compile`, direct strict
self/no-libpython closure for the five touched freestanding modules, and
`git diff --check` also passed. `ruff` is not installed in this environment;
the attempted `uv run ruff check ...` failed before linting and is not a gate.

One 120-second pytest wrapper expired during the final cold runtime archive
build without a summary. No pytest/pcc/bootstrap child survived. The same
focused set passed under a measured 240-second inner budget and then on the
final warm archive; the interrupted run is not counted above.

## Frozen identities

```text
e6ebcde9979da910e254058ffa7bf4810f94602da41f0fe5aec34dde940a74b7  pcc/py_runtime/src/py_gc_backend.c
0a7adecaf131512a09a19e97475a8a76035697495e210bc565593222b419e30d  pcc/py_runtime/src/py_internal.h
f7f0889c5454978f00c5df1d43081352966f50160046cdb3e49089821b16fb8f  pcc/py_runtime/py/freestanding_gc_object_slots.py
c7a842cb7a5b98fe4ee68cbd7efeee9a43f0b3847d90b7a381d689da2ca30f67  pcc/py_runtime/py/freestanding_gc_object_nodes.py
5c59ece6daa022b078c8263dc04b333b2f4e7ee309033cff9757a793b1ff0d8e  pcc/py_runtime/py/freestanding_gc_generational_promotion.py
00d0337bf8fb5638e93b58a1a9b158d39348f0113237cc332b8bda7911ab81fe  pcc/py_runtime/py/freestanding_gc_generational_scheduler.py
f68d158549e2fc1cdf2361074fd9d2b9722873315cbfc686a26fc33235e5b19c  pcc/py_runtime/py/freestanding_gc_barrier_dispatcher.py
f24806baccb6133cc1ff9c9f78b6698be7c5a1733fdf07ef7ad4c91d2de0af17  pcc/py_runtime/py/freestanding_gc_state.py
b0de1664fbb58bd427a2744b9e11519b251cdae0d2cf0c8e3c4513d43358566e  pcc/py_runtime/py/py_gc_backend.py
00258e88d8181c3a0d98af4cf6a64e74c405f9299549ffbb3044f57c692b46b4  pcc/py_frontend/codegen/runtime_abi.py
1243559498d4cf0f30309a32c565ebd5d22779fedea75081c884e7b6963deff5  tests/python/test_gc_threading_substrate.py
8248c9821b8dd8f7f0cf0b1aa1be93aebf389a5988ea5c4848925d6d1670c754  tests/python/test_gc_update_referents.py
d30e3d3651535d6ae160e8808d093a165e613697fae22f4eef2cc8931524b94b  build/gc3-owner-worklist-runtime-final.log
f7dd0dc0bc27444ee27a6ab021167515d645af2945846cffdc99e8f581b7f50a  build/gc4-relocation-mutator-quiescence.log
6cbe3b9d67213467f3d8c8a25dea3ff05d1374cfa3367c96070b8c900b4e37e1  pcc/py_runtime/libpy_runtime_pcc_py.a.provenance.json
```

## Nonclaims

C-extension slot traversal remains an external callback fallback under the
graph lock and is not bounded by this slice. No A3c graph-lock/no-park
connection, raw container transaction, collector-owned STW phase,
source/page lifetime, ABA/backend-switch proof, constructor publication,
C-API raw-view lease, callback-root, resurrection, stale-candidate fairness,
stage2 performance, fixed point, or broad five-GC claim follows. The parent
task remains `IN_PROGRESS`.
