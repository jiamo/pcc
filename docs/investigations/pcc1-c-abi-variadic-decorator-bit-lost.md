# Investigation: pcc1 loses the C-ABI variadic decorator bit

## Status
resolved

## Problem Description

After the callable-regex varargs postprocessor failure was repaired, a fresh
self-backend/no-libpython pcc1 passes its publication smoke but cannot compile
the real `freestanding_stdio.py` module.  It reports that `va_start` requires
`@c_abi_variadic_export` even inside `snprintf`, which has exactly that
decorator.

Debug output proves that pcc1 sees one decorator and resolves the exported C
symbol.  An explicit decorator scan is required to compute the variadic bit,
but rebuilding with that scan still reproduced the failure.  The decisive IR
showed the bit was true before the scaffold bridge and false after it: a native
`i1 true` was lowered as `inttoptr i1 true`, producing pointer value 1.  PCC's
tagged-integer representation interprets pointer value 1 as integer zero, so
`FunctionType___init___dyn` evaluated it as false.  The resulting LLVM function
type was non-variadic and `va_start` correctly rejected it.

Predecessor (separate first failure):
[`pcc1-varargs-postprocess-callable-replacement.md`](pcc1-varargs-postprocess-callable-replacement.md).

## Repro

```text
gtimeout 180s env -u LC_ALL PCC_RUNTIME_CC=pcc PCC_RUNTIME_HIGH=py \
  build/libc-stdio-stage1-v2/pcc1 --ir-scaffold=on --backend self \
  --python-libpython off --python-library \
  --emit-llvm=build/libc-stdio-stage1-v2/freestanding-stdio-pcc1.ll \
  pcc/py_runtime/py/freestanding_stdio.py
```

Observed exit code: 1, with
`pcc.unsafe.va_start requires @c_abi_variadic_export`.

## Test [CONFIRMED]

The command above fails deterministically.  With
`PCC_DEBUG_BOOTSTRAP_TRACE=1`, `snprintf` reports a decorator tuple of length
one and successfully resolves its C ABI symbol before failing at `va_start`.
The required green gate is the same command under a newly rebuilt pcc1,
followed by an IR check for variadic `snprintf`/`fprintf`, `va_arg`, and absence
of `__pcc_va_arg_*` helpers.

## Proposals

- No.1 Replace the generator-expression `any` with an explicit decorator loop [CONFIRMED, necessary but insufficient]
- No.2 Box native bools crossing the scaffold object-handle ABI [CONFIRMED]

## No.1 Replace the generator-expression `any` with an explicit decorator loop

### Code Change

Scan the already-materialized decorator tuple with the same explicit-loop
dialect used by `_func_c_abi_export_symbol` and the unrecognized-decorator
check.  Set the bit once a variadic export decorator is found.

### CONFIRMED, necessary but insufficient

The explicit scan makes the source-level variadic decision bootstrap-safe and
the generated compiler IR proves it stores `i1 1` for the real decorator.  A
v3 pcc1 rebuilt with this change still failed because the following scaffold
handle conversion changed that true bit into tagged integer zero.  The scan is
therefore retained as required declaration behavior, but it was not the second
failure's root cause.

## No.2 Box native bools crossing the scaffold object-handle ABI

### Code Change

`_scaffold_to_handle` now recognizes a native `BoolType` integer value and
calls `py_bool_from_bit` before passing it to an object-handle scaffold helper.
Other established pointer/native-scalar handle representations are unchanged.
`test_function_type_ctor_dynamic_variadic_flag_boxes_bool_handle` fails if the
bridge regresses to `inttoptr i1`.

### CONFIRMED

Before the change, the compiler closure IR contained:

```text
%c_abi_variadic = load i1, ...
%raw = inttoptr i1 %c_abi_variadic to ptr
call ptr @...FunctionType___init___dyn(..., ptr %raw)
```

After the change the focused scaffold/variadic suite reports `76 passed`.  A
fresh v4 self-backend/no-libpython pcc1 passed its publication barrier in
65.995 seconds and compiled the real `freestanding_stdio.py` in 0.83 seconds.
The output defines variadic `snprintf` and `fprintf`, contains ten native
`va_arg` instructions, and contains zero `__pcc_va_arg_*` helpers.
