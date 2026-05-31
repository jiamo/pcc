# Investigation: `@property` return type does not propagate in pcc's type inference

## Status
open — multi-file closed-world compile of `pcc/py_stdlib/pathlib.py`
falls back to libpython because property-returning-str chains land as
`DynType` and downstream str methods (`rfind`, slice, ...) lower
through `py_cpy_getattr`.

## Problem description

`pcc/py_frontend/type_infer.py` has **zero references** to `@property`,
`is_property`, or the property decorator:

```bash
$ grep -nE "@property|is_property|property.*return" \
    pcc/py_frontend/type_infer.py
# (no output)
```

Consequence: when a class declares

```python
class C:
    @property
    def name(self) -> str:
        return self._x
```

`type_infer` records `name` as an ordinary method whose **call** would
return `str`, but every **attribute** access `c.name` types as
`DynType`. Codegen's already-implemented property fast path
(`pcc/py_frontend/codegen/layer1.py::_emit_attr` line ~19238) handles
the dispatch, but downstream str-method calls on the value have already
been routed through `py_cpy_*` by type-driven choices upstream.

Concretely, in `pcc/py_stdlib/pathlib.py::PurePath.suffix`:

```python
@property
def suffix(self) -> str:
    n = self.name       # n typed as DynType, not str
    i = n.rfind(".")    # n.rfind dispatches via py_cpy_getattr
    if i <= 0:
        return ""
    return n[i:]
```

The `n.rfind(".")` call is the canary: `py_str_rfind` exists in the
runtime (`pcc/py_runtime/src/py_str_accessors.c:271`,
`runtime_abi.py:192`) and the codegen lowering exists
(`layer1.py:27284`), but it only fires when `attr.obj.ty` is `StrType`.
With `n` typed as `DynType` the dispatcher prefers the cpython path
and emits

```
%cpy.from_pccstr.20.8 = call ptr @py_cpy_from_pccstr(...)
%cpy.fn.rfind.21.10  = call ptr @py_cpy_getattr(...)
%cpy.call1.rfind.22.11 = call ptr @py_cpy_call1(...)
```

which trips the no-libpython gate.

## Repro

`tests/python/test_py_cross_module_class_inference.py` covers both the
minimal single-file shape and the stdlib bridge:

- `test_gap2_property_return_type_propagates_single_file` —
  baseline: property returns are typed as `str`. **PASSES** today, so
  pcc does know `c.name` is `str` at the read site; the gap shows up
  only when the property result feeds into a method call.
- `test_gap2_str_method_on_property_result_single_file` — minimal
  failing case: `n = self.name; n.rfind(".")`. **FAILS**: multi-file
  compile errors with
  `Python pipeline requires libpython fallback for multi-file compile (modules: entry)`.
- `test_gap1_gap2_pathlib_purepath_name_only` — same shape via the
  real `pcc/py_stdlib/pathlib.py` skeleton. **FAILS** for the same
  reason: pathlib's body uses `n.rfind`.

Run:

```bash
env -u LC_ALL uv run pytest \
  tests/python/test_py_cross_module_class_inference.py -v -n0
```

Expected today: 3 pass, 2 fail (the two above).

## Test [CONFIRMED]

- `tests/python/test_py_cross_module_class_inference.py` (2 failing
  tests pinned to this investigation; the remaining 3 cover Gap 1 and
  the simpler property-return shape that already works).

## Root cause surface

`type_infer.py` builds `ClassType` instances at class definition time
but does not record per-method "is this a property" metadata.
Downstream:

- `Attr(obj, name)` resolution checks `ClassType.fields[name]` and
  `ClassType.methods[name]` but has no `ClassType.properties[name]`
  view.
- When `name` IS a property, type-infer falls through to the generic
  `DynType` attribute-access path, even though codegen would happily
  call the getter.

## Fix plan (No.1 — minimal property awareness)

### Code change

1. **Scan decorators when registering class methods**
   (`pcc/py_frontend/type_infer.py::_infer_classdef` or its analog).
   For each `FuncDef` in the class body, check
   `funcdef.decorators` for `Name("property")` (and the typing-style
   `Attr("builtins", "property")`). When matched:
   - Type-infer the funcdef's body to get its declared return type.
   - Register the function under
     `ClassType.properties[name] = return_ty` (new field on the
     `ClassType` dataclass).
   - Do **not** register it as a normal callable method — that would
     allow `c.name()` to type-check, which is wrong.

2. **Augment Attr resolution.** Where `_infer_expr` handles
   `Attr(obj_expr, attr_name)` and `obj_expr.ty` is `ClassType`,
   try `properties[attr_name]` before falling through to
   `DynType`.

3. **Verify codegen fast path still fires.** The property dispatch at
   `layer1.py:19238` looks up `env_class_hint[obj.ident]` and calls
   `_resolve_property_mro(hint, attr_name)`. With type-infer now
   tagging the attr's `ty` and (more importantly) updating
   `env_class_hint` for receivers known to be class instances, the
   getter call (`user_<Mod>_<Class>_<prop>(obj)`) emits correctly. No
   codegen change should be required.

### Out of scope for this proposal

- **Setter / deleter** support (`@name.setter`). pcc's targeted self-
  host surface uses read-only properties; add later if needed.
- **Property descriptor introspection** — `vars(C)["name"].fget`. Not
  used by closed-world targets.
- **Cross-module class table** — already covered by
  `codegen-mixin-self-cross-module-types.md`. The minimal Gap 1 cases
  (`from lib import C; C(...)` and `import lib; lib.C(...)`) pass
  today; the mixin-flavour cross-module problem stays the broader
  follow-up.

### PENDING

Code change not yet applied. The two regression tests above lock the
specification: they must pass without any `py_cpy_*` dispatch call
appearing in the multi-file LLVM IR.

## Cross-references

- `docs/investigations/codegen-mixin-self-cross-module-types.md` —
  sibling cross-module class-type problem (different surface, same
  family: type-infer per-module table is incomplete).
- `pcc/py_frontend/codegen/layer1.py:19238` — codegen property fast
  path (already implemented, currently underused).
- `pcc/py_runtime/src/py_str_accessors.c:271` /
  `runtime_abi.py:192` — `py_str_rfind` already wired; the only thing
  blocking `n.rfind(...)` from using it is the `DynType` tag on `n`.

## Why this is not (only) "pathlib_parts is Mode P"

`tests/py_corpus/phase4/pathlib_parts` was the surface that pushed
this open, but the root issue is general:

- Any pcc-internal class with `@property def x(self) -> str` whose
  callers do `obj.x.<str-method>` will incur the same fallback.
- Once Gap 2 is fixed, the regression locks the property-return-type
  contract for `pcc/py_stdlib/*`, `pcc/py_frontend/*` (where a few
  `@property` declarations live), and any future user-typed-Python
  code.
