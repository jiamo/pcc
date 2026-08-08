# Investigation: C LVN reuses a string literal as an array initializer

## Status

resolved

## Problem Description

The C frontend's portable freestanding memory/string differential produced a
corrupted second local buffer.  Direct code generation retained both
`char[20]` initializers, while the normal high-tier pipeline removed the
stores for the second array.

## Repro

```bash
gtimeout 30s env -u LC_ALL uv run pytest -q -n0 \
  tests/c/test_lvn_translation.py::test_lvn_does_not_reuse_string_literal_as_an_array_initializer
```

Expected: the second declaration keeps its string `Constant` initializer.
Observed before the fix: its initializer was rewritten to `ID(first)`.

## Test [CONFIRMED]

The focused test above failed deterministically before the source change.  The
public symptom was also confirmed by:

```bash
gtimeout 180s env -u LC_ALL uv run pytest -q -n0 \
  tests/python/test_freestanding_mem_str.py::test_freestanding_mem_str_matches_host_libc_for_portable_c_surface
```

Before the fix the left-overlap case printed uninitialized bytes instead of
the host result.

## Proposals

- No.1 Exclude C array initialization from source-level LVN [CONFIRMED]

## No.1 Exclude C array initialization from source-level LVN

### Code Change

`_LocalValueNumbering._have_compatible_types` now rejects cached or target
types containing the array marker `[]`.  Repeated scalar and pointer
expressions remain eligible.  This is the narrow semantic boundary: C array
initialization is not an ordinary value expression, so
`char second[] = "x"` cannot be rewritten as initialization from another array.

### CONFIRMED

```text
8 passed in 0.14s
  tests/c/test_lvn_translation.py

1 passed in 1.11s
  portable C libc differential through the freestanding route
```

## Report

Proposal No.1 fixed the first mutating pass identified by a pass-by-pass AST
audit.  The change preserves scalar LVN while preventing aggregate
initialization from being converted into an invalid source-level copy.
