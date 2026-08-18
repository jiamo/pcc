# Investigation: `str +=` drops the old value and `len(call)` drops an owned list result

## Status

active — CONFIRMED by micro-benchmarks; fix pending (test-first).

## Problem Description

Two generic pcc codegen ownership leaks, found while attributing the 3.27 GiB
peak of a pcc1 native-object worker
(`pcc1-small-lane-native-object-worker-memory.md`).  Both are visible in any
compiled program, both are pervasive in the compiler's own source, and both
contribute directly to the 5-10x pcc1-vs-host memory amplification.

## Repro

Programs under `/private/tmp/claude-501/.../scratchpad/leak/` (to be turned
into `tests/python/`), compiled with
`uv run pcc --backend self --python-libpython=off --ir-scaffold=on`, measured
with `/usr/bin/time -l` maximum resident set size; runtime baseline 3-36 MB.

## Test [CONFIRMED]

```text
main(): s = "abcdefghij"*2000; cur = ""; for ch in s: cur = cur + ch     3 MB
main(): same loop with            cur += ch                             299 MB
g(): cur = ""; 19x cur += "x"; return len(cur)   called 300k times     597 MB
f() -> list (2 appends); main: x = f(i); total += len(x)  300k          3 MB
f() -> list;             main: total += len(f(i))         300k        116 MB
f() -> str;              main: total += len(f(i))         300k         36 MB
```

Leak A: augmented assignment `+=` on a str local does not release the
previous value (plain `x = x + y` does).  Leak B: `len(...)` applied directly
to a call returning an exact list keeps the owned result alive (assigning
first, or a str result, is fine), i.e. a consumer-boundary ownership hole in
the exact-list `len` path.

## Proposals

- No.1 release the old binding in str `+=` lowering `[pending]`
- No.2 release owned exact-list call results consumed by `len` `[pending]`

Each needs a red-first RSS test in `tests/python/`, the focused ownership
gates, byte-stable IR where expected, and a pcc1 canary before claiming.

## Update 2026-09-03 — both leaks fixed and pcc1-verified; the str-temporary class remains

### No.1 release the old binding in str `+=` `[CONFIRMED]`

`assignment_statement_lowering._emit_augassign` now routes a Name target
typed `str`/`bytes` through the exact Assign path (a synthesized ``BinOp``),
exactly as the exact-int branch already did: str/bytes are immutable, so
``x += y`` is ``x = x + y`` and inherits lhs pinning, RHS error cleanup,
owned-result replacement and the local owned flag.  The generic augassign
store never released the previous owned value.
Red test `str_iadd` 314 MB -> green (< 90 MB); 20k-char output unchanged.

### No.2 release owned exact-list call results consumed by `len` `[CONFIRMED]`

`builtin_type_attr_lowering._emit_len_call` now calls `_gc_release_if_owned`
on its operand after the typed `py_*_len` (and the boxed generic path).  The
classifier already answered "owned" for a user call whose return type is an
object; only the release was missing.  Red test `len_of_list_call` 122 MB ->
green; the `len_of_str_call` control stays correct and well under the limit.

### Gates

28 focused augassign/return/attr/subscript-ownership tests, bootstrap gate
baseline 2, fallback baselines 43 (xdist, 568 s, full summary), Stage1 v8 from
the frozen worktree (rc 0, canary 42, libSystem-only, sha f2a8d9a2): the
compiler built with the new lowering compiles cli_bootstrap and
exception_lowering correctly.  cli_bootstrap's assembly differs from the v4
pcc1's only in the intended shape (added `pcc_gc_pin/unpin/release/load_ptr`
around the rewritten sites plus label renumbering); exception_lowering's
`.pco` is byte-identical.

### Effect on the pcc1 native-object worker

module 151 native-object replay: 3.27 -> 2.92 GiB, 19.5 -> 19.8 s.
cli_bootstrap serial worker: 6.06 -> 6.14 GiB (noise): these two leaks are
not that worker's owner.

### Still open — owned str temporaries consumed by other consumers

Every micro-benchmark whose loop body produces a str temporary consumed by
another expression (`str(i)` under `len`, `base.strip().lower()`) sits at
36 MB where the equivalent temporary-free loop sits at 3 MB: about 110 bytes
per iteration of unreleased str results.  `len` is now fixed; method
receivers and other argument positions are the remaining consumers.  This is
the generic consumer-boundary release owned by
PY-P0-CONSUMER-BOUNDARY-OWNERSHIP-LEDGER; the probes in
tests/python/test_ownership_str_iadd_and_len_call_result.py are the template.
