# Text-free direct capture loses the terminator around a fence

Task: PY-P1-DIRECT-INDEXED-FENCE-CAPTURE.
Status: observed, not fixed.
Date: 2026-09-05.

The native-buffer canary initially used `pcc.llvm_capi.ir.IRBuilder` with
`PCC_DIRECT_INDEXED_KERNEL_CAPTURE=1` and `PCC_DIRECT_INDEXED_KERNEL_EMIT=1`:
one function builds entry/yes/no blocks, emits `fence("seq_cst")`, a global
load, an integer add/compare, conditional branch and returns. Calling
`module.direct_indexed_module()` fails before any native worker starts:

```text
direct indexed finalize failed for function probe: BackendUnavailable:
self backend does not support terminator in 'probe'/'entry':
```

The observed stack goes through `build_direct_indexed_function`,
`build_indexed_function_seed_from_block_lines` and `_FunctionBlockPlane.append`.
The fence is outside the direct publication surface, triggering the text
adapter after other instructions/terminators have already omitted their text.
The empty terminator diagnostic does not identify the missing fence capability.

The buffer test now freezes equivalent complete textual IR and successfully
runs the fence through the actual v74 native ASM/PCO worker. That isolates the
emission-buffer gate; it does not fix the capture defect. No source fallback
baseline, capture requirement or existing test was weakened. The task needs a
focused direct-capture regression, native fence publication and an audit of
other unsupported instructions that enter this partial-text fallback class.
