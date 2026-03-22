Pcc
====================

What is this?
--------------------
Pcc is a C compiler written in Python, built on cpp + ply + pycparser + llvmlite + llvm.
Run C programs like Python scripts: `pcc test.c`. Powerful enough to compile and run the full [Lua 5.5.0](https://github.com/lua/lua) interpreter (~30k lines of C).

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
uv run pytest    # run all 500+ tests
```

Multi-file projects: put `.c` and `.h` files in a directory, one `.c` must contain `main()`. Pcc auto-discovers all `.c` files, merges them, and compiles.

Compiling Lua 5.5.0
--------------------

Pcc can compile the entire [Lua 5.5.0](https://github.com/lua/lua) interpreter (~30k lines of C) and run Lua scripts directly.

For one representative script (`math.lua`), these are the important cases:

```bash
git clone https://github.com/jiamo/pcc && cd pcc
uv sync

# 1) Canonical Lua tree in default directory mode: fails
#    Reason: directory mode merges every .c into one TU, and projects/lua-5.5.0
#    contains both onelua.c and the individual source files.
uv run pcc projects/lua-5.5.0 -- projects/lua-5.5.0/testes/math.lua

# 2) Explicit amalgamation mode: works
uv run pcc projects/lua-5.5.0/onelua.c -- projects/lua-5.5.0/testes/math.lua

# 3) Canonical Lua tree, but collect only the sources used by `make -nB lua`: works
uv run pcc --sources-from-make lua projects/lua-5.5.0 -- projects/lua-5.5.0/testes/math.lua

# 4) Same make-derived source list, but compile as separate translation units: works
uv run pcc --separate-tus --sources-from-make lua projects/lua-5.5.0 -- projects/lua-5.5.0/testes/math.lua

# 5) Same separate-TU path, but compile translation units in parallel: works
uv run pcc --separate-tus --sources-from-make lua --jobs 2 projects/lua-5.5.0 -- projects/lua-5.5.0/testes/math.lua

# 6) Canonical Lua tree with raw --separate-tus: fails
#    Reason: onelua.c and the individual source files become separate TUs with
#    duplicate external definitions at link semantics.
uv run pcc --separate-tus projects/lua-5.5.0 -- projects/lua-5.5.0/testes/math.lua

# Run all 130+ Lua tests (pcc vs native, pcc vs makefile, makefile baseline)
uv run pytest tests/test_lua.py -v
```

```
projects/lua-5.5.0/         - canonical Lua 5.5.0 source tree, includes onelua.c and the standard multi-file sources
projects/lua-5.5.0/testes/  - Lua test suite
```

Use `projects/lua-5.5.0/onelua.c` when you want Lua's official amalgamation build.
Use `--sources-from-make lua` when you want `pcc` to follow the same source list
that Lua's makefile uses for the `lua` target.
Use `--separate-tus` when you want normal multi-file C semantics: each `.c`
file is compiled as its own translation unit instead of being merged into one
big source string first.

In other words:

- default directory mode: merge all selected `.c` files into one TU
- `--sources-from-make lua`: keep directory mode, but let `make -nB lua` choose which `.c` files participate
- `--separate-tus`: compile each selected `.c` as a separate TU, then link them together

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

Supports all C99 features needed to compile real-world projects like Lua 5.5.0: all types (int, float, double, char, void, structs, unions, enums, typedefs, pointers, arrays, function pointers), all operators, all control flow (if/else, for, while, do-while, switch, goto), variadic functions, preprocessor directives, and 133 libc functions auto-declared from stdio.h, stdlib.h, string.h, math.h, etc.
