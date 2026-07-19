# PKG-P1 native-extension ladder — DONE_STRONG

Claim boundary: two real packages traverse the same package-name-agnostic
local-source/site/pcc1 no-libpython runner; this does not claim arbitrary
CPython extension compatibility or complete cffi/pybind11/MLX/vLLM support.

Implemented and verified:

- `tests/integration/pcc_native_e2e.py` invokes the compiled pcc1 binary for
  install/compile/run; it has no package-name branches.
- wheel, simplejson (cold source install), and NumPy use the same runner.
  The integration gate passed `3 passed in 93.79s`.
- simplejson exercises a second package through the generic mechanism and
  returns correct encode/decode results under pcc1 with libpython disabled.
- the native ABI tag/build/import checks, acquire-hint behavior, and
  PCC-PKG-004 rejection gate passed `6 passed in 5.18s`.
- the final pcc1 package artifact also passed the focused wheel/NumPy and
  simplejson preflights recorded during the slice.
- the final no-libpython self-host matrix passed:

  ```text
  5 passed in 2.43s
  ```

The five-backend result was obtained after completing the interrupted GC4 and
GC2 workers individually (`1 passed` each), then rerunning the exact matrix
command against content-addressed success manifests. The first interrupted
parallel attempt had no final pytest summary and is not counted as evidence.

Open boundary: empty for this row. Long-tail packages remain separate future
work and are not promoted by this evidence.
