# LLVM structured-format auto-index ABI evidence

Date: 2026-07-10

Task: `M0-HEAD-LLVM-FORMAT-AUTO-IDX-ABI`

## Boundary

This evidence proves that the current worktree can compile the compiler source
through the strict no-libpython LLVM `pcc0 -> pcc1` boundary after preserving
the structured-format helper's typed auto-index across its tuple return. It does
not by itself prove `pcc1 -> pcc2 -> pcc3` fixed-point equality; that remains in
the HEAD truth manifest task.

## Red

The new focused export-contract test observed
`_emit_structured_spec_obj.return_ty == DynType` instead of a two-element tuple
whose second element is `IntType`. The enclosing truth run failed LLVM parsing
because `%m.int_box.1005` was a pointer at an `i64` call position.

## Change

`_emit_structured_spec_obj` now declares `auto_idx: int` and returns
`tuple[Optional[ir.Value], int]`. This preserves semantic type information at
the cross-mixin export boundary and lets tuple unpacking restore the raw integer
projection used by this LLVM-scaffold module.

## Gates

```text
gtimeout 180s env -u LC_ALL uv run pytest -q -n0 tests/python/test_native_str_format_index.py
9 passed in 5.58s

gtimeout 240s env -u LC_ALL uv run pcc --backend llvm --python-libpython=off --ir-scaffold=on pcc/__main__.py -o build/head-truth/bootstrap-llvm-format-pcc1
exit 0

file build/head-truth/bootstrap-llvm-format-pcc1
Mach-O 64-bit executable arm64

otool -L build/head-truth/bootstrap-llvm-format-pcc1
/usr/lib/libSystem.B.dylib only; no libpython
```
