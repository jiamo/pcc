# TileLang package-import vs pcc source-subset naming boundary

Date: 2026-07-08

Task: `GPU-P0-TILELANG-NATIVE-NAMING-BOUNDARY`

Scope:
- Establish a hard, gated naming/claim boundary between a runtime `import tilelang`
  (package/cpython-compat work) and `import_tilelang_source`/`import_tilelang_file`
  (compiler-side AST parsing of a DSL subset into pcc Kernel IR). The latter must
  never be described as a pcc-native `import tilelang`.
- No DSL subset / parsing behavior changed; this is a claim-label + diagnostic +
  documentation slice.

Changed files:
- `pcc/kernel_ir/tilelang_import.py` — claim-label constants, claim metadata
  stamped on every returned `KernelModule` (via `object.__setattr__`, so it does
  not join the frozen dataclass field/eq/hash surface), `tilelang_source_import_claim()`,
  `tilelang_source_import_claim_of(module)`, and `assert_not_native_import_tilelang_claim(...)`.
- `tests/kernel/test_tilelang_import_claim_boundaries.py` — new test file.
- `docs/design/pcc-tilelang-claim-boundary.md` — new doc defining the two labels.
- `docs/design/pcc-kernel-ir.md` — appended `## 7. TileLang claim labels`.

Two labels defined:
- `tilelang-package-cpython-compat` = runtime `import tilelang` via package/
  cpython-compat path (links libpython, runs upstream tilelang). NOT provided by
  this module.
- `pcc-tilelang-source-subset` = AST parse of a DSL subset into pcc Kernel IR; no
  execution, no runtime `import tilelang`, no pcc-native import claim.

Gates:
- `gtimeout 300s env -u LC_ALL uv run pytest -q -n0 tests/kernel/test_tilelang_import_claim_boundaries.py::test_normal_pcc1_kernel_py_does_not_silently_reinterpret_import_tilelang_as_kernel_ir`
  - passed
- `gtimeout 300s env -u LC_ALL uv run pytest -q -n0 tests/kernel/test_tilelang_import_claim_boundaries.py::test_explicit_kernel_import_mode_required_for_tilelang_source_subset_routing`
  - passed
- Combined with regression `tests/kernel/test_tilelang_import.py`: `16 passed in 0.69s`
- Prose gates ("docs define the two labels separately"; "diagnostics reject
  saying pcc-native import tilelang when only source import was used"): satisfied
  by the new doc + `assert_not_native_import_tilelang_claim`.

Result: DONE_STRONG (promoted 2026-07-08 after re-verifying all four required
gates, 16 passed). The four required gates fully prove the separation claim; the
runtime `import tilelang` (tilelang-package-cpython-compat) path is a DIFFERENT
concept tracked as separate package/cpython-compat work, not a boundary of this
row, so the open boundary for THIS claim is empty.

Claim: the source-subset importer stamps honest `pcc-tilelang-source-subset`
metadata (`executes_tilelang_runtime=False`, `is_pcc_native_import_tilelang=False`),
there is no import hook silently reinterpreting a plain `import tilelang`, and a
guard rejects prose asserting a native `import tilelang` for this path.

Open boundary: the claim surface and guard are library-level; they are not yet
wired into a CLI compile diagnostic, and the runtime `tilelang-package-cpython-compat`
path itself is separate package/cpython-compat work not implemented here.
