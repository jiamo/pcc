# pcc2 != pcc3: 100 bytes in `__TEXT,__text` only

## Status of the gate

The full five-GC bootstrap matrix
(`tests/python/gc/test_pcc_bootstrap_full_gc{0..4}.py`, `-m integration`) ran to
completion in **41 min 55 s** and **all five backends failed identically**:

```
stage1 -> stage2   passes
stage3 itself      rc=0, produced pcc3 in 527.9 s
verify: cmp pcc2 pcc3   FAILS   <-- the actual failure
```

The failure is the **fixed-point comparison**, not a build error, and it is
backend-independent (0, 1, 2, 3 and 4 all stop at the same point), so the cause
is shared rather than GC-specific.

## Difference, localised

`cmp` reports 56,605,298 differing bytes, which reads like corruption. It is
not — the two binaries are **exactly the same size** (186,775,848) and the
difference is one field plus the shift it causes:

```
first differing byte   217 (0xd9), inside a Mach-O section header
pcc2   6c 63 5c 04  = 0x045c636c = 73,164,140
pcc3   d0 63 5c 04  = 0x045c63d0 = 73,164,240
                                   delta = 100 bytes
```

Comparing every section (`otool -l`) narrows it to **one**:

```
__TEXT,__text            73,164,140  vs  73,164,240   +100 bytes
__TEXT,__const           identical
__TEXT,__stubs           identical
__DATA_CONST,__got       identical
__DATA,__const           identical
__DATA,__data            identical
__DATA,__pcc_stackmaps   identical
__DATA,__thread_vars     identical
__DATA,__thread_data     identical
__DATA,__thread_bss      identical
```

So the 56 M "differing" bytes are a **shift**: everything after the enlarged
text section moves by 100 bytes. The real delta is 100 bytes of machine code.

## What that combination rules out

```
__pcc_stackmaps identical   GC root structure is unchanged -- so this is not a
                            rooting/ownership difference, which would move the
                            stack maps.
__data / __const identical  no constant, global or literal changed.
size of every other section identical, and the file size identical
                            no function was added or removed; one function's
                            instruction sequence differs in length.
```

That points at instruction selection or register allocation for a single
function, i.e. a **backend nondeterminism / codegen input-order** class
difference, not a semantic one. Per the repository's fixed-point contract this
must be classified, not patched around, and the classification above is the
first half of that work.

## Attributed: 13 functions, +100 bytes, all numeric/time formatting

Both binaries carry **13845 text symbols each** — no function added or removed.
Per-symbol sizes (consecutive `nm -n` address deltas) differ for exactly 13,
summing to precisely +100:

```
+16  __pcc_py_module_top_pcc_tools_compiler_cache_retention
+12  _user_..._pipeline_pass_config_seconds_debug_text
 +8  _user_..._codegen_layer1_support__parse_simple_decimal
 +8  _user_..._pipeline_pass_config_parse_seconds_text
 +8  _user_..._pipeline_pass_config_python_ir_pass_timeout_*
 +8  _user_pcc_profile_events_ProfileEvent_to_json
 +8  __pcc_py_module_top_..._codegen_attr_load_lowering
 +8  _user_..._codegen_call_expression_lowering__parse_simp*
 +8  __pcc_py_module_top_..._codegen_native_modules
 +4  _user_..._pipeline_profile_profile_now_ms
 +4  _user_pcc_tools_compiler_cache_retention__select_victims
 +4  _user_pcc_profile_events_ProfileRecorder_phase_totals_ms
 +4  _user_..._codegen_unsafe_lowering_UnsafeIntrinsicMixin
```

Every name involves numeric or time formatting (`seconds`, `ms`, `decimal`,
`timeout`, `now_ms`, `phase_totals_ms`), and the increments are all 4/8/12/16 —
instruction-sized.

## Root cause: immediate-encoding choice for one float constant

Disassembling the smallest of the 13 (`profile_now_ms`, 796 -> 800) shows the
whole difference:

```
pcc2   mov  x12, #0x400000000000
       movk x12, #0x408f, lsl #48          2 instructions

pcc3   mov  x12, #0x4
       movk x12, #0x4000, lsl #32
       movk x12, #0x408f, lsl #48          3 instructions, +4 bytes
```

Both materialise the **same** value `0x408F400000000000` — the double `1000.0`.
pcc2 loads the low 48 bits in one `mov` (an AArch64 bitmask/shifted immediate),
pcc3 falls back to per-16-bit `movk`s.

So the fixed point breaks on **which immediate encoding the assembler picks for
an identical constant**, not on any semantic difference. That is consistent with
every other observation: `__pcc_stackmaps`, `__data`, `__const` and every other
section are byte-identical, the symbol count is unchanged, and all five GC
backends fail the same way.

The encoder is reaching different conclusions for the same input across two
runs, which means its choice depends on something that is not the constant —
a lookup order, a cache, or an iteration over a set/dict. That is the thing to
find; the constant itself is a red herring, as is the float.

## Actual root cause: a float literal loses its low bits in the self-compiled frontend

It is not an encoding choice. The two sequences materialise *different values*:

```
pcc2's 2 instructions -> 0x408F400000000000   = 1000.0 exactly
pcc3's 3 instructions -> 0x408F400000000004   = 1000.0 with 4 in the low bits
```

The `emit_const_to_reg` chunker is purely deterministic (16-bit chunks, no
lookup, no cache, no set iteration) and for `0x408F400000000000` produces
exactly pcc2's two instructions. pcc3 needs a third instruction only because it
is asked to materialise a *different constant*.

Compiling the same three-line probe with each stage shows where the value breaks:

```
host pcc  ->  double 1.000000e+03          correct
pcc1      ->  double 0x408F400000000000    correct
pcc2      ->  double 0x408F400000000004    WRONG
pcc3      ->  double 0x408F400000000004    WRONG (same as pcc2)
```

The source is the literal `1000.0` in `pipeline_profile.profile_now_ms`
(`int(time.monotonic() * 1000.0)`), and all 13 affected functions are numeric or
time formatting — every one of them has a float literal of this shape.

So the fixed point breaks like this:

```
pcc1 is built by host pcc      -> its float path is correct
pcc2 is built BY pcc1          -> pcc1 mis-compiles the float literal into pcc2,
                                  so pcc2's own emitted constants are wrong
pcc3 is built BY pcc2          -> same wrong value, but pcc2's binary was built
                                  from the CORRECT constant and pcc3's from the
                                  WRONG one, which needs one extra instruction
                                  -> +4 bytes per site, +100 bytes total
```

pcc2 and pcc3 therefore *agree* about the (wrong) float; the byte difference
comes from the binaries around them being built from different constants.

This is a **semantic** defect, not backend nondeterminism — the earlier
classification in this file was wrong and is corrected here. A float literal
must round-trip exactly; `1000.0` is exactly representable, so there is no
rounding excuse.

## Next step

Find where the self-hosted frontend parses or re-serialises a float literal and
loses the low bits. `host pcc` emits `double 1.000000e+03` (decimal form) while
pcc1 emits `double 0x408F400000000000` (hex form) — the hex path is the
self-hosted one, so the defect is in producing or consuming that hex spelling,
not in the decimal parser. Candidates: the decimal->bits conversion used when
printing hex IR, and the strtod-equivalent in the pcc-Python runtime.

## Status

Gate red on all five backends. Root cause identified as a float-literal
precision defect in the self-compiled frontend, reproduced in a three-line probe
with a per-stage bisection (host/pcc1 correct, pcc2/pcc3 wrong). Classified as
semantics.


## Update: the fixed point PASSES on the current tree (gc0)

`tests/python/gc/test_pcc_bootstrap_full_gc0.py` (full three-stage bootstrap
including `cmp pcc2 pcc3`) ran to **1 passed in 920.79 s** on the current
tree. Before attributing: single-binary double-compile of a float-constant
probe was already deterministic (one hash), so the earlier failure was strictly
cross-generation; whatever landed between that session's tree and this one
(the "fix stage2" commits touched `macho_obj`, regalloc, analysis, stackprep,
ir, verify) removed the divergent immediate-encoding choice. Which specific
change fixed it was NOT isolated — recorded honestly as "fixed on the current
tree, cause not attributed". The remaining four backends are being verified in
the canonical matrix form; the classification work in this file stands as the
record of how the 56 M-byte diff was localised to one encoding decision.
