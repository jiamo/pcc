# List subscript STORES raise catchable IndexError (public/internal split)

## Claim

User-visible list subscript assignment now raises CPython-compatible catchable
`IndexError("list assignment index out of range")` on out-of-range indices
under strict no-libpython, across ordinary assignment, unpack-target
assignment, augmented assignment, and genuinely dynamic receivers — in BOTH
runtime tiers (default/pcc-Python port and `PCC_RUNTIME_CC=cc`). Internal
non-raising setters are preserved: `py_list_set` keeps its no-raise contract
for sort/insert shifts and generator frames.

## Changes

- Runtime public raising store `py_list_setitem` (0/-1 + IndexError), C
  `py_list.c` + port `py_list.py` mirrored; declared in `py_runtime.h`,
  registered in `runtime_abi.py`.
- `py_obj_setitem` / `py_obj_setitem_i64` list branches (C + port dispatch)
  route through the raising store and propagate rc.
- Frontend: exact-ListType store branch in `subscript_lowering` calls
  `py_list_setitem` + err-check; the dyn `py_obj_setitem{,_i64}` store calls
  gained the missing err-checks; `_store_value_at_subscript`
  (assignment_store_lowering — the unpack/walrus store path) gained its
  missing err-check (a raised store previously left a PENDING exception that
  skipped the enclosing try/except and corrupted later dispatch);
  `_emit_augassign`'s subscript branch got: err-check after the load (CPython
  order: the load raises before the RHS evaluates), err-check after the
  store-back, and exact-list receivers now use the raising typed accessors
  (`py_list_getitem`/`py_list_setitem`, one i64 index evaluation) while the
  generic object-key middle stays shared.

## Verification [CONFIRMED]

- New gate `tests/python/test_native_list_setitem_index_error.py` (6 passed):
  parameterized over BOTH tiers; expected outputs verified against CPython
  first. Covers in-bounds pos/neg stores, five OOB indices raising with the
  exact CPython message, list unchanged after failed stores, unpack-target
  OOB, augmented in-bounds (`a[1] += 5` -> 25) and OOB (`a[9] += 1` ->
  IndexError), dyn-receiver store OOB + dict store via the same dyn helper,
  and sort/insert/reverse (internal setters) unbroken.
- Row gates: `test_native_subscript_raise.py` + `test_native_list_index_error.py`
  -> 6 passed; slice suites (`test_py_slice_augassign.py`,
  `test_py_native_slice_mutation.py`, subscript raise) -> 8 passed.

## Boundary

- Touches bootstrap-critical frontend/runtime paths; the full self-host
  bootstrap gates run once in the end-of-goal full-project validation (with
  the other same-day runtime edits) before any DONE_STRONG promotion.
- Pre-existing and out of scope here: dict augmented assignment on a missing
  key (`d[k] += 1`) still routes through non-raising `py_dict_get` inside
  `py_obj_getitem`, so it does not yet raise CPython's KeyError (behavior
  unchanged by this slice; the added err-check is inert when nothing raises).
