# pcc

[![PyPI](https://img.shields.io/pypi/v/python-cc)](https://pypi.org/project/python-cc/)
[![Python](https://img.shields.io/pypi/pyversions/python-cc)](https://pypi.org/project/python-cc/)
[![License](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

A Python-implemented LLVM compiler toolchain for **C** and an experimental native frontend for **Python**.

`pcc` started as a compiler experiment and has grown into a large repository with:

- a real C compilation pipeline
- an experimental Python-to-native pipeline
- project collection/build orchestration for multi-file codebases
- a multi-tier optimization/pass framework
- compile caching and system-link workflows
- large integration targets such as Lua, SQLite, PostgreSQL `libpq`, nginx, zlib, lz4, zstd, PCRE, OpenSSL, and readline
- thousands of regression and corpus tests against native toolchains

The C frontend is the most mature subsystem today. The Python frontend is actively evolving, but already supports typed-native code, CPython fallback for imported modules, and direct C interop through `pcc.extern`.

---

## Why `pcc`

`pcc` is designed for people who want more than a toy parser but still want a compiler they can read, debug, and extend quickly.

### What makes it different

- **Compiler implementation in Python** for fast iteration
- **LLVM backend** for object emission, optimization, and native execution
- **Real-project workflows**: single-file, merged-directory, separate-TU, make-derived source selection, dependency builds, and host linking
- **Compiler as a library**: use `CEvaluator`, `build(...)`, or `module(...)`
- **Large-scale validation**: Lua, SQLite, PostgreSQL, nginx, GCC torture, Clang C tests, and more
- **A pass framework with measurable tiers** instead of treating optimization as a single opaque backend step

---

## Project status at a glance

| Area | Status | Notes |
|---|---|---|
| C frontend | advanced | real-project tested, most mature part of the repo |
| Python frontend | experimental | typed-native path + CPython fallback + extern-C bridge |
| Build orchestration | strong | directory mode, `--separate-tus`, `--sources-from-make`, `--depends-on`, `--system-link` |
| Validation | broad | 4900+ tests passing across unit, corpus, and integration coverage |
| Python corpus | active | 108/139 end-to-end corpus tests passing as of 2026-04-20 |
| Performance tooling | mature | microbenchmark matrix + standalone benchmark suite + pass attribution |

---

## Bootstrap / self-host status

`pcc` is actively working toward a three-stage self-host bootstrap for its
Python frontend:

1. CPython-hosted `pcc` builds `pcc1`
2. `pcc1` builds `pcc2`
3. `pcc2` builds `pcc3`

Current verified progress in this repository:

- the `llvm_capi` text-first builder / binding path is landed and covered by
  dedicated parity and end-to-end tests
- experimental multi-file Python compilation is landed for native sibling
  imports and shared cross-module type inference
- stage 1 can now produce a `pcc1` executable in the supported development
  environment

Current boundary:

- the bootstrap path is not closed yet; stage 2 / stage 3 still need
  reliability work
- some bootstrap flows may still link `libpython`, so the pure self-host
  target is not claimed yet

For milestone details, see:

- [python-frontend-plan.md](docs/plans/python-frontend-plan.md)
- [p6c6-bootstrap-spike-report.md](docs/plans/p6c6-bootstrap-spike-report.md)
- [llvmcapi-beta4-backlog.md](docs/plans/llvmcapi-beta4-backlog.md)

---

## Quick start

### Install

```bash
pip install python-cc
```

For repository development:

```bash
git clone https://github.com/jiamo/pcc
cd pcc
uv sync
```

### Compile and run C

```bash
pcc hello.c
pcc myproject/
pcc --llvmdump hello.c
pcc myproject/ -- arg1 arg2
```

### Compile and run Python

```bash
pcc hello.py
pcc hello.py -o hello
pcc hello.py --emit-llvm
```

### Use the evaluator directly

```python
from pcc.evaluater.c_evaluator import CEvaluator

cc = CEvaluator()

cc.evaluate(r'''
#include <stdio.h>
int main(void) {
    printf("hello from pcc\\n");
    return 0;
}
''')

result = cc.evaluate(r'''
int add(int a, int b) { return a + b; }
''', entry="add", args=[3, 7])
print(result)  # 10
```

### Use C from Python with `module(...)`

```c
// arith.c
int add(int a, int b) { return a + b; }
int mul(int a, int b) { return a * b; }
```

```python
from pcc import module

m = module("arith.c")
print(m.add(3, 4))
print(m.mul(5, 6))
print(m.__pcc_artifact__.exports)
```

---

## System architecture

`pcc` is organized as a layered compiler platform rather than a single monolithic script.

```text
CLI / API
  -> project collection
  -> C frontend or Python frontend
  -> pass framework
  -> LLVM optimization / emission
  -> MCJIT, object emission, or system-link execution
  -> tests / integrations / benchmarks
```

### Core layers

| Layer | Main paths | Responsibility |
|---|---|---|
| CLI and public API | `pcc/pcc.py`, `pcc/api.py` | end-user commands and embeddable build/module APIs |
| Project collection | `pcc/project.py` | collect translation units, infer source sets from make, handle dependencies |
| C frontend | `pcc/evaluater/`, `pcc/codegen/`, `pcc/parse/`, `pcc/lex/` | preprocess, parse, analyze, and lower C to LLVM IR |
| Pass framework | `pcc/passes/` | HighTier / MidTier / LowTier / BackendTier optimization plumbing |
| Python frontend | `pcc/py_frontend/`, `pcc/py_runtime/`, `pcc/extern/` | parse typed Python, infer types, emit LLVM IR, bridge to runtime or CPython |
| Validation | `tests/`, `projects/`, `bench/`, `benchmarks/` | correctness, integration, and performance coverage |

Read the full architecture guide here:

- [docs/system-architecture.md](docs/system-architecture.md)

---

## Build modes for real projects

`pcc` supports several compilation models because large C projects are not all built the same way.

| Mode | Command shape | Best for |
|---|---|---|
| Single file | `pcc hello.c` | small programs, reproducers |
| Directory merge | `pcc myproject/` | quick project experiments |
| Separate translation units | `pcc --separate-tus myproject/` | more realistic C semantics |
| Make-derived source set | `pcc --sources-from-make lua projects/lua-5.5.0` | upstream projects with real build logic |
| Driver + dependency project | `pcc --depends-on projects/pcre-8.45=libpcre.la projects/test_pcre_main.c` | library integration testing |
| Host linking | `pcc --system-link ...` | large binaries and realistic final link behavior |

---

## C frontend capabilities

The C pipeline is built on preprocessing + parsing + semantic lowering to LLVM IR.

### Highlights

- C99-oriented frontend with support for the features needed by real projects in this repo
- multi-file compilation in merged or separate-TU modes
- explicit signedness tracking on top of LLVM integer types
- compile-time constant evaluation and runtime lowering handled as separate semantic layers
- translation-unit compile cache
- object, assembly, and LLVM IR emission
- MCJIT execution for evaluator workflows and system-link workflows for larger binaries

### Public C APIs

#### `CEvaluator`

Use the compiler as an in-process evaluator for C source strings.

#### `build(...)`

```python
from pcc.api import build

artifact = build(
    ["src/main.c", "src/util.c"],
    include_dirs=["include"],
    libs=["m"],
    optimize=2,
    kind="exe",
)
print(artifact.output_path)
```

#### `module(...)`

Compile one or more C files into a shared library and load it with `ctypes`.

```python
from pcc import module

m = module(["src/a.c", "src/b.c"], include_dirs=["include"], libs=["z"])
print(m.__pcc_artifact__.pass_report)
```

---

## Python frontend

`pcc hello.py` uses an experimental Python frontend that lowers Python through LLVM as well.

### What works today

- typed Python lowered directly to native LLVM IR
- no PyObject layer for pure typed/native programs
- CPython C-API fallback when `import` is used
- direct C calls through `pcc.extern`
- classes, exceptions, dunders, and selected stdlib coverage in the current corpus

### Python path selection

For `.py` inputs, the main CLI surface is intentionally small. The Python path
dispatch happens before the C-specific validation, so flags such as
`--jobs`, `--separate-tus`, `--target`, `--system-link`, and `--backend`
do not currently change Python compilation behavior.

#### Main `pcc foo.py` controls

| Surface | Choices | Effect | Notes |
|---|---|---|---|
| invocation mode | `pcc foo.py` | compile to a temporary native executable and run it immediately | arguments after `--` are forwarded to the produced program |
| output mode | `pcc foo.py -o prog` | compile and save a native executable to `prog` | does not auto-run after build |
| IR mode | `pcc foo.py --emit-llvm` | emit LLVM IR only and stop before linking | bare form writes `<stem>.ll`; `-o` can override the output path |
| logging | `pcc foo.py --verbose` | print parse / type inference / codegen / link timings | Python pipeline only |

#### Automatic routing inside the Python pipeline

| Trigger | Route selected | Result |
|---|---|---|
| no `import` and the code stays in the typed/native subset | typed-native path | lowers directly to LLVM IR and can stay libpython-free |
| any `import` is present | CPython fallback path | links `libpython` and routes imported values through CPython C-API shims |
| default parser configuration | native parser + lift | uses `pcc.parse.py_parse` + `pcc.parse.py_lift` |

#### Internal / debug toggles for `.py`

| Env var | Choices | Effect |
|---|---|---|
| `PCC_USE_CPYTHON_AST` | unset / `1` | when set to `1`, opts out of the native Python parser and uses the legacy stdlib-`ast` parser path |
| `PCC_USE_LLVMLITE` | unset / `1` | when set to `1`, forces all subsystems, including Python codegen, back to `llvmlite` |
| `PCC_USE_LLVMLITE_PY` | unset / `1` | when set to `1`, forces only the Python frontend codegen path back to `llvmlite` |

#### Experimental multi-file Python entry

| Surface | Choices | Effect | Notes |
|---|---|---|---|
| command | `python scripts/pcc_multi.py` | compile several `.py` files into one native output | separate from the main `pcc` CLI |
| required flags | `--entry`, `--out` | choose the entry module and output path | `--entry` uses dotted module names |
| optional flags | `--emit-llvm`, `--verbose` | emit combined LLVM IR or print timings | mirrors the single-file Python pipeline options |
| source mapping syntax | `path.py` or `path.py=module.name` | lets callers assign explicit dotted module names | useful for `__main__.py`, `__init__.py`, and relative imports |
| current limitation | unresolved imports / full bootstrap closure | known native sibling function/class imports are supported, but unresolved imports may still fall back to the CPython import path and pull `libpython`; stage 2/3 bootstrap is not closed yet | bootstrap reliability work is still in progress |

#### Related: `.c` path environment controls

For `.c` inputs, the main execution mode is still selected by CLI flags such as
`--separate-tus`, `--system-link`, `--sources-from-make`, `--depends-on`,
`--target`, `--emit-obj`, `--emit-asm`, and `--emit-llvm`. Environment
variables mainly affect backend selection, parser choice, caching, LLVM
pipeline behavior, and diagnostics.

Common controls:

| Env var | Effect | Notes |
|---|---|---|
| `PCC_BACKEND` | choose the C backend (`llvm`, `llvm_capi`, `self`) | same surface as `--backend` |
| `PCC_USE_PLY_C_PARSER=1` | opt out of the native C parser and use the legacy PLY parser | parser compatibility / regression isolation |
| `PCC_PLY_CACHE_DIR` | override the PLY lextab / yacctab cache directory | only matters on the legacy PLY parser path |
| `PCC_COMPILE_CACHE_DIR` | override the translation-unit compile cache directory | defaults under `~/.cache` or `XDG_CACHE_HOME` |
| `PCC_DISABLE_COMPILE_CACHE=1` | disable the translation-unit compile cache | useful for debugging cache-key issues |
| `PCC_DISABLE_PASSES` | disable named managed passes | comma-separated pass names |
| `PCC_LLVM_DISABLE_PASSES` | disable named concrete LLVM passes | comma-separated pass names |
| `PCC_CHEAP_LLVM_PIPELINE` | enable the cheap LLVM pass bundle, or provide a custom cheap-pass list | affects the low-opt / O0-style backend path |
| `PCC_LLVM_PIPELINE` | run an external text LLVM pipeline | `1`, `true`, or `default` selects the default pipeline; custom specs are also accepted |
| `PCC_LLVM_OPT_BIN` | point at a matching LLVM `opt` binary | required when using the external LLVM text pipeline or LLVM pass selection that needs `opt` |
| `PCC_LIBLLVM_PATH` | point at `libLLVM-C` explicitly | used by the native LLVM-C binding path |
| `PCC_USE_LLVMLITE=1` | force all subsystems back to `llvmlite` | reverse-opt-out from the native LLVM-C path |
| `PCC_USE_LLVMLITE_C=1` | force only the C codegen path back to `llvmlite` | useful for C-only regression isolation |
| `PCC_USE_LLVMLITE_PASSES=1` | force only the pass layer back to `llvmlite` | useful for pass-only regression isolation |

Diagnostics:

| Env var | Effect | Notes |
|---|---|---|
| `PCC_DUMP_BAD_IR=/path` | dump invalid or unparsable LLVM IR to disk when LLVM parsing fails | writes per-TU `.ll` snapshots for inspection |
| `PCC_DEBUG_PHI_TYPES=/path` | append SSA phi type-mismatch diagnostics to a log file | supports parallel builds by appending |
| `PCC_DEBUG_SSA_LOWER_FAIL=1` | print traceback when SSA lowering fails and falls back | diagnostic only; does not change correctness behavior |

### Example: typed-native Python

```python
def fib(n: int) -> int:
    if n < 2:
        return n
    return fib(n - 1) + fib(n - 2)

def main() -> None:
    for i in range(10):
        print(fib(i))

main()
```

### Example: pure native FFI with `pcc.extern`

```python
from pcc.extern import extern, c_int

getpid = extern("getpid", (), c_int)

pid: int = getpid()
print(pid)
```

### Python corpus status

As of 2026-04-20, `tests/py_corpus/` has **108/139** passing end-to-end cases:

| Phase | Pass | Coverage |
|---|---:|---|
| phase1 | 23/25 | typed Python MVP |
| phase2 | 15/30 | core Python data semantics |
| phase3 | 31/45 | classes, MRO, dunders, exceptions |
| phase4 | 37/37 | CPython fallback path |
| phase6c | 2/2 | extern-C direct calls |

Related docs:

- [docs/python-tutorial.md](docs/python-tutorial.md)
- [docs/python-howto.md](docs/python-howto.md)
- [docs/python-limitations.md](docs/python-limitations.md)
- [docs/python-scorecard.md](docs/python-scorecard.md)
- [docs/changelog.md](docs/changelog.md)

---

## Real-world integration coverage

One of `pcc`'s strengths is that correctness work is validated against real software, not just toy examples.

| Integration | Representative path | Typical workflow |
|---|---|---|
| Lua 5.5.0 | `projects/lua-5.5.0/` | single-file amalgamation or make-derived source list |
| PCRE 8.45 | `projects/pcre-8.45/` + `projects/test_pcre_main.c` | driver + dependency-project build |
| zlib 1.3.1 | `projects/zlib-1.3.1/` + `projects/test_zlib_main.c` | make-derived dependency build |
| SQLite 3.49.1 | `projects/sqlite-amalgamation-3490100/sqlite3.c` + `projects/test_sqlite_main.c` | amalgamation + driver |
| PostgreSQL 17.4 `libpq` | `projects/postgresql-17.4/` + `projects/test_postgres_main.c` | make-goal discovery + support archives |
| nginx 1.28.3 | `projects/nginx-1.28.3/` | compile all project sources and system-link |
| Other libraries | `tests/test_lz4.py`, `tests/test_zstd.py`, `tests/test_openssl.py`, `tests/test_readline.py` | focused integration suites |

### Representative commands

#### Lua 5.5.0

```bash
uv run pcc \
  --cpp-arg=-DLUA_USE_JUMPTABLE=0 \
  --cpp-arg=-DLUA_NOBUILTIN \
  projects/lua-5.5.0/onelua.c -- projects/lua-5.5.0/testes/math.lua
```

```bash
uv run pcc \
  --cpp-arg=-DLUA_USE_JUMPTABLE=0 \
  --cpp-arg=-DLUA_NOBUILTIN \
  --separate-tus --sources-from-make lua --jobs 2 \
  projects/lua-5.5.0 -- projects/lua-5.5.0/testes/math.lua
```

#### PCRE 8.45

```bash
uv run pcc \
  --cpp-arg=-DHAVE_CONFIG_H \
  --depends-on projects/pcre-8.45=libpcre.la \
  projects/test_pcre_main.c
```

#### zlib 1.3.1

```bash
uv run pcc \
  --cpp-arg=-DHAVE_UNISTD_H \
  --cpp-arg=-DHAVE_STDARG_H \
  --cpp-arg=-U__ARM_FEATURE_CRC32 \
  --depends-on projects/zlib-1.3.1=libz.a \
  projects/test_zlib_main.c
```

#### SQLite 3.49.1

```bash
uv run pcc \
  --cpp-arg=-U__APPLE__ \
  --cpp-arg=-U__MACH__ \
  --cpp-arg=-U__DARWIN__ \
  --cpp-arg=-DSQLITE_THREADSAFE=0 \
  --cpp-arg=-DSQLITE_OMIT_WAL=1 \
  --cpp-arg=-DSQLITE_MAX_MMAP_SIZE=0 \
  --depends-on projects/sqlite-amalgamation-3490100/sqlite3.c \
  projects/test_sqlite_main.c /tmp/pcc_sqlite.db
```

#### PostgreSQL 17.4 `libpq`

```bash
uv run pcc --system-link --jobs 2 \
  --depends-on projects/postgresql-17.4/src/interfaces/libpq=libpq.a \
  --depends-on projects/zlib-1.3.1=libz.a \
  --link-arg=projects/postgresql-17.4/src/common/libpgcommon_shlib.a \
  --link-arg=projects/postgresql-17.4/src/port/libpgport_shlib.a \
  --link-arg=-lm \
  projects/test_postgres_main.c
```

#### nginx 1.28.3

```bash
cd projects/nginx-1.28.3 && ./configure --with-cc-opt=-Wno-error && cd ../..
uv run pytest tests/test_nginx.py -v
```

---

## Performance and optimization

`pcc` keeps a substantial amount of optimization and benchmark infrastructure in-tree.

### Benchmark harnesses

- `bench/bench.py` — 80-case microbenchmark matrix, pass ablations, clean exec-only summaries
- `benchmarks/run_benchmarks.py` — 46 standalone C programs, compile/exec/total timings
- `benchmarks/quantify_passes.py` — aggregate pass-cost attribution

### Current headline numbers

As documented in the benchmark sections of the repo, recent one-run macOS results include:

- **80-case microbenchmark**, `pcc -O2` vs `clang -O2`: compile `1.12x`, exec `1.00x`, total `1.08x`, with `78/80` matched and clean
- **46-file standalone suite**, `pcc/clang` geomeans at `O2`: compile `3.53x`, exec `1.00x`, total `2.00x`
- **pcc-only O2/O0** on the 46-file suite: compile `1.02x`, exec `0.41x`, total `0.71x`

Interpretation:

- `pcc` is already near `clang` at runtime on the microbenchmark suite once LLVM `-O2` is enabled
- compile-time cost is still meaningfully higher on the standalone suite
- the pass framework is measured separately from LLVM backend optimization rather than being conflated with it

### Reproduce benchmark runs

```bash
uv run python bench/bench.py --opt-level 1 --opt-level 2 --opt-level 3 --runs 1 --top-passes 12
uv run python bench/bench.py --opt-level 1 --opt-level 2 --opt-level 3 --runs 1 --group-matrix
uv run python bench/bench.py --opt-level 0 --opt-level 2 --runs 1 --top-passes 12
uv run python benchmarks/run_benchmarks.py --opt-level 0 --opt-level 2 --runs 1
uv run python benchmarks/run_benchmarks.py --opt-level 1 --opt-level 2 --opt-level 3 --runs 1
uv run python benchmarks/quantify_passes.py --top 12
```

---

## Testing and quality gates

`pcc` is validated with both focused regressions and large external suites.

### Major suites

| Suite | Scale | Purpose |
|---|---:|---|
| `tests/test_c_testsuite.py` | 220 cases | C conformance-style corpus |
| `tests/test_clang_c.py` | 161 cases | Clang-derived C coverage |
| `tests/test_gcc_torture_execute.py` | 1684 cases | GCC torture runtime stress |
| `tests/test_lua.py` | 130+ Lua scripts / modes | real interpreter integration |
| `tests/test_sqlite.py`, `tests/test_postgres.py`, `tests/test_nginx.py` | project-scale | large software validation |
| `tests/py_corpus/` | 139 programs | Python frontend end-to-end corpus |

### Common commands

```bash
uv run pytest                # default suite (excludes expensive integration tests)
uv run pytest -m integration # expensive end-to-end integration suite
uv run pytest tests/test_lua.py -q -n0
uv run pytest tests/test_sqlite.py -q -n0
uv run pytest tests/test_postgres.py -q -n0
uv run pytest tests/test_nginx.py -q -n0
```

---

## Compile cache

`pcc` keeps a translation-unit compile cache on disk by default.

### CLI

```bash
uv run pcc hello.c
uv run pcc --cache-dir .pcc-cache hello.c
uv run pcc --no-cache hello.c
```

### Library usage

```python
from pcc.evaluater.c_evaluator import CEvaluator

ev = CEvaluator()
ev.evaluate("int main(void) { return 0; }\n")
ev.evaluate("int main(void) { return 0; }\n")  # cache hit
```

### Cache characteristics

- keyed from source/preprocess context and compiler fingerprint inputs
- reused across evaluator runs, translation-unit compilation, and CLI workflows
- useful for repeated large-project iteration
- controlled via `--cache-dir`, `--no-cache`, and `PCC_COMPILE_CACHE_DIR`

---

## Documentation map

| Topic | Path |
|---|---|
| System architecture | [docs/system-architecture.md](docs/system-architecture.md) |
| Python tutorial | [docs/python-tutorial.md](docs/python-tutorial.md) |
| Python how-to | [docs/python-howto.md](docs/python-howto.md) |
| Python limitations | [docs/python-limitations.md](docs/python-limitations.md) |
| Python scorecard | [docs/python-scorecard.md](docs/python-scorecard.md) |
| Changelog | [docs/changelog.md](docs/changelog.md) |
| Investigation reports | [docs/investigations/](docs/investigations/) |
| Design plans | [docs/plans/](docs/plans/) |
| Contributor/agent notes | [AGENTS.md](AGENTS.md) |

Recommended deep-dive investigation docs:

- [docs/investigations/lua-sort-random-pivot-signedness.md](docs/investigations/lua-sort-random-pivot-signedness.md)
- [docs/investigations/pcre-op-lengths-incomplete-array-binding.md](docs/investigations/pcre-op-lengths-incomplete-array-binding.md)
- [docs/investigations/zlib-integration-static-local-arrays-and-layout.md](docs/investigations/zlib-integration-static-local-arrays-and-layout.md)
- [docs/investigations/sqlite-integration-vfs-init-and-mcjit-lifecycle.md](docs/investigations/sqlite-integration-vfs-init-and-mcjit-lifecycle.md)
- [docs/investigations/sqlite-forward-declared-bitfield-struct-tags.md](docs/investigations/sqlite-forward-declared-bitfield-struct-tags.md)
- [docs/investigations/nbody-shootout-fp-contract-and-vectorization.md](docs/investigations/nbody-shootout-fp-contract-and-vectorization.md)

---

## Repository map

| Path | Role |
|---|---|
| `pcc/pcc.py` | CLI entrypoint |
| `pcc/api.py` | build/module APIs |
| `pcc/project.py` | source collection and build orchestration |
| `pcc/evaluater/c_evaluator.py` | C compilation/execution coordinator |
| `pcc/codegen/c_codegen.py` | C semantic lowering |
| `pcc/passes/` | optimization framework |
| `pcc/py_frontend/` | Python frontend |
| `pcc/py_runtime/` | Python runtime archive |
| `pcc/extern/` | extern-C bridge |
| `utils/fake_libc_include/` | fake libc headers |
| `tests/` | correctness and integration suites |
| `projects/` | third-party software used as stress targets |
| `bench/`, `benchmarks/` | performance tooling |

---

## Supported C feature set

`pcc` supports the C features needed by the real-world integrations in this repo, including:

- scalar types, pointers, arrays, structs, unions, enums, typedefs, and function pointers
- arithmetic, comparison, casts, bitwise ops, shifts, and control flow
- variadic functions
- preprocessing with macro expansion and conditional compilation
- multi-file builds and project-style source collection

The practical standard here is not “can it parse a feature in isolation”, but “can it preserve the right semantics once the code is lowered to LLVM IR and exercised by real software”.

---

## Development

Requires Python 3.13+ and [uv](https://docs.astral.sh/uv/).

```bash
uv sync
uv run pytest
uv run pytest -m integration
```

If you are contributing compiler changes, read [AGENTS.md](AGENTS.md) first. It documents the repository's debugging playbook, testing policy, C signedness model, project workflows, and definition of done.

---

## License

MIT. See [LICENSE](LICENSE).
