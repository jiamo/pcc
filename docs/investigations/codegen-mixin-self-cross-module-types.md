# Investigation: layer1 mixin split breaks no-libpython self-host

## Status
resolved for the closed-world self-host path as of 2026-05-13. The
frontend now has mixin-aware `self` type inference plus receiver-aware
codegen for `self.attr`, `self.attr = value`, and `self.method(...)`
inside mixin bodies.

## Problem description

On 2026-05-08 the layer1 codegen split landed: 15 commits (`🧭 [codegen→layer1]`)
moved native-module lowering helpers out of the monolithic
`pcc/py_frontend/codegen/layer1.py` into 11 mixin files
under `pcc/py_frontend/codegen/native_*.py`. Layer1 now declares:

```python
class L1CodeGen(
    NativeAsyncioLoweringMixin,
    NativeDataclassesLoweringMixin,
    NativeFilesLoweringMixin,
    NativeGcLoweringMixin,
    NativeMathLoweringMixin,
    NativeModuleAliasMixin,
    NativeOsLoweringMixin,
    NativeSystemLoweringMixin,
    NativeTextModulesLoweringMixin,
    NativeThreadingLoweringMixin,
    NativeWeakrefLoweringMixin,
    ...base classes...,
):
    ...
```

The split is a **pure refactor** — same behaviour, same IR, when run
through host CPython. No semantic change. The mixin classes are all
empty other than the methods that were moved.

But pcc cannot compile its own post-split codebase under
`--python-libpython=off`. All 11 mixin modules emit `py_cpy_*`
calls and trigger the libpython fallback gate.

## Symptom

`tests/test_fallback_baseline.py` 6 of 7 tests fail:
- `test_total_fallbacks_under_ratchet`: 1636 vs baseline 0 (+5.0%)
- `test_per_module_fallbacks_under_ratchet`: per-module regressions
- `test_on_mode_total_fallbacks_under_ratchet`: 1636 vs 0
- `test_on_mode_bridge_calls_do_not_regress`: 10 > 0
- `test_on_mode_non_bridge_fallbacks_do_not_regress`: 1626 > 0
- `test_on_mode_per_module_fallbacks_under_ratchet`: per-module regressions

`tests/test_bootstrap_gate_baseline.py` skipped (no fresh binaries) but
its baseline (`tests/bootstrap_gate_baseline.json`, 2026-05-01) cannot
be reproduced today: stage1 build fails with `PyPipelineError: Python
pipeline requires libpython fallback for multi-file compile (modules: ...)`
listing all 11 mixin modules.

## Root cause

`pcc/py_frontend/type_infer.py::_infer_funcdef` (line 1558) uses
`self_ty: Optional[ClassType]` to type the implicit `self` parameter.
The caller at `pcc/py_frontend/type_infer.py:1113` sets:

```python
self_ty = ctx.class_types.get(stmt.name)  # current class only
```

For a method body inside `class NativeTextModulesLoweringMixin:`,
`self_ty` is `NativeTextModulesLoweringMixin` — a class with **no
fields** and no methods other than the four moved into it. Every
`self.builder` / `self.runtime[...]` / `self._fresh(...)` / etc.
inside the mixin body resolves against this empty type, falls
through to DynType, and lowers as `py_cpy_getattr` /
`py_cpy_call*` / etc.

Per-mixin × ~150 `py_cpy_*` calls × 11 mixins ≈ 1636 fallback
total — matches the regression baseline gap exactly.

## Update 2026-05-13: receiver-aware mixin codegen

The type-inference half of this issue had already grown a
`derived_class_map`: when a class such as `ExceptionLoweringMixin` is a
unique base of `L1CodeGen` in the closed-world closure, the mixin method's
implicit `self` parameter is typed as `L1CodeGen`.

That was necessary but not sufficient. `layer1.py` still had codegen fast
paths that ignored the inferred receiver type and used `current_class`,
which is the class currently being lowered. For a method body physically
defined on `ExceptionLoweringMixin`, `current_class` is the mixin, not
`L1CodeGen`.

Minimal split that exposed the remaining bug:

```python
class ExceptionLoweringMixin:
    def _push_try_err_block(self, err_bb):
        prev = self._try_err_block
        self._try_err_block = err_bb
        return prev

    def _restore_try_err_block(self, prev_err_block) -> None:
        self._try_err_block = prev_err_block

    def _current_try_err_block(self):
        return self._try_err_block
```

Moving only those helpers out of concrete `L1CodeGen` first failed pcc1
stage2 in `ObservabilityOptions.__init__` after lowering a `raise`, with
`PCC-PY-COMPILE-001: opname`. Moving the helpers to the exception mixin
instead of a neutral module still failed. Adding concrete shims in a
different class-body position changed the failure to a self-backend
unterminated block, which proved the problem was not the helper logic
itself.

The decisive trace after receiver-aware method-call lowering showed the
next failing edge:

```text
[pcc.codegen] ...:_run_pytest_from_pcc1:try restore err begin
error: PCC-PY-COMPILE-001: [python-frontend] name
```

That was `_restore_try_err_block(prev_err_block)`. `_push_try_err_block`
had succeeded because it stored a non-null block. The restore path can
store the previous value `None`; when the mixin body was lowered against
`ExceptionLoweringMixin` instead of the actual receiver `L1CodeGen`,
`self._try_err_block = prev_err_block` fell through to the generic
attribute store path instead of the concrete field slot.

Fix applied:

- `self.attr` inside a method body chooses the class from the inferred
  `self` receiver `ClassType` when available, falling back to
  `current_class`.
- `self.attr = value` uses the same receiver class for field-slot stores.
- `self.method(...)` resolves the method MRO from the inferred receiver
  class when available.

After this, `_push_try_err_block`, `_restore_try_err_block`, and
`_current_try_err_block` can live in `ExceptionLoweringMixin`, and
`tests/python/test_pcc_bootstrap_full.py::test_full_three_stage_bootstrap_self`
passes.

## Why the obvious workarounds don't work

### `self: "L1CodeGen"` forward-ref annotation

`_infer_funcdef` already honours an explicit annotation when present:

```python
if (
    self_ty is not None
    and index == 0
    and a.name in ("self", "cls")
    and a.annotation is None  # only inject if no annotation
):
    ty = self_ty
```

When an annotation is present, pcc calls `resolve_annotation` →
`resolve_type_refs`, which looks up the name in
`ctx.class_types`. But `ctx.class_types` is **per-module**:
`L1CodeGen` is not registered when type-inferring
`pcc/py_frontend/codegen/native_text_modules.py`, so the
`ClassType` shell stays unresolved and degrades to DynType.

### `if TYPE_CHECKING: from .layer1 import L1CodeGen`

Empirically tested 2026-05-09 by adding the TYPE_CHECKING block plus
`self: "L1CodeGen"` annotations to all four methods of
`native_text_modules.py`. Re-running the no-libpython compile
gate shows `native_text_modules` is **still** in the libpython-needed
list — pcc's type-infer doesn't process imports inside
`if TYPE_CHECKING:` blocks, and even if it did, the annotation
target (`L1CodeGen`) lives in a module that itself imports
`native_text_modules` (circular).

### Direct `from .layer1 import L1CodeGen`

Creates a Python-level circular import. Layer1 imports
native_text_modules (as a base class) at module load time;
native_text_modules importing layer1 closes the cycle and fails
at the host CPython import step before pcc's frontend even runs.

## Real fix surface

Three frontend-level changes, ordered by depth:

### 1. Multi-file shared class table

Update `pcc/py_frontend/pipeline.py`'s multi-file walker to
populate every module's `_InferCtx.class_types` with classes
defined anywhere in the closure, not just the current module.
The walker already builds `native_exports` for codegen
cross-module dispatch; type_infer needs the same view.

Effect: a forward-ref annotation `self: "L1CodeGen"` in a mixin
resolves correctly even though `L1CodeGen` is defined in
another module.

### 2. Recognise TYPE_CHECKING-block imports

Update `_infer_stmt` to walk `If(test=Name("TYPE_CHECKING") or
Attribute("typing", "TYPE_CHECKING"), body=[ImportFrom(...)],
...)` shape and register the imported names in
`class_types` / scope without requiring the import to execute.

Useful but partial — relies on every mixin author writing the
TYPE_CHECKING boilerplate manually.

### 3. Mixin-aware self-type inference

When `class L1CodeGen(MixinA, MixinB, ..., Base):` is type-inferred,
re-type-infer the mixin method bodies with `self_ty=L1CodeGen`.
This is what mainstream typed-Python frontends (mypy, pyright)
do conceptually. It removes the need for any annotation in the
mixin source.

Effect: idiomatic mixin Python compiles natively without authors
needing to know about pcc's type registry.

## Recommendation

Land #1 first (smallest patch, biggest closure-level win): the
multi-file walker already touches every module's IR; piping the
class table through is a 1-2 day change.

Then #3 as the principled long-term fix once #1 unblocks the
bootstrap gate.

#2 is optional — a stop-gap that costs little but only helps
mixin-style code; once #1 + #3 land, TYPE_CHECKING is unnecessary.

## Don't recapture the baseline

`tests/fallback_baseline.json` says:

> if this is intentional reduction, recapture baseline JSON

This is a **regression** (0 → 1636), not a reduction. The baseline
captures a real correctness gate (no-libpython bootstrap is the
Issue 1 success criterion). Recapturing would silently accept the
regression and break the gate's purpose. Leave the baseline alone;
fix the frontend instead.

## References

- Issue 1 close criterion: `tests/bootstrap_gate_baseline.json`
- Codex split commits 2026-05-08: 15 commits prefixed
  `🧭 [codegen→layer1]`
- Failing tests: `tests/test_fallback_baseline.py` (6 of 7 in
  this file)
- Sibling investigation:
  `docs/investigations/threading-list-index-native-dispatch.md`
  (different surface, similar shape — pcc-typed-Python coverage
  gap exposed by an idiomatic Python pattern)
