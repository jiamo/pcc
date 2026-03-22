# Investigation Report: Lua `sort.lua` Failure Caused by Lost Unsigned Semantics

## Executive Summary

A flaky failure in Lua's `sort.lua` test turned out to be a compiler bug in `pcc`, not a Lua bug.

The real issue was not the quicksort algorithm itself. It was a signedness-propagation bug in `pcc`:

- an expression involving unsigned XOR produced the correct bit pattern
- but the resulting IR value was no longer marked as unsigned
- a later `%` therefore used signed remainder semantics
- Lua's random pivot selection chose an out-of-range pivot
- the sort implementation eventually compared `nil` with numbers or reported an invalid ordering

The concrete broken expression was inside Lua's `choosePivot`:

```c
(rnd ^ lo ^ up) % (r4 * 2) + (lo + r4)
```

With the failing input:

- native result: `731`
- old `pcc` result: `475`

That one wrong pivot was enough to corrupt quicksort's assumptions.

The fix was to preserve unsignedness for:

- `^` results
- unsigned `>>` results
- integer compound-assignment results
- unsigned prefix `++` / `--` results

Regression coverage was added for all of these paths.


## Why This Bug Was Hard

This bug was a good example of why real-program failures are valuable:

- most of the Lua test suite already passed
- `sort.lua` only failed for some random seeds or input shapes
- the data structures were large and the failure appeared far away from the real cause
- the raw values often looked plausible

At first glance it looked like:

- a stack corruption bug
- a bad aggregate copy bug
- a struct layout bug
- a comparator bug
- or a Lua runtime issue

It was none of those. It was a semantic bug in integer expression lowering.


## Initial Symptom

The public integration test sometimes failed with a sort mismatch:

- `tests/test_lua.py::test_pcc_runtime_matches_native[sort.lua]`

The most visible failure form was:

- `pcc`-compiled `onelua.c` exited non-zero on `sort.lua`
- native `cc`-compiled `onelua.c` passed

This already gave the first important constraint:

- same source
- same Lua version
- different compiler behavior

So the bug belonged to the compiler or its fake-libc/runtime assumptions.


## Step 1: Make the Failure Deterministic

The first useful move was not reading code. It was removing randomness.

Two successful reductions were:

1. seed the Lua random generator
2. build array shapes that fail deterministically

Examples that failed under the buggy compiler:

- `math.randomseed(15)` with a large array of random numbers
- a reversed array of integers

Eventually a much smaller deterministic Lua-level shape was identified:

- reversed input
- custom comparator
- minimum failing size around `1921`

This was already much better than "sometimes `sort.lua` fails".


## Step 2: Reproduce with the Real Runtime but a Smaller Harness

Instead of debugging the entire Lua script harness, the next step was to call the real internal sorting code from a small C harness.

The temporary harness did this:

1. `#define main pcc_onelua_main`
2. `#include "onelua.c"`
3. create a Lua state
4. build a table with reversed integers
5. call the real internal `auxsort`
6. verify the table is sorted

This harness had two critical properties:

- native `cc` passed
- `pcc` failed deterministically

That meant:

- the bug still existed without the full Lua test suite
- we were still exercising the real Lua implementation


## Step 3: Identify What Was *Not* Broken

Before chasing codegen, several plausible hypotheses were tested and rejected.

### Hypothesis A: Struct/union layout mismatch

Lua relies heavily on:

- `TValue`
- `StackValue`
- `CallInfo`
- `Table`
- `Node`
- `lua_State`

If layout were wrong, many sorts of stack and table bugs would appear.

A dedicated layout probe compared:

- `sizeof(...)`
- `offsetof(...)`

between native `cc` and `pcc`.

They matched for the critical structures. So this was not a gross data-layout bug.


### Hypothesis B: `luaL_makeseed` changed the Lua stack

Because the failure correlated with random pivoting, `luaL_makeseed(L)` was a suspect.

A probe checked:

- stack top before `luaL_makeseed`
- stack top after `luaL_makeseed`
- visible types in the stack slots

Result:

- stack shape stayed unchanged

So `luaL_makeseed` was not directly corrupting the stack.


### Hypothesis C: the comparator path was broken

Temporary diagnostics showed the comparator sometimes received:

- `nil` and number

That looked like a comparator or stack-copy bug. However, replacing pieces one at a time showed:

- `sort_comp` itself was not the root cause
- `partition` itself was not the root cause

Those functions only became wrong after an earlier mistake violated quicksort's invariants.


## Step 4: Bisect the Sort Implementation by Substitution

A very effective technique was to copy the relevant sort helpers into a temporary harness and reintroduce the original behavior piece by piece.

This isolated four functions:

- `sort_comp`
- `partition`
- `choosePivot`
- `auxsort`

Key findings:

- copied `sort_comp` plus copied `partition` could work
- the failure reappeared when the random-pivot path was restored
- a simplified `auxsort` with no randomization passed
- a fixed tiny `rnd` value passed
- a large real `rnd` value failed

This narrowed the search from "Lua sort is wrong" to:

- something about random pivot arithmetic
- especially with large unsigned values


## Step 5: Reduce Further to Pure C

Once the failure was clearly tied to random pivot arithmetic, the next step was to remove Lua entirely.

A pure C reproducer implemented the same formula used by Lua's `choosePivot`:

```c
typedef unsigned int IdxT;

static IdxT choosePivot(IdxT lo, IdxT up, unsigned int rnd) {
  IdxT r4 = (up - lo) / 4;
  IdxT p = (rnd ^ lo ^ up) % (r4 * 2) + (lo + r4);
  return p;
}
```

With:

- `lo = 1`
- `up = 1921`
- `rnd = 3426782842u`

Results:

- native: `r4=480 p=731 low=481 high=1441`
- old `pcc`: `r4=480 p=475 low=481 high=1441`

This was the decisive reduction.

At that point the bug was no longer:

- a Lua bug
- a stack bug
- a runtime bug
- or even a sort bug

It was an unsigned arithmetic bug in the compiler.


## Step 6: Reason About the Wrong Result

The incorrect result `475` was especially informative because it was *just* outside the legal pivot range.

The legal range was:

- low bound: `lo + r4 = 481`
- high bound: `up - r4 = 1441`

Native produced `731`, which is valid.

`pcc` produced `475`, which is `6` below the lower bound.

That pattern matched signed remainder behavior.

Python arithmetic confirmed it:

- unsigned interpretation gave the expected pivot
- signed 32-bit interpretation gave a remainder of `-6`
- adding that to `lo + r4` produced `475`

So the wrong path was:

1. bitwise XOR computed the right bits
2. the resulting value lost unsignedness metadata
3. `%` used signed remainder


## Root Cause in `pcc`

`pcc` lowers both signed and unsigned 32-bit integers to LLVM `i32`.

That means LLVM type alone is not enough. The compiler must keep track of whether an integer value should be treated as signed or unsigned when later operators run.

The repository already had a metadata mechanism for this:

- `_tag_unsigned`
- `_clear_unsigned`
- `_is_unsigned_val`

The bug was that some operators returned new integer values without preserving the unsigned tag.

The key broken path was in `pcc/codegen/c_codegen.py`:

- `^` returned `builder.xor(...)`
- but did not re-tag the result when the C result type was unsigned

That was enough to break a later `%`.


## Additional Nearby Bug Found During the Audit

Once the main bug was understood, nearby expression paths were audited for the same pattern: "right bits, lost unsignedness".

That audit found another real bug:

- prefix `++x` / `--x` on unsigned integers

The stored variable value was fine, but the expression result itself was not re-tagged as unsigned. This caused failures in expressions such as:

```c
(++x % 960)
(--x % 960)
```

This bug was separate from Lua but belonged to the same semantic family.


## Code Fix

The final fix in `pcc/codegen/c_codegen.py` does four things:

1. preserve unsignedness on `^`
2. preserve unsignedness on unsigned `>>`
3. preserve unsignedness on integer compound-assignment results
4. preserve unsignedness on unsigned prefix `++` / `--`

The important point is not the specific operators. The important point is:

- any expression node that manufactures a new integer IR value must decide whether that result is signed or unsigned in C terms


## Regression Tests Added

Regression tests were added to `tests/test_unsigned_loads.py` for:

- unsigned XOR result followed by modulo
- unsigned XOR compound-assignment result followed by modulo
- unsigned prefix increment result followed by modulo
- unsigned prefix decrement result followed by modulo
- unsigned right-shift result followed by modulo
- unsigned ternary result followed by modulo

These tests intentionally use a signed constant modulus in several cases. That is important because it catches the exact class of bug where the expression result silently stops being unsigned.


## Why the Existing Tests Missed It

Before this fix, the repository already had many tests for:

- unsigned loads
- unsigned comparisons
- pointer arithmetic
- compound operators

What was missing was a specific kind of downstream-sensitive test:

- create an unsigned-producing expression
- immediately feed it into an operator where signedness matters
- make the other operand a plain signed constant when possible

Without that shape, a lot of signedness bugs stay invisible because the bit pattern alone still looks correct.


## General Debugging Lessons

These lessons are useful for humans and AI agents alike.

### 1. Real-program failures are often expression bugs in disguise

A failure in Lua sorting looked like a container or runtime bug. It was really a small integer-semantics bug.


### 2. Shrinking the reproducer is not optional

The winning path was:

- flaky `sort.lua`
- fixed random seed
- smaller failing Lua input
- C harness calling internal sort code
- pure C `choosePivot` reproducer

That sequence made the root cause obvious.


### 3. Prove negatives quickly

The struct-layout probe was valuable because it removed a large class of hypotheses early. Once layout matched, attention could move to expression semantics.


### 4. Audit nearby operators after fixing one

If one integer expression form loses signedness metadata, adjacent forms are suspicious:

- bitwise operators
- shifts
- prefix operators
- compound assignments
- ternary and assignment expressions

Never stop at the first green test if the failure mode is a metadata-propagation bug.


### 5. The best regression tests target semantic boundaries

The strongest tests do not merely assert a final constant. They force the compiler to carry the correct semantic meaning from one operator into the next.


## A Reusable Debugging Template for Similar Bugs

If another real-world program fails in this repository, use this sequence:

1. Reproduce with native and `pcc` from the same source.
2. Remove randomness and environmental noise.
3. Create the smallest integration-level failing harness.
4. Split layout/ABI hypotheses from expression-semantics hypotheses.
5. Reduce to the smallest pure-C reproducer possible.
6. Identify whether the failure is:
   - wrong bits immediately
   - or right bits with wrong later semantics
7. Inspect `c_codegen.py` for metadata propagation, not just arithmetic instructions.
8. Add one micro test and re-run the original integration case.


## Final Outcome

After the fix:

- the pure `choosePivot` reproducer returned the correct pivot
- the pure C sort reproducer passed
- the Lua internal sort harness passed
- `tests/test_lua.py::test_pcc_runtime_matches_native[sort.lua]` passed
- the full suite passed

This was a textbook example of why a good compiler bug investigation should end with:

- a minimal reproducer
- a precise semantic explanation
- and regression tests that lock down the bug class, not just the one original symptom
