# S-P0 strict self-backend M1 closure

Date: 2026-07-13

Task: `S-P0-SELF`

Status: `DONE_STRONG`

## Claim boundary

This evidence proves that the installed real simplejson 4.1.1 pcc-native
canary application compiles and executes through `backend=self` with
`python-libpython=off` and both host process escape hatches disabled. The
produced application has no libpython or LLVM dynamic dependency. It also
proves that the final shared compiler source completes the GC0
`pcc1 -> pcc2 -> pcc3` chain with normalized pcc2/pcc3 identity.

This does not prove the same installed extension under GC1..4. That remains
the finite open boundary of `G-P0-GC`.

## Generic fixes

- The compiled AArch64 Darwin self emitter is invoked as a pcc-native
  component; strict pcc1 emission does not call host Python or host pcc and
  does not silently select LLVM.
- Reachability filtering uses stable integer block-name keys plus collision
  buckets, so compiled string hashing cannot drop the parsed entry block.
- Numeric SSA values retain numeric ids through used-value analysis, stack
  preparation, materialization, and call lowering.
- The pcc-Python `_signature_valid` mirror uses the same owned tuple-get
  contract as the C semantic runtime.
- Bootstrap setup explicitly prepares a fresh host-built pcc-Python runtime
  archive before asking standalone pcc1 to run its publish smoke.

No compiler or runtime dispatch checks the `simplejson` package name.

## Strict M1 application gate

```text
gtimeout 900s env -u LC_ALL \
  PCC_CURRENT_PCC1=build/bootstrap-compat-runner-pcc1/pcc1 \
  PCC_REQUIRE_CURRENT_PCC1=1 \
  PCC_M1_SIMPLEJSON_SITE=build/m1-site/simplejson-4.1.1 \
  uv run pytest -q -n0 tests/python/test_m1_simplejson_import_behavior.py
```

Result: `2 passed in 79.87s`.

The positive executable reports active native scanner/decoder/encoder
bindings, exact nested-container behavior parity with CPython, and only the
platform system library in its dynamic dependency list. The negative case
retains the mode-labelled `PCC-PYEXT-IMPORT-001 [pcc-native/no-libpython]`
diagnostic.

## Compiler closure gates

```text
gtimeout 700s env -u LC_ALL uv run pytest -q -n0 \
  tests/python/test_fallback_baseline.py \
  tests/python/test_ir_py_fallback_baseline.py \
  tests/python/test_bootstrap_gate_baseline.py
```

Result: `23 passed, 4 skipped in 343.58s`.

```text
gtimeout 3000s env -u LC_ALL uv run pytest -q -n0 \
  tests/python/gc/test_pcc_bootstrap_full_gc0.py
```

Result: `1 passed in 1952.44s (0:32:32)`.

The GC0 gate rebuilt the final-source pcc1, completed pcc1-to-pcc2 and
pcc2-to-pcc3 native self emission, rejected libpython linkage, and verified
pcc2/pcc3 normalized identity.

Bootstrap harness helper tests also pass: `11 passed in 1.44s`.
