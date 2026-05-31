# pcc-native extension module state roots must survive GC

resolved 2026-05-31 — pcc-native extensions can now allocate `PyModuleDef.m_size`
module state, read it through `PyModule_GetState`, and expose PyObject
references from that state through `m_traverse`; those references are visited as
runtime roots under all five GC backends.

## Repro

The reduced contract lives at
`tests/python/gc_production_contract/test_extension_module_state_roots.py`.

It builds a strict no-libpython pcc-native extension with:

- `PyModuleDef.m_size = sizeof(StateDemoState)`
- a `Py_mod_exec` slot that creates a list and stores it only in module state
- `m_traverse` that `Py_VISIT`s that state-held list
- a `push()` method that appends through `PyModule_GetState(self)`

The Python driver imports the extension, calls `gc.collect()` between calls, and
expects sizes `2`, `3`, `4` under `PCC_GC_BACKEND=0..4`.

## Root Cause

`utils/fake_libc_include/Python.h` declared `PyModuleDef.m_size`,
`m_traverse`, `m_clear`, and `m_free`, but the no-libpython shim did not expose
`PyModule_GetState` or allocate per-module state in `PyModule_Create2`.

Even after state allocation, tracing collectors need an explicit bridge from
extension-owned raw state into pcc's runtime root walk. A C extension state field
is a raw `PyObject *`, not a pcc-owned updateable slot, so backend #4 cannot
rewrite it like a frame/root slot; the safe narrow policy is to pin objects
reported by `m_traverse` before visiting them as roots.

## Fix

- Add `PyModule_GetState` to the pcc-native `Python.h` compile surface.
- Allocate zeroed `m_size` state in `PyModule_Create2` and register it beside
  the pcc module object and its `PyModuleDef`.
- Visit `m_traverse` references from the GC runtime root seed path.
- Promote the same roots during backend #3's generational root promotion.
- Pin state-reported objects before root visitation because extension state owns
  raw `PyObject *` fields that are not relocation-updateable slots.

## Evidence

- `tests/python/gc_production_contract/test_extension_module_state_roots.py -q -n0`
  -> 5 passed
- `tests/python/gc_production_contract -q -n0` -> 110 passed
- `tests/python/test_pcc_native_extension_loader.py::test_pcc_native_multiphase_init_under_self_backend_no_libpython`
  + `::test_pcc_native_extension_import_runs_under_self_backend_no_libpython`
  + `::test_pcc_native_generic_getdict_under_self_backend_no_libpython`
  -> 3 passed
- `tests/python/test_package_extension_abi.py -q -n0` -> 8 passed, 4 skipped
- `tests/python/test_pcc_bootstrap_full.py::test_full_three_stage_bootstrap_self -q -n0`
  -> 1 passed

## Boundaries

This is a pcc-native no-libpython extension-state root contract. It does not
claim complete CPython extension ABI compatibility, multi-interpreter module
state isolation, `m_clear`/`m_free` lifecycle completeness, successful NumPy
native extension execution, or relocation-updateable C-extension state slots.
