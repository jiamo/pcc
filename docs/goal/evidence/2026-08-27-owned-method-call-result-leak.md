# Owned reference returned from a container METHOD CALL leaks one ref

Date: 2026-08-27
Task: `PY-P1-OWNED-METHOD-CALL-RESULT-LEAK` (new)
Found while: repurposing `tests/python/test_native_refcount_managed_variants.py`
into a container-retain parity gate after the `py_incref_managed` ABI was
denied. The finalizer round-trip in that test surfaced this pre-existing leak.
Claim level: focused, minimally reproduced, localized to the frontend/consumer
side of an owned method-call result. **No fix is made here.** pcc-native,
`--backend llvm --python-libpython=off --ir-scaffold=on`, `PCC_GC_BACKEND=0`.

## Symptom

An object whose only remaining reference is returned by a container METHOD
CALL (`list.pop()`, `dict.get()`) is never freed even after every visible
binding is dropped: its `__del__` does not fire. Subscript get, append, copy,
repeat, clear, delitem, setitem are all balanced.

## Minimal repros (each: exactly one `__del__` expected)

```text
append + clear + c=None                       -> 1   balanced
append + del box[0] + c=None                  -> 1   balanced
append + box[0] get + drop + clear + c=None   -> 1   balanced (subscript get OK)
append + copy + drop + clear + c=None         -> 1   balanced
dict setitem + clear + c=None                 -> 1   balanced
dict setitem + del d["k"] + c=None            -> 1   balanced

append + pop + c=None                         -> 0   LEAK (pop, result unused)
append + x=pop + c=None + x=None              -> 0   LEAK (pop, result used)
dict setitem + got=d.get(k) + drop + del + c=None -> 0  LEAK (dict.get)
```

The bare last-decref control (`c = Canary(); c = None`) fires correctly (1), so
finalization itself works; only the method-call-result path leaks.

## Localization

`py_list_pop` (pcc/py_runtime/py/py_list.py) is correct: it removes the slot
and returns the element by TRANSFER (no incref, list length decremented), so the
returned reference is already owned. The extra reference is therefore added on
the frontend/consumer side of the call result, not in the runtime. `list.pop`
and `dict.get` are METHOD CALLS; the subscript get `box[0]` goes through a
different lowering and is balanced — so the gap is specific to how an owned
method-call result temp is (not) released.

This is the `PY-P0-CONSUMER-BOUNDARY-OWNERSHIP-LEDGER` /
`PY-P0-EXACT-CONTAINER-DYN-SUBSCRIPT-OWNERSHIP` family, surfaced from the
method-call side.

## Not proven

Which lowering site owns the temp; whether every owned method-call result
leaks or only container methods; behaviour under GC1/GC2. The fix and the
five-GC evidence belong to the fixing row.
