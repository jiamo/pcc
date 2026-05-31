# Investigation: extern c_ptr arg for a Name leaks py_cpy_wrap_pcc_<N>arg into the no-libpython runtime archive

## Status
resolved

## Problem Description

`tests/py_corpus/phase4/re_match` fails at link time in the default pcc
mode (`--python-libpython=off`):

```
ld: Undefined symbols:
  _py_cpy_wrap_pcc_2arg, referenced from:
      _py_re_compile_method in libpy_runtime_pcc_py.a[30](py_re.o)
clang: error: linker command failed with exit code 1
```

`libpy_runtime_pcc_py.a` is the no-libpython baseline archive (the pcc-Python
runtime ported to native lowering). It must not reference any libpython
helper. `py_re.o` was compiled by `pcc --python-library` and ended up with a
`call ptr @py_cpy_wrap_pcc_2arg(...)` from `py_re_compile_method`.

Source site (pcc/py_runtime/py/py_re.py):

```python
def py_re_bound_method_call(captures, args):
    ...

@c_abi_export("py_re_compile_method")
def py_re_compile_method(pattern, flags: int, method_kind: int):
    ...
    fn = py_func_new_named(py_re_bound_method_call, captures, name)
    py_decref(captures)
    return fn
```

`py_func_new_named` is declared:

```python
py_func_new_named = extern("py_func_new_named", (c_ptr, c_ptr, c_ptr), c_ptr)
```

So the first argument's declared type is `c_ptr` (raw `void*` function
pointer), and `py_re_bound_method_call` is a bare `Name` referring to a user
function.

## Repro

```bash
env -u LC_ALL uv run pytest \
  'tests/python/test_py_corpus.py::test_py_corpus_cases[phase4/re_match]' -q -n0
```

Pre-fix: AssertionError with the linker `Undefined symbols` above.

## Test [CONFIRMED]

Same pytest case; pre-fix fails, post-fix passes.

## Proposals

- No.1 Special-case extern call arg `Name → c_ptr` to emit the raw fn-ptr  [CONFIRMED]

## No.1 Special-case extern call arg `Name → c_ptr` to emit the raw fn-ptr
### Code Change
`pcc/py_frontend/codegen/extern_lowering.py::_emit_extern_call` — when the
positional arg is a `Name` whose ident is a known user function AND the
target ctype is `c_ptr`, bitcast the function global to `i8*` directly
instead of routing through `_emit_expr` (which would hit
`name_lowering.py`'s value-position FuncDef branch and emit
`py_cpy_wrap_pcc_<N>arg`).

### CONFIRMED
- `py_re.o` no longer references `py_cpy_wrap_pcc_2arg` (`nm` returns no
  matches).
- `phase4/re_match` corpus case passes.
- Bootstrap / fallback baselines unchanged (`tests/python/test_fallback_baseline.py`,
  `test_ir_py_fallback_baseline.py`, `test_bootstrap_gate_baseline.py` all green).
- 244 focused Python-frontend tests pass.

### Why this is correct
`py_cpy_wrap_pcc_2arg` returns a CPython `PyCFunction` object (`void*`),
which `py_func_new_named` would then store at offset 16 of its `PyFunc`.
At call time, `py_func_call` reads that offset and invokes it as
`call_ptr2(entry, captures, args)`. A CPython `PyCFunction` object is
not a raw pcc function pointer with that calling convention, so the
wrap was already semantically wrong even when libpython was linked —
the corpus test merely surfaced it as a link error in no-libpython mode.

The fix emits the raw pcc function pointer for `py_re_bound_method_call`,
which is exactly what `py_func_new_named`/`py_func_call` need.

## Report
Landed via a narrow `_emit_extern_call` branch. No fallback baseline
changes were required because no `py_cpy_*` count was tracked for the
runtime-library compile path.
