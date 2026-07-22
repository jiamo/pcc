# pcc1 owner-default evidence

## Claim

For Python inputs, the compiled-stage CLI resolves omitted modes to
`backend=self`, `python-libpython=off`, and `ir-scaffold=on` before compilation,
cache selection, diagnostics, or profile publication. Explicit LLVM,
libpython, scaffold, and environment overrides remain separately labeled. The
host C/project CLI default is unchanged.

This closes compiler-mode ownership only. Default package-site discovery is
the next package-environment task; this evidence does not claim that bare
`pcc1 np_demo.py` can discover a prior NumPy install yet.

## Evidence

- Owner/default, explicit override, diagnostic, profile, and unchanged-host-C
  contracts: 10 passed in 0.19s.
- Compiled bootstrap-shim and pcc1 Python smoke surface: 149 passed with 1
  deselected in 374.79s.
- Current-source GC0 pcc1-to-pcc2-to-pcc3 integration and identity contract:
  1 passed in 1.14s from the verified current success manifest.
- The forced-rebuild five-GC matrix separately passed all five backends in
  1306.29s.
- Complete integration passed 4551 tests with 12 skips in 669.70s; complete
  non-integration passed 9503 tests with 28 skips in 970.63s.

Unsupported self-backend behavior fails through the mode-labeled diagnostic
path; the self path has no implicit LLVM or libpython fallback.
