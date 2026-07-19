# M1 pcc-native real source-build evidence

Date: 2026-07-11

Task: `M1-PCC-NATIVE-SOURCE-BUILD`

## Result

The current strict pcc1 built the pinned, unmodified `simplejson` 4.1.1 sdist
as a pcc-native extension while both host escape hatches pointed to
`/usr/bin/false`. Compilation and linking completed with return code 0.

The installed artifact is:

```text
simplejson/_speedups.pcc3-pcc_native-macosx_arm64.so
```

Its suffix is the PCC tag `pcc3-pcc_native-macosx_arm64`; it makes no
`cpython-*` or `abi3` ABI claim. The package path is inferred structurally from
the sdist and the selected package name does not appear in compiler/runtime
dispatch.

## Real no-host gate

```text
PCC_HOST_PYTHON=/usr/bin/false PCC_HOST_PCC=/usr/bin/false \
  build/bootstrap-compat-runner-pcc1/pcc1 -m pip install \
  /tmp/pcc-m1-canary-probe/simplejson-4.1.1.tar.gz \
  --abi pcc-native --target <fresh-site> --cache-dir <fresh-cache>

compile: /usr/bin/cc -c -fPIC ... simplejson/_speedups.c
compile returncode: 0
link: /usr/bin/cc -shared -undefined dynamic_lookup ...
link returncode: 0
install returncode: 0
```

The emitted manifest and nested linkage report record:

```text
abi_mode: pcc-native
execution_mode: pcc-native
links_libpython: false
no_libpython_runtime: true
uses_cpython_extension_abi: false
```

`otool -L` lists the Mach-O install-name entry and `/usr/lib/libSystem.B.dylib`;
it lists no libpython dependency. The manifest deliberately leaves the
top-level import-backed `native_package_claim` false because this task proves
build/link only. Import and behavior are the next task's claim boundary.

## Focused regression gates

```text
gtimeout 300s env -u LC_ALL uv run pytest -q -n0 \
  tests/python/test_m1_pcc_native_source_build.py \
  tests/python/test_package_build_exec.py \
  tests/python/test_package_linkage.py \
  <three focused strict native-extension-loader nodes>
38 passed, 9 skipped in 16.25s

gtimeout 240s env -u LC_ALL \
  PCC_CURRENT_PCC1=build/bootstrap-compat-runner-pcc1/pcc1 \
  PCC_REQUIRE_CURRENT_PCC1=1 uv run pytest -q -n0 \
  tests/python/test_m1_pcc_native_source_build.py
5 passed in 2.00s

gtimeout 120s make -C pcc/py_runtime libpy_runtime.a
PASS (one pre-existing incompatible-function-pointer cast warning)

Black on the edited Python regression file
PASS
```

The C-API behavior gates cover Unicode writer/read, Unicode decode and
module-associated heap types, `Py_IS_FINITE`, generic
`PyObject_CallMethodObjArgs`, and the honest zero-length-only
`PyUnicode_New` bridge. No GCC torture or full GCC validation was run.
