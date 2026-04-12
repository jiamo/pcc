# Top-Level Python API Plan

## Why This Exists

`pcc` is already a meaningful compiler research project:

- it compiles real C projects,
- it has an explicit pass surface instead of a black-box optimizer,
- it now has a visible SSA/MidTier roadmap,
- and it already exposes Python-side entrypoints for compiling and invoking C code.

What it does not yet have is a simple public API surface that makes those compiler capabilities immediately legible to new users.

This plan is about product surface, not about replacing the compiler roadmap.

The goal is to make `pcc` more attractive without diluting the fact that it is a compiler research project.

## Positioning

The public story should become:

- `pcc` is a programmable C compiler.
- compiler research remains the core of the project,
- top-level Python APIs make the compiler easy to use from scripts and notebooks.

This plan explicitly prefers:

```python
from pcc import build
from pcc import module
```

This plan explicitly does not introduce a parallel package such as `pccffi`.

## Core Surfaces

### `build(...)`

`build(...)` is the compiler-facing surface.

It should:

- compile one or more C translation units or a source tree,
- preserve access to compiler-facing knobs such as include dirs, cpp args, link args, optimization level, pass settings, cache settings, and output mode,
- return a structured artifact object instead of immediately binding functions into Python.

`build(...)` is the right API when the caller wants:

- compilation,
- linking,
- emitted artifacts,
- IR / pass reports,
- benchmarking or compiler inspection,
- or explicit control over the build step.

Expected mental model:

- `build` is closer to `clang` / `cc`,
- but with Python-native inputs and outputs.

### `module(...)`

`module(...)` is the Python binding surface.

It should:

- build or reuse a shared-library artifact,
- load it into Python,
- expose selected C functions as Python-callable attributes,
- stay thin over `build(...)` instead of inventing a second compilation pipeline.

`module(...)` is the right API when the caller wants:

- "use this C code from Python now",
- minimal boilerplate,
- repeatable source-first loading of local C code or a local C project.

Expected mental model:

- `module` is `build + load + bind`,
- not a separate compiler subsystem.

## Design Rules

### Rule 1: Do not split the brand

All public surfaces should live under `pcc`.

Good:

```python
from pcc import build, module
```

Bad:

```python
from pccffi import build
```

Internal packages may exist, but the public surface should stay unified.

### Rule 2: `module(...)` must reuse `build(...)`

`module(...)` should call into `build(...)` or its internal implementation path.

Do not create:

- one compiler path for `build`,
- another one for `module`,
- and a third one for CLI-only operation.

The artifact model should stay shared.

### Rule 3: product APIs must expose compiler value

The public APIs should not hide the fact that `pcc` is a compiler.

Artifacts returned from `build(...)` should make it easy to inspect:

- emitted shared library or executable path,
- compiled translation units,
- optimization level,
- pass report,
- optional LLVM IR dumps,
- cached vs rebuilt status.

### Rule 4: research features stay explicit

Compiler-explainer and experiment surfaces are features, not embarrassment.

This plan is compatible with adding later APIs such as:

- `pcc.explain(...)`
- `pcc.diff(...)`
- `pcc.inspect(...)`

Those are aligned with the project's research identity.

### Rule 5: do not overpromise on Python semantics

`module(...)` should be about C interop.

This plan does not promise:

- full CFFI replacement,
- broad Python semantic coverage,
- automatic Numba-like acceleration for arbitrary Python functions,
- or a near-term Python-front-end surface.

## System Library Linking

### Problem

pcc already links against libc implicitly — every compiled program can call `printf`, `malloc`, etc. without extra configuration. But real C projects depend on other pre-compiled libraries: `libz`, `libssl`, `libpthread`, `libm`, etc.

Today the only way to use these is to pass raw linker flags via `link_args=["-lz"]`. This is low-level, platform-dependent, and invisible to the build artifact.

### Design

The `build(...)` and `module(...)` APIs should accept a `libs` parameter that names system libraries to link against, analogous to how libc is already linked:

```python
# Link against libz — just like libc, no need to compile zlib yourself
artifact = build(
    sources=["src/compress.c"],
    libs=["z"],            # → -lz at link time
    optimize=2,
)

# Multiple libraries
artifact = build(
    sources=["src/server.c"],
    libs=["ssl", "crypto", "pthread"],
    include_dirs=["/opt/homebrew/include"],
)

# In module(...) — same parameter
m = module(
    sources=["src/compress.c"],
    libs=["z"],
)
result = m.my_compress(data, len(data))
```

Implementation rules:

- `libs=["z"]` translates to `link_args=["-lz"]` on Unix and appropriate flags on other platforms.
- `libs` is a semantic declaration ("I depend on zlib"); `link_args` is escape-hatch for raw flags.
- The artifact should record which system libraries were linked for debugging and reproducibility.
- Library resolution uses the system linker's default search paths. Users can extend search paths via `link_args=["-L/path"]` or a future `lib_dirs` parameter.
- pcc does NOT compile these libraries — it links against their pre-built `.so`/`.dylib`/`.a` files, exactly like libc.

### Scope

First wave (same as libc model):

- named system libraries via `-l` flag,
- standard search paths,
- works immediately with any library installed via the system package manager (`apt install libz-dev`, `brew install zlib`, etc.).

Future extensions (not in first wave):

- `lib_dirs=["path"]` for explicit library search paths,
- `pkg_config=["zlib"]` for automatic include/lib discovery via `pkg-config`,
- static vs shared preference (`static_libs=["z"]`),
- bundled dependency builds (compile a vendored library as part of the build).

## Initial API Shape

### `build(...)`

Initial target:

```python
artifact = build(
    sources=["src/a.c", "src/b.c"],
    header=None,
    include_dirs=["include"],
    cpp_args=["-DMYFLAG=1"],
    libs=[],               # system libraries to link (e.g. ["z", "ssl"])
    link_args=[],           # raw linker flags (escape hatch)
    optimize=2,
    kind="sharedlib",
    out_dir=None,
    use_compile_cache=True,
)
```

Initial return shape should include enough information for both users and tests:

- `kind`
- `output_path`
- `compiled_units`
- `pass_report`
- `exports`
- `optimize`
- `rebuilt`
- `libs` — which system libraries were linked

### `module(...)`

Initial target:

```python
m = module(
    sources=["src/a.c", "src/b.c"],
    header="include/a.h",
    include_dirs=["include"],
    libs=[],                # system libraries (e.g. ["z"])
    cpp_args=[],
    link_args=[],
    optimize=2,
)
```

Initial return shape:

- Python object with loaded library handle,
- callable exported functions,
- attached build artifact metadata for debugging.

The first version does not need automatic full-header binding coverage.

It is acceptable to start with:

- explicit exported symbol list,
- or a narrow supported C signature subset.

## Non-Goals

Not immediate goals:

- replacing the CLI,
- replacing direct `CEvaluator` access for compiler tests,
- full automatic header parsing and complete C binding coverage,
- a new package name,
- a Python-subset front-end or decorator-based acceleration surface,
- or pausing SSA/backend/compiler work in favor of API polish only.

## Implementation Phases

### Phase 0: Freeze The Public Story — ✅ Complete

Deliverables:

- write this plan,
- define `build` vs `module` responsibilities,
- commit to top-level `pcc` exports.

Exit criteria:

- ✅ one clear positioning statement exists,
- ✅ no parallel package naming is planned.

### Phase 1: Land `build(...)` — ✅ Complete

Implementation: `pcc/api.py` — `build()` wraps `CEvaluator.compile_translation_units` + system-cc linking.

- `BuildArtifact` dataclass with `kind`, `output_path`, `compiled_units`, `pass_report`, `exports`, `optimize`, `rebuilt`, `libs`, `ir_text`.
- Supports `kind="exe"`, `kind="sharedlib"`, `kind="object"`.
- `libs=["z"]` translates to `-lz` at link time.
- `cpp_args`, `include_dirs`, `link_args`, `out_dir`, `use_compile_cache`, `jobs` all wired through.

Exit criteria:

- ✅ one-file and multi-file C builds work through `from pcc import build`,
- ✅ tests assert over structured artifact object (18 tests in `tests/test_api.py`),
- README example: see below.

### Phase 2: Land `module(...)` — ✅ Complete

Implementation: `pcc/api.py` — `module()` calls `build(kind="sharedlib")` + `ctypes.CDLL` load.

- `Module` dataclass with `__getattr__` dispatch to `ctypes` function lookup.
- `module.__pcc_artifact__` exposes full build metadata.
- Reuses `build()` entirely — no separate compilation path.

Exit criteria:

- ✅ local C sources turn into Python-callable object with one function call,
- ✅ compilation cache is reused across repeated builds,
- ✅ implementation reuses `build(...)`.

### Phase 3: Add Compiler-Visible Debugging — ✅ Complete

Implemented surfaces:

- ✅ `artifact.pass_report` — per-unit pass statistics from the HighTier pipeline.
- ✅ `artifact.ir_text` — generated LLVM IR text for all compiled units.
- ✅ `module.__pcc_artifact__` — full build metadata reachable from loaded module.
- `pcc.explain(...)` — deferred to future work; `ir_text` + `pass_report` cover the immediate need.

Exit criteria:

- ✅ top-level API makes compiler work more visible,
- ✅ tests verify `ir_text` contains LLVM `define`, `pass_report` is populated.

## Validation — ✅ Complete

`tests/test_api.py` contains 18 tests:

1. ✅ focused unit tests for `build()` (exe, sharedlib, object, out_dir, missing source, invalid kind),
2. ✅ multi-file integration test (`test_build_multi_sources`),
3. ✅ cache reuse test (`test_build_cache_reuse`),
4. ✅ debugging/metadata tests (`test_artifact_has_ir_text`, `test_artifact_has_pass_report`, `test_module_pcc_artifact_accessible`),
5. ✅ `libs` tests (`test_build_with_libs`, `test_module_with_libs`),
6. ✅ `module()` tests: scalar call (`test_module_call_function`), missing symbol (`test_module_missing_function_raises`), repr, exports.

## Usage Examples

### build()

```python
from pcc import build

# Compile to executable
artifact = build("main.c", kind="exe")
# artifact.output_path → "/tmp/pcc_build_.../a.out"

# Compile with system library
artifact = build("compress.c", libs=["z"], kind="sharedlib")
# links against libz — no need to compile zlib yourself

# Multi-source with include dirs
artifact = build(
    ["src/utils.c", "src/main.c"],
    include_dirs=["include"],
    cpp_args=["-DNDEBUG"],
    optimize=2,
    kind="exe",
)

# Inspect compiler output
print(artifact.ir_text)        # LLVM IR
print(artifact.pass_report)    # pass statistics
print(artifact.exports)        # exported function names
```

### module()

```python
from pcc import module

# Compile and load C functions into Python
m = module("math_utils.c", libs=["m"])

# Call C functions directly
result = m.add(3, 4)         # → 7
result = m.mul(5, 6)         # → 30

# Access compiler metadata
print(m.__pcc_artifact__.ir_text)
print(m.__pcc_artifact__.exports)
```

## Definition Of Done

This plan is complete when all of the following are true:

- ✅ `from pcc import build, module` is stable and documented,
- ✅ `module(...)` is implemented as a thin layer over `build(...)`,
- ✅ compiler artifacts and pass/debug information remain visible to users,
- ✅ the public surface makes the compiler easier to adopt without obscuring that `pcc` is a compiler research project.
