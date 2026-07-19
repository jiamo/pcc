# Runtime-object comparison lowering owner

Date: 2026-07-17

Task: `AUD-P1-PY-COMPARISON-LOWERING-CONSOLIDATION`

## Inventory and selected family

Comparison lowering currently contains distinct exact integer, boxed integer,
float, complex, valueclass, class-dunder, CPython bridge, DynType string,
static string, runtime object, DynType ordering, identity, membership, and
chained-control-flow paths. Chained comparison is control-flow composition,
not a second primitive comparison owner. DynType string equality has a guarded
string fastpath and remains intentionally separate.

The finite selected family is runtime-object comparison. Two former branches
inside `_emit_compare` independently selected `py_obj_lt/le/gt/ge`, called the
runtime, emitted the TLS exception edge, and normalized the integer result to
`i1`:

- object-vs-object comparison after value/object projection;
- DynType ordering after operand marshaling.

## One behavior owner

`_emit_runtime_object_compare` now owns the full operator-to-runtime table for
`==`, `!=`, `<`, `<=`, `>`, and `>=`, the runtime call, post-call error edge,
result-width-independent zero comparison, and `!=` inversion. Its callers
still own operand evaluation/projection, so valueclass boxing and DynType
marshaling order did not move.

Class dunder dispatch and CPython reverse-receiver selection remain before the
runtime-object branches. Exact scalar, float, DynType string, membership, and
identity paths remain outside the selected owner.

The helper is declared in `L1_CODEGEN_HOST_METHODS`; the generated pcc1 static
method table contains its four-parameter signature.

## Source and runtime parity

The AST source guard requires the four ordering runtime names to appear in the
owner and not as string literals in `_emit_compare`, requires exactly two
owner call sites, requires the common post-call error edge, and requires the
pcc1 host-method contract entry.

The boxed-float runtime test now includes both former paths:

- DynType float vs scalar ordering;
- DynType/object float vs DynType/object int ordering and equality.

Both preserve CPython numeric comparison results. Existing DynType string and
chained integer comparison tests remain green.

## Gates

Required task gate:

```bash
gtimeout 180s env -u LC_ALL uv run pytest -q -n0 \
  tests/python/test_native_obj_compare_float.py \
  tests/python/test_native_dyn_str_compare_fastpath.py \
  tests/python/test_py_compare_chains.py -rs
```

Result: `5 passed in 2.19s`. An earlier concurrent cold run of the DynType
string file timed out without a pytest summary and was discarded; the process
audit found no survivors, and its non-concurrent rerun passed `2 tests in
1.51s` before the combined gate.

Bootstrap-closure checks:

```bash
gtimeout 360s env -u LC_ALL scripts/bootstrap.sh \
  --out-dir build/bootstrap-compare-owner-pcc1 --backend self --stage 1

gtimeout 90s env -u LC_ALL build/bootstrap-compare-owner-pcc1/pcc1 \
  --python-libpython=off --ir-scaffold=on \
  --emit-llvm=/tmp/pcc_runtime_object_compare_pcc1.ll \
  /tmp/pcc_runtime_object_compare_probe.py
```

Results: current-source stage1 succeeded in `101384 ms`; the pcc1 IR contains
`py_obj_gt`, `py_obj_eq`, and `py_obj_le`, each immediately followed by a
`py_err_occurred` branch. No pcc2/pcc3 or GC matrix was run or claimed.

## Claim boundary

This proves one lowering owner for runtime-object comparison selection,
raising-call handling, and bool normalization across the two former frontend
branches. It does not consolidate exact integer/float, complex, string,
identity, membership, chain construction, or CPython comparison paths, and it
does not prove a self-hosted fixed point or all comparison semantics.
