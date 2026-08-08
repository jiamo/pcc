# CLI helper duplication pinned instead of merged

Date: 2026-08-01

Task: `AUD-P2-CLI-SHARED-HELPER-DUPLICATION`

## The defect

`pcc/cli_core.py` and `pcc/cli_bootstrap.py` each carried their own copy of
the FNV-1a hashing (`_fnv1a_update_u64`, `_fnv1a_update_bytes_u64`) and the
`.py` source walk (`_iter_py_sources_under`). Two copies of a hash function
are two chances for the run-cache key to drift between the host CLI and the
bootstrap CLI — and a content-addressed cache cannot detect that drift on its
own; it shows up as a mysterious miss, or worse, a wrong hit.

## What was tried first, and why it was rejected

The obvious fix — one shared module, both CLIs import it — was implemented
and did work end to end: helper outputs matched all three ways before the
merge, and stage1/stage2/stage3 completed with the pcc2/pcc3 fixed point
intact.

The fallback ratchet then failed:

```text
pcc.cli_bootstrap: 47 vs baseline 0 (+5.0%)      (OFF and ON mode)
```

`pcc/cli_bootstrap.py` compiles with **zero** CPython fallbacks as a single
translation unit. A cross-module import turns those helper calls into
getattr-on-not-in-unit bridges, which is worth 47 fallbacks in the per-module
ratchet. Rewriting the baseline to absorb that is exactly what the protocol
forbids, so the shared-import shape was reverted for the bootstrap CLI.

## What landed

- `pcc/cli_shared_paths.py` (new): the single source, written in the
  pcc-Python subset (explicit index loops, no augmented xor-assign, no
  comprehensions). The bodies are the bootstrap copies verbatim — valid host
  Python too — so deduplicating toward the subset form keeps compiled
  behavior byte-identical instead of re-deriving it.
- `pcc/cli_core.py` imports from it (no fallback cost).
- `pcc/cli_bootstrap.py` keeps a self-contained copy, with a comment stating
  why (zero-fallback single-unit property, 47-fallback cost, pinning test).
- `tests/python/test_cli_shared_helpers_contract.py` (new) makes the
  remaining duplication safe: it compares the two implementations as parsed
  **ASTs** (same code, not merely same behavior) and cross-checks outputs
  over empty/long/non-ASCII text, empty/full-byte-range/1KiB byte strings,
  and real directory walks.

## Commands and results

```text
tests/python/test_cli_shared_helpers_contract.py     16 passed
tests/python/test_fallback_baseline.py
tests/python/test_ir_py_fallback_baseline.py         27 passed in 251.72s
  (the same pair was 3 failed with the shared-import shape)
stage1: S1=0, zero worker failures, libc imports still 64, pcc1 --help runs
stage2/stage3: pcc2 and pcc3 metadata-normalized byte-identical
```

## Supported claim

The drift risk the row names is closed: the two implementations can no longer
diverge silently, because a test fails the moment their ASTs differ. One copy
is now the shared module used by the host CLI; the bootstrap CLI's copy is
retained deliberately to preserve its zero-fallback single-unit property, and
that trade is recorded where the code is.

## Not proven

- The row also lists run-cache path computation as duplicated. That was not
  touched: those functions differ between the two CLIs (different cache
  roots and key material), so they are not the same helper wearing two names.
- The duplication is pinned, not eliminated. Eliminating it needs the
  per-module fallback bridge cost to go away first — that is a frontend
  cross-module-import lowering question, not a CLI question.
