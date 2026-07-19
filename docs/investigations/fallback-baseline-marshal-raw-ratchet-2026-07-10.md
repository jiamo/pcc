# Investigation: marshal raw per-module fallback ratchet exceeds baseline

## Status
resolved

## Problem Description

After fixing the independent ON-mode contextual regression in
`assignment_statement_lowering`, the current HEAD fallback gate has one
remaining failure. The legacy scaffold-off raw per-module compile of
`pcc.py_frontend.codegen.marshal` emits 341 `py_cpy_*` calls; the checked
baseline is 310 with a five-percent allowance. The scaffold-on raw compile is
still zero, and the multi-file strict closure remains zero.

## Repro

Run the minimized raw-module gate:

```bash
gtimeout 120s env -u LC_ALL uv run pytest -q -n0 \
  tests/python/test_fallback_baseline.py::test_marshal_raw_per_module_fallbacks_stay_under_ratchet
```

Expected: pass at 325 calls or fewer. Current result: 341 calls.

The full observed gate is:

```bash
gtimeout 300s env -u LC_ALL uv run pytest -q -n0 \
  tests/python/test_fallback_baseline.py \
  tests/python/test_ir_py_fallback_baseline.py
```

It produced `1 failed, 19 passed in 166.16s`; the only failure was the marshal
raw per-module ratchet.

## Test [CONFIRMED]

The minimized test fails deterministically on current HEAD. Direct probes under
CPython 3.13.2 and 3.14.5 both produce 341 calls, ruling out host interpreter
version as the cause. A parent-source substitution at `cb5d37e8` produced zero
calls with the older inference/codegen stack; `marshal.py` itself is unchanged,
so the regression is owned by current frontend inference/lowering interaction.

## Proposals

- No.1 Recapture the marshal baseline at 341 [DENIED]
- No.2 Pin the gate to CPython 3.13 [DENIED]
- No.3 Identify and remove the incremental scaffold-off dynamic call class [CONFIRMED]

## No.1 Recapture the marshal baseline at 341

### Code Change

Raise `tests/fallback_baseline.json` from 310 to 341 for marshal.

### DENIED

This would hide a real ratchet regression and violates the M0 task boundary.

## No.2 Pin the gate to CPython 3.13

### Code Change

Run the fallback gate only with CPython 3.13.

### DENIED

Direct 3.13 and 3.14 probes both emitted the same 341 calls.

## No.3 Identify and remove the incremental scaffold-off dynamic call class

### Code Change

Generated-call comparison localized the 16-call delta to two additional
single-element list literals in `marshal_to_object` being lowered through
CPython `list()+append`. All five single-element `IRBuilder.call` argument
sequences now use tuple literals. The IRBuilder contract accepts any
`Iterable[Value]` and materializes its own list.

### CONFIRMED

The raw scaffold-off count fell from 341 to 316, scaffold-on stayed zero, the
focused raw ratchet passed, default LLVM-CAPI parity/end-to-end passed, and the
full fallback/no-libpython gate reported `21 passed in 188.25s`. The baseline
was not changed.

## Report

No.3 landed as the smallest semantics-preserving source-idiom change. No.1 was
rejected because baseline recapture would hide the regression. No.2 was
rejected because Python 3.13 and 3.14 produced the same count. The separate
assignment contextual regression is documented in
`contextual-per-module-fallback-gate.md` and its own evidence file.
