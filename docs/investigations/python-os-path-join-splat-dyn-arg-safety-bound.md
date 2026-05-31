# Investigation: `os.path.join(a, *parts)` native lowering smuggled CPython parts into `py_list_extend`

## Status
resolved

## Problem Description

`tests/python/test_native_os_path_join_splat.py::test_join_splat_dyn_arg_falls_back`
asserts that an *untyped* splat in `os.path.join` must fall back to the
CPython bridge:

```python
def f(a, parts):                   # ``parts`` has no annotation → DynType
    return os.path.join(a, *parts)
```

After recent generic `os.path.join(*expr)` native lowering, the IR for
`f` started emitting:

```
%os.path.join.args.1.5 = call ptr @py_list_new(i64 0)
call void @py_list_append(ptr %os.path.join.args.1.5, ptr %a)
call void @py_list_extend(ptr %os.path.join.args.1.5, ptr %parts)
%os.path.join.4.8 = call ptr @py_os_path_join(ptr %os.path.join.args.1.5)
```

— i.e., `*parts` (DynType) was forced through `py_list_extend`.
`py_list_extend` (`pcc/py_runtime/src/py_list.c::py_list_extend`) only
has fast paths for `PY_TYPE_LIST` and `PY_TYPE_TUPLE`; everything else
falls through to `py_obj_iter`, which raises `TypeError: object is not
iterable` for any CPython value whose `__iter__` is not visible to
`py_user_iter_dispatch`. So a user passing a CPython list/tuple to
`os.path.join(*cpy_parts)` would crash at runtime instead of going
through the libpython bridge path.

The native lowering's safety predicate
`_native_os_path_arg_can_stay_native` already filtered out CPython
*modules* but treated every other `Name` (including DynType locals) as
safe to keep native.

## Repro

```bash
env -u LC_ALL uv run pytest \
  tests/python/test_native_os_path_join_splat.py::test_join_splat_dyn_arg_falls_back \
  -q -n0
```

Pre-fix: `assert "@py_os_path_join" not in body` fails — native path
fired for an untyped splat target.

## Test [CONFIRMED]

Same pytest case. Pre-fix fails, post-fix passes; the typed
list / tuple cases (`test_join_splat_list`, `test_join_splat_tuple_with_trail`)
keep using the native path.

## Proposals

- No.1 Require typed `list` / `tuple` for the starred inner expression  [CONFIRMED]

## No.1 Type-gate starred splat in native `os.path.join` lowering
### Code Change

`pcc/py_frontend/codegen/native_os.py`, in the `os.path.join` arm of
the native dispatcher — when the arg is a starred unpack, the inner
expression's type must be `ListType` or `TupleType`. DynType (and
other non-iterable-typed) starred targets decline the native path and
fall back to the CPython bridge:

```python
for arg in expr.args:
    if self._is_starred_unpack_expr(arg):
        inner = arg.args[0]
        if not self._native_os_path_arg_can_stay_native(inner):
            return None
        if not isinstance(inner.ty, (ListType, TupleType)):
            return None
    else:
        ...
```

### CONFIRMED
- `tests/python/test_native_os_path_join_splat.py` — 5 passed (was 1
  failing; the others stay green).
- `tests/python/test_native_os_misc.py` — 55 passed (unchanged).
- Wider `tests/python/test_native_*.py` — 222 / 223 passed (the one
  remaining failure is a CPython `_decimal_exec` SIGSEGV in the
  libpython embedded path, a separate Python 3.14 + macOS issue and
  not related to this slice).
- Fallback baselines + corpus: 194 passed, 4 skipped (unchanged).

### Why this is the correct fix
The native lowering's value proposition is "if we know the shape, we
can pack the args without going through CPython." Splat targets are
the only place where the IR-side type bound determines runtime
correctness: `py_list_extend` is a pcc-native helper, not a generic
Python iteration machine. The fix restores the previously-implicit
safety contract: native dispatch may keep the call out of CPython only
when the splat target's static type is one that `py_list_extend`
handles by inlined offsets.

## Report
Landed. No fallback-baseline recapture needed — the off-mode count for
this specific IR shape changes back to its pre-`os.path.join(*expr)`
lowering value (i.e., one bridge call instead of the rejected native
emission), which is already covered by the ratchet's slack and the
typed `list[str]` / `tuple[str, ...]` cases still use the native path.
