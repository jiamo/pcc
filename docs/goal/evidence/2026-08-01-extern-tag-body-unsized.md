# BUG-P1-CC-EMBEDDED-TAG-IN-EXTERN-DECL-UNSIZED — fixed

Mode: host pcc, C frontend, LLVM backend. The vendored musl math tree is
compiled by pcc into `libpy_runtime_pcc_py.a`.

## What was actually wrong

Not the type resolver. `ElimAvailExternPass`
(`pcc/passes/ipo_boundary.py`) removes file-scope `extern` declarations that
nothing in the translation unit references. A declaration like

```c
extern const struct T { double a; } g;
```

does two things at once: it declares `g` **and** it defines `struct T`.
Dropping it took the tag body with it, so the later `const struct T g = {...}`
found no tag, created an opaque identified type, and LLVM aborted at object
emission:

```text
Assertion failed: (Ty->isSized() && "Cannot getTypeInfo() on a type that is
unsized!"), function getTypeSizeInBits, file DataLayout.h, line 618.
```

The earlier note on this row blamed `_collect_file_scope_object_ir_types`
caching an opaque type. That was wrong: the pre-pass never saw the extern
declaration at all, because the pass had already deleted it from the AST.

## Why the two-line repro looked like it passed

The recorded repro compiled fine — because it was run *without* `--emit-obj`,
so it needed a `main` that read `g.a`, and that read is exactly what made the
pass keep the declaration. The failure needs a unit where **nothing reads the
object**:

```text
m1.c (extern-tag decl + definition, nothing else)   --emit-obj -> ABORT
m2.c (same, plus an unrelated function)             --emit-obj -> ABORT
m3.c (same, plus `double get(void){ return g.a; }`) --emit-obj -> ok
```

Located by dumping the FileAST: one ext node reached codegen where the parser
had produced two.

## The silent half of the bug

Before the abort, the object's initializer was already gone:

```llvm
%__pcc_m1_c_2_struct_0_T = type opaque
@g = local_unnamed_addr global %__pcc_m1_c_2_struct_0_T zeroinitializer
```

For `__exp_data` / `__pow_log_data` that is a table of zeros, not a crash — so
this class of failure can produce wrong math instead of a diagnostic wherever
the unsized type happens to be tolerated.

## Fix

`_writes_a_tag_body()` walks the declarator chain and reports whether the
declaration is where a `struct`/`union` body or an `enum` value list is
written; `ElimAvailExternPass` no longer removes those. Unused externs with no
tag body are still removed, so the pass keeps doing its job.

## Evidence

```text
tests/c/test_extern_decl_tag_body.py                                  5 passed
  - the tag-carrying extern survives the pass (struct and enum forms)
  - a plain unused extern is still removed
  - the object is sized and keeps a real initializer
  - the values read back correctly at runtime
tests/c/test_c_parser.py test_unsigned_loads.py test_lz4.py          64 passed
tests/c/test_ir_passes_phase5_8.py test_ipo_boundary_translation.py
  test_ir_passes_elim_avail_extern_real.py
  tests/python/test_llvm_python_registry.py                         296 passed
tests/python/test_libc_import_baseline.py
  tests/python/test_musl_string_differential.py                       5 passed
```

The vendored headers are back to the upstream shape (the tag body inside the
extern declaration) and the local-patch entry for the split is gone from
`vendor/musl-1.2.5/string/VENDOR.json`; `exp_data.c`, `pow_data.c` and `pow.c`
compile, and the rebuilt archive gives CPython-identical results:

```text
pow(2.0,10.0)  1024.0
pow(1.5,3.5)   4.133513940946613
pow(10.0,-3.0) 0.001
2.0 ** 0.5     1.4142135623730951
```
