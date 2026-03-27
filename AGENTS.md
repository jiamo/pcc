# AGENTS.md

This file is for humans and AI agents working in this repository.

## Project Summary

`pcc` is a C compiler implemented in Python on top of `pycparser`, `llvmlite`, and a lightweight fake-libc layer. Most fixes in this repository are not parser bugs; they are semantic bugs that only show up when expressions are combined, lowered to LLVM IR, and then exercised by real programs.

The fastest way to get useful results is:

1. Reproduce with a tiny C program when possible.
2. Compare against a native compiler built from the same source.
3. Add one small regression test and one realistic integration confirmation.


## Environment Rules

- Use `uv run ...` for Python entrypoints. Do not rely on bare `python`; local `pyenv` state may not match the repository.
- **Codex-specific**: Codex sets `LC_ALL=C` which can break Python locale handling. Unset it with `env -u LC_ALL` before running commands. See https://github.com/openai/codex/issues/14723. Not needed for Claude Code or other agents.

```bash
# Codex only:
env -u LC_ALL uv run pytest -q
env -u LC_ALL uv run pcc hello.c
# Claude Code / others:
uv run pytest -q
uv run pcc hello.c
```

- While debugging one failure, prefer `-n0` so xdist does not hide ordering or temp-file problems.
- Use ripgrep (`rg`), or your agent's built-in code search tools (e.g. Grep/Glob in Claude Code) for source discovery.
- Do not leave temporary `.c` files inside real project directories. Directory-based source collection can accidentally compile them.


## Repository Map

- `pcc/pcc.py`
  Command-line entrypoint.
- `pcc/project.py`
  Directory collection, `--sources-from-make`, and translation-unit selection.
- `pcc/evaluater/c_evaluator.py`
  Preprocessing, parsing, IR generation, LLVM parsing, optimization, and execution.
- `pcc/codegen/c_codegen.py`
  Main semantic lowering logic. Most tricky C correctness bugs land here.
- `utils/fake_libc_include/`
  Fake libc headers. Bugs here usually look like host ABI or declaration mismatches.
- `tests/`
  Fast regression coverage. Add small tests here for every semantic fix.
- `projects/lua-5.5.0/`
  Real-program stress target. Very effective for catching interactions missed by unit tests.


## Source Collection Modes

The repository has multiple ways to compile projects. Be explicit about which one you are debugging.

- Single-file mode:
  Compile exactly one `.c` file.
- Merged directory mode:
  The default directory path behavior. It concatenates selected `.c` files into one large translation unit.
- `--separate-tus`:
  Compile each `.c` as its own translation unit, then link at the LLVM/module layer.
- `--sources-from-make GOAL`:
  Use `make -nB GOAL` to discover which `.c` files belong to a target.
  It can also recover common preprocessor flags from real compile commands, but
  only if the build system actually emits them. It cannot infer compatibility
  flags that exist only in documentation, headers, or repository conventions.

For Lua, these are the most useful entrypoints:

```bash
env -u LC_ALL uv run pcc --cpp-arg=-DLUA_USE_JUMPTABLE=0 --cpp-arg=-DLUA_NOBUILTIN projects/lua-5.5.0/onelua.c -- projects/lua-5.5.0/testes/math.lua
env -u LC_ALL uv run pcc --cpp-arg=-DLUA_USE_JUMPTABLE=0 --cpp-arg=-DLUA_NOBUILTIN --sources-from-make lua projects/lua-5.5.0 -- projects/lua-5.5.0/testes/math.lua
env -u LC_ALL uv run pcc --cpp-arg=-DLUA_USE_JUMPTABLE=0 --cpp-arg=-DLUA_NOBUILTIN --separate-tus --sources-from-make lua projects/lua-5.5.0 -- projects/lua-5.5.0/testes/math.lua
```


## Debugging Playbook

### 1. Make the failure deterministic

Do not start by reading all of `c_codegen.py`. First make the failure repeatable.

Examples:

- fix the random seed
- replace filesystem/time input with constants
- run with `-n0`
- isolate one test file instead of a whole suite

If the failure is random, your first job is to remove randomness from the reproduction, not to guess the cause.


### 2. Compare `pcc` against native from the same source

When the bug appears in a real program:

1. Compile the same source with `pcc`
2. Compile the same source with the system C compiler
3. Compare behavior, not assumptions

This separates "the program is odd" from "the compiler lowered it incorrectly".


### 3. Shrink the reproducer in stages

A productive sequence is:

1. failing integration test
2. smaller script or input
3. small C harness that calls the same internal code path
4. pure C expression or arithmetic reproducer

Do not stop at step 1 if the failure is deep in semantics. The time you spend shrinking the reproducer is usually returned many times over during diagnosis.


### 4. Test hypotheses by substitution, not only by inspection

If a large function is suspect:

- copy it into a temporary harness
- replace one helper at a time with the real implementation
- reintroduce branches incrementally

This is often faster than staring at 500 lines of codegen or IR.


### 5. Separate data-layout bugs from expression-semantics bugs

When a real program fails, two common classes are:

- ABI/layout bugs
  `sizeof`, `offsetof`, fake libc declarations, struct or union layout
- expression-semantics bugs
  signedness, promotions, comparisons, shifts, division/remainder, aggregate copy, control flow

If layout is suspicious, build a dedicated probe with `sizeof` and `offsetof` and compare native vs `pcc`. Once layout matches, move on.


### 6. Prefer downstream-sensitive regression tests

A good regression test does not just check "the bits look right right now". It checks an expression in a context where the next operation would be wrong if semantic metadata were lost.

Good examples:

- unsigned expression followed by `%` with a signed constant
- unsigned expression followed by `>>`
- unsigned expression used in `<`, `>`, `/`, `%`

This matters because LLVM uses the same integer types for signed and unsigned values. The compiler must preserve signedness intent itself.


## Signedness Model

This repository lowers both `int` and `unsigned int` to LLVM `i32`. Therefore signedness is tracked separately in `pcc/codegen/c_codegen.py`.

Important helpers:

- `_tag_unsigned`
- `_clear_unsigned`
- `_is_unsigned_val`
- `_convert_int_value`
- `_usual_arithmetic_conversion`
- `_shift_operand_conversion`

When you add or change an expression form, ask:

1. Does this produce an integer result?
2. If yes, should the result remain unsigned?
3. Will that result later feed `%`, `/`, `>>`, comparisons, or another arithmetic conversion?

The classic failure mode in this repository is:

- the immediate value bits are correct
- but the returned IR value is no longer marked unsigned
- so a later operator uses `sdiv`, `srem`, `ashr`, or signed comparison

This class of bug is easy to miss with toy tests and shows up quickly in Lua, libc-heavy code, and control-flow-heavy programs.


## Common Pitfalls

- Prefix operators on unsigned values:
  `++x` / `--x` must preserve unsignedness on the expression result.
- Bitwise operators on unsigned values:
  `&`, `|`, `^`, `~`, shifts, and compound assignments must preserve unsignedness where C requires it.
- Function-scope static arrays:
  `static const code lenfix[512] = { ... };` must become an internal global, not a stack allocation. If a returned pointer or cached table looks correct inside one helper but turns to garbage after the function returns, inspect block-scope `static` lowering first.
- Function-scope static incomplete arrays:
  `static const char my_version[] = "1.3.1";` must infer its top-level size from the initializer before the internal global is created. A zero-length `[0 x i8]` static local is a codegen bug, not a source quirk.
- Function-scope aggregate init-lists:
  `struct S s = {0};` and nested aggregate initializers must really zero and initialize the local object. If a real program shows impossible garbage in supposedly zero-initialized local state, inspect recursive local aggregate lowering before looking at the program logic.
- File-scope incomplete arrays:
  `extern int table[]; int table[] = { ... };` must end up as one real symbol. If IR contains both `@"table"` and `@"table.1"`, the compiler created a zero-length placeholder and then lost the real definition behind a renamed symbol.
- Standalone tag definitions:
  `struct S { ... };`, `union U { ... };`, and `enum E { ... };` with no declarator are type definitions, not object declarations. If a recursive type graph keeps one side opaque in IR, inspect `codegen_Decl()` before blaming the source program.
- Forward-declared named structs with bitfields:
  if `struct A` and `struct B` reference each other and one side contains bitfields, the final definition must reuse the existing identified tag type instead of inventing a fresh layout-only type. If `p->db == db` fails on obviously valid nested pointers, inspect named-struct finalization and tag reuse.
- Casted function-pointer globals:
  `static const entry table[] = { {(fnptr)target} };` must preserve the function address through the cast. Null-filled dispatch tables in VFS/syscall layers are usually constant-initializer bugs, not parser bugs.
- Assignment expressions:
  The stored value and the expression result are both semantically meaningful.
- Temporary probe files:
  Putting `foo_tmp.c` inside a project directory can accidentally change project collection behavior.
- Build configuration vs compiler compatibility:
  `--sources-from-make` can only recover flags that appear in real compile
  commands. If a project still needs explicit `--cpp-arg` values after make
  inference, first decide whether they are true build-configuration results
  that should come from configured build metadata, or temporary compiler
  compatibility flags that should remain explicit.
- Fake libc changes:
  These can fix or break real programs without touching parser or codegen.
- Darwin multi-TU MCJIT teardown:
  A large program can run correctly and still die later during llvmlite/LLVM cleanup. If the runtime output is correct but the host Python process crashes during GC or teardown, treat that as a lifecycle/isolation problem, not automatically a codegen bug.


## Testing Policy

For every semantic bug fix:

1. Add a focused regression test in `tests/`.
2. Confirm the original realistic reproducer is fixed.
3. Run the full suite before finishing.

Useful commands:

```bash
env -u LC_ALL uv run pytest tests/test_unsigned_loads.py -q -n0
env -u LC_ALL uv run pytest tests/test_lua.py -q -n0
env -u LC_ALL uv run pytest -q
```


## Definition of Done

Before you stop, confirm all of the following:

- the smallest reproducer passes
- the original integration scenario passes
- any temporary debug edits and probe files are removed
- a regression test exists
- the full suite is green


## Platform-Specific Gotchas (macOS)

- No `/dev/full` — Lua's `files.lua` tests `/dev/full` for write failure. On macOS this device doesn't exist. The test harness patches it out in a temporary copy.
- Makefile flags — Lua's Makefile uses `-DLUA_USE_LINUX` and `-Wl,-E`. On macOS, override with `MYCFLAGS=-std=c99 -DLUA_USE_MACOSX`, `MYLDFLAGS=`, `MYLIBS=`.
- `-ldl` — Not needed on macOS (dlopen is in libc), but harmless. The linker warns but still works.


## LLVM Version Mismatch

`llvmlite` bundles its own LLVM version, which may be newer than the system `clang`.

- Older repository paths wrote LLVM IR text to disk and then asked the system compiler to compile that IR. In that setup, LLVM O2 could emit attributes the system `clang` did not understand, such as `nuw`, `nneg`, `range()`, `initializes()`, and `dead_on_unwind`.
- The current system-link path does **not** rely on the system compiler parsing LLVM IR text. `run_translation_units_with_system_cc()` optimizes each module with llvmlite's LLVM and emits native object files directly, then links those objects with `cc`.
- The remaining limitation is not "no optimization". It is "no cross-translation-unit optimization" in the separate-TU path. Each TU gets optimized on its own before linking, but there is no LTO-style whole-program pass over the linked module set.
- If you ever reintroduce a text-IR handoff to the system compiler, keep the attribute-stripping warning in mind and centralize those rewrites in `postprocess_ir_text()`.


## IR Fix Centralization

All IR text-level fixes belong in `postprocess_ir_text()` in `pcc/codegen/c_codegen.py`, **not** in test helpers. This function is used by both the JIT path (`evaluate()`) and the test compilation path. Fixes that only exist in test code will not help `uv run pcc`.

Current fixes in `postprocess_ir_text`:
- `bitcast int → ptr` → `inttoptr`
- Python `<ir.Constant>` repr leak → `zeroinitializer`
- `alloca/load/store void` → removed
- duplicate switch case values → deduplicated
- dead code after terminators → removed
- consecutive empty labels → `br`/`unreachable` inserted


## Packaging

- The wheel must include `utils/fake_libc_include/`. Without it, preprocessing fails on install.
- PyPI package name is `python-cc` (not `pcc`, which is taken).
- GitHub Actions uses Trusted Publisher (OIDC). The PyPI project name must match `pyproject.toml`'s `name` field exactly.


## Additional Reading

- `docs/investigations/lua-sort-random-pivot-signedness.md`
  Detailed report for a representative real-world debugging session that started as a flaky Lua `sort.lua` failure and ended as an unsigned-expression codegen fix.
- `docs/investigations/pcre-op-lengths-incomplete-array-binding.md`
  Detailed report for a PCRE failure that first looked like an MCJIT/link bottleneck and turned out to be a file-scope incomplete-array binding bug in global initializer lowering.
- `docs/investigations/zlib-integration-static-local-arrays-and-layout.md`
  Detailed report for a zlib integration session that exposed three different bug classes in sequence: enum layout, block-scope static arrays, and function-scope static incomplete arrays.
- `docs/investigations/sqlite-integration-vfs-init-and-mcjit-lifecycle.md`
  Detailed report for a SQLite integration session that exposed three different layers in sequence: casted syscall-table pointers, broken local aggregate zero-initialization, and Darwin MCJIT teardown instability.
- `docs/investigations/sqlite-forward-declared-bitfield-struct-tags.md`
  Detailed report for a later SQLite integration failure that first looked like a runtime logic bug and turned out to be incorrect handling of standalone tag definitions and forward-declared named bitfield structs in the type system.
- `docs/investigations/make-derived-cpp-flags-vs-explicit-project-config.md`
  Detailed report on why make-derived preprocessor inference covered PCRE but not Lua, zlib, or SQLite, and when explicit `--cpp-arg` values are the right interface instead of compiler-side project detection.
