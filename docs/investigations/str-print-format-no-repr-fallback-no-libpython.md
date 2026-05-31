# Investigation: str()/print()/format() of an instance does not fall back to __repr__ (no-libpython)

## Status
resolved

## Problem Description
Under strict no-libpython, `print(obj)` / `str(obj)` / `f"{obj}"` / `"x" + str(obj)`
on a user instance whose class defines **only `__repr__`** (no `__str__`) did not
use `__repr__`. `print(obj)` showed `<object tag=104>`; `str(obj)`/`f"{obj}"`
showed `<null>`. CPython's `object.__str__` delegates to `__repr__`, so defining
only `__repr__` (the standard debug-class idiom) makes `str()`/`print()` use it.

Found 2026-05-30 by real17 (a `Point` class with `__repr__`).

## Repro
```bash
cat > /tmp/rf.py <<'PY'
class A:
    def __repr__(self): return "A-repr"
def main():
    a = A()
    print(a); print(str(a)); print(f"{a}"); print("v=" + str(a))
main()
PY
env -u LC_ALL uv run pcc --backend self --python-libpython=off --ir-scaffold=on /tmp/rf.py -o /tmp/rf_bin
/tmp/rf_bin         # before: <object tag=104> / <null> / <null> / <null>
python3 /tmp/rf.py  # A-repr / A-repr / A-repr / v=A-repr
```

## Test [CONFIRMED]
`tests/python/test_native_str_falls_back_to_repr.py` — only-`__repr__` class via
print/str/repr/f-string/`+`/`.format()`, plus a both-`__str__`-and-`__repr__`
class (str→__str__, repr→__repr__ kept distinct). 1 passed; observed the
`<object tag=104>` / `<null>` failures before the fix.

## Proposals
- No.1 py_obj_str falls back to py_obj_repr when __str__ absent   [CONFIRMED]
- No.2 container element repr [CONFIRMED #39] + default repr for no-__repr__ [pending — rare]

## No.1 py_obj_str → py_obj_repr fallback
### Code Change
`pcc/py_runtime/src/py_obj_stubs.c` `py_obj_str`: after `py_user_str_dispatch`
returns NULL, if `!py_err_occurred()` (no `__str__`, vs a `__str__` that raised)
`return py_obj_repr(o)`. Mirrored in the pcc-Python port
`pcc/py_runtime/py/py_obj_stubs.py` (added the `py_err_occurred` extern). DEFAULT
runtime mode links the port; `touch pcc/py_runtime/Makefile` to rebuild the
archive. No recursion risk: `py_obj_repr` only calls `py_obj_str` for
int/bool/None (handled before the instance dunder path).
### CONFIRMED
test_native_str_falls_back_to_repr.py 1 passed. repr_fb probe: cases print(a),
str(a), repr(a), f"{a}", "v="+str(a), "{}".format(a) all → the repr; both-defined
class keeps str→__str__ / repr→__repr__. Full bootstrap: gate b9o8wszdh.

## No.2 container element repr + default repr (follow-on, pending)
Two residual gaps from the same probe, NOT covered by No.1:
- **Container element repr** (`print([a, c])`): EXACT locus confirmed —
  `py_print_fmt.c:190` `py_format_repr()` special-cases only str/bytes, then
  falls through to `py_format(fp, o)` (line 208), whose instance `default:` case
  (line 283) calls `py_obj_str` (→ `__str__`). So a class with BOTH `__str__`
  and `__repr__` renders its `__str__` inside a list (`[..., C-str]`) where
  CPython uses `__repr__` (`[..., C-repr]`). `py_format_list/tuple/dict/set`
  already recurse via `py_format_repr` (lines 140/150/164/184), so the fix is
  local: in `py_format_repr`, before the `py_format` fallthrough, handle
  `tag == PY_TYPE_INSTANCE || tag >= PY_TYPE_USER` by calling `py_obj_repr(o)`
  (then `py_format_str`); on NULL fall through. Mirror in port `py_print_fmt.py`.
- **Default repr for no-`__repr__`** (`repr(b)`, only `__str__`): CPython yields
  `<module.ClassName object at 0xADDR>`; pcc returns NULL → `<null>`. `py_obj_repr`
  final NULL branch (`py_obj_stubs.c:326`) should synthesize `<ClassName object
  at 0x...>` — `py_obj_type_name(o)` (py_obj_ops_dispatch.c:280) gives the class
  name; the address is `(uintptr_t)o`. Non-deterministic address → test the shape
  only; lower priority. Both follow-ons are RUNTIME changes (one bootstrap).

## Report
Landed No.1 (the common, deterministic, high-value case: every debug class that
defines only `__repr__`). No.2 (container element repr + default repr) is filed as
a follow-on with exact loci. Predecessor: the #25 fix routed the print `_format`
default through `py_obj_str`; this fix completes the chain by making `py_obj_str`
itself honor `object.__str__`→`__repr__` delegation.
