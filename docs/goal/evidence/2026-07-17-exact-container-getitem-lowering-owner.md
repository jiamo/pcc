# Exact-container getitem lowering owner

Date: 2026-07-17

Task: `AUD-P1-PY-SUBSCRIPT-LOWERING-CONSOLIDATION`

## Inventory and selected family

The active subscript surface was inventoried before selecting a finite slice:

- ordinary expression lowering owns slice, value-array, native-module,
  user-dunder, `os.environ`, CPython-bridge, exact-container, string/bytes,
  DynType integer-fast, and generic object paths;
- exact-int object-boundary lowering separately repeated exact list, tuple, and
  dict getitem before its DynType fallback;
- store lowering has distinct ordinary assignment, unpack-target, and
  augmented-assignment entries, plus slice/weak-dict/CPython special cases;
- the exception-producing exact getitem paths use `py_list_getitem`,
  `py_tuple_getitem`, and `py_dict_getitem`, followed by a TLS error edge so
  `IndexError` and `KeyError` are catchable.

The selected family is only exact `ListType` / `TupleType` / `DictType`
getitem shared by ordinary expression lowering and the exact-int object
boundary. DynType, slices, stores, CPython bridge, strings/bytes, and value
arrays are inventoried but not claimed by this slice.

## One behavior owner

`SubscriptLoweringMixin._emit_exact_container_subscript_load_object` now owns:

- the exact-container-to-runtime-symbol decision;
- integer-index or object-key projection;
- the raising public getitem variants;
- the post-call exception edge;
- receiver release;
- the raw object plus semantic element-type result contract.

Ordinary expression lowering only applies the semantic coercion. The exact-int
object boundary consumes the raw object. The former behavior-bearing runtime
call blocks were removed from both callers.

The new cross-mixin helper is declared in `L1_CODEGEN_HOST_METHODS`, and the
pure-data pcc1 static method table was regenerated with its three-parameter
signature.

## Parity and source guard

The focused test emits four functions: list/dict value contexts and list/dict
object-boundary contexts. Every function contains the same canonical
`subscript.{list,dict}.getitem` call shape followed by `py_err_occurred`.

The source guard requires all three raising runtime symbols to exist only in
the common owner, rejects them in both former callers, requires both callers to
invoke the owner, and requires the helper in the pcc1 host method contract.

## Gates

Required task gate:

```bash
gtimeout 180s env -u LC_ALL uv run pytest -q -n0 \
  tests/python/test_native_subscript_raise.py -rs
```

Result: `4 passed in 67.35s`. The runtime cases preserve valid/negative list
indices, catch missing-dict `KeyError`, catch list `IndexError`, and preserve
the separate non-raising `dict.get` / `pop` / `setdefault` contracts. DynType
integer getitem/setitem remained on its existing i64 helpers.

Bootstrap-closure checks:

```bash
gtimeout 360s env -u LC_ALL scripts/bootstrap.sh \
  --out-dir build/bootstrap-subscript-owner-pcc1 --backend self --stage 1

gtimeout 90s env -u LC_ALL \
  build/bootstrap-subscript-owner-pcc1/pcc1 \
  --python-libpython=off --ir-scaffold=on \
  --emit-llvm=/tmp/pcc_exact_container_owner_pcc1.ll \
  /tmp/pcc_exact_container_owner_probe.py
```

Results: current-source stage1 succeeded in `134132 ms`; the pcc1 emit-only
probe produced the expected 2 list calls, 2 dict calls, and 4 error checks.
No pcc2/pcc3 or GC matrix was run or claimed.

## Claim boundary

This proves one lowering owner and cross-entry parity only for exact
list/tuple/dict getitem under host pcc plus a current-source pcc1 emit gate. It
does not prove DynType getitem, setitem, slices, CPython bridging, value arrays,
or a fixed point. Inventory also found that the internal non-raising
`py_list_set` is used by user-visible list subscript stores; that separate
IndexError boundary is retained as
`AUD-P2-PY-LIST-SUBSCRIPT-STORE-INDEXERROR` rather than being folded into this
refactor.
