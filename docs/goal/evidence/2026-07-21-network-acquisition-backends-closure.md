# Network package acquisition backends and NumPy command gate

## Provenance repair (2026-07-22)

The evidence below proved acquisition-mode, URL, hash, target-Python, and host
assistance fields, but a later full package gate found that the downstream
install manifest relabeled an owned index acquisition as
`resolved_from: direct` after receiving its immutable local cache path.  The
install boundary was repaired by carrying the acquisition origin alongside
the immutable artifact path.  Host and owned acquisition now retain
`resolved_from: index-url` while `source_path` remains the verified cache
artifact.  The combined package/file group passed 77 tests with 3 skips, the
original current-pcc1 no-host install scenario passed in 6.65 seconds, the
complete integration suite passed 4551 tests with 12 skips in 669.70 seconds,
and the complete non-integration suite passed 9503 tests with 28 skips in
970.63 seconds.

Task: `PKG-P0-NETWORK-ACQUISITION-BACKENDS`

Date: 2026-07-21

## Claim

`pcc1 -m pip install <requirement>` now accepts a bounded bare requirement and
routes acquisition through explicit `auto`, `host`, `owned`, or `offline`
modes. The report separates acquisition provenance from pcc-native build and
runtime claims. `auto` now selects the owned, hash-verified Simple Repository
path; explicit `host` remains a labeled compatibility mode. This avoids host
pip's PEP 517 metadata build before pcc performs its own native source build.
The command-shaped NumPy gate proves that an ordinary
`pcc1 -m pip install numpy` invocation can acquire a compatible source
artifact, build/install the required pcc-native NumPy extensions, compile a
self-backend no-libpython application, and run basic array addition under all
five GC backends.

This does not claim a general dependency resolver, PEP 517 build isolation, or
complete NumPy API compatibility. `auto` currently selects the explicitly
labeled owned backend. The owned backend implements the standards-based Simple
Repository API download and hash verification path for the deliberately strict
single-requirement subset; explicit owned mode rejects build-isolation shapes,
while auto may delegate a supported source tree to pcc's native builder and
still fails closed if that builder cannot satisfy it.

## Implemented boundaries

- `pcc.package.acquire` parses the supported requirement subset, selects and
  reports acquisition mode, records index and artifact origin, verifies
  SHA-256, stores immutable cached artifacts, and distinguishes the requested
  target Python from the host interpreter used for acquisition.
- Host acquisition remains an explicit compatibility mode. It delegates only
  the acquire step to a bounded host pip subprocess and discovers a host
  interpreter that actually has pip instead of assuming the repository
  virtualenv does.
- Owned acquisition fetches and parses PEP 503/691 Simple Repository data,
  follows the selected source link over the runtime's native HTTPS path, and
  verifies the advertised digest without a host Python/pip subprocess.
- `pip_shim.py` and the compiled-stage `cli_bootstrap.py` share the same mode
  labels and feed the existing local-artifact installer; local paths and
  `--find-links` remain supported.
- Meson replay selects only extension targets required by the current NumPy
  import/array surface and caps package C compilation at two workers by
  default. The installed site contains pcc-native extensions rather than
  CPython-tagged extension artifacts.
- Target-Python-aware selection chooses the NumPy 2.4.x source line for the
  supported Python 3.11 target instead of acquiring an incompatible newer
  source solely because the host interpreter is newer.

## Generic compiler/runtime regressions exposed by the real gate

- Set values now work through `list(set)`, tuple unpacking with correct arity
  errors, real `dict.keys()` set operators, and checked dynamic peer operands;
  in-place set operations preserve left-hand identity. These are generic
  frontend rules, not NumPy source recognition.
- A function-local `globals()` result used to release the module side-table's
  borrowed dictionary and leave later attributes pointing at freed memory.
  The call-result boundary now retains it. A package-neutral regression and a
  large relative-extension wrapper cover the lifetime and binding behavior.
- The NumPy-specific runtime trace used to classify the stale dictionary was
  removed after the package-neutral failure was reproduced.

## Required gates

- `PCC_ACQUIRE_TEST_PCC1=/tmp/pcc1_network_acquisition_final` plus
  `tests/python/test_package_network_acquisition.py` — **16 passed in 6.14s**.
- Package import/install/ABI regression batch — **41 passed, 1 warning in
  3.79s**.
- Command-shaped default-environment NumPy integration with the current pcc1 —
  **2 passed in 213.02s** after removing an accidental uv project build. The
  same emitted application ran NumPy 2.4.x array
  addition under GC0, GC1, GC2, GC3, and GC4 with host Python and package-site
  discovery disabled at runtime; linkage checks rejected libpython/Python.
- Focused set/module-namespace/relative-extension regressions — **18 passed in
  7.41s**, followed by the self-host-sensitive dynamic set test — **1 passed in
  33.84s**.
- Fallback and bootstrap baseline batch — **27 passed, 4 skipped in 257.43s**.
- Multi-file and compiled bootstrap-shim regression batch, with inner frontend
  and self-backend work capped at two workers — **131 passed in 347.51s**.
- Goal-state and startup-document consistency gate — **10 passed in 0.16s**
  after regenerating the derived startup state from the structured board.

## Open boundary

Empty for this task. Broader dependency resolution, build isolation, and NumPy
API coverage remain explicitly outside this acquisition slice.
