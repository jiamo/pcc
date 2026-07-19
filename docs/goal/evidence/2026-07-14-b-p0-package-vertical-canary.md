# B-P0 real pcc-native extension-package vertical canary

Date: 2026-07-14

Task: `B-P0-PKG`

## Result

The pinned, unmodified `simplejson==4.1.1` sdist now has one current,
end-to-end M1 command chain. The current shared pcc1 installs the source as a
pcc-native extension with both host escape hatches disabled, compiles the
real package application through strict self/no-libpython mode, and runs the
same executable unchanged under GC0..4. Native scanner, decoder, and encoder
bindings are active; behavior matches the CPython source-package oracle; every
run exits zero with empty stderr.

The official PyPI sdist used by the gate has SHA-256:

```text
c08eb9f7a90f77ae470e19a07472e9a79ebc0d1c2315d86a72767665bd5ba79f
```

This equals the digest in the PyPI 4.1.1 release metadata.

## Fresh no-host install

```text
gtimeout 180s env -u LC_ALL \
  PCC_HOST_PYTHON=/usr/bin/false \
  PCC_HOST_PCC=/usr/bin/false \
  build/bootstrap-pytest-shared-stage1/pcc1 -m pip install \
  /tmp/simplejson-4.1.1.tar.gz \
  --abi pcc-native \
  --target build/m1-exit-site-20260714 \
  --cache-dir build/m1-exit-cache-20260714 \
  --json
```

Result: exit 0 in 1.11 seconds. The C compile and native link actions both
returned zero. The fresh site contains exactly one native artifact:

```text
simplejson/_speedups.pcc3-pcc_native-macosx_arm64.so
```

The install manifest records:

```text
manifest_schema: pcc.package-manifest.v1
schema_version: 1
abi_mode: pcc-native
install_success: true
linkage_native_package_claim: true
links_libpython: false
no_libpython_runtime: true
uses_cpython_extension_abi: false
```

The top-level `native_package_claim` remains false at install time because the
installer did not execute an import probe. This is an honest install/import
claim boundary: the immediately following strict application gate supplies
the import and behavior proof. `otool -L` lists only the artifact install name
and `/usr/lib/libSystem.B.dylib`; it lists no libpython, Python.framework, or
LLVM dependency.

## Strict real-package behavior under five GCs

```text
gtimeout 300s env -u LC_ALL \
  PCC_CURRENT_PCC1=build/bootstrap-pytest-shared-stage1/pcc1 \
  PCC_REQUIRE_CURRENT_PCC1=1 \
  PCC_M1_SIMPLEJSON_SITE=build/m1-exit-site-20260714/simplejson-4.1.1 \
  uv run pytest -q -n0 \
  tests/python/test_m1_simplejson_import_behavior.py
```

Result: `2 passed in 8.09s`.

The test's compiler environment sets `PCC_HOST_PYTHON=/usr/bin/false` and
`PCC_HOST_PCC=/usr/bin/false`, selects `backend=self`,
`python-libpython=off`, and `ir-scaffold=on`, then checks the resulting
executable for libpython/Python.framework/LLVM dependencies. It runs that
single executable under `PCC_GC_BACKEND=0`, `1`, `2`, `3`, and `4` without
recompilation. Every backend prints exactly:

```text
native True
encoded {"items":[1,"two",null],"ok":true}
roundtrip True
```

Every backend returns zero and emits no stderr. The final two lines equal the
CPython oracle. A negative direct-extension import retains the stable
`PCC-PYEXT-IMPORT-001 [pcc-native/no-libpython]` diagnostic for the
intentionally omitted `simplejson.raw_json` closure.

The same current compiler separately passed the formal GC0
pcc1-to-pcc2-to-pcc3 gate in 287.69 seconds with normalized pcc2/pcc3
identity. The earlier `G-P0-GC` evidence supplies the formal all-five-GC
self-host matrix and production GC contract for this same M1 artifact shape;
that multi-hour matrix was not repeated for this packaging aggregation task.

## Generic-mechanism proof

```text
gtimeout 180s env -u LC_ALL \
  PCC_CURRENT_PCC1=build/bootstrap-pytest-shared-stage1/pcc1 \
  PCC_REQUIRE_CURRENT_PCC1=1 \
  uv run pytest -q -n0 \
  tests/python/test_m1_pcc_native_source_build.py::test_current_pcc1_imports_generic_built_extension_without_host_helpers \
  tests/python/test_m1_pcc_native_source_build.py::test_source_build_dispatch_has_no_candidate_package_names \
  tests/python/test_py_multi_file_bootstrap_shim.py::MultiFileBootstrapShimTests::test_absolute_from_import_detects_native_extension_alias
```

Result: `5 passed in 1.77s`.

The second anonymous PEP 489 extension is built/imported with both host
helpers disabled, reaches `Py_mod_exec`, resolves generic C-API exports from
the executable, and prints `ready 1`. Absolute `from package import
extension` detection is structural. The compiler's source-build dispatcher is
regressed against `simplejson`, `immutables`, and `pyahocorasick` names; none
appears there.

The complete bootstrap shim file also passes: `86 passed in 307.08s`.

## Claim boundary

This is one pinned real third-party C-extension vertical canary, not a claim
that arbitrary packages or arbitrary CPython extension ABIs work. It proves a
pcc-native/no-libpython/self-backed install-import-behavior-cleanup path and
the generic mechanisms exercised by a second synthetic extension. No full
GCC validation was run.
