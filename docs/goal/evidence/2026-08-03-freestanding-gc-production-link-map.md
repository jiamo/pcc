# Freestanding GC production link-map evidence (2026-08-03)

## Claim boundary

The no-libpython pcc-Python production runtime archive contains no C-owned
collector policy definition. Retained C collector sources remain oracle/test
inputs only. This proof does not remove the explicitly permitted C-level
machine boundary or C-extension ABI shim, and does not close the remaining
semantic/fixed-point/long-run gates.

## Fail-closed source attribution

`tests/python/test_freestanding_gc_production_link_map.py` reads the complete
`nm -A` symbol table, including local symbols, and maps every archive member to
its same-stem pcc-Python source when one exists.

- More than 600 `pcc_gc_*` / `py_gc_*` collector definitions are present; all
  have pcc-Python source owners.
- The complete C-level GC-kernel allowance in
  `py_runtime_high_substrate.o` is six callable graph-lock/minor-block TLS
  access ABIs plus four private TLS storage symbols. The test uses an exact
  symbol allowlist, not a member wildcard.
- Four `PyObject_GC_*` compatibility entries are independently attributed to
  `py_capi_shim.o`; they are extension ABI, not one of the five collector
  implementations.
- C-only oracle members `py_gc_index_table.o` and
  `pcc_gc_external_resource.o` are absent. Same-named members such as
  `py_gc_backend.o` are accepted only because the corresponding
  `pcc/py_runtime/py/*.py` source exists and is in the pcc-Python build plan.

This classification follows the project contract: collector semantics move to
pcc-Python while the minimal allocation/atomics/TLS/thread/C-extension machine
boundary remains explicit.

## Gate

```text
gtimeout 120s env -u LC_ALL uv run pytest -q -n0 \
  tests/python/test_freestanding_gc_production_link_map.py

3 passed in 0.60s
```

Scoped hash:

```text
e2658b63e0f43dcd0bf91177e22977e413301f739f51d414599d428aa72f7b43  tests/python/test_freestanding_gc_production_link_map.py
```

Git HEAD while collecting evidence:
`6219a61f8f1ea84b13d9448ad66898d5ebf24a7c` (working tree intentionally
uncommitted).

## Remaining task boundary

Prove the weakref/finalizer/resurrection/suspended-frame/scheduler/
C-extension-root/relocation/synchronization semantic gate, run the one-shot
five-GC semantic/fixed-point matrix, and record long-running RSS,
fragmentation, pause, and throughput deltas.
