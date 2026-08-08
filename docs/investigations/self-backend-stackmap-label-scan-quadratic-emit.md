# Investigation: stack-map plan per-call label scans make huge-module emit quadratic

## Status

resolved locally 2026-08-15

## Problem Description

Third defect uncovered by the same stage1 failure chain as
[self-backend-verifier-dense-dominator-sets-oom.md](self-backend-verifier-dense-dominator-sets-oom.md):
after the verifier OOM fix, the emit worker for the 43 MB
`_l1_codegen_static_methods` module (72,100-block module top) ran with
bounded memory (~740 MB RSS) but burned 27+ CPU-minutes without finishing.
Stage1 previously completed in ~5-7 minutes total, so this is a stage1
wall-time regression class, not just an inefficiency.

## Repro [CONFIRMED]

Same RSS-capped single-worker run as the OOM investigation. A macOS `sample`
of the worker showed almost all time under `_PyEval_EvalFrameDefault` +
`_platform_memcmp`; a SIGINT after 27 CPU-minutes landed in:

```text
File "pcc/backend/self_backend_emit.py", line 46, in emit_function_blocks
    stack_map_plan.instruction_suffix_lines(block, instruction_index)
File "pcc/backend/self_backend_precise_stackmaps.py", line 123, in instruction_suffix_lines
```

## Root Cause [CONFIRMED]

`FunctionStackMapPlan.instruction_suffix_lines(block, index)` linearly scans
the whole `instruction_suffix_labels` tuple on every call, and both emit
loops (`self_backend_emit.emit_function_blocks` for aarch64, and the lambda
trio in `self_backend_x86_64_linux._emit_function`) call it once per
instruction. Total cost O(instructions x labels) — for a module top with
~10^5 instructions and ~10^4-10^5 safepoint labels that is 10^9-10^10 string
compares (the observed memcmp storm). `block_entry_lines` /
`terminator_prefix_lines` have the same shape per block, and the inner
`next(item for item in self.records if item.label == label)` adds an
O(records) rescan per matched label.

## Test [CONFIRMED]

- 27+ CPU-minutes with no completion before the fix (SIGINT'd, traceback
  above); post-fix timing of the same module is pending (see Result).
- Behavioral coverage: `tests/c/test_self_backend.py`,
  `tests/c/test_self_backend_verifier.py`,
  `tests/python/test_precise_stackmap_abi.py`,
  `tests/c/test_self_backend_linux_assemble.py` (both targets' emit paths).

## Proposals

- No.1 Precompute a per-function line index; keep line order byte-identical
  [pending measurement]

## No.1 Precompute a per-function line index

### Code Change

- `FunctionStackMapPlan.build_line_index()` builds, in one pass over the
  label tuples plus a `records`-by-label dict, three dicts: block ->
  entry-label lines, block -> instruction-index -> suffix lines (label +
  reload asm via the extracted `_reload_asm_lines`), block -> terminator
  prefix lines. Iteration order of the original tuples is preserved, so the
  emitted text is byte-identical to the per-call methods.
- `emit_function_blocks` builds the index once per function and uses dict
  membership instead of per-instruction scans.
- `self_backend_x86_64_linux._emit_function` drops its three lambdas and
  passes `stack_map_plan=` so both targets share the fast path.
- Explicit `in` + subscript everywhere (no `dict.get`/`setdefault`) per the
  pcc1 dict-lowering constraint.

### Result

`tests/c/test_self_backend.py` + verifier + precise-stackmap ABI +
Linux-assemble suites: 344 passed. The 43 MB module single-worker emit
re-run is in progress; its wall time / peak RSS and the stage1 rebuild
verdict will be recorded here.
