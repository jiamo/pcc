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

Development
--------------------

Requires Python 3.13+ and [uv](https://docs.astral.sh/uv/).

```bash
uv sync          # install dependencies
uv run pytest    # run all 365+ tests (~9s parallel)
```

Run pcc
--------------------

```bash
# Single file
uv run pcc hello.c

# Multi-file project (auto-collects all .c files, resolves .h includes)
uv run pcc myproject/

# Dump LLVM IR
uv run pcc --llvmdump test.c
```

Multi-file projects: put `.c` and `.h` files in a directory, one `.c` must contain `main()`. Pcc auto-discovers all `.c` files, merges them, and compiles.

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
#include <stdio.h>          // system headers: silently handled (127 libc functions auto-declared)
#include "mylib.h"          // user headers: read and inline file content
#define MAX_SIZE 100        // simple macro replacement
#define DEBUG               // flag for conditional compilation
#ifdef DEBUG / #ifndef / #else / #endif   // conditional compilation (nested)
#undef NAME                 // undefine macro
```

Built-in macros: `NULL`, `EOF`, `EXIT_SUCCESS`, `EXIT_FAILURE`, `RAND_MAX`, `INT_MAX`, `INT_MIN`, `CHAR_BIT`, `true`, `false`

Supported C Features
--------------------

### Types
`int`, `double`, `char`, `void`, pointers (multi-level), arrays (multi-dim),
structs (named, anonymous, nested, with array members, pointer-to-struct),
unions, enums (with expressions), typedef (scalar, struct, pointer)

### Operators
- Arithmetic: `+` `-` `*` `/` `%`
- Bitwise: `&` `|` `^` `<<` `>>`
- Comparison: `<` `>` `<=` `>=` `==` `!=` (including pointer comparison)
- Logical: `&&` `||` (short-circuit evaluation)
- Unary: `-x` `+x` `!x` `~x` `sizeof` `&x` `*p`
- Increment/Decrement: `++x` `x++` `--x` `x--` (int and pointer)
- Assignment: `=` `+=` `-=` `*=` `/=` `%=` `<<=` `>>=` `&=` `|=` `^=`
- Ternary: `a ? b : c`
- Pointer: `p + n`, `p - n`, `p - q`, `p++`, `p[i]`
- Struct access: `.` and `->`
- Chained: `a = b = c = 5`

### Control Flow
`if` / `else` / `else if`, `while`, `do-while`, `for` (all variants including `for(;;)`),
`switch` / `case` / `default`, `goto` / `label`, `break`, `continue`, `return`

### Functions
Definitions, forward declarations, mutual recursion, void functions,
pointer/array arguments, `static` local variables, recursive functions

### Libc Functions (127 total, auto-declared on first use)

| Header | Functions |
|--------|-----------|
| stdio.h | printf, fprintf, sprintf, snprintf, puts, putchar, getchar, fopen, fclose, fread, fwrite, fseek, ftell, fgets, fputs, scanf, sscanf, ... |
| stdlib.h | malloc, calloc, realloc, free, abs, labs, atoi, atol, atof, strtol, strtod, rand, srand, exit, abort, qsort, bsearch, getenv, system, ... |
| string.h | strlen, strcmp, strncmp, strcpy, strncpy, strcat, strncat, strchr, strrchr, strstr, memset, memcpy, memmove, memcmp, memchr, strtok, ... |
| ctype.h | isalpha, isdigit, isalnum, isspace, isupper, islower, isprint, ispunct, isxdigit, toupper, tolower, ... |
| math.h | sin, cos, tan, asin, acos, atan, atan2, exp, log, log2, log10, pow, sqrt, cbrt, hypot, ceil, floor, round, trunc, fmod, fabs, ... |
| time.h | time, clock, difftime |
| unistd.h | sleep, usleep, read, write, open, close, getpid, getppid |

### Literals
Decimal, hex (`0xFF`), octal (`077`), char (`'a'`, `'\n'`), string (`"hello\n"`), double (`3.14`)

### Other
C comments (`/* */`, `//`), array initializer lists (1D and multi-dim),
string escape sequences (`\n \t \\ \0 \r`), implicit type promotion (int/char/double),
array-to-pointer decay, `NULL` pointer support, `static` locals,
struct with array members, pointer comparison/subtraction
