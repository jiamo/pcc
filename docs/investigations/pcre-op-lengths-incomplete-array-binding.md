# Investigation Report: PCRE `OP_lengths` Failure Caused by Incomplete-Array Global Binding

## Executive Summary

A PCRE runtime hang first looked like a multi-module MCJIT performance problem.

It was not.

The real issue was a compiler bug in `pcc`:

- a file-scope array definition with inferred size, such as
  `const pcre_uint8 _pcre_OP_lengths[] = { ... };`
- was first materialized as a zero-length global
- then later replaced with a second global under a renamed symbol
- external references still resolved to the zero-length placeholder
- table-driven PCRE code read zeros from `_pcre_OP_lengths`
- `auto_possessify()` stopped advancing and looped forever

The immediate symptom was a hang inside PCRE compilation. The actual cause was
wrong global symbol binding for incomplete-array declarations plus late size
inference during global lowering.


## Why This Bug Was Misleading

The failing scenario involved:

- 22 library translation units
- one test main
- separate-TU compilation
- LLVM module linking or system-`cc` object linking
- a real C library, not a toy reproducer

That made the first theory tempting:

- "too many modules"
- "MCJIT link/finalize is hanging"
- "system-linking the IR will avoid the problem"

That theory was wrong because the linked program still hung after the linker
strategy changed. The linker was not the root cause. The compiled program was.


## Initial Symptom

The PCRE library compiled successfully, but the pcc-built runtime binary hung
very early while running the test harness.

The first visible output was:

```text
=== PCRE test suite ===

pcre_version: 8.45 2021-06-15
PASS: version non-empty
PASS: version starts with 8
```

The next step in the harness was `pcre_compile("hello", ...)`, and execution
never returned from that call.


## Step 1: Prove It Is Not a Linker-Only Problem

The original work was focused on adding a system-link path for separate
translation units:

- compile each `.c` to LLVM IR
- emit native object files
- link with the system `cc`

That was useful, but it did not fix the PCRE hang. The runtime still stalled.

This was the first important pivot:

- if both MCJIT-linked and system-linked binaries hang in the same way
- the bug is probably in code generation or data semantics
- not in the link strategy itself


## Step 2: Shrink the Runtime Scenario

The full PCRE test program was reduced in stages.

First, the harness was instrumented to flush progress so the last successful
step was visible.

Then the reproducer was shrunk to an even smaller program:

1. print `start`
2. call `pcre_compile("hello", ...)`
3. print `after compile`

This tiny harness still hung after printing only:

```text
start
```

That reduction removed:

- the full PCRE functional suite
- matching and study paths
- almost all test harness complexity

At that point the bug was clearly inside compilation of a trivial pattern.


## Step 3: Use the Runtime Stack, Not Guesswork

Attaching with `lldb` showed the live stack stopped in:

- `auto_possessify()`
- called from `pcre_compile2()`
- called from `pcre_compile()`

This was a strong clue that the failure was not:

- host ABI setup
- top-level `main`
- or general process startup

The compiler had produced a program that reached real PCRE internals and then
got stuck in one specific optimization pass.


## Step 4: Instrument the Tight Loop

Temporary instrumentation inside `auto_possessify()` printed:

- current opcode `c`
- `PRIV(OP_lengths)[c]`
- a few nearby bytes

The repeated output showed:

```text
c = 131
len = 0
```

over and over at the same address.

`131` is `OP_BRA`, and `OP_lengths[OP_BRA]` must not be zero. Once that table
entry became zero, the loop body executed:

```c
code += PRIV(OP_lengths)[c];
```

without advancing `code`.

That explained the infinite loop directly.


## Step 5: Read the Table in Isolation

At this point there were two plausible causes:

1. `auto_possessify()` was accessing the table incorrectly
2. the table itself was wrong at runtime

The fastest discriminator was a tiny program that did nothing except print:

```c
_pcre_OP_lengths[129]
_pcre_OP_lengths[130]
_pcre_OP_lengths[131]
_pcre_OP_lengths[132]
_pcre_OP_lengths[133]
```

Under `pcc`, those values came out as:

```text
0 0 0 0 0
```

That immediately moved the investigation away from loop control flow and toward
global constant initialization.


## Step 6: Compare Preprocessed Source and Generated IR

The preprocessed source for `pcre_tables.c` was correct. The relevant section
looked like:

```c
const pcre_uint8 _pcre_OP_lengths[] = { 1, 1, 1, ..., 1+LINK_SIZE, ... };
```

So the source seen by `pcc` was not the problem.

The decisive clue came from the emitted LLVM IR:

```llvm
@"_pcre_OP_lengths" = global [0 x i8] zeroinitializer
@"_pcre_OP_lengths.1" = global [162 x i8] [i8 1, i8 1, ...]
```

This was the actual bug.

The compiler had created:

- a zero-length placeholder under the real symbol name
- then a second, correctly initialized array under a renamed symbol

External references resolved to the placeholder, so the real data was never
used by the rest of the program.


## Root Cause

There were two coupled problems.

### Problem A: late size inference for `[]` globals

In the old array-declaration path, `codegen_Decl()` created the file-scope
global before it had inferred the real array length from the initializer.

That meant:

- `const unsigned char table[] = { ... };`
- initially became something like `[0 x i8]`
- then later the compiler realized the real count
- but the original symbol had already been bound

The fallback behavior was to create a second symbol with a unique name such as
`table.1`.

### Problem B: incomplete-array declarations were treated too rigidly

PCRE headers declare the table as:

```c
extern const pcre_uint8 _pcre_OP_lengths[];
```

and the definition later provides the initializer:

```c
const pcre_uint8 _pcre_OP_lengths[] = { OP_LENGTHS };
```

This is valid C. An incomplete array declaration followed by a complete
definition must resolve to one symbol.

The compiler needed to treat:

- incomplete array type
- complete array type with the same element type

as compatible at file scope.


## The Fix

The fix had two parts in `pcc/codegen/c_codegen.py`.

### 1. Infer top-level array length before creating the global

For declarations such as:

- `int a[] = {1, 2, 3};`
- `const char *names[] = {"a", "b"};`
- `char s[] = "hello";`

the compiler now computes the outer array length from the initializer before it
creates any file-scope symbol.

That removes the bad `[0 x T]` placeholder path entirely for normal
initialized-array definitions.

### 2. Allow incomplete-array declaration + complete definition at file scope

File-scope object state tracking now treats these as compatible:

- `extern int table[];`
- `int table[] = {1, 2, 3};`

as long as the element type matches.

This is enough to let header declarations and real definitions unify cleanly.


## Regression Tests Added

The fix was covered with targeted tests instead of relying only on PCRE.

### Cross-TU inferred scalar array

One test now verifies:

- `const unsigned char lengths[] = {1, 2 + 2, 5};`
- `extern const unsigned char lengths[];`
- a second translation unit can read the real values

This is checked through both:

- the LLVM/MCJIT path
- the system-`cc` object-link path

### Cross-TU inferred pointer array

Another test verifies:

- `const char *names[] = {"a", "bc"};`
- `extern const char *names[];`
- another translation unit sees the correct pointer array contents

### Full PCRE runtime

PCRE now has runtime coverage through both:

- system linking
- MCJIT

This matters because the original failure was a real-program bug, not just a
unit-level IR oddity.


## Lessons for Future Debugging

### 1. A hang in a real program may still be a constant-data bug

When a loop hangs, do not only inspect control flow.

If the loop is table-driven:

- print the table entry
- compare native vs `pcc`
- verify the data before rewriting the loop

### 2. Linker changes are useful experiments, not proof

Changing from MCJIT to system `cc` linking was still a good test because it
separated link strategy from runtime semantics.

But a behavior change must be demonstrated, not assumed. If the linked program
still hangs, keep moving downward.

### 3. Preprocessed source can be correct while symbol binding is still wrong

Looking only at preprocessed C would not have found this bug.

The crucial step was checking emitted IR and noticing:

- correct initialized data existed
- but under the wrong symbol

### 4. Incomplete arrays need explicit semantic handling

Treating file-scope object types as simple string-equality is not enough.

These are compatible in valid C:

- incomplete array declaration
- complete array definition with the same element type

Without that compatibility rule, the compiler either rejects legal code or
creates broken placeholder objects.


## Final Outcome

After the fix:

- the targeted array regressions pass
- PCRE runtime passes with system linking
- PCRE runtime also passes with MCJIT
- the original "MCJIT bottleneck" diagnosis no longer holds

The case is a good example of a deep real-program failure that became tractable
only after repeated reduction:

PCRE suite -> tiny compile harness -> live stack -> table entry probe -> IR
inspection -> file-scope array binding fix.
