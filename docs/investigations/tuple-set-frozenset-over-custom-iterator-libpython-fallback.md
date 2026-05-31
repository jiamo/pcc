# Investigation: tuple()/set()/frozenset() over a custom __iter__/__next__ iterator falls back to libpython

## Status
resolved (#42 — tuple/set/frozenset landed + bootstrap-passed; max/min/sorted-over-custom-iter remain, see below)

## Problem Description
`tuple(CustomIterator())`, `set(CustomIterator())`, `frozenset(CustomIterator())`
where the arg is a user class instance with `__iter__`/`__next__` (a ClassType,
not List/Tuple/Dyn) force the libpython fallback under
`--python-libpython=off`:

```
PCC-PY-COMPILE-001: Python pipeline requires libpython fallback ...
(generated IR still calls py_cpy_* helpers)
```

i.e. a hard error in strict no-libpython mode. (`list()` and `sum()` over the
same iterator were fixed in #41; `sorted()`, `max()`, `min()`, and generator
`any()`/`all()` over a custom iterator already compile.)

## Repro
```bash
cat > /tmp/t.py <<'PY'
class C:
    def __init__(self, n): self.n = n
    def __iter__(self): self.i = 0; return self
    def __next__(self):
        if self.i >= self.n: raise StopIteration
        v = self.i; self.i += 1; return v
def main():
    print(tuple(C(3)))   # (0, 1, 2)
main()
PY
env -u LC_ALL uv run pcc --backend self --python-libpython=off --ir-scaffold=on /tmp/t.py -o /tmp/t_bin
# -> PCC-PY-COMPILE-001 libpython fallback required
```

## Root cause (CONFIRMED by bisect)
- `_maybe_emit_tuple_builtin` (tuple_zip_lowering.py): handles only literal
  list/tuple, DictType, and `(ListType, TupleType, DynType)`; a ClassType arg
  returns None → caller surfaces the unknown-builtin path → cpy fallback.
- `_maybe_emit_set_builtin` (set_lowering.py): same — builds via py_obj_len +
  py_obj_getitem for generic iterables; a ClassType iterator (no __len__) isn't
  handled via the iterator protocol.
- frozenset: same family.
This is the exact ClassType-bail pattern fixed for list()/sum() in #41
(sequence-builtins-len-getitem-not-iterator-protocol.md family).

## Proposals
- No.1 Route ClassType args through the iterator protocol   [CONFIRMED #42 for tuple/set/frozenset]

## No.1 Route ClassType args through the iterator protocol
### Fix pattern (clear; mirrors #41)
For a ClassType arg, build a list via the existing
`self._emit_list_append_via_iter(tmp_list, src_obj, span)`
(list_builtin_lowering.py:269 — py_obj_iter/py_obj_next loop, StopIteration tag
8 cleared), then convert:
- `tuple()`: `n = py_list_len(lst); tup = py_tuple_new(n);` copy loop
  `tup[i] = py_list_get(lst, i)`.
- `set()` / `frozenset()`: `s = py_set_new();` iterate the list and
  `py_set_add(s, py_list_get(lst, i))`. (Confirm frozenset's runtime
  representation — likely the same PySetObject; check py_set_new / a frozenset
  constructor.)
Frontend-only (no runtime/refcount change), ~25 lines per builtin. Add a
regression mirroring test_native_sum_list_custom_iterator.py and run the
FOREGROUND bootstrap (frontend-only ~7min).

### pending
Deferred from the 2026-05-30 session (already landed #41 for the more common
list()/sum() case). Niche relative to `tuple(gen_expr)` (DynType, already works);
this is specifically `tuple/set/frozenset(<user-class instance with
__iter__/__next__>)`. Clear, bounded next task.

## Report (#42)
tuple()/set()/frozenset() over a ClassType custom iterator now build a list via
`_emit_list_append_via_iter` then materialise (tuple: py_tuple_new + copy; set:
py_set_new + py_set_add). tuple_zip_lowering.py + set_lowering.py (frozenset
shares set's lowering). test_native_tuple_set_custom_iterator.py 1 passed; full
bootstrap PASSED (18 passed/4 skipped, 483s).

## Resolved follow-ons (#43 min/max, #44 sorted) — from /tmp/gap_probe/iter_builtins.py
- `max(C())` / `min(C())` over a custom iterator -> RESOLVED #43:
  _maybe_emit_min_max_iter materialises a ClassType arg to a list via
  _emit_list_append_via_iter then runs the index fold. (was runtime NameError).
- `sorted(C())` over a custom iterator / generator -> RESOLVED #44: the
  pcc-Python port `py_obj_sorted` general-iterable branch used an index-based
  py_obj_len + py_obj_getitem loop (the C version already iterated); rewrote the
  port branch to the iterator protocol (py_obj_iter/py_obj_next, clearing a
  terminal StopIteration) + cleared the spurious py_obj_len sizing error. Now
  sorted(<custom iterator>) and sorted(<generator>) work in default mode.
  test_native_sorted_custom_iterator.py. ALL iter-builtins now IDENTICAL.
