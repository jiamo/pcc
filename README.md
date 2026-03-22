Pcc
====================

What is this?
--------------------
Pcc is a C compiler based on ply + pycparser + llvmlite + llvm.
We can run C programs like Python: `pcc test.c` to run C code.
Pcc was inspired by: https://github.com/eliben/pykaleidoscope.

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

Development
--------------------

Requires Python 3.13+ and [uv](https://docs.astral.sh/uv/).

```bash
uv sync          # install dependencies
uv run pytest    # run all 500+ tests
```

Multi-file projects: put `.c` and `.h` files in a directory, one `.c` must contain `main()`. Pcc auto-discovers all `.c` files, merges them, and compiles.

Lua Compilation Goal
--------------------

The target is to compile and run [Lua 5.5.0](https://github.com/lua/lua) using pcc.

```
projects/lua-5.5.0/         - Lua 5.5.0 source code + Makefile
projects/lua-5.5.0/testes/  - Lua test suite
```

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

### Types
`int`, `double`, `float`, `char`, `void`, `unsigned`/`signed`/`long`/`short` (all combinations),
`size_t`, `int8_t`..`uint64_t`,
pointers (multi-level), arrays (multi-dim),
structs (named, anonymous, nested, with array/pointer/function-pointer members, pointer-to-struct),
unions, enums (with constant expressions), typedef (scalar, struct, pointer, function pointer),
`static` local variables, `const`/`volatile` qualifiers

### Operators
- Arithmetic: `+` `-` `*` `/` `%`
- Bitwise: `&` `|` `^` `<<` `>>`
- Comparison: `<` `>` `<=` `>=` `==` `!=` (including pointer comparison)
- Logical: `&&` `||` (short-circuit evaluation)
- Unary: `-x` `+x` `!x` `~x` `sizeof` `&x` `*p`
- Increment/Decrement: `++x` `x++` `--x` `x--` (int and pointer, including struct members)
- Assignment: `=` `+=` `-=` `*=` `/=` `%=` `<<=` `>>=` `&=` `|=` `^=` (including pointer `+=`/`-=`)
- Ternary: `a ? b : c`
- Pointer: `p + n`, `p - n`, `p - q`, `p++`, `p[i]`
- Struct access: `.` and `->` (including nested `a.b.c` and `s->fn(args)`)
- Chained: `a = b = c = 5`

### Control Flow
`if` / `else` / `else if`, `while`, `do-while`, `for` (all variants including `for(;;)`),
`switch` / `case` / `default`, `goto` / `label`, `break`, `continue`, `return`

### Functions
Definitions, forward declarations, mutual recursion, void functions, variadic (`...`),
pointer/array arguments, `static` local variables, function pointers (declaration, assignment,
calling, as parameters, in structs, typedef'd), callback patterns

### Libc Functions (133 total, auto-declared on first use)

| Header | Functions |
|--------|-----------|
| stdio.h | printf, fprintf, sprintf, snprintf, puts, putchar, getchar, fopen, fclose, fread, fwrite, fseek, ftell, fgets, fputs, scanf, sscanf, ... |
| stdlib.h | malloc, calloc, realloc, free, abs, labs, atoi, atol, atof, strtol, strtod, rand, srand, exit, abort, qsort, bsearch, getenv, system, ... |
| string.h | strlen, strcmp, strncmp, strcpy, strncpy, strcat, strncat, strchr, strrchr, strstr, memset, memcpy, memmove, memcmp, memchr, strtok, ... |
| ctype.h | isalpha, isdigit, isalnum, isspace, isupper, islower, isprint, ispunct, isxdigit, toupper, tolower, ... |
| math.h | sin, cos, tan, asin, acos, atan, atan2, exp, log, log2, log10, pow, sqrt, cbrt, hypot, ceil, floor, round, trunc, fmod, fabs, ... |
| time.h | time, clock, difftime |
| unistd.h | sleep, usleep, read, write, open, close, getpid, getppid |
| setjmp.h | setjmp, longjmp |
| signal.h | signal, raise |

### Literals
Decimal, hex (`0xFF`), octal (`077`), char (`'a'`, `'\n'`), string (`"hello\n"`), double (`3.14`)

### Other
C comments (`/* */`, `//`), array initializer lists (1D and multi-dim),
string escape sequences (`\n \t \\ \0 \r`), implicit type promotion (int/char/double/pointer),
array-to-pointer decay, `NULL` pointer support, opaque/forward-declared structs,
two-pass codegen (types first, functions second)
