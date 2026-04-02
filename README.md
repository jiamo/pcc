Pcc
====================

What is this?
--------------------
Pcc is a C compiler written in Python, built on cpp + ply + pycparser + llvmlite + llvm.
Run C programs like Python scripts: `pcc test.c`. Powerful enough to compile and run real-world C projects including Lua 5.5.0, SQLite, PostgreSQL (libpq), nginx, pcre, zlib, lz4, zstd, openssl, and readline — with 4900+ tests passing (including 220 c-testsuite, 161 clang C, and 1684 GCC torture conformance cases).

Inspired by: https://github.com/eliben/pykaleidoscope.

Notice
--------------------
1. Some code skeleton comes from pykaleidoscope.
2. ply and pycparser are embedded into this project for debug use.

Install
--------------------

```bash
pip install python-cc
```

This gives you the `pcc` command:

```bash
pcc hello.c                        # compile and run
pcc myproject/                     # compile all .c files in directory
pcc --llvmdump test.c              # dump LLVM IR
pcc myproject/ -- arg1 arg2        # pass args to compiled program
```

Use as a Python library:

```python
from pcc.evaluater.c_evaluator import CEvaluator

pcc = CEvaluator()

# Run main()
pcc.evaluate(r'''
#include <stdio.h>
int main() { printf("Hello from pcc!\n"); return 0; }
''')

# Call any C function directly
ret = pcc.evaluate(r'''
int add(int a, int b) { return a + b; }
''', entry="add", args=[3, 7])
print(ret)  # 10
```

Development
--------------------

Requires Python 3.13+ and [uv](https://docs.astral.sh/uv/).

```bash
uv sync          # install dependencies
uv run pytest    # run all 4900+ tests
```

Compile Cache
--------------------

`pcc` keeps a translation-unit compile cache on disk by default.

CLI:

```bash
uv run pcc hello.c
uv run pcc --cache-dir .pcc-cache hello.c
uv run pcc --no-cache hello.c
```

Library:

```python
from pcc.evaluater.c_evaluator import CEvaluator

evaluator = CEvaluator()
evaluator.evaluate("int main(void) { return 0; }\n")
evaluator.evaluate("int main(void) { return 0; }\n")  # hits cache
```

Notes:

- the cache key is based on the preprocessed source plus the active compiler implementation fingerprint
- unchanged translation units are reused across repeated `evaluate(...)`, `evaluate_translation_units(...)`, `compile_translation_units(...)`, and CLI runs
- only dirty translation units are recompiled in multi-TU builds
- set `PCC_COMPILE_CACHE_DIR` to override the default cache location
- set `PCC_DISABLE_COMPILE_CACHE=1` or pass `--no-cache` to disable it

Multi-file projects: put `.c` and `.h` files in a directory, one `.c` must contain `main()`. Pcc auto-discovers all `.c` files, merges them, and compiles.

Third-Party Project Integrations
--------------------

For real library integrations in `projects/`, the dependency boundary is explicit:

- each integration keeps a small driver such as `projects/test_pcre_main.c` or `projects/test_postgres_main.c`
- the library sources come from that project's own source tree under `projects/<name>/`
- `--depends-on PATH[=GOAL]` and `--sources-from-make GOAL` only collect sources and CPP args from the named dependency path
- `pcc` does not implicitly borrow `.c` files or link libraries from unrelated project trees

So for example:

- PCRE uses `projects/pcre-8.45/` plus `projects/test_pcre_main.c`
- SQLite uses `projects/sqlite-amalgamation-3490100/sqlite3.c` plus `projects/test_sqlite_main.c`
- PostgreSQL uses `projects/postgresql-17.4/` plus `projects/test_postgres_main.c` / `projects/test_postgres_query_main.c`

If a project needs extra native support archives at runtime, those are built from the same source tree and called out explicitly in the tests.

Compiling Lua 5.5.0
--------------------

Pcc can compile the entire [Lua 5.5.0](https://github.com/lua/lua) interpreter (~30k lines of C) and run Lua scripts directly.

For one representative script (`math.lua`), these are the recommended entrypoints:

```bash
git clone https://github.com/jiamo/pcc && cd pcc
uv sync

# 1) Lua's official amalgamation build
uv run pcc \
  --cpp-arg=-DLUA_USE_JUMPTABLE=0 \
  --cpp-arg=-DLUA_NOBUILTIN \
  projects/lua-5.5.0/onelua.c -- projects/lua-5.5.0/testes/math.lua

# 2) Follow the same source list as Lua's makefile `lua` target
uv run pcc \
  --cpp-arg=-DLUA_USE_JUMPTABLE=0 \
  --cpp-arg=-DLUA_NOBUILTIN \
  --sources-from-make lua projects/lua-5.5.0 -- projects/lua-5.5.0/testes/math.lua

# 3) Same make-derived source list, but with normal multi-file C semantics
uv run pcc \
  --cpp-arg=-DLUA_USE_JUMPTABLE=0 \
  --cpp-arg=-DLUA_NOBUILTIN \
  --separate-tus --sources-from-make lua --jobs 2 projects/lua-5.5.0 -- projects/lua-5.5.0/testes/math.lua

# Run all 130+ Lua tests (pcc vs native, pcc vs makefile, makefile baseline)
uv run pytest tests/test_lua.py -v
```

```
projects/lua-5.5.0/         - canonical Lua 5.5.0 source tree, includes onelua.c and the standard multi-file sources
projects/lua-5.5.0/testes/  - Lua test suite
```

Summary:

- `projects/lua-5.5.0/onelua.c`
  Use this when you want Lua's official single-file amalgamation build.
- `--sources-from-make lua`
  Use this when you want `pcc` to follow the same source list as Lua's `lua` make target.
- `--separate-tus --sources-from-make lua --jobs 2`
  Use this when you want normal multi-file C semantics plus parallel TU compilation.
- Lua also needs `--cpp-arg=-DLUA_USE_JUMPTABLE=0 --cpp-arg=-DLUA_NOBUILTIN` when built through `pcc`; keep those choices explicit instead of relying on compiler-side project detection.
- Do not use raw `projects/lua-5.5.0` directory mode for Lua.
  The canonical tree contains both `onelua.c` and the individual source files, so naive directory collection produces duplicate-definition conflicts.

### Test Structure

Tests in `tests/test_lua.py` compare three builds:

| Build | Method |
|-------|--------|
| **pcc** | `onelua.c` → pcc preprocess/parse/codegen → LLVM IR → cc compile+link |
| **native** | `onelua.c` → `cc -O0` single-file compile |
| **makefile** | `make` with project Makefile (separate compilation of each .c, static lib + link) |

```bash
# Run all Lua tests (~25s with auto workers)
uv run pytest tests/test_lua.py -v

# Individual file compilation through pcc pipeline
uv run pytest tests/test_lua.py::test_lua_source_compile -v
# pcc vs native (same onelua.c, test pcc as C compiler)
uv run pytest tests/test_lua.py::test_pcc_runtime_matches_native -v
# pcc vs Makefile-built lua (official reference)
uv run pytest tests/test_lua.py::test_pcc_runtime_matches_makefile -v
# Lua test suite with Makefile-built binary (baseline)
uv run pytest tests/test_lua.py::test_makefile_lua_test_suite -v
```

Note: `heavy.lua` is excluded from automated tests (runs ~2 min+, may timeout). Run manually:
```bash
# Build Makefile lua, then run heavy.lua directly
cd projects/lua-5.5.0 && make CC=cc CWARNS= MYCFLAGS="-std=c99 -DLUA_USE_MACOSX" MYLDFLAGS= MYLIBS=
./lua testes/heavy.lua
```

Compiling PCRE 8.45
--------------------

`pcc` also supports a "main file + dependency project" workflow through
`--depends-on PATH[=GOAL]`.

For PCRE, the test driver lives at `projects/test_pcre_main.c`, while the
library sources remain under `projects/pcre-8.45/`.

```bash
# Build the PCRE sources selected by the `libpcre.la` make target with the test main
uv run pcc \
  --cpp-arg=-DHAVE_CONFIG_H \
  --depends-on projects/pcre-8.45=libpcre.la \
  projects/test_pcre_main.c

# The explicit separate-TU form is equivalent here
uv run pcc \
  --cpp-arg=-DHAVE_CONFIG_H \
  --separate-tus \
  --depends-on projects/pcre-8.45=libpcre.la \
  projects/test_pcre_main.c
```

Use `PATH=GOAL` when the dependency directory contains more `.c` files than the
real target needs. `pcc` will ask `make -n GOAL` for the participating source
files and fall back to `make -nB GOAL` only when needed.

In practice:

- `--depends-on projects/pcre-8.45=libpcre.la projects/test_pcre_main.c`
  is the recommended PCRE entrypoint
- PCRE expects `config.h`, so pass `--cpp-arg=-DHAVE_CONFIG_H` explicitly instead of relying on compiler-side auto-detection
- `--depends-on` already uses the multi-input path, so `--separate-tus` is optional here

Compiling zlib 1.3.1
--------------------

zlib fits the same workflow:

- keep the library sources under `projects/zlib-1.3.1/`
- keep a small driver at `projects/test_zlib_main.c`
- use `--depends-on PATH=GOAL` to let `pcc` collect the real library sources

```bash
# Build the zlib sources selected by the libz.a target and run the test driver
uv run pcc \
  --cpp-arg=-DHAVE_UNISTD_H \
  --cpp-arg=-DHAVE_STDARG_H \
  --cpp-arg=-U__ARM_FEATURE_CRC32 \
  --depends-on projects/zlib-1.3.1=libz.a \
  projects/test_zlib_main.c

# The explicit separate-TU form is also supported
uv run pcc \
  --separate-tus \
  --jobs 2 \
  --cpp-arg=-DHAVE_UNISTD_H \
  --cpp-arg=-DHAVE_STDARG_H \
  --cpp-arg=-U__ARM_FEATURE_CRC32 \
  --depends-on projects/zlib-1.3.1=libz.a \
  projects/test_zlib_main.c
```

Notes:

- zlib's source tree ships an unconfigured `Makefile`, so `pcc` falls back to `Makefile.in` when collecting sources from `libz.a`
- zlib also needs a few explicit configuration-style defines; pass them with `--cpp-arg` instead of relying on compiler-side project detection
- `--depends-on projects/zlib-1.3.1=libz.a projects/test_zlib_main.c` is the core zlib entrypoint, paired with the `--cpp-arg` flags shown above
- `--separate-tus` is optional here as well; `--depends-on` already uses the multi-input path

Compiling SQLite 3.49.1
-----------------------

SQLite uses the same "driver + dependency source" model, but the dependency is
the amalgamation file directly:

```bash
# Run the SQLite smoke driver against a real on-disk database path
uv run pcc \
  --cpp-arg=-U__APPLE__ \
  --cpp-arg=-U__MACH__ \
  --cpp-arg=-U__DARWIN__ \
  --cpp-arg=-DSQLITE_THREADSAFE=0 \
  --cpp-arg=-DSQLITE_OMIT_WAL=1 \
  --cpp-arg=-DSQLITE_MAX_MMAP_SIZE=0 \
  --depends-on projects/sqlite-amalgamation-3490100/sqlite3.c \
  projects/test_sqlite_main.c /tmp/pcc_sqlite.db

# Explicit separate-TU form
uv run pcc \
  --separate-tus \
  --jobs 2 \
  --cpp-arg=-U__APPLE__ \
  --cpp-arg=-U__MACH__ \
  --cpp-arg=-U__DARWIN__ \
  --cpp-arg=-DSQLITE_THREADSAFE=0 \
  --cpp-arg=-DSQLITE_OMIT_WAL=1 \
  --cpp-arg=-DSQLITE_MAX_MMAP_SIZE=0 \
  --depends-on projects/sqlite-amalgamation-3490100/sqlite3.c \
  projects/test_sqlite_main.c /tmp/pcc_sqlite.db
```

Notes:

- `projects/test_sqlite_main.c` now exercises DDL via `sqlite3_exec`, prepared inserts with binds/resets, `sqlite3_last_insert_rowid`, `sqlite3_changes`, aggregate queries, updates, rollback, and reopen/persistence checks
- SQLite also needs a few explicit compile-time knobs when built through `pcc`; pass them with `--cpp-arg` as shown above instead of relying on compiler-side project detection
- pass a real database file path if you want to exercise Unix VFS/open/reopen behavior; omitting the argument falls back to `:memory:`
- on macOS, the multi-TU MCJIT runtime is isolated in a subprocess to avoid llvmlite teardown crashes after successful execution

Compiling PostgreSQL 17.4 `libpq`
-----------------------

PostgreSQL is integrated in two layers:

- `projects/test_postgres_main.c`
  Builds `libpq` through `--depends-on projects/postgresql-17.4/src/interfaces/libpq=libpq.a` and runs a basic `PQconninfoParse` / `PQlibVersion` smoke test.
- `projects/test_postgres_query_main.c`
  Used by `tests/test_postgres.py` to connect to a real PostgreSQL server, run SQL, and verify results.

Direct `pcc` entrypoint for the `libpq` smoke binary on an already-prepared tree:

```bash
uv run pcc --system-link --jobs 2 \
  --depends-on projects/postgresql-17.4/src/interfaces/libpq=libpq.a \
  --depends-on projects/zlib-1.3.1=libz.a \
  --link-arg=projects/postgresql-17.4/src/common/libpgcommon_shlib.a \
  --link-arg=projects/postgresql-17.4/src/port/libpgport_shlib.a \
  --link-arg=-lm \
  projects/test_postgres_main.c
```

This route asks `pcc` to compile both the `libpq` sources and the repo-local zlib project sources, then hand the resulting objects to the host C compiler for final linking. Plain `uv run pcc --depends-on ... projects/test_postgres_main.c` still uses MCJIT by default; the `--system-link` form is the closer match to PostgreSQL's real multi-object build.

From a fresh checkout, use the generic prepare/build hooks to create the supporting native artifacts first:

```bash
env -u LC_ALL uv run pcc --system-link --jobs 2 \
  --prepare-cmd 'cd projects/zlib-1.3.1 && ./configure --static' \
  --prepare-cmd 'cd projects/postgresql-17.4 && CPPFLAGS=-I../zlib-1.3.1 LDFLAGS=-L../zlib-1.3.1 ./configure --with-zlib --without-readline --without-openssl --without-icu --without-ldap --without-gssapi' \
  --ensure-make-goal projects/zlib-1.3.1=libz.a \
  --ensure-make-goal projects/postgresql-17.4/src/backend=generated-headers \
  --ensure-make-goal projects/postgresql-17.4/src/port=pg_config_paths.h \
  --ensure-make-goal projects/postgresql-17.4/src/port=libpgport_shlib.a \
  --ensure-make-goal projects/postgresql-17.4/src/common=libpgcommon_shlib.a \
  --depends-on projects/postgresql-17.4/src/interfaces/libpq=libpq.a \
  --depends-on projects/zlib-1.3.1=libz.a \
  --link-arg=projects/postgresql-17.4/src/common/libpgcommon_shlib.a \
  --link-arg=projects/postgresql-17.4/src/port/libpgport_shlib.a \
  --link-arg=-lm \
  projects/test_postgres_main.c
```

`--prepare-cmd` and `--ensure-make-goal` are generic CLI features; PostgreSQL just happens to be a good stress case for them.

To clean generated integration artifacts afterwards:

```bash
uv run python run.py clean
```

You can also clean specific targets only, for example `uv run python run.py clean zlib readline postgres`.

Important dependency boundary:

- the PostgreSQL integration tests configure `projects/postgresql-17.4/` against the repo-local `projects/readline-8.2/` and `projects/zlib-1.3.1/` trees
- they do not use repo-local `projects/openssl-3.4.1/` in this path; PostgreSQL's native `libpq` build rejects the current static OpenSSL integration during its own reference check
- for runtime tests, the native support archives `src/common/libpgcommon_shlib.a` and `src/port/libpgport_shlib.a` are built from the same PostgreSQL source tree
- the full query test also builds native `postgres`, `initdb`, and `pg_ctl` from the same tree, stages a temporary `make install DESTDIR=...` runtime, starts a local server, then runs the `pcc`-built client against it

The test helpers auto-configure PostgreSQL if needed, including a temporary include overlay so PostgreSQL picks the repo-local GNU Readline headers instead of the macOS system editline headers.

Then run:

```bash
env -u LC_ALL uv run pytest tests/test_postgres.py -q -n0
```

The PostgreSQL test file covers:

- make-goal source discovery for `libpq.a`
- make-derived CPP arg collection without leaking recursive submake flags
- `pcc`-compiled `libpq` linked with PostgreSQL's own support archives from the same tree
- a real query roundtrip against a temporary native PostgreSQL server

Compiling nginx 1.28.3
--------------------

`pcc` can compile all ~130 nginx source files through its full pipeline
(preprocess → parse → codegen → LLVM IR → verify), and produce a working
nginx binary via `--system-link`.

nginx uses system pcre2 and zlib (via `pkg-config`), so no repo-local
library setup is needed.

```bash
# Configure nginx (only needed once)
cd projects/nginx-1.28.3 && ./configure --with-cc-opt=-Wno-error && cd ../..

# Run all nginx tests (per-file compilation + system-link binary)
uv run pytest tests/test_nginx.py -v
```

Tests in `tests/test_nginx.py`:

| Test | What it does |
|------|-------------|
| `test_nginx_make_goal_collects_source_files` | Verifies make-goal discovery finds nginx sources without pcre/zlib contamination |
| `test_nginx_source_compile[<file>]` | Each `.c` file through pcc: preprocess → parse → codegen → IR serialize → LLVM verify |
| `test_nginx_native_build` | Builds nginx natively as a baseline |
| `test_nginx_full_system_link` | Compiles all sources with pcc, links with system cc, verifies the binary runs `nginx -V` |

c-testsuite
--------------------

The project includes 220 test cases from [c-testsuite](https://github.com/c-testsuite/c-testsuite),
a standard C conformance test suite. Each case is run through both the native
compiler and `pcc`, comparing return codes and stdout/stderr.

```bash
uv run pytest tests/test_c_testsuite.py -v
```

A manifest at `tests/c_testsuite_manifest.json` categorizes every case into:
- `runtime_exact_match` — pcc output matches native exactly
- `runtime_returncode_match_only` — return code matches, output may differ
- `runtime_native_pass_pcc_fail` — known pcc failures tracked as expected
- `runtime_timeout` — cases that hang or take too long

Clang C Tests
--------------------

161 test cases derived from Clang's C test suite, covering compile-only
checks and runtime correctness. Each case is compared between the native
compiler and `pcc`.

```bash
uv run pytest tests/test_clang_c.py -v
```

A manifest at `tests/clang_c_manifest.json` categorizes cases into:
- `compile_only_success` — both native and pcc compile successfully
- `compile_only_both_fail` / `compile_only_native_pass_pcc_fail` / `compile_only_native_fail_pcc_pass` — compile-only edge cases
- `runtime_exact_match` — pcc runtime output matches native exactly
- `runtime_returncode_match_only` — return code matches, output may differ
- `runtime_both_fail` — both native and pcc fail at runtime

GCC Torture Execute
--------------------

1684 test cases from GCC's `torture/execute` suite — a comprehensive
stress test of C compiler correctness. Each case runs through both the
native compiler and `pcc`, comparing return codes and output.

```bash
uv run pytest tests/test_gcc_torture_execute.py -v
```

A manifest at `tests/gcc_torture_manifest.json` categorizes cases into:
- `runtime_exact_match` — pcc output matches native exactly
- `runtime_returncode_match_only` — return code matches, output may differ
- `runtime_both_fail` — both native and pcc fail
- `runtime_native_pass_pcc_fail` — known pcc failures tracked as expected
- `runtime_native_fail_pcc_pass` — pcc accepts but native rejects
- `runtime_timeout` — cases that hang or take too long

Add C test cases
--------------------

```bash
# Single file: add to c_tests/ with expected return value
echo '// EXPECT: 42
int main(){ return 42; }' > c_tests/mytest.c

# Multi-file project: create a directory with main.c
mkdir c_tests/myproject
# ... add .c and .h files, main.c must have: // EXPECT: N

# Run all C file tests
uv run pytest tests/test_c_files.py -v
```

Preprocessor
--------------------

```c
#include <stdio.h>          // system headers: 133 libc functions auto-declared
#include "mylib.h"          // user headers: read and inline file content
#define MAX_SIZE 100        // object-like macro
#define MAX(a,b) ((a)>(b)?(a):(b))  // function-like macro
#define DEBUG               // flag for conditional compilation
#ifdef / #ifndef / #if / #elif / #else / #endif  // conditional compilation
#if defined(X) && (VERSION >= 3)  // expression evaluation with defined()
#undef NAME                 // undefine macro
```

Built-in macros: `NULL`, `EOF`, `EXIT_SUCCESS`, `EXIT_FAILURE`, `RAND_MAX`, `INT_MAX`, `INT_MIN`, `LLONG_MAX`, `CHAR_BIT`, `true`, `false`, `__STDC__`

Built-in typedefs: `size_t`, `ssize_t`, `ptrdiff_t`, `va_list`, `FILE`, `time_t`, `clock_t`, `pid_t`

Supported C Features
--------------------

Supports all C99 features needed to compile real-world projects like Lua 5.5.0 and nginx: all types (int, float, double, char, void, structs, unions, enums, typedefs, pointers, arrays, function pointers), all operators, all control flow (if/else, for, while, do-while, switch, goto), variadic functions, preprocessor directives, and 133 libc functions auto-declared from stdio.h, stdlib.h, string.h, math.h, etc.
