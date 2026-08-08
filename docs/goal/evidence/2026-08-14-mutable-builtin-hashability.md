# Mutable builtin hashability — current-source executable evidence

Date: 2026-08-14

Mode proved here: host-pcc, self backend, strict no-libpython, using the
content-addressed production pcc-Python runtime archive. This evidence does
not claim current-pcc1 executable parity.

Implemented contract:

- `list`, `dict`, `set`, and `bytearray` hashing raises `TypeError` and returns
  the hash-error sentinel `-1`.
- tuple and valuebox hashing stops immediately when a child hash fails.
- direct dict/set hash callers check the pending exception before lookup,
  mutation, missing-key handling, or boolean conversion. This covers
  subscription load/store/delete, `get`, both `pop` forms, `setdefault`,
  membership, `add`/`remove`/`discard`/`update`, constructors,
  comprehensions, and `dict.fromkeys`.
- C and pcc-Python runtime mirrors use the same contract.

Focused evidence:

- `cc -fsyntax-only` for `py_obj_ops_compare.c`, `py_dict.c`, and `py_set.c`:
  PASS (two pre-existing incompatible-pointer warnings in iterator code).
- host-pcc `--python-library --emit-llvm` for `py_obj_ops_compare.py`,
  `py_dict.py`, and `py_set.py`: PASS.
- `test_hash_runtime_mirrors_reject_mutable_builtins_and_guard_callers`:
  `1 passed in 0.08s`.
- task-board validation after ingestion: `OK: 349 tasks validated`.

Current-source executable evidence:

- `PCC_RUNTIME_ARCHIVE=.../75966df8ae5a3fd42cc6e575-pcc-py/libpy_runtime_pcc_py.a`
  `pytest -q -x -n0 tests/python/test_native_hashability_contract.py`:
  `2 passed in 3.30s`.
- Adjacent dict/set/membership cluster (`test_native_dict_fromkeys.py`,
  `test_python_set_methods_parity.py`, and
  `test_native_membership_cpy_bridge.py`): `18 passed in 13.68s`.
- The executable differential compares exact stdout with CPython and asserts
  that rejected operations do not mutate the dict/set.

Open boundary:

- Repeat that focused behavior through the deliberate current-pcc1 build and
  final sequential pcc1 -> pcc2 -> pcc3 chain.
- Memoryview hashing is not claimed: the current runtime layout has no
  readonly/export metadata from which to implement CPython's conditional
  memoryview hashability honestly.
