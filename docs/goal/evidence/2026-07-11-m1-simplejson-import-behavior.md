# M1 real simplejson pcc-native import and behavior evidence

Date: 2026-07-11

Task: `M1-PACKAGE-IMPORT-BEHAVIOR`

Status: `DONE_STRONG`

## Claim boundary

This evidence proves that a fresh pcc1 compiler, using `backend=self`,
`python-libpython=off`, and `ir-scaffold=on`, compiles and runs an application
against the real pinned `simplejson` 4.1.1 pcc-native `_speedups` extension.
The application uses the C semantic runtime explicitly
(`PCC_RUNTIME_CC=cc`, `PCC_RUNTIME_HIGH=c`). The extension completes module
initialization, its scanner/decoder/encoder bindings are active, and one
deterministic function plus nested object/container round trip matches the
CPython source-package oracle.

This does **not** prove no host Python/pcc subprocess during application
compilation, pcc1-to-pcc2-to-pcc3 normalized identity, the pcc-Python semantic
runtime, or execution under all five GC backends. Those remain S-P0 and G-P0
boundaries.

## Artifact and install

- Package: `simplejson` 4.1.1, pinned sdist selected by
  `M1-PKG-CANARY-SELECTION`.
- Installed root: `build/m1-site/simplejson-4.1.1`.
- Native artifact:
  `simplejson/_speedups.pcc3-pcc_native-macosx_arm64.so`.
- The fresh pcc1 install ran with both host escape variables set to
  `/usr/bin/false` and reported `execution_mode=pcc-native`,
  `links_libpython=false`, `uses_cpython_extension_abi=false`, and
  `no_libpython_runtime=true`.

Install command:

```text
gtimeout 180s env -u LC_ALL \
  PCC_HOST_PYTHON=/usr/bin/false PCC_HOST_PCC=/usr/bin/false \
  build/bootstrap-compat-runner-pcc1/pcc1 -m pip install \
  /tmp/pcc-m1-canary-probe/simplejson-4.1.1.tar.gz \
  --abi pcc-native --target build/m1-site --cache-dir build/m1-cache
```

Result: exit 0; the C source compiled and linked to the pcc-native extension
suffix, and the install manifest retained the mode/linkage labels above.

## Positive behavior oracle

The integration test requires all of the following so the package's
pure-Python fallback cannot satisfy the claim:

- `scanner.c_make_scanner is not None`;
- `scanner.make_scanner is scanner.c_make_scanner`;
- `decoder.c_scanstring is not None`;
- `encoder.c_make_encoder is not None`.

The pcc-native executable prints:

```text
native True
encoded {"items":[1,"two",null],"ok":true}
roundtrip True
```

The last two lines exactly match the CPython run over the same installed source
package and payload.

## Stable negative boundary

A reduced direct `_speedups` import intentionally omits the compiled
`simplejson.raw_json` dependency closure. It exits 1 with exactly:

```text
Traceback (most recent call last):
RuntimeError: PCC-PYEXT-IMPORT-001 [pcc-native/no-libpython] module not found: simplejson.raw_json
```

This locks the first initialization failure as a stable, mode-labelled
pcc-native/no-libpython diagnostic.

## Gates

Fresh pcc1 build:

```text
gtimeout 900s env -u LC_ALL uv run pcc --no-cache --backend self \
  --python-libpython=off --ir-scaffold=on pcc/__main__.py \
  -o build/bootstrap-compat-runner-pcc1/pcc1
```

Result: exit 0.

Real positive and negative package integration:

```text
gtimeout 900s env -u LC_ALL \
  PCC_CURRENT_PCC1=build/bootstrap-compat-runner-pcc1/pcc1 \
  PCC_REQUIRE_CURRENT_PCC1=1 \
  PCC_M1_SIMPLEJSON_SITE=build/m1-site/simplejson-4.1.1 \
  uv run pytest -q -n0 tests/python/test_m1_simplejson_import_behavior.py
```

Result: `2 passed in 6.26s`.

Adjacent multi-file gate:

```text
gtimeout 180s env -u LC_ALL uv run pytest -q -n0 \
  tests/python/test_py_multi_file_compile.py
```

Result: `33 passed in 24.87s`.

The CPython typing-container regression exposed by the adjacent gate:

```text
gtimeout 180s env -u LC_ALL uv run pytest -q -n0 \
  tests/python/test_py_multi_file_bootstrap_shim.py::MultiFileBootstrapShimTests::test_callable_type_alias_literal_with_cpython_values
```

Result: `1 passed in 46.71s`.

Latest independent fallback ratchets after all compiler-source changes:

```text
gtimeout 360s env -u LC_ALL uv run pytest -q -n0 \
  tests/python/test_fallback_baseline.py::test_per_module_fallbacks_under_ratchet
gtimeout 360s env -u LC_ALL uv run pytest -q -n0 \
  tests/python/test_fallback_baseline.py::test_on_mode_per_module_fallbacks_under_ratchet
```

Results: OFF `1 passed in 125.22s`; ON `1 passed in 106.02s`. The earlier
combined fallback/IR/bootstrap-baseline run was also green before the final
typing-container fix: `22 passed, 4 skipped in 192.97s`; the two ratchets above
are the post-fix fallback evidence.

## Generic mechanisms closed

- compiled-module proxies visible to C-API imports during PEP 489 execution;
- compiled sibling initialization and relative extension import ordering;
- cross-module exception constructor metadata and structural `FuncDef` lookup;
- dynamic `.decode` runtime receiver dispatch with a bytes fast path and an
  ordinary method slow path;
- pcc-native string/regex/class/container behavior required by simplejson;
- static package-schema exports preserving the no-libpython fallback ratchet;
- CPython typing-key containers no longer mix pcc-native builtin type pointers.

No compiler/runtime dispatch checks the `simplejson` package name.
