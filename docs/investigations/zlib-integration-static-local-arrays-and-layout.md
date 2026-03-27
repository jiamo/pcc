# Investigation Report: zlib Integration Exposed Layout and Static-Local Array Bugs

## Executive Summary

Bringing `projects/zlib-1.3.1/` up through `pcc` looked at first like a build-system and host-header problem.

That was only the outer layer.

The zlib integration actually exposed three independent compiler bug classes:

1. `enum` layout was wrong because `enum` types were lowered as 64-bit integers instead of C `int`.
2. block-scope `static` arrays were lowered like automatic locals instead of internal globals.
3. block-scope incomplete arrays such as `static const char my_version[] = "1.3.1";` did not infer their top-level size from the initializer, so they became zero-length globals.

All three bugs produced realistic runtime failures:

- broken inflate state layout
- corrupted fixed Huffman decode tables
- `deflateInit_()` returning `Z_VERSION_ERROR`

This was a useful example of a real-program integration forcing several different semantic layers to be correct at once:

- project source collection
- fake-libc declarations
- preprocessor feature detection
- aggregate and layout semantics
- static storage duration lowering


## Initial Goal

The goal was to support a project-level invocation analogous to the PCRE flow:

```bash
env -u LC_ALL uv run pcc --depends-on projects/zlib-1.3.1=libz.a projects/test_zlib_main.c
```

That requires all of the following to work:

- source discovery for the real zlib library target
- preprocessing without a configured `config.h`
- enough fake-libc surface for `gz*.c`
- correct multi-TU semantics
- correct runtime behavior of both `compress()` and `uncompress()` / `inflate()`


## Step 1: Make the Build System Consumable

zlib ships an unconfigured `Makefile` that only says "run ./configure first". The real target definitions live in `Makefile.in`.

That meant plain directory scanning was not the right model, and the make-derived source collector needed a fallback:

- try `make -n GOAL`
- then `make -nB GOAL`
- if the top-level makefile is a stub, also try `-f Makefile.in`

This was not a compiler bug yet, but it mattered because otherwise the zlib integration would not even reach semantic codegen issues.

This support was added in `pcc/project.py`.


## Step 2: Teach Preprocessing Enough zlib Context

Unconfigured zlib depends on feature macros that are normally injected by configure:

- `HAVE_UNISTD_H`
- `HAVE_STDARG_H`

Also, on Apple ARM targets, zlib's CRC code can take a CRC32 inline-assembly path guarded by `__ARM_FEATURE_CRC32`, which `pcc`'s parser does not handle.

The preprocessing layer was therefore taught to:

- recognize zlib-style `zconf.h`
- define `HAVE_UNISTD_H`
- define `HAVE_STDARG_H`
- undefine `__ARM_FEATURE_CRC32`

At the same time, the fake libc had to grow several declarations and flags used by zlib's `gz*` code:

- `read`
- `write`
- `close`
- `lseek`
- `open`
- `O_CREAT`, `O_TRUNC`, `O_EXCL`, `O_CLOEXEC`, `O_BINARY`, etc.

This work made zlib parsable and compilable, but not yet correct at runtime.


## Step 3: First Runtime Failure Was `uncompress()` / `inflate()`

After the preprocessing and fake-libc layer were fixed, a small zlib smoke harness still failed during decompression.

The harness structure was straightforward:

1. compress `"hello, hello!\0"`
2. uncompress it
3. separately use `deflateInit` / `deflate` and `inflateInit` / `inflate`
4. compare outputs

`compress()` produced plausible bytes, but `uncompress()` and `inflate()` returned bad results.

This was the first important narrowing:

- compression output was correct
- decompression path was not

So the next question became:

- bad data layout?
- bad fixed-Huffman tables?
- bad aggregate/static storage semantics?


## Step 4: Prove the Compressed Bytes Are Fine

A tiny dump harness was used to print the output bytes from `compress("hello, hello!")`.

Native and `pcc` matched exactly.

That mattered because it ruled out:

- incorrect checksum generation
- incorrect deflate bitstream emission
- early corruption in `compress()`

The bug had to be later, in the readback/decompression path.


## Step 5: Confirm zlib Takes the Fixed-Huffman Path

The first data byte after the zlib header indicated:

- `BFINAL = 1`
- `BTYPE = 01`

So `inflate()` should use fixed Huffman tables, not dynamic code construction.

This was a productive constraint because it sharply reduced the relevant runtime code:

- `fixedtables()`
- `inflate_state`
- fixed decode tables


## Step 6: Compare Native and `pcc` Struct Layout

At this point the best hypothesis was a layout bug.

A dedicated probe compared native and `pcc` for:

- `sizeof(struct inflate_state)`
- `offsetof(mode)`
- `offsetof(last)`
- `offsetof(hold)`
- `offsetof(lencode)`
- related fields

The mismatch was real and large enough to matter:

- native and `pcc` diverged beginning at `last`
- downstream pointer and state fields all shifted
- total struct size also differed

This was not a zlib bug. It was a compiler layout bug.


## Root Cause A: `enum` Lowered as 64-bit Instead of `int`

The bad layout traced back to `enum` lowering.

`pcc` had several enum paths that defaulted to `int64_t`:

- `_resolve_node_type()`
- `_resolve_ast_type()`
- `codegen_Enum()`
- typedef handling for `enum`

In C, plain `enum` is represented as an `int`-sized type for the purposes used here. zlib relies on that assumption in `inflate_state`.

Once enum lowering was corrected to use 32-bit `int`, the `inflate_state` layout matched native again.

That fix removed the first runtime blocker.


## Step 7: Layout Matched, but Inflate Still Failed

Once `inflate_state` matched native, the decompressor was no longer obviously malformed.

However, `inflate()` still misbehaved.

This was the crucial moment to **not stop after the first fix**. A layout bug had existed, but it was not the only bug.

Temporary debug instrumentation showed something revealing:

- the fixed decode tables looked correct while `fixedtables()` was executing
- but they looked wrong later when `inflate()` used them

That suggested a lifetime/storage bug rather than a pure layout bug.


## Root Cause B: Block-Scope `static` Arrays Were Allocated Like Locals

zlib's `fixedtables()` uses block-scope static arrays:

```c
static const code lenfix[512] = { ... };
static const code distfix[32] = { ... };
```

These must have static storage duration.

The old `pcc` lowering only handled one subset of function-scope statics correctly:

- scalar-like `TypeDecl` statics

It did **not** handle `ArrayDecl` statics correctly. Those arrays were emitted as normal stack locals rather than internal globals.

That created exactly the observed behavior:

- the arrays looked valid while still inside the helper
- pointers to them were cached in the inflate state
- later code read from dead stack memory
- the decode tables appeared corrupted

The fix was to generalize function-scope static lowering so that all object types, including arrays, become internal globals.

After that fix:

- `inflate()` succeeded
- `uncompress()` succeeded

At that point, decompression was correct.


## Step 8: New Failure on the Compression Side

Once the fixed-table bug was fixed, the remaining zlib smoke test changed shape.

The harness now failed much earlier:

```text
compress: -6
```

In zlib, `-6` is `Z_VERSION_ERROR`.

The immediate check inside `deflateInit2_()` is:

```c
if (version == Z_NULL || version[0] != my_version[0] ||
    stream_size != sizeof(z_stream)) {
    return Z_VERSION_ERROR;
}
```

That produced a clean binary split:

- either `stream_size != sizeof(z_stream)`
- or `version[0] != my_version[0]`


## Step 9: Shrink to a Minimal `deflateInit_()` Reproducer

A smaller harness called only:

```c
deflateInit_(&stream, Z_DEFAULT_COMPRESSION, ZLIB_VERSION, (int)sizeof(z_stream))
```

The caller printed:

- `sizeof(z_stream)`
- field offsets
- `zlibVersion()`
- `zlibCompileFlags()`

Useful observations:

- the caller saw `sizeof(z_stream) == 112`
- native saw the same
- `zlibVersion()` returned `"1.3.1"`
- `zlibCompileFlags()` looked normal
- but `deflateInit_()` still returned `-6`

Even more telling, trying many `stream_size` values still returned `-6`.

That strongly suggested the failure was not the size check at all. It was the version comparison.


## Step 10: Reduce Further to a Tiny Static-Local String Reproducer

The next minimal reproducer was not zlib-specific:

```c
const char *get_my_version(void) {
    static const char my_version[] = "1.3.1";
    return my_version;
}
```

Under native compilation:

- the returned pointer referenced `"1.3.1"`
- `p[0] == '1'`

Under the buggy `pcc` lowering:

- the returned pointer referenced an empty zero-length array
- `p[0] == 0`

This was the decisive reduction.


## Root Cause C: Function-Scope Incomplete Arrays Did Not Infer Size

`pcc` already had logic to infer the top-level size of:

- file-scope incomplete arrays
- ordinary array declarations with initializers

However, the function-scope static-object path called:

- `_static_local_ir_type(node.type)`

without passing the initializer through to the array type builder.

So:

```c
static const char my_version[] = "1.3.1";
```

was lowered as:

```llvm
@"__static_..._my_version" = internal global [0 x i8] c""
```

instead of:

```llvm
@"__static_..._my_version" = internal global [6 x i8] c"1.3.1\00"
```

That made zlib's `my_version[0]` equal to `0`, so the version check always failed.


## The Fix

The fix was small but semantically important:

1. allow `_build_array_ir_type()` to receive an initializer
2. infer the top-level array size from that initializer when the declared size is omitted
3. pass `node.init` through the function-scope static-object path

This repaired all of the following forms:

- `static const char s[] = "abc";`
- `static const int table[] = {1, 2, 3};`
- cross-function users that return or cache pointers to those arrays


## Regression Tests Added

Three layers of regression coverage were added.

### 1. Small static-local regressions

In `tests/test_static.py`:

- `static const char my_version[] = "1.3.1";`
- `static const int table[] = {7, 11, 13};`

These catch the exact missing-size-inference bug at the smallest scale.

### 2. Multi-TU regression

In `tests/test_separate_tus.py`:

- one TU returns a pointer to `static const char my_version[] = "1.3.1";`
- another TU reads it and verifies the bytes

This makes sure the bug does not hide behind single-TU behavior.

### 3. Real zlib runtime confirmation

In `tests/test_zlib.py`:

- collect zlib sources from `libz.a`
- run the zlib test main through MCJIT
- run the same units through the system-link path

This confirms the full integration scenario, not just a toy reduction.


## Final Result

After the fixes:

- `inflate()` works
- `uncompress()` works
- `deflateInit_()` no longer returns `Z_VERSION_ERROR`
- the zlib smoke program prints:

```text
zlib version 1.3.1 = 0x1310, compile flags = 0xa9
compress/uncompress: hello, hello!
deflate/inflate: hello, hello!
OK
```


## Lessons for Future Debugging

This session is a good template for future real-program investigations.

### 1. A real library can expose several independent bug classes in sequence

Do not assume the first successful fix is the last one.

zlib surfaced:

- one layout bug
- one storage-duration bug
- one incomplete-array-size bug

### 2. Always shrink after each symptom change

The right reproducer changed over time:

- full zlib smoke test
- fixed compressed-byte harness
- direct `inflate()` harness
- direct `deflateInit_()` harness
- tiny static-local string function

Each smaller reproducer made the next bug much easier to reason about.

### 3. Separate layout bugs from storage-lifetime bugs

Both can look like "random table corruption".

Useful discriminators:

- `sizeof` / `offsetof` probes point to layout
- "looks correct inside helper, wrong later" points to lifetime/storage

### 4. If a version check fails, inspect the supposedly constant data

A failure like:

```c
version[0] != my_version[0]
```

does not automatically mean ABI mismatch or caller-side corruption. It can also mean:

- static storage was lowered incorrectly
- a string initializer was lost
- an incomplete array became zero-length

### 5. Block-scope `static` is a high-risk area

It combines:

- non-local storage duration
- local declaration syntax
- initializer semantics
- name uniqueness rules

That is exactly the kind of C feature that a toy compiler gets "almost right" and a real library punishes immediately.


## Practical Commands

Useful commands from this investigation:

```bash
env -u LC_ALL uv run pcc --depends-on projects/zlib-1.3.1=libz.a projects/test_zlib_main.c
env -u LC_ALL uv run pcc --separate-tus --depends-on projects/zlib-1.3.1=libz.a --jobs 2 projects/test_zlib_main.c
env -u LC_ALL uv run pytest tests/test_static.py tests/test_separate_tus.py tests/test_zlib.py -q -n0
```


## Relevant Files

- `pcc/project.py`
- `pcc/evaluater/c_evaluator.py`
- `pcc/codegen/c_codegen.py`
- `utils/fake_libc_include/unistd.h`
- `utils/fake_libc_include/fcntl.h`
- `projects/test_zlib_main.c`
- `tests/test_static.py`
- `tests/test_separate_tus.py`
- `tests/test_zlib.py`
