# PCC TileLang claim boundary

This document is the authoritative text for the
`GPU-P0-TILELANG-NATIVE-NAMING-BOUNDARY` task. It exists to keep one honest
distinction from ever collapsing: **there are two different things that both
mention "tilelang", and pcc's `pcc/kernel_ir/tilelang_import.py` module provides
only the second one.**

## Two separate labels (never conflate)

### `tilelang-package-cpython-compat`

A *runtime* `import tilelang`, served through pcc's package / cpython-compat
path. This is what happens when a Python program executes the statement
`import tilelang` and expects the upstream TileLang/TVM runtime to load and run.

- It links libpython and executes the upstream TileLang/TVM runtime.
- It is ordinary package + cpython-compat work, on the same footing as any other
  third-party package install/import claim (see the Package / NumPy claim
  hygiene rules in `AGENTS.md` and `docs/goal/goal-prompt.md` §0.10).
- **It is NOT provided by `pcc/kernel_ir/tilelang_import.py`.** That module never
  imports, installs, or executes the `tilelang` package.

### `pcc-tilelang-source-subset`

What `import_tilelang_source` and `import_tilelang_file` in
`pcc/kernel_ir/tilelang_import.py` actually provide:

- A compiler-side `ast` parse of a **strict** TileLang Python-DSL subset
  (`@T.prim_func` / `T.Kernel` / `T.copy` / `T.gemm` / ... — the shapes listed in
  the module docstring) into pcc Kernel IR (`pcc/kernel_ir/ir.py`).
- **No execution.** It does not run TileLang, TVM, torch, or user code; it reads
  source text and builds IR.
- **No runtime `import tilelang`.** The word `tilelang` in the parsed source is
  DSL surface syntax the parser recognizes; nothing is imported.
- **Not a pcc-native `import tilelang` claim.** Parsing a DSL subset into Kernel
  IR is not the same as pcc natively supporting the runtime `import tilelang`
  statement.
- Unknown constructs **fail closed** (raise `TileLangImportError`) so pcc never
  claims support by accident.

## What the module surfaces to enforce this

`pcc/kernel_ir/tilelang_import.py` makes the boundary machine-checkable:

- Constants `TILELANG_PACKAGE_CPYTHON_COMPAT_CLAIM = "tilelang-package-cpython-compat"`
  and `TILELANG_SOURCE_SUBSET_CLAIM = "pcc-tilelang-source-subset"`.
- `tilelang_source_import_claim()` returns the honest metadata for this path:
  `mode="pcc-tilelang-source-subset"`, `executes_tilelang_runtime=False`,
  `is_pcc_native_import_tilelang=False`, `links_libpython=False`.
- Every `KernelModule` returned by `import_tilelang_source` /
  `import_tilelang_file` is stamped with that metadata;
  `tilelang_source_import_claim_of(module)` reads it back and raises if the
  module did not come from this importer.
- `assert_not_native_import_tilelang_claim(claim_text)` raises
  `TileLangImportError` if prose describes this path as a native
  `import tilelang`, as executing the TileLang runtime, or (mis)applies the
  `tilelang-package-cpython-compat` label to it.

## What this boundary does NOT claim

- It does not claim a runtime `import tilelang` works under pcc or pcc1.
- It does not claim libpython-free or cpython-compat package support for the
  `tilelang` package.
- It does not claim device execution; that lives behind the separate Metal
  runtime-source / finalize claim levels in `docs/design/pcc-kernel-ir.md` and
  `docs/design/pcc-gpu-next-work.md`.

This is a statement about the currently implemented source-subset path, not a
permanent ban on an owner backend.  The separately selected and pinned
`gpu-owner=tvm-tilelang` mode designed in
`docs/design/pcc-gpu-owner-backends.md` may become an execution owner after its
device-result, no-fallback, pcc1, dependency, and five-GC gates pass.  That mode
does not retroactively turn `import_tilelang_source(...)` into runtime package
support, and it does not transfer pcc's Kernel IR, ABI, or lifetime semantics to
an ambient TileLang installation.

## Tests

`tests/kernel/test_tilelang_import_claim_boundaries.py`:

- `test_normal_pcc1_kernel_py_does_not_silently_reinterpret_import_tilelang_as_kernel_ir`
  — a normal Python module that merely mentions `import tilelang` is not routed
  through the source-subset importer; no import hook is registered, the
  entrypoint requires explicit arguments, and a non-kernel source fails closed.
- `test_explicit_kernel_import_mode_required_for_tilelang_source_subset_routing`
  — routing to the subset requires the explicit `import_tilelang_source` call,
  the result carries `mode == "pcc-tilelang-source-subset"` with
  `is_pcc_native_import_tilelang` False, and
  `assert_not_native_import_tilelang_claim` raises on a native-import claim.
