# GC effectiveness and GC3 wrapper closure evidence

Source identity: dirty worktree based on
`20afd0795e24c9abc178f87b3304cc1f4760a312` on macOS arm64.  This is
worktree-local evidence, not a clean-commit or release claim.

## Changed behavior

- Backend 0's pcc-Python object walker now traces all six current function
  owner slots, including closure captures.
- Effectiveness counts remain exact but are relative to the program's measured
  module-root baseline, so valid module functions/dictionaries are not called
  leaks.
- GC3 minor-arena provenance is checked before a later legal young-to-old
  promotion clears the allocation-origin bit.
- The C-API state-root source contract uses a function boundary rather than
  comment text as its delimiter.

## Exact evidence

```text
gtimeout 300s env -u LC_ALL uv run pytest -q -n0 \
  tests/python/test_gc_effectiveness.py::test_cycle_collects_self_referential_list \
  tests/python/test_gc_effectiveness.py::test_tuple_unpack_instance_return_no_growth \
  tests/python/test_gc_effectiveness.py::test_tuple_unpack_dict_self_cycle_reclaims_between_iterations \
  tests/python/test_gc_effectiveness.py::test_closure_cell_cycle_collected \
  tests/python/test_gc_update_referents.py::test_pcc_python_function_slot_walkers_match_current_layout_source
result: 5 passed in 1.97s

gtimeout 360s env -u LC_ALL uv run pytest -q -n0 \
  tests/python/test_gc_effectiveness.py
result: 27 passed in 15.88s

gtimeout 180s env -u LC_ALL uv run pytest -q -n0 \
  tests/python/test_gc_update_referents.py
result: 31 passed in 35.64s

gtimeout 300s env -u LC_ALL PCC_BACKEND=llvm PCC_GC_BACKEND=3 \
  uv run pytest -q -n0 \
  tests/python/test_gc_backend_generational.py::test_generational_backend_pcc_python_runtime_uses_minor_bump_arena
result: 1 passed in 0.61s

gtimeout 300s env -u LC_ALL uv run pytest -q -n0 \
  'tests/python/test_gc_backend_under_env.py::test_gc_backend_subset_under_frontend_backend[frontend=llvm-gc=3-test_gc_backend_generational.py-test_generational_backend_pcc_python_runtime_uses_minor_bump_arena]'
result: 1 passed in 0.98s
```

## Supported claim

The four reported backend-0 effectiveness regressions and the reported GC3
wrapper case pass on the current dirty worktree.  Function capture tracing is
covered by a layout-parity contract, and GC3's allocation and promotion
semantics are asserted at their correct boundaries.

## Not proven

This is not a five-GC bootstrap result, full-suite result, integration-suite
result, or clean-commit result.  Those remain separate requested gates.

