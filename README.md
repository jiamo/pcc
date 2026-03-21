Pcc
====================

What is this?
--------------------
Pcc is a C compiler based on ply + pycparser + llvmlite + llvm.
We can run C programs like Python: `pcc test.c` to run C code.
(no header file support).
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
uv run pytest    # run all tests
```

Run pcc test
--------------------
```bash
uv run pytest                        # parallel by default (~6s for 290+ tests)
uv run pytest tests/test_if.py -v    # single file
```

Run pcc
--------------------
```bash
uv run pcc clang_study/simple_func.c     # exit code = main() return value
uv run pcc --llvmdump clang_study/if.c   # also dump LLVM IR to temp files
```

Supported C Features
--------------------

### Types
`int`, `double`, `char`, `void`, pointers (`int *p`), arrays (`int a[10]`),
structs (named, anonymous, nested, pointer-to-struct), enums, typedef

### Operators
- Arithmetic: `+` `-` `*` `/` `%`
- Bitwise: `&` `|` `^` `<<` `>>`
- Comparison: `<` `>` `<=` `>=` `==` `!=`
- Logical: `&&` `||` (short-circuit)
- Unary: `-x` `+x` `!x` `~x` `sizeof` `&x` `*p`
- Increment/Decrement: `++x` `x++` `--x` `x--` (int and pointer)
- Assignment: `=` `+=` `-=` `*=` `/=` `%=` `<<=` `>>=` `&=` `|=` `^=`
- Ternary: `a ? b : c`
- Pointer arithmetic: `p + n`, `p - n`, `p++`
- Struct access: `.` and `->`

### Control Flow
`if` / `else` / `else if`, `while`, `do-while`, `for` (all variants including `for(;;)`),
`switch` / `case` / `default`, `goto` / `label`, `break`, `continue`, `return`

### Functions
Function definitions, forward declarations, mutual recursion, void functions,
pointer arguments, recursive functions

### Libc Functions
`printf`, `malloc`, `free`, `memset`, `strlen`, `strcmp`, `strcpy`

### Literals
Decimal, hex (`0xFF`), octal (`077`), char (`'a'`, `'\n'`), string (`"hello\n"`), double (`3.14`)

### Other
C comments (`/* */`, `//`), array initializer lists (`int a[3] = {1,2,3}`, multi-dim),
string escape sequences (`\n \t \\ \0`), implicit type promotion (int/char/double),
enum with expressions (`1 << 2`), `static` local variables, `typedef struct`,
struct with array members, pointer comparison, pointer subtraction,
chained assignment (`a = b = c = 5`), comma expressions, `char *s = "hello"`
