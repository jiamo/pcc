# Investigation: nested closure comprehension target leak in `compute_free_names`

## Status
resolved (2026-05-11) — No.3 root-cause codegen fix applied; bootstrap unblocked, multi-file closure remains 0 fallbacks.

## Problem Description

Self-host bootstrap can fail with:

`PCC-PY-COMPILE-001: reference to unbound name <target>`

where `<target>` is a comprehension iterator variable (for example `h`) from a nested
`list/dict/set/genexpr` inside a nested function.

The failing pattern is: bound names from generator clauses are not consistently excluded from
the nested function free-name set, so closure conversion synthesizes a wrapper parameter for the
clause target.

## Repro

1. Build a self-host stage-1 compiler:

```bash
env -u LC_ALL uv run pcc --backend self --python-libpython=off --ir-scaffold=on \
  pcc/__main__.py -o /tmp/pcc_stage1_test
```

2. Compile `pcc/__main__.py` with stage-1:

```bash
env -u LC_ALL /tmp/pcc_stage1_test --backend self --python-libpython=off --ir-scaffold=on \
  pcc/__main__.py -o /tmp/pcc_stage2_test
```

Observed symptom in this file: `reference to unbound name h` before bootstrap can finish.

3. Minimal reproducer (added as a regression test):

```python
def outer(xs):
    offset = 1
    def inner():
        return tuple(h + offset for h in xs)
    return inner()
```

## Test [N/A]

- `tests/python/test_pcc_codegen_nested_closure.py`

## Update 2026-05-11: lldb localisation — actual root cause is `_call_ident`

The previous framing ("comprehension clause extraction is wrong in `walk`") is
**incorrect** for the current source. The source-level logic in
`compute_free_names.walk()` (`pcc/py_frontend/codegen/layer1.py:~5917-6020`)
correctly handles `_list_comp` / `__listcomp__` / `_set_comp` / `__setcomp__` /
`_gen_comp` / `__genexpr__` / `_dict_comp` / `__dictcomp__` / `_gen_clause` —
CPython-hosted pcc compiles all three regression probes in
`tests/python/test_pcc_codegen_nested_closure.py` and
`tests/python/test_py_nested_hoist_comprehension_scope.py` without any patch.

The failure is **stage divergence**: pcc-compiled `compute_free_names`
behaves differently from CPython-interpreted `compute_free_names`. Root
cause located via lldb on `./pcc1`:

### Counts (from `lldb -b breakpoint set --auto-continue 1` over a failing
`outer(xs)/def inner(): return tuple(h + 1 for h in xs)` compile):

| symbol | hits |
|---|---|
| `___nested_walk_5` (the `walk` inside `compute_free_names`) | 192 |
| `___nested__is_call_node` | 177 |
| `___nested__call_ident` | 12 |
| `___nested__has_gen_clause` | 9 |
| `___nested__collect_target_names` | **0** |

`_collect_target_names` is the function that adds the comprehension target
to `comp_bound`. It is never called. The comp branch is never entered.

### Disassembly of `_user_pcc_py_frontend_codegen_layer1___nested__call_ident`

```
0x1003a22b4  stp  x29, x30, [sp, #-0x10]!
0x1003a22b8  mov  x29, sp
0x1003a22bc  sub  sp, sp, #0x20
0x1003a22c0  stur x0, [x29, #-0x8]    ; expr (arg) → slot
0x1003a22c4  ldur x9, [x29, #-0x8]    ; reload (dead)
0x1003a22c8  stur x9, [x29, #-0x10]   ; copy (dead)
0x1003a22cc  adrp x9, 0x10060a000     ; load GOT slot for static "None" obj
0x1003a22d0  add  x9, x9, #0x420
0x1003a22d4  ldr  x10, [x9]           ; → fixed pointer
0x1003a22d8  stur x10, [x29, #-0x18]
0x1003a22dc  ldur x0, [x29, #-0x18]   ; return value
0x1003a22e4  ldp  x29, x30, [sp], #0x10
0x1003a22e8  ret
```

There is **no call to `py_obj_getattr`**, and the `expr` argument (x0) is
written to a stack slot and never read again. The function always returns
the same global pointer — the "default" of the `getattr(expr, "ident", None)`
call in source.

The cascade:

1. `_call_ident(x.func)` always returns the default → `fname` is always
   `None` (or some sentinel != any of the comp sentinel strings).
2. The fallback `_has_gen_clause(gen_arg)` inside `_call_ident is None` also
   relies on `_call_ident(node.func) == "_gen_clause"`, which is False for
   the same reason → `_has_gen_clause` returns False even on genuine
   `_gen_clause` calls.
3. The `if fname in ("_list_comp", ..., "__genexpr__")` check at
   `walk()` never fires.
4. `walk()` falls through to the generic dataclass field-walker, which
   visits `_gen_clause(Name("h"), Name("xs"), ())` and treats `Name("h")`
   as a free read in the nested function.
5. The hoister synthesizes a capture for `h`. Codegen of the wrapper
   raises ``reference to unbound name h`` because nothing binds `h` in the
   outer scope.

### What pcc-codegen actually mis-optimises

`def _call_ident(expr): return getattr(expr, "ident", None)`.

`expr` is bound from a nested-closure call to a `DynType` expression
(`x.func` inside `walk`). The pcc type-inferer for this nested-of-nested
closure apparently concludes that `expr` cannot have an attribute `ident`
and the codegen lowers `getattr(expr, "ident", None)` to a constant return
of the default, **skipping the runtime `py_obj_getattr` call entirely**.

This is a soundness bug in the codegen of `getattr(<dyn>, <str>, <const>)`
when invoked through a deeply-nested closure call chain.

## Test [CONFIRMED]

- `tests/python/test_pcc_self_host_getattr_default.py` — three e2e probes
  driving `./pcc1` directly:
  - `test_self_host_compiles_nested_genexpr_without_unbound_target`
    (user-facing failure)
  - `test_self_host_getattr_default_returns_actual_attr_for_dyn_obj`
    (minimal codegen probe; pcc1 prints `<null>` instead of `ok`)
  - `test_self_host_nested_def_with_getattr_default`
    (minimal nested-def codegen probe; pcc1 prints `None` instead of
    `xyz`)
- Pre-existing regression tests that pass under CPython-hosted pcc but
  do not catch the self-host bug:
  - `tests/python/test_pcc_codegen_nested_closure.py`
  - `tests/python/test_py_nested_hoist_comprehension_scope.py`

## Proposals

- No.1 ~~Normalize generator clause extraction in `compute_free_names`~~ —
  superseded; the source-level logic is correct.
- No.2 **Workaround in `_call_ident`**: replace
  `return getattr(expr, "ident", None)` with explicit `isinstance` /
  attribute access. Unblocks self-host without touching codegen.
- No.3 **Root-cause fix in pcc codegen**: identify which pass mis-optimises
  `getattr(<dyn>, <const-str>, <const-default>)` inside a nested closure
  to "return default", and disable that path until the type analysis is
  sound.

## No.2 Source workaround in `_call_ident`

### Code Change (proposed)

In `pcc/py_frontend/codegen/layer1.py`:

```python
def _call_ident(expr):
    if isinstance(expr, _Name):
        return expr.ident
    return None
```

(or simply `return expr.ident if isinstance(expr, _Name) else None`).

This avoids the buggy `getattr(<dyn>, <str>, <None>)` lowering by using
`isinstance` followed by direct attribute access — both of which pcc
handles correctly today.

### PENDING

Not yet applied / verified. See `test_pcc_self_host_getattr_default.py`
for the regression that must pass.

## No.3 Root-cause codegen fix

### CONFIRMED 2026-05-11

The unsound branch was located in
`pcc/py_frontend/codegen/layer1.py::_maybe_emit_native_module_getattr`
(line ~11497). Old behavior: for any 3-arg `getattr(obj, "name", default)`
where `obj` was **not** a recognized native-module reference,
`_native_module_object_export_info(obj, name)` returned `None`, and the
code path immediately returned `_emit_as_object(args[2])` — i.e.,
the default — **without ever lowering `py_obj_getattr` at runtime**.

This conflated two distinct cases:

1. ``obj is a recognized native module that lacks this attribute`` —
   legitimately can return the default (the module has no such name).
2. ``obj is an arbitrary dyn object`` — must lower to `py_obj_getattr`
   and only fall back to the default when the call returns `NULL`.

`_call_ident(expr) → getattr(expr, "ident", None)` fell into case (2)
and was silently collapsed to "return None" for every call, which is the
constant-folding LLDB observed. The fix splits the two cases and adds an
extra guard for cpython-flavored receivers so the closed-world IR
baseline does not regress:

```python
module_name = self._native_module_name_for_object_expr(expr.args[0])
if module_name is None:
    if self._expr_looks_cpython(expr.args[0]):
        if len(expr.args) == 3:
            return self._emit_as_object(expr.args[2])
        return None
    return None  # fall through to _emit_getattr_builtin -> py_obj_getattr
native_table = self._native_module_exports or {}
info = native_table.get(module_name, {}).get(expr.args[1].value)
if info is None:
    if len(expr.args) == 3:
        return self._emit_as_object(expr.args[2])
    return None
return self._emit_native_module_export_value(...)
```

### Cascade discoveries

Verifying the fix surfaced two separate pre-existing closed-world bugs:

- `_to_double(v, FloatType)` returned `v` unchanged even when `v` was a
  PointerType (`PyObject*`). `float(x)` lowered via `py_cpy_call1` would
  flow into `scaffold.Constant_f64` expecting a native `double` and the
  IR pass rejected the type. **Fix**: PointerType branch added that
  routes through `marshal.marshal_from_object(v, FloatType)` (closed
  world, uses `py_float_to_f64`) or `py_cpy_to_f64` if `v` is already a
  CPython value.
- The `float(<dyn>)` builtin lowering had no DynType branch and fell
  through to `py_cpy_import('builtins') + py_cpy_getattr + py_cpy_call1`.
  **Fix**: add `if isinstance(ty, DynType): ... return self._to_double`
  branch which now goes through `py_float_to_f64`.

These two cascade fixes are recorded separately in
`docs/investigations/pcc-py-codegen-float-dyn-closed-world.md`.

### Verification

- 8/8 regression tests pass:
  - `tests/python/test_pcc_self_host_getattr_default.py` (3 e2e)
  - `tests/python/test_pcc_codegen_nested_closure.py` (2)
  - `tests/python/test_py_nested_hoist_comprehension_scope.py` (3)
- Multi-file closure probe (`scripts/probe_stage1_closure.py`,
  `--ir-scaffold=on`): `multi_ok=True, py_cpy_*=0` after all three fixes.
- `./pcc1 → pcc2 → pcc3` bootstrap proceeds past the previous
  `unbound name h` failure.
- `otool -L pcc1` shows zero libpython dependency.
