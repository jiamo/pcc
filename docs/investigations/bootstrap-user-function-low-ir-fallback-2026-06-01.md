# Investigation: LowIR fallback and borrowed return ownership broke full bootstrap

## Status
resolved locally 2026-06-01. The first failing boundary was a strict
no-libpython fallback in `user_function_lowering`; after that was fixed, the
next boundary was a pcc1->pcc2 runtime double-free caused by a generic return
ownership bug. The existing single-backend full bootstrap gate passes after
both fixes; all-five-GC full-bootstrap matrix evidence is not yet implemented.

GitHub tracking issue: https://github.com/jiamo/pcc/issues/6

## Problem Description
The full strict self bootstrap regressed after a long period of success:

```bash
env -u LC_ALL uv run pytest \
  tests/python/test_pcc_bootstrap_full.py::test_full_three_stage_bootstrap_self \
  -q -n0
```

Stage1 failed while trying to build pcc2:

```text
error: PCC-PY-COMPILE-001: [python-frontend] Python pipeline requires
libpython fallback for multi-file compile
(modules: pcc.py_frontend.codegen.user_function_lowering)
```

A direct strict compile of the pcc entrypoint reproduced the same boundary:

```bash
env -u LC_ALL uv run pcc --backend self --python-libpython=off \
  --ir-scaffold=on pcc/__main__.py -o /tmp/pcc1_head_probe
```

After the LowIR fallback boundary was fixed, strict pcc0->pcc1 compile passed,
but the generated pcc1 crashed while compiling `pcc/cli_bootstrap.py` into
pcc2. LLDB reduced that second boundary to a double-free in generated
`pcc.py_frontend.type_infer._infer_expr` around the `IfExpr` branch:

```python
ty = common_type(then_e.ty, else_e.ty)
return replace(expr, cond=cond, then_e=then_e, else_e=else_e, ty=ty)
```

The generated `common_type(...)` function returned borrowed parameters and
module globals directly while callers treated user-function call results as
owned references. That made the caller release a result it did not own.

## Repro

First boundary, strict fallback during pcc0->pcc1:

```bash
env -u LC_ALL -u LC_CTYPE perl -e 'alarm shift; exec @ARGV' 180 \
  uv run pcc --backend self --python-libpython=off --ir-scaffold=on \
  pcc/__main__.py -o /tmp/pcc1_head_probe
```

Expected failing marker before the first fix:

```text
PCC-PY-COMPILE-001 ... modules: pcc.py_frontend.codegen.user_function_lowering
```

Second boundary, pcc1->pcc2 runtime crash after the first fix only:

```bash
env -u LC_ALL -u LC_CTYPE perl -e 'alarm shift; exec @ARGV' 180 \
  env PCC_RUNTIME_CC=pcc PCC_RUNTIME_HIGH=c PCC_PYTHON_IR_PASSES=off \
  PCC_PY_FRONTEND_JOBS=1 /tmp/pcc1_current_bin --backend self \
  --python-libpython off pcc/cli_bootstrap.py \
  -o /tmp/cli_bootstrap_stage2_current_bin
```

Observed failing marker before the return-ownership fix:

```text
malloc: Double free of object ...
frame #4 pcc_gc_free_object_memory
frame #8 py_decref
frame #9 pcc_gc_release
frame #10 user_pcc_py_frontend_type_infer__infer_expr
```

## Test [CONFIRMED]

Both failures were observed before fixing:

- `user_function_lowering` contextual fallback count was 80 before the LowIR
  helper fix and 0 after it.
- `tests/python/test_return_ownership.py::test_returning_borrowed_parameter_retains_for_owned_call_result`
  was added from the minimized ownership contract; before the return fix the
  generated `identity(xs)` IR returned `%xs` without `pcc_gc_retain`.

Fix gates run locally:

```bash
env -u LC_ALL -u LC_CTYPE perl -e 'alarm shift; exec @ARGV' 300 \
  uv run pytest tests/python/test_return_ownership.py \
  tests/python/test_fallback_baseline.py::test_on_mode_user_function_low_ir_helpers_contextual_fallback_zero \
  -q -n0
# 2 passed in 55.99s
```

```bash
env -u LC_ALL -u LC_CTYPE perl -e 'alarm shift; exec @ARGV' 720 \
  uv run pytest tests/python/test_pcc_bootstrap_full.py::test_full_three_stage_bootstrap_self \
  -q -n0
# 1 passed in 48.80s
```

## Proposals

- No.1 Use a typed LowIR value helper for `.ty` reads in
  `user_function_lowering` [CONFIRMED]
- No.2 Disable tuple auto-tracking or weaken tuple/GC runtime semantics
  [DENIED BY USER]
- No.3 Treat user-function call results as borrowed in callers [DENIED]
- No.4 Retain borrowed object returns in callee return lowering [CONFIRMED]
- No.5 Dataclass copy / `replace(...)` field incref bug [DENIED]

## No.1 Use a typed LowIR value helper for `.ty` reads in `user_function_lowering`
### Code Change
Add `_low_value_ty(value: pcc_low_ir.LowValue) -> int` and use it for LowIR
helper comparisons instead of direct `value.ty` / `lhs.ty` / `cond.ty` reads.
Add a contextual fallback canary for
`pcc.py_frontend.codegen.user_function_lowering`.
### CONFIRMED
The broken module was part of the recent LowIR / layer1 split work, not an
older bootstrap-stable path. `pcc.py_frontend.codegen.user_function_lowering`
and `pcc.py_frontend.low_ir` are in the recent `fe1de470` patch range relative
to `v0.1.2` (`88ee9157`), and the failing strict gate named
`user_function_lowering` directly.

The immediate cause was missing return type information on the recursive LowIR
builder helpers:

```python
def _low_ir_coerce_value(...):
def _low_ir_expr_to_value(...):
```

`_low_ir_expr_to_value()` returns `LowValue` instances or `None`, but without a
return annotation type inference gave callers `DynType`. That made expressions
such as:

```python
operand.ty == _LOW_F64
lhs.ty != pcc_low_ir.LOW_I64
value.ty == target_ty
```

lower as dynamic Python attribute/comparison operations instead of native int
field reads. In generated IR this became `py_cpy_*` calls, for example
`py_cpy_from_pcc_obj`, `py_cpy_getattr`, `py_cpy_call1`, and `py_cpy_truthy`.
Strict no-libpython mode correctly rejected that IR.

The closed-world contextual probe measured the real failure:

```text
before fix:
{'pcc.py_frontend.codegen.user_function_lowering': 80}
```

The 80 calls were concentrated in:

```text
user_pcc_py_frontend_codegen_user_function_lowering__low_ir_expr_to_value
user_pcc_py_frontend_codegen_user_function_lowering__low_ir_lower_stmt_block
```

After this change the same contextual module count was 0 and direct strict
pcc0->pcc1 compile passed. This did not complete the bootstrap fix; it exposed
the second, runtime ownership boundary.

## No.2 Disable tuple auto-tracking or weaken tuple/GC runtime semantics
### Code Change
No code change.
### DENIED BY USER
The user explicitly rejected disabling tuple auto `py_gc_track` in
`py_tuple_set_item`, and the evidence does not implicate tuple tracking as the
first failing boundary. The first failure was compile-time libpython fallback;
the second was a double-free of a returned type object from `common_type(...)`.
Weakening tuple tracking, barriers, or runtime ownership would only mask a
caller/callee contract violation and would regress the 5-GC contract.

## No.3 Treat user-function call results as borrowed in callers
### Code Change
No code change.
### DENIED
Generated pcc user-function calls use the normal owned-result convention:
callers may store and later release the returned reference. Making calls
borrowed would avoid this trace by changing the global call ownership contract
and would leak or mis-handle genuinely owned return expressions such as
constructors, literals, and owned locals. The double-free was in the callee
return path: borrowed parameters/module globals were returned without retaining
them.

## No.4 Retain borrowed object returns in callee return lowering
### Code Change
Add `ReturnLoweringMixin._return_value_needs_retain(...)` and
`_retain_borrowed_return_value(...)`. The return path now retains object return
values that are borrowed in the callee: parameters, module globals, environment
names, and non-owned locals. It does not retain owned locals, owned expression
results, CPython-tagged fallback values, unsafe raw pointers, or C-ABI raw-int
scaffold returns.
### CONFIRMED
The LLDB backtrace and generated IR tied the double-free to a user-function
call result from `common_type(...)`. Before the fix, generated IR for
`common_type` returned `%a`, `%b`, `TYPE_STR`, and `TYPE_DYN` directly with no
retain, while callers released the call result as owned. After the fix,
returning a borrowed parameter in the minimized `identity(xs) -> xs` case emits
`pcc_gc_retain` in the callee and the executable prints:

```text
1
1
```

The direct strict pcc1 build and the existing single-backend full bootstrap
gate pass after this change.

## No.5 Dataclass copy / `replace(...)` field incref bug
### Code Change
No code change.
### DENIED
This was a plausible locator because the crash occurred near
`replace(expr, ..., ty=ty)`, but source inspection denied it. The runtime field
set/copy paths use `pcc_gc_store_ptr` and retain semantics rather than plain
slot overwrite. The freed object was already under-owned before `replace(...)`
stored it.

## Misleading Trace
A raw single-file compile of `user_function_lowering.py` reported:

```text
Function._fresh ... too many positional args: got 1, expected at most 0
```

That was only a locator. Raw single-file probing gives codegen mixin modules
the wrong host context. Under the correct closed-world contextual compile,
`self._fresh` was typed as an `L1CodeGen` method and did not explain the full
bootstrap failure. The real strict-bootstrap root cause was the residual
`py_cpy_*` fallback emitted by dynamic LowIR `.ty` reads.

## Non-Causes Checked
- Not a tuple GC tracking issue. No runtime tuple/GC change is needed to
  explain the failure: the strict compiler rejected generated IR before pcc2
  execution.
- Not the recent pcc-native extension module-state-root or native file-handle
  lifetime investigations. Those are runtime/GC lifetime fixes; this failure is
  in Python frontend codegen IR before linking/running pcc2.
- Not fixed by `PCC_PYTHON_LOW_IR=off`. The LowIR helper module remains in the
  pcc1 closure and still must compile without libpython fallback.
- Not an old stable bootstrap failure. The failing source is in the recent
  LowIR/layer1 split range, and full bootstrap had been green before that range.
- Not a dataclass `replace(...)` copy-field ownership bug. The object had
  already been returned without retain from `common_type(...)`.

## Fix
Keep the first fix in the type/codegen boundary that caused the fallback:

- introduce typed `_low_value_ty(value: pcc_low_ir.LowValue) -> int`
- route LowIR helper `.ty` comparisons through that helper
- add an ON-mode contextual fallback canary for
  `pcc.py_frontend.codegen.user_function_lowering`

Keep the second fix in the generic caller/callee ownership contract:

- user-function call results remain owned in callers
- callee return lowering retains borrowed object returns before cleanup/return
- no tuple/GC/runtime tracking semantics are weakened

## Evidence
Focused contextual fallback count:

```text
before fix:
{'pcc.py_frontend.codegen.user_function_lowering': 80}

after fix:
{'pcc.py_frontend.codegen.user_function_lowering': 0}
```

Direct strict pcc1 compile:

```bash
env -u LC_ALL -u LC_CTYPE perl -e 'alarm shift; exec @ARGV' 300 \
  uv run pcc --backend self --python-libpython=off --ir-scaffold=on \
  pcc/__main__.py -o /tmp/pcc1_user_function_fix_probe
# passed
```

Focused regression:

```bash
env -u LC_ALL -u LC_CTYPE perl -e 'alarm shift; exec @ARGV' 300 \
  uv run pytest \
  tests/python/test_fallback_baseline.py::test_on_mode_user_function_low_ir_helpers_contextual_fallback_zero \
  -q -n0
# 1 passed
```

Final full bootstrap evidence:

```bash
env -u LC_ALL -u LC_CTYPE perl -e 'alarm shift; exec @ARGV' 720 \
  uv run pytest tests/python/test_pcc_bootstrap_full.py::test_full_three_stage_bootstrap_self \
  -q -n0
# 1 passed in 48.80s
```

This is the repository's current single-backend full bootstrap gate. It does
not prove the all-five-GC bootstrap matrix requested in the goal protocol; that
matrix is now documented as the target full-bootstrap proof for runtime/GC and
object-lifetime claims.

## System Improvement
This was a useful process failure, not just a one-line typing bug.

The specific missing guard was that `user_function_lowering` did not have a
named ON-mode contextual fallback canary. Existing baseline coverage can catch
some contextual regressions, but a targeted test makes this failure class
obvious and preserves the exact self-host invariant: LowIR helper code in pcc1
must not emit libpython fallback.

A broader future improvement is return-type inference for helpers whose return
arms are `SubclassOf(T)` or `None`; until that exists, recursive bootstrap
helpers that feed native field reads should carry explicit return annotations.

The broader process improvement is now written into `AGENTS.md` and
`codex-goal-prompt.md`: a long-green bootstrap regression starts with a
causality audit of recent changes, separates stacked failures, and forbids
weakening GC/runtime semantics to get a green stage.

## Report

Two confirmed fixes landed locally:

1. LowIR fallback: `user_function_lowering` lost static `LowValue.ty` knowledge
   inside recursive helper code, causing `py_cpy_*` fallback in the pcc1
   closure. The fix routes type reads through a typed helper and adds a
   contextual fallback canary.
2. Return ownership: pcc user-function calls return owned references, but return
   lowering could return borrowed parameters/module globals without retaining
   them. The fix retains borrowed object returns in the callee, preserving the
   generic caller/callee ownership contract.

The denied approaches are important: tuple auto-tracking was not the root
cause and must not be disabled; caller-side call ownership must not be weakened;
`replace(...)` was only where the under-owned object became visible, not where
ownership was lost.

Tracking issue updated: https://github.com/jiamo/pcc/issues/6
