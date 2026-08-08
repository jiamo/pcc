# Cross-module -> None return-ABI drift: host-side fix

Date: 2026-07-31 (late)

Task: `LIBC-P1-PCC-RUNTIME-ARCHIVE` (slice 2; slice 1 fixed the
implicit-declaration truncations)

## Source identity

Base commit `98e62890963c60515d6f8ddc8c31996b04500f95` plus this session's
slices. Changed behavior in this slice:

- `pcc/py_frontend/codegen/class_gen.py`: the extern-class method
  declaration plan carries a per-method `returns_none` (NoneType decoded by
  name; indexed plan access instead of a wide for-target unpack) and the
  declaration loop lowers `-> None`/unannotated methods to `void`
  (async keeps PTR), matching the defining module.
- `pcc/py_frontend/codegen/marshal.py`: `marshal_to_object` materializes
  `py_None` for a void SSA value in the DynType branch.
- New regression `tests/python/test_cross_module_none_return_abi.py`:
  compiles a two-module program and asserts every `declare`d symbol's
  return type equals its `define` (generic net for the whole drift class).

## Commands and results

```text
RED (before): combined toy IR had
  declare ptr  @user_helper_mod_Helper_reset(ptr)
  define void @user_helper_mod_Helper_reset(ptr %self)
and the pure-C-archive pcc1 aborted its stage1 smoke in py_decref
(refcount underflow) rooting the leftover-x0 "return value" of
NativeModuleAliasMixin._rewrite_traceback_handler_bindings.

GREEN (after, host path):
tests/python/test_cross_module_none_return_abi.py       1 passed
full pcc.__main__ closure emit-llvm scan: 5515 defines / 5243 declares /
  0 declare-define return mismatches
pure-C archive stage1 (HIGH=c, CC=pcc): S1=0 including the publish-barrier
  smoke compile that previously aborted

Gates on the final tree:
  test_py_multi_file_compile + test_py_class_export_schema   42 passed
  test_py_multi_file_bootstrap_shim                          93 passed
  llvm_capi parity pair                                      24 passed
  default chain stage1/2/3: pcc2/pcc3 metadata-normalized byte-identical
  fallback ratchets                                          27 passed
```

## Supported claim

Host-compiled artifacts no longer contain cross-module return-ABI drift for
`-> None`/unannotated methods; the defect that made every rooted
`-> None` cross-module call incref leftover x0 (latent under HIGH=py,
fatal under all-C runtime archives) is fixed at the declaration layer and
regression-locked generically.

## Not proven / residual

- pcc1-emitted code still declares these methods as ptr because
  `encode_type`'s isinstance chains degrade schema types to `("dyn",)`
  under the self-hosted compiler (probe: host `name=None/_is_ast_node=True`
  vs pcc1 `name=dyn/False`). Carrying a `returns_none` bool in the schema
  fixes it, but the export schema shape is contract-pinned (wire tables +
  generated static tables), and an ad-hoc key broke pcc1's method
  resolution wholesale — bisect-proven and reverted. Follow-up: register
  the field through the schema contract, then prove the pure-C
  `HIGH=c` pcc1→pcc2→pcc3 chain (pcc2's HIGH=c smoke still exits 134
  today).
- See `docs/investigations/libpy-runtime-pcc-archive-pure-c-chain-crashes.md`
  for the complete evidence chain and open items (encode_type dyn
  degradation card, `__atomic_exchange_n`, implicit-declaration
  diagnostic).
