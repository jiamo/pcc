# GC4 relocation shared-slot contract evidence

Date: 2026-07-11

Task: `AUD-P0-GC-RELOCATION-SLOT-CONTRACT`

## Source identity

- Base commit: `58c595ac0bea18c2f74af52581d259f29aac5d6d`.
- Evidence applies to the current dirty working-tree fingerprint only; no clean
  commit or published-CI claim is made.

## Changed behavior

- C backend-4 relocation now prepares and pairs source/target object slots via
  `py_obj_visit_slots`, heals source slots through `py_obj_update_slot`, and
  centrally applies ownership retention, self-reference rewriting, and
  remembered-set retargeting.
- The pcc-Python mirror adds relocation visit modes to
  `_py_obj_visit_covered_slots` and performs the same paired-slot operation.
- Per-type relocation branches now copy only raw/out-of-line payload storage
  and validate object-specific raw layout constraints. They no longer enumerate
  owned `PyObject *` fields or container entries.
- A stale class-layout probe was corrected to the actual 120-byte
  `PyClassObject` contract after the shared visitor exposed its missing
  `metaclass` slot and reversed `del_method`/`attrs` fields.

## Red-green regression

```text
gtimeout 60s env -u LC_ALL uv run pytest -q -n0 \
  tests/python/test_gc_update_referents.py::test_backend4_relocation_reuses_shared_slot_contract

before: FAILED; relocation helpers did not invoke the shared slot contract
after:  1 passed in 0.27s
```

## Focused gates

```text
gtimeout 240s env -u LC_ALL make -B -C pcc/py_runtime libpy_runtime.a
PASS

gtimeout 600s env -u LC_ALL PATH="$PWD/.venv/bin:$PATH" \
  make -B -C pcc/py_runtime libpy_runtime_pcc_py.a
PASS

C relocation list/tuple/task/set/dict/instance probes
6 passed in 39.51s

pcc-Python relocation list/tuple/task/set/dict/instance probes
6 passed in 168.09s

gtimeout 240s env -u LC_ALL uv run pytest -q -n0 \
  tests/python/test_gc_update_referents.py
23 passed in 21.97s

gtimeout 360s env -u LC_ALL uv run pytest -q \
  tests/python/test_gc_backend4_production.py
125 passed in 219.87s

gtimeout 300s env -u LC_ALL uv run pytest -q \
  tests/python/gc_production_contract
140 passed in 34.09s
```

The task card's combined 300-second `-n0` form cannot finish because the
backend-4 production file rebuilds many independent probes; a 600-second
serialized attempt reached only 57%. The complete file therefore used the
repository's configured six xdist workers and produced the final summary
above. No result without a final summary is counted as green.

## Five-backend fixed point

```text
gtimeout 900s env -u LC_ALL uv run pytest -q \
  tests/python/gc/test_pcc_bootstrap_full_gc0.py \
  tests/python/gc/test_pcc_bootstrap_full_gc1.py \
  tests/python/gc/test_pcc_bootstrap_full_gc2.py \
  tests/python/gc/test_pcc_bootstrap_full_gc3.py \
  tests/python/gc/test_pcc_bootstrap_full_gc4.py

5 passed in 846.59s
```

This is self-backed, strict no-libpython `pcc1 -> pcc2 -> pcc3` proof for
GC0..4 through the existing bootstrap contract. It is not GCC torture-suite,
package-import, performance, or published-release evidence.

## Claim boundary

Backend-4 C and pcc-Python relocation consume the shared object-slot visitor
contract for ownership and pointer updating, while object-specific raw payload
storage remains explicitly copied. Focused relocation behavior, the complete
GC production contract, and all five self-backed bootstrap fixed points pass.

