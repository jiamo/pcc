# Investigation: native binary file write serializes bytes with `repr`

## Status

active

## Problem Description

pcc's native file object tracked whether `open(..., mode)` contained `b` and
returned bytes from binary reads, but `py_file_write` ignored that flag. It
coerced every value through `str()`, so writing `b"PCCNOBJ\x01"` to a `wb`
stream produced the ASCII text `b'PCCNOBJ\\x01'`.

This blocked worker `.pco` publication: v30 pcc1 generated a structurally valid
native-object payload, wrote a 59,585-byte repr instead of the 17,233-byte raw
payload, and the host linker rejected its magic/version.

## Repro

Retained v30 artifact:

`build/no105-summary-pco-stage1-v30/work/`
`stage1_function_smoke.pcc-pco.12930/module_0.direct.pco`

Its first bytes were `62 27 50 43 43 4e 4f 42` (`b'PCCNOB`) instead of
`50 43 43 4e 4f 42 4a 01` (`PCCNOBJ\x01`).

## Test [CONFIRMED]

`tests/python/test_native_file_readline_seek_tell.py::`
`test_native_file_binary_write_preserves_bytes_like_payloads` writes bytes,
bytearray and memoryview values to `wb`, reads them through `rb`, and diffs
write counts plus exact hex against CPython. Before the fix pcc wrote repr
strings; after the fix both runtime implementations pass.

## Proposals

- No.1 Honor binary mode and write raw bytes-like payloads [CONFIRMED focused]

## No.1 Honor binary mode and write raw bytes-like payloads

### Code Change

Both C and freestanding pcc-Python `py_file_write` now branch on the existing
binary flag. Binary mode accepts bytes/bytearray and follows memoryview bases
through `pcc_gc_load_ptr`, then calls `fwrite` on the raw payload. A non-bytes-
like value raises TypeError. Text mode retains its existing behavior.

### CONFIRMED focused

The red-first binary roundtrip passed under the pcc-Python runtime (21.46s) and
C mirror (2.94s). The full native file/open suite passed 9/9. A rebuilt pcc1
worker `.pco` canary remains required before closing.
