# Dict-splat rooted unwind — focused completion evidence

Task: `TEST-P0-DICT-SPLAT-ROOTED-UNWIND`

Claim boundary: native AArch64 Darwin self/no-libpython lowering of dict
literals containing `**mapping` now balances the result-dict temporary root,
operand pins, and owned references on both successful and exceptional paths.
It does not claim final publication of the mutable production runtime archive
or completion of the broad host/pcc1 corpus.

Current working-tree source:

```text
pcc/py_frontend/codegen/literal_lowering.py
sha256 a1f8a6e7fb38e66341b61fb49fa75ee272d394a207bb700e910df8289c61b161
```

Fail-first gates:

```text
PCC_RUNTIME_ARCHIVE=.../libpy_runtime_pcc_py.a \
  uv run pytest -q -x -n0 tests/python/test_dict_literal_temp_release.py
6 passed in 2.82s

PCC_RUNTIME_ARCHIVE=.../libpy_runtime_pcc_py.a \
  uv run pytest -q -x -n0 \
    tests/python/test_native_dict_merge_splat.py \
    tests/python/test_native_dict_update_kwargs.py
2 passed in 4.97s
```

The archive override is deliberate and bounded: it is an integrity-valid
runtime link bundle whose current-checkout compiler checksum is independently
being repaired, while the compiler/lowering under test is current working-tree
source.  All five runtime GC selectors return `created == finalized == 200`,
and the self-backend precise stack-map rejection no longer occurs.

Root cause, rejected shortcuts, and implementation details are recorded in
`docs/investigations/dict-splat-stackmap-rooted-unwind.md`.
