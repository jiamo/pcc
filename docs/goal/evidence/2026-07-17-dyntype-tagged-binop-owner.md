# DynType tagged-integer binary-operation owner

Date: 2026-07-17

Task: `AUD-P1-PY-DYNTYPE-OP-LOWERING-CONSOLIDATION`

## Inventory and selected family

`_emit_binop_value` has deliberately separate paths for static scalars,
sequences, sets, CPython values, true/floor division, modulo, power, matrix
multiply, bit operations, and user-class operands. This slice selected only
the three DynType tagged-integer-capable object operations `+`, `-`, and `*`.

The former branches repeated the same behavior-bearing wrapper:

- marshal both operands to pcc-native objects;
- choose `py_obj_add`, `py_obj_sub`, or `py_obj_mul` as the slow path;
- enter `_emit_inline_tagged_int_binop_or_call` for the tagged fast path;
- emit the post-call exception edge after the joined value.

The three call sites remain in their prior locations. In particular, native
set difference and list/tuple repetition still take precedence over the
generic DynType `-`/`*` branches.

## One behavior owner

`_emit_dyn_tagged_int_object_binop` now owns the operator/runtime-symbol table,
both object projections, tagged fast/slow emission, and the post-call error
edge. `_emit_binop_value` contains three thin calls to that owner and no longer
contains the three runtime-symbol string literals.

The source guard checks those facts mechanically, including the exact three
call sites and the `L1_CODEGEN_HOST_METHODS` entry. The generated pcc1 static
method table was regenerated and contains 243 entries.

The lower `_emit_inline_tagged_int_binop_or_call` remains shared with other
integer lowering and was not broadened. Runtime slow paths remain
`py_obj_add/sub/mul`, which continue to route user objects through
`py_user_binop_dispatch` and therefore retain forward-dunder,
`NotImplemented`, reflected-dunder, and exception behavior.

## Gates

To avoid repeating the 40-second compile file after it had already passed,
the required two-file task gate was executed as the same two non-overlapping
pytest files:

```bash
gtimeout 180s env -u LC_ALL uv run pytest -q -n0 \
  tests/python/test_native_dyn_tagged_int_binop.py

gtimeout 180s env -u LC_ALL \
  PCC_CURRENT_PCC1=/Users/jiamo/my/pcc/build/bootstrap-dyn-tagged-owner-pcc1/pcc1 \
  uv run pytest -q -n0 tests/python/test_py_dynamic_dunder_binary.py
```

Results: `3 passed in 39.96s` and `6 passed in 1.26s`. The first file proves
runtime `+/-/*`, the tagged fast path, the string-add slow path, and the source
owner guard. The second file uses the current-source pcc1 and keeps dynamic
dunder/self-backend compilation green.

Slow-path dispatch and host-contract neighbors:

```bash
gtimeout 180s env -u LC_ALL uv run pytest -q -n0 \
  tests/python/test_binary_dunder_dispatch_runtime.py \
  -k binary_dunder_dispatch_and_err_check

gtimeout 30s env -u LC_ALL uv run pytest -q -n0 \
  tests/python/test_fallback_baseline.py \
  -k pipeline_and_codegen_host_contract_do_not_drift
```

Results: `2 passed, 2 deselected in 1.15s` across the pcc-Python and C runtime
implementations, and `1 passed, 23 deselected in 0.23s`.

Bootstrap closure was limited to one current-source stage1:

```bash
gtimeout 360s env -u LC_ALL scripts/bootstrap.sh \
  --out-dir build/bootstrap-dyn-tagged-owner-pcc1 \
  --backend self --stage 1
```

Result: `PCC_BOOTSTRAP_STAGE_RESULT stage=1 elapsed_ms=81660`; the resulting
`pcc1` supplied the six dynamic-dunder gates above. No pcc2/pcc3, GC matrix,
full GCC, or full test suite was run or claimed.

## Claim boundary

This proves one frontend owner for DynType `+/-/*` operand projection,
tagged-fast/runtime-slow selection, and post-call error propagation. It does
not consolidate division, modulo, power, bit operations, augmented assignment,
static typed-integer lowering, CPython numeric dispatch, or the runtime dunder
implementation itself, and it does not claim a self-hosted fixed point.
