# Investigation: pcc1 stage2 cli_bootstrap ObservabilityOptions format failure

## Status

active, opened 2026-05-21.

The full self-backend bootstrap fails in stage 2 while compiled `pcc1` compiles
`pcc/__main__.py`. The current narrowed failing module is `pcc.cli_bootstrap`,
inside `bootstrap_cli_main()` while lowering the construction of
`ObservabilityOptions(...)`.

## Why this doc exists

This took long enough that continuing only in terminal history is wasteful. The
useful pattern from prior investigations is:

- `pcc-py-codegen-nested-closure-genexpr-scope.md`: first prove host pcc and
  pcc1 diverge, then use LLDB on pcc1 to locate the generated function whose
  behavior differs.
- `pcc-bootstrap-stage2-type-infer-runtime-corruption.md`: keep stage2 evidence
  as a running log, and do not restart from an obsolete symptom after a new
  failure phase is observed.
- `malloc-history-uaf-localization.md`: write down the debugging method, not
  only the specific fix, because the same self-host failure shape returns.

This investigation follows that style: preserve the exact repro commands,
record every narrowed boundary, and separate temporary probe artifacts from
candidate fixes.

## Repro

Original failing test:

```bash
env -u LC_ALL uv run pytest \
  tests/python/test_pcc_bootstrap_full.py::test_full_three_stage_bootstrap_self \
  -q -n0
```

Equivalent direct bootstrap command:

```bash
env -u LC_ALL bash scripts/bootstrap.sh \
  --backend self \
  --out-dir build/bootstrap-pytest-self \
  --stage 3
```

Observed failure:

```text
stage 1: succeeds and writes build/bootstrap-pytest-self/pcc1
stage 2: fails while pcc1 compiles pcc/__main__.py
error: PCC-PY-COMPILE-001: [python-frontend] compile failed
  note: exception_type=Exception
```

Focused pcc1 repro:

```bash
env -u LC_ALL -u LC_CTYPE PCC_RUNTIME_CC=pcc PCC_RUNTIME_HIGH=py \
  perl -e 'alarm shift; exec @ARGV' 300 \
  build/bootstrap-pytest-self/pcc1 \
  --backend self --python-libpython off --diagnostic-format=json \
  pcc/cli_bootstrap.py -o /tmp/pcc_cli_bootstrap_probe
```

Current focused diagnostic, after temporary narrowing probes:

```text
codegen[pcc.cli_bootstrap]:
  assign observability emit value failed:
  class call ObservabilityOptions instantiate failed: type
```

## Confirmed facts

Host compiler path succeeds:

```bash
env -u LC_ALL uv run python -m pcc \
  --backend self --python-libpython off \
  pcc/__main__.py -o /tmp/pcc_host_self_probe
```

The pcc1 path initially failed earlier in `type_infer[pcc.cli_bootstrap]`.
LLDB localized that first blocker to `_check_returns()` rejecting a bare
`return` from a nested function annotated as `-> None`. The source-level issue
was that the check used `isinstance(ret_ty, NoneType)`, which is not stable in
the self-hosted frontend. The candidate fix is to use `type_eq(ret_ty,
TYPE_NONE)`.

After that, pcc1 progressed into codegen and failed allocating a local named
`path` whose inferred type was the frontend meta-type `Type`. The candidate
fix is to treat frontend `Type` metadata as an object-shaped ABI value in
`type_abi_lowering`.

After those fixes, the remaining failure is in `codegen[pcc.cli_bootstrap]`,
statement `observability = ObservabilityOptions(...)` in `bootstrap_cli_main`.

The RHS emit path reaches class construction:

```text
AssignmentStatementLoweringMixin._emit_assign
  -> _emit_expr(Call ObservabilityOptions)
  -> CallExpressionLoweringMixin._emit_call
  -> ClassLowering.emit_instantiate("ObservabilityOptions", ...)
```

LLDB at `ClassLowering.emit_instantiate` confirms the class name argument is
the expected string:

```text
(lldb) expr -- (char*)py_str_utf8((void*)$x1)
"ObservabilityOptions"
```

Breaking on `py_raise` after entering `emit_instantiate` gives the current
most useful stack:

```text
py_raise
py_obj_ops_dispatch__raise_attribute_error
py_obj_getattr
py_obj_format
ClassLowering_emit_instantiate
CallExpressionLoweringMixin__emit_call
ExprDispatchLoweringMixin__emit_expr_impl
L1CodeGenEntrypointMixin__emit_expr
AssignmentStatementLoweringMixin__emit_assign
```

This means the current `type` message is not yet proof that an LLVM IR operand
lacks `.type`. The observed throw is an AttributeError raised from
`py_obj_format` inside the self-hosted `ClassLowering.emit_instantiate` path.

## Current hypothesis

The leading hypothesis is that a formatting operation inside
`ClassLowering.emit_instantiate` is not self-host-safe for one of the values it
formats. Existing source in this area uses f-strings for generated LLVM names,
for example names derived from `class_name`.

The symptom `type` may be the attribute name from an AttributeError rather than
the root cause. Do not assume `IRBuilder.call(... args ...)` is directly seeing
a non-IR value until the `py_obj_format` path is eliminated or localized.

## Methods that worked

1. Avoid the full pytest loop while localizing. Rebuild a strict pcc1 probe,
   then compile only `pcc/cli_bootstrap.py`.
2. Keep host pcc as the reference. Host success plus pcc1 failure is stage
   divergence, not ordinary source-invalid input.
3. Use `--diagnostic-format=json` for stable phase/error text, but do not
   trust the short message as root cause.
4. Use `PCC_DEBUG_CODEGEN_PHASES=1` only to identify the statement boundary,
   then switch to LLDB. Broad logs perturb self-host behavior.
5. In LLDB, break first at the narrowed generated function, then set
   `py_raise` and continue. This avoids drowning in expected exceptions from
   earlier package/module discovery.

Useful LLDB shape:

```bash
env -u LC_ALL -u LC_CTYPE perl -e 'alarm shift; exec @ARGV' 120 \
  lldb -b \
    -o 'breakpoint set -n user_pcc_py_frontend_codegen_class_gen_ClassLowering_emit_instantiate' \
    -o 'run --backend self --python-libpython off pcc/cli_bootstrap.py -o /tmp/probe' \
    -o 'breakpoint delete 1' \
    -o 'breakpoint set -n py_raise' \
    -o 'continue' \
    -o 'bt 18' \
    -- /tmp/pcc1_debug_probe
```

## Temporary probes used in this investigation

Temporary source probes were added around assignment lowering, class-call
lowering, and class instantiation to split the failure boundary. They should
not be committed as-is. Cleanup before continuing or landing any fix:

- `pcc/py_frontend/codegen/assignment_statement_lowering.py`
- `pcc/py_frontend/codegen/call_expression_lowering.py`
- `pcc/py_frontend/codegen/class_gen.py`

The durable candidate fixes so far are separate:

- `type_infer.py`: use `type_eq(ret_ty, TYPE_NONE)` for bare returns from
  `-> None`.
- `type_abi_lowering.py`: ensure frontend `Type` metadata values use the
  object ABI in self-host codegen.
- `assignment_statement_lowering.py` and `coercion_lowering.py`: compare type
  metadata by stable `.name` rather than host class identity in self-host
  paths, if still required after minimizing.

## Next steps

1. Remove the temporary broad try/except probes, then reproduce the
   `py_obj_format` LLDB stack on a clean probe build.
2. Disassemble or breakpoint within `ClassLowering.emit_instantiate` around
   f-string/name construction sites to identify exactly which format operation
   raises AttributeError.
3. Minimize the formatting issue into a pcc1-driven regression test. A likely
   shape is a small class instantiation whose lowering constructs formatted
   IR names from a self-hosted string or frontend type object.
4. Only after the minimized test exists, patch either the string formatting
   lowering/runtime `py_obj_format` path or the class-gen naming code.
5. Rerun the focused pcc1 `pcc/cli_bootstrap.py` compile, then the full
   `test_full_three_stage_bootstrap_self` gate.

