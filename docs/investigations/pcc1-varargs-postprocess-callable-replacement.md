# Investigation: pcc1 varargs postprocess calls unsupported regex replacement

## Status
resolved

## Problem Description

A fresh self-backend/no-libpython stage1 is produced and starts normally, but
its publication smoke cannot compile even the phase1 integer corpus.  The
compiled compiler raises `TypeError: pcc re: sub expects string pattern,
replacement, and text` while generating LLVM IR.  This is a distinct
`pcc1 -> program` boundary after a successful `pcc0 -> pcc1` build.

LLDB shows that the failure originates in
`pcc.codegen.c_varargs.postprocess_varargs_ir`: the source uses a callable
replacement with `Pattern.sub`, while pcc's native regex replacement surface
accepts only literal string replacements.  A no-placeholder early return is
insufficient because pcc1 must later process the real variadic exports in
`freestanding_stdio.py` while producing pcc2.

## Repro

```text
gtimeout 180s env -u LC_ALL PCC_RUNTIME_CC=pcc PCC_RUNTIME_HIGH=py \
  build/libc-stdio-stage1/pcc1 --ir-scaffold=on --backend self \
  --python-libpython off tests/py_corpus/phase1/hello_int/source.py \
  -o build/libc-stdio-stage1/hello-int-smoke
```

Observed exit code: 1.  Required diagnostic marker:
`pcc re: sub expects string pattern, replacement, and text`.

The stage result separates compilation from the failed publication barrier:
`compile_wall_ms=106864`, `publish_barrier_returncode=1`.

## Test [CONFIRMED]

The command above fails deterministically on the fresh current-source pcc1.
LLDB breaks in `py_re_engine_sub`; all three initial object tags are strings,
and the native stack is
`re_pattern_method_call -> ... ->
user_pcc_codegen_c_varargs_postprocess_varargs_ir -> ...generate_impl`.

Existing structured rewrite tests are in
`tests/c/test_c_varargs_split.py`; the final gate must additionally rerun the
fresh pcc1 publication smoke and a real variadic-export compile.

## Proposals

- No.1 Replace callable-regex rewriting with a deterministic LLVM-line scanner [CONFIRMED]
- No.2 Return early when no helper marker is present [DENIED]

## No.1 Replace callable-regex rewriting with a deterministic LLVM-line scanner

### Code Change

Parse only the exact helper declaration/call grammar emitted by pcc, construct
the same `VarargsRewrite` report, and emit the LLVM `va_arg` line without a
callable regex replacement.  Reject non-matching lines by leaving them byte
identical.  This keeps the transformation usable by host pcc and compiled pcc1.

### CONFIRMED

`pcc.codegen.c_varargs.postprocess_varargs_ir` now uses a deterministic line
scanner for the exact generated helper declaration/call grammar.  It preserves
non-helper IR byte-for-byte, handles quoted SSA names, records the structured
rewrite report, and contains no callable regex replacement.

Evidence on the repaired source:

- `tests/c/test_c_varargs_split.py` passes together with the scaffold and
  freestanding variadic suites (`76 passed`).
- A fresh self-backend/no-libpython pcc1 completed its publication barrier in
  65.995 seconds.
- That pcc1 compiled the real `freestanding_stdio.py` module successfully.
  The resulting IR contains ten native `va_arg` instructions and contains no
  `__pcc_va_arg_*` helper declaration or call.

## No.2 Return early when no helper marker is present

### DENIED

That would repair the minimal publication smoke but fail later when pcc1
compiles the actual `freestanding_stdio` variadic exports for pcc2.  It narrows
the reproducer without closing the required self-host boundary.
