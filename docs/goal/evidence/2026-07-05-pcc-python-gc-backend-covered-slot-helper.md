# Evidence: pcc-Python GC Backend Covered-Slot Helper

task: `AUD-P0-GC-SLOT-VISITOR`

status: `DONE_WEAK`

## Changed Files

- `pcc/py_runtime/py/py_gc_backend.py`
- `tests/python/test_gc_update_referents.py`

## Claim

The pcc-Python GC backend no longer repeats the covered object-slot family
sequence independently in each generic consumer. Trace, cycle-ref subtraction,
generational promotion, backend-4 remap, and clear now all dispatch through
`_py_obj_visit_covered_slots(o, mode, recurse)`.

That shared helper preserves the covered family order:

1. core list/tuple/dict/set owner slots
2. fixed owner slots
3. weakref slots
4. continuation stack slots
5. class metadata slots
6. pcc-native C-extension object slots
7. instance/valuebox/user-instance owner slots

The source guard verifies that each consumer calls the shared helper with the
intended literal mode and no longer names the family helpers directly. Existing
C-extension and weakref source guards were updated to check the shared helper
instead of the old duplicated consumer bodies.

## Gates

- `env -u LC_ALL uv run python -m py_compile pcc/py_runtime/py/py_gc_backend.py tests/python/test_gc_update_referents.py`
  - result: passed
- `env -u LC_ALL uv run pytest -q -n0 tests/python/test_gc_update_referents.py`
  - result: `22 passed`
- `env -u LC_ALL uv run pytest -q -n0 tests/python/gc_production_contract/test_valueclass_direct_payload_roots.py`
  - result: `5 passed`
- `make -B -C pcc/py_runtime libpy_runtime_pcc_py.a PYTHON='../../.venv/bin/python' PCC='env -u LC_ALL uv run pcc'`
  - result: passed with existing runtime warnings only
- `PCC_RUNTIME_CC=pcc PCC_RUNTIME_HIGH=py PCC_GC_BACKEND=0 env -u LC_ALL uv run pytest -q -n0 tests/python/test_gc_g1_cycle_collector.py`
  - result: `8 passed`
- `env -u LC_ALL uv run pytest -q -n0 tests/python/gc_production_contract`
  - result: `140 passed`

## Open Boundary

This is a pcc-Python mirror drift-reduction slice for the generic GC backend
consumers. It does not close broader value payload forms, remaining mirror
parity outside the covered consumer/object families, pcc1 bootstrap proof, or
the full unified slot visitor production contract.
