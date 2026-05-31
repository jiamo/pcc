# Investigation: class instantiation lowering rejects `*args` / `**kwargs` in `__init__`

## Status
resolved

## Problem Description

`tests/py_corpus/phase4/pathlib_parts` fails:

```
error: PCC-PY-COMPILE-001: [python-frontend] codegen[source]:
NotImplementedError: instantiation: PurePath.__init__ missing argument
'extra' and has no default
```

`pcc.py_stdlib.pathlib::PurePath.__init__`:

```python
class PurePath:
    def __init__(self, path: str = "", *extra) -> None:
        raw = str(path)
        for part in extra:
            raw = _op.join(raw, str(part))
        self._raw = raw
```

The caller is `pathlib.PurePath("/tmp/foo/bar.txt")` — one positional, no
extras. pcc's class instantiation lowering iterates over the declared
`__init__` params, sees `extra` (kind `"*args"`, `has_default=False`), and
treats it like a missing required positional.

A second site in `pcc/py_frontend/codegen/class_gen.py` has the same
NotImplementedError; the corpus case actually trips the
`pcc/py_frontend/codegen/native_modules.py` site, but both are wrong for
varargs.

## Repro

```bash
env -u LC_ALL uv run pytest \
  'tests/python/test_py_corpus.py::test_py_corpus_cases[phase4/pathlib_parts]' -q -n0
```

## Test [CONFIRMED]

Same pytest case; pre-fix fails (NotImplementedError, then after a partial
fix an IR error `not enough parameters specified for call`), post-fix
passes.

## Proposals

- No.1 Filter varargs out of "missing required arg" check (insufficient)  [DENIED]
- No.2 Pass empty tuple / dict at the call site for unfilled `*args` / `**kwargs`  [CONFIRMED]

## No.1 Filter varargs out of "missing required arg" check
### Code Change
In the default-fill loop, `continue` past `arg.kind in ("*args", "**kwargs")`
so no `NotImplementedError` is raised.

### DENIED
The IR signature of `__init__` is still 3 params (`self, path, extra`). If
the call-site skips the vararg slot, IR fails with:

```
error: not enough parameters specified for call
  %.3 = call ptr (ptr, ptr, ptr) @user_pathlib_PurePath___init__(
      ptr %inst.PurePath.2.2, ptr @.pystr.obj.1)
```

So we must still pass *something* for the vararg slot.

## No.2 Pass empty tuple / dict at the call site for unfilled varargs
### Code Change

`pcc/py_frontend/codegen/native_modules.py` (the live instantiation path):

```python
for j in range(len(args), len(declared)):
    arg = declared[j]
    arg_kind = getattr(arg, "kind", "pos")
    if arg_kind == "*args":
        empty_tuple = self.builder.call(
            self.runtime["py_tuple_new"],
            [ir.Constant(ir.IntType(64), 0)],
            name=self._fresh(f"{class_name}.init.extras"),
        )
        init_args.append(empty_tuple)
        continue
    if arg_kind == "**kwargs":
        empty_dict = self.builder.call(
            self.runtime["py_dict_new"],
            [],
            name=self._fresh(f"{class_name}.init.kwextras"),
        )
        init_args.append(empty_dict)
        continue
    if not getattr(arg, "has_default", False):
        raise NotImplementedError(...)
```

`pcc/py_frontend/codegen/class_gen.py` has the same default-fill loop;
filtering varargs there too is safe (the class_gen path is not the
active instantiation site for `PurePath` but the symmetric fix prevents
future regressions if the dispatch flips).

### CONFIRMED
- `phase4/pathlib_parts` corpus case now passes.
- All 177 corpus tests pass.
- Bootstrap / fallback baselines unchanged.

### Why this is correct
The callee's body in `PurePath.__init__` reads `extra` as `for part in extra:`,
which expects an iterable. Passing an empty pcc tuple makes the loop body
execute zero iterations — matching CPython semantics for
`PurePath("/x")` (no extras → empty tuple). The vararg slot in the IR
signature is satisfied with a real `PyObject*` value, so the call
typechecks.

This does not fix the case where the caller passes extra positional args
(those would still be paired positionally with the vararg slot, which is
wrong). Supporting genuine vararg packing on the caller side is a
separate, larger change; the current fix is the narrowest one that
unblocks `PurePath("path")` and similar `__init__(self, x, *args)`
patterns where the call uses no extras.

## Report
Landed via the `native_modules.py` site (active path) and the parallel
`class_gen.py` site (dormant path made consistent). One narrow regression
test exists in `tests/py_corpus/phase4/pathlib_parts/`.
