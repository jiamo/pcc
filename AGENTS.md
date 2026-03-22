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
- For reproducible runs, prefer unsetting `LC_ALL`:

```bash
env -u LC_ALL uv run pytest -q
env -u LC_ALL uv run python - <<'PY'
print("ok")
PY
env -u LC_ALL uv run pcc hello.c
```

- While debugging one failure, prefer `-n0` so xdist does not hide ordering or temp-file problems.
- Use `rg` / `rg --files` for source discovery.
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

For Lua, these are the most useful entrypoints:

```bash
env -u LC_ALL uv run pcc projects/lua-5.5.0/onelua.c -- projects/lua-5.5.0/testes/math.lua
env -u LC_ALL uv run pcc --sources-from-make lua projects/lua-5.5.0 -- projects/lua-5.5.0/testes/math.lua
env -u LC_ALL uv run pcc --separate-tus --sources-from-make lua projects/lua-5.5.0 -- projects/lua-5.5.0/testes/math.lua
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
- Assignment expressions:
  The stored value and the expression result are both semantically meaningful.
- Temporary probe files:
  Putting `foo_tmp.c` inside a project directory can accidentally change project collection behavior.
- Fake libc changes:
  These can fix or break real programs without touching parser or codegen.


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

`llvmlite` bundles its own LLVM version, which may be newer than the system `clang`. This matters when pcc writes IR to a file and compiles with `cc -c`:

- LLVM O2 optimizer can emit attributes the system clang doesn't understand: `nuw`, `nneg`, `range()`, `initializes()`, `dead_on_unwind`.
- For this reason, the test path currently does **not** run LLVM optimization passes. The JIT path (via `evaluate()`) does run O2 and works fine because llvmlite's own LLVM parses its own output.
- If you need to compile optimized IR with the system compiler, strip these attributes with regex post-processing.


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
