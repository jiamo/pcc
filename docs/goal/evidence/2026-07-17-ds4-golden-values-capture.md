# ds4 pinned golden values capture — 2026-07-17

Task: `DS4-P0-INVENTORY-ORACLE`.

## Claim

The pinned ds4 inventory now includes the actual small oracle values, not only
paths to an external checkout. The official and local-golden fixtures are
captured byte-for-byte with source commit/path/SHA-256 metadata and meaningful
parser checks. This remains an external-reference oracle only; no pcc ds4
compile, runtime, model-quality, or GPU support claim is attached.

## Captured evidence

- Pinned checkout HEAD, read directly from `.git/HEAD` and its ref file:
  `80ebbc396aee40eedc1d829222f3362d10fa4c6c`.
- `official.vec`: SHA-256
  `0223bbe1eaa3b626be87849df389af91c3f3f6e6b0d4436baf2dbb6ed624b1ac`,
  5 cases and 17 selected-token/top-logprob steps.
- `local-golden.vec`: SHA-256
  `e9003e7d1adfda3a12e0358580fbdbd84bfd62a10fb3746a43cd0e692f42bdfa`,
  one 64-entry ranked logit frontier.
- Source vector manifest SHA-256:
  `4eac228274116bc84f3b6e41f543cbf678008052530e82531f19c0bb00ec7723`.
- The live optional cross-check no longer spawns Git; it reads checkout
  metadata and compares the external/captured bytes directly.

## Gate

- `tests/ds4_oracle/test_ds4_inventory.py`: `12 passed in 0.20s`.
- The current machine had the pinned external checkout, so this run included
  the live commit, captured-byte, and source-manifest-hash checks; it did not
  skip them.

No ds4 compilation, model execution, GPU backend, GCC suite, bootstrap, or
five-GC matrix was run for this inventory/oracle closure.
