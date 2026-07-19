# Investigation: LLVM bootstrap structured-format auto-index ABI mismatch

## Status
resolved

## Problem Description

The commit-bound M0 truth run proves the self-backend five-GC bootstrap but
fails independently at the LLVM `pcc0 -> pcc1` boundary. The host compiler
emits `FormatLoweringMixin._emit_structured_spec_obj` with an `i64` final
parameter, then calls it with the boxed pointer held by `auto_idx`. LLVM rejects
the module before the first-stage compiler can link.

This is a compiler-source ABI mismatch, not a nested-format behavior failure:
the ordinary format tests compile user code, while the failing gate compiles
the compiler implementation itself.

## Repro

Run the strict first-stage LLVM compile:

```bash
gtimeout 240s env -u LC_ALL uv run pcc --backend llvm \
  --python-libpython=off --ir-scaffold=on pcc/__main__.py \
  -o build/head-truth/bootstrap-llvm-format-pcc1
```

Expected: exit 0 and a linked `pcc1` artifact. Observed: exit 1 while parsing
the generated LLVM IR. The diagnostic says `%m.int_box.1005` is defined as
`ptr` but expected `i64` at the call to
`FormatLoweringMixin__emit_structured_spec_obj`.

## Test [CONFIRMED]

The same failure was observed in the all-suite truth runner. Its LLVM bootstrap
record is `FAIL`; the independent fallback, GC production contract, and self
five-GC records are `PASS`. The LLVM gate produced no `pcc1`, `pcc2`, or `pcc3`
artifact, which localizes the first failing boundary to `pcc0 -> pcc1` IR
validation.

The regression gate combines the strict compile above with
`tests/python/test_native_str_format_index.py`, so ABI repair cannot discard the
nested-spec semantics that introduced the helper.

## Proposals

- No.1 Regenerate the static L1 method table unchanged [DENIED]
- No.2 Preserve the typed auto-index across the helper return [CONFIRMED]
- No.3 Change format semantics to keep `auto_idx` unboxed [DENIED]

## No.1 Regenerate the static L1 method table unchanged

### Code Change

Run `scripts/regen_l1_codegen_static_methods.py` without changing its inputs or
ABI policy.

### DENIED

`_emit_structured_spec_obj` is not present in the generated static table. The
table generator also intentionally writes `box_int_abi: False` for every host
method, so an unchanged regeneration cannot reconcile this helper's boxed
caller with its compiled signature.

## No.2 Preserve the typed auto-index across the helper return

### Code Change

Give `_emit_structured_spec_obj` an explicit `auto_idx: int` parameter and
`tuple[Optional[ir.Value], int]` return annotation. This makes the closed-world
export table preserve the second tuple element as `IntType`, so tuple unpacking
converts the runtime object projection back to the raw `i64` lane used inside
this scaffold module.

### CONFIRMED

The focused export-contract test failed first because both the return and final
parameter were `DynType`, then passed after the annotation. All nine
`test_native_str_format_index.py` tests passed. The strict LLVM command exited
zero and linked `build/head-truth/bootstrap-llvm-format-pcc1`; `otool -L` lists
only `/usr/lib/libSystem.B.dylib`, not libpython.

## No.3 Change format semantics to keep `auto_idx` unboxed

### Code Change

Force `auto_idx` into a raw machine-integer lane throughout format lowering.

### DENIED

The project contract defines ordinary Python `int` as a semantic type with a
boxed object projection when values cross general Python method boundaries.
Weakening the helper to make invalid IR parse would hide the ABI disagreement
and risk silent wrap behavior.

## Report

No.2 landed as a type-contract repair. The `i64` helper ABI was already correct
for a module importing the raw LLVM scaffold; the actual loss occurred when an
unannotated helper return was exported as `DynType` and tuple unpacking rebound
`auto_idx` as a boxed pointer. No.1 could not affect this non-static method
entry, and No.3 would have weakened the value-model contract. The full
commit-bound truth matrix remains the enclosing M0 gate.
