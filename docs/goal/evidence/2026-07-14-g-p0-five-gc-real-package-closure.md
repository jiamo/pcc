# G-P0 five-GC real-package closure

Date: 2026-07-14

Task id: `G-P0-GC`

## Claim boundary

The pinned `simplejson==4.1.1` M1 install supplies one
`_speedups.pcc3-pcc_native-*.so` artifact. One pcc1/self/no-libpython
application executable was compiled against that install and then executed
unchanged under `PCC_GC_BACKEND=0..4`. Every backend selected the native
scanner/decoder/encoder bindings, produced identical nested-container JSON,
matched the CPython source-package oracle, returned zero, and emitted no
stderr. Linkage inspection found no libpython, Python.framework, or LLVM
dependency.

The negative import path also retained the mode-labeled
`PCC-PYEXT-IMPORT-001 [pcc-native/no-libpython]` diagnostic. The installed
artifact's source-build and ABI provenance remain covered by
`M1-PCC-NATIVE-SOURCE-BUILD`; this slice proves five-GC execution of that same
artifact rather than rebuilding five variants.

## Failure and implementation

The first full-matrix attempt exposed a GC1 pcc1-to-pcc2 failure after module
publication while profile JSON was being written. The cached `StopIteration`
class had been swept and its storage reused: built-in exception classes were
held in `py_exc_classes`, but those cache entries were absent from the GC root
slot visitor and therefore from relocation updates.

The runtime now exposes each built-in exception cache entry as a slot through
`py_subs_exc_cache_slot`. The C and pcc-Python GC implementations route the
same slot family through current-root marking, GC3 promotion, and GC4 remap.
The pcc-Python exception table count was also brought into parity with the
19-entry public table. Focused tests lock the C/pcc-Python mirror contract and
prove that a cached `StopIteration` class is visited exactly once and survives
an explicit GC1 collection.

Supporting generic ownership fixes keep native file context-manager values,
dynamic iterators, and dynamic loop targets rooted for their live ranges and
release their owned references at the matching boundary. None of these paths
contains a `simplejson` package-name dispatch.

## Gates

- Focused root/update/profile stress:
  `gtimeout 300s env -u LC_ALL uv run pytest -q -n0
  tests/python/test_gc_root_precision.py
  tests/python/test_gc_update_referents.py
  tests/python/test_gc_incremental_profile_stress.py`
  -> `38 passed in 80.65s`.
- Dedicated GC1 full self-host localization gate ->
  `1 passed in 2142.04s (0:35:42)`. This proves the original pcc1-to-pcc2
  profile-writing boundary and the subsequent pcc2-to-pcc3 fixed point.
- Five-GC production contract:
  `gtimeout 300s env -u LC_ALL uv run pytest -q -n0
  tests/python/gc_production_contract`
  -> `140 passed in 32.36s`.
- Real-package gate with
  `PCC_CURRENT_PCC1=build/bootstrap-pytest-shared-stage1/pcc1`,
  `PCC_REQUIRE_CURRENT_PCC1=1`, and
  `PCC_M1_SIMPLEJSON_SITE=build/m1-site/simplejson-4.1.1`:
  `uv run pytest -q -n0
  tests/python/test_m1_simplejson_import_behavior.py`
  -> `2 passed in 27.14s`.
- Formal xdist five-backend self-host matrix, containing the unchanged GC0,
  GC1, GC2, GC3, and GC4 test files, used a `14400s` outer watchdog because
  measured machine contention made a single backend take more than 35 minutes:
  `5 passed in 12842.97s (3:34:02)`. Every backend completed
  pcc1-to-pcc2-to-pcc3, strict self-backend/no-libpython execution, and the
  fixed-point assertions. A final pytest summary was observed; dots alone were
  not accepted as evidence.

## Result and limits

`G-P0-GC` is `DONE_STRONG`: the same pinned real package artifact and app pass
under all five production GC selections, teardown is a clean zero/stderr-free
exit, the production GC contract is green, and the five-GC self-host matrix is
green.

This evidence does not claim arbitrary Python packages or arbitrary extension
ABIs work. It proves one pinned M1 third-party pcc-native vertical canary and
the generic GC/ownership mechanisms exercised by it.
