# M2 NumPy pcc-native package artifact

Date: 2026-07-14

Task: `M2-NUMPY-PCC-NATIVE-ARTIFACT`

## Claim boundary and modes

This slice proves that the generic host-current-source package executor can
select one explicit Meson target, compile NumPy 2.4.4's real 136-object
`_multiarray_umath` closure against PCC C-API/runtime headers, and emit a
pcc-native-tagged Mach-O bundle. A strict `backend=self`,
`python-libpython=off`, `ir-scaffold=on` loader accepts that artifact, enters
`PyInit__multiarray_umath`, and enters PEP 489 `Py_mod_exec`.

It does not prove pcc1 automatic `pip install numpy`, a pcc1/pcc2/pcc3 fixed
point, the NumPy Python module graph, `import numpy` L4, or array behavior L5.
The `.pcc3-pcc_native-*` loader suffix is the current pcc-native ABI tag; it is
not a claim that this host-produced artifact was built by pcc3.

## Generic implementation

- `pcc.package.build_exec` now accepts explicit `meson_target` /
  `--meson-target`. It recursively reads the Ninja target/archive graph and
  joins each object leaf to `compile_commands.json` by exact output or stable
  source/target identity.
- Only the selected target closure is replayed. Outputs are rewritten under
  `build/pcc-package/pcc-native-target/objects`; old archive paths and unrelated
  extension objects are excluded from the direct-object link.
- PCC C-API/runtime includes replace CPython includes for both C and C++.
  Target link flags and C++ linker choice are retained. The current Meson
  libc++ assertion-to-hardening flag migration is replayed.
- `--jobs` parallelizes only explicit target replay and defaults to one, so
  existing generic build behavior remains ordered and compatible. The NumPy
  gate uses eight workers.
- `scripts/numpy_package_artifact_gate.py` owns the repeatable current-source
  build, artifact/linkage inspection, and strict loader proof. NumPy-specific
  target selection remains in this milestone gate; no package-name branch was
  added to compiler or package behavior.

## Required gate

```text
gtimeout 180s env -u LC_ALL uv run python \
  scripts/numpy_package_artifact_gate.py \
  --jobs 8 \
  --output build/head-truth/numpy-package/result.json
```

Observed result from `build/head-truth/numpy-package/result.json`:

```text
status: PASS
duration: 15.112 seconds
target objects: 136
compile: 136/136 passed (24 C++)
fresh object outputs: true
retained CPython header tokens: 0
link: 136 inputs, /usr/bin/c++, passed
artifact: _multiarray_umath.pcc3-pcc_native-macosx_14_0_arm64.so
artifact SHA-256: 16f289f31299818986f9664b78d8d4c5974b70aa32a13f6551bc563034c1c32f
artifact dependencies: Accelerate, libc++, libSystem; no libpython
exports PyInit__multiarray_umath: true
loader compile: exit 0
loader dependencies: libSystem only; no libpython and no LLVM
entered PyInit: true
entered Py_mod_exec: true
first subsequent blocker: first_missing_module / Py_mod_exec / math
```

The initial serial package replay passed 136/136 but took about 65 seconds.
After the bounded eight-worker executor change, the same formal gate completed
in 15.112 seconds (about 4.3x faster). No stage bootstrap, five-GC matrix, full
GCC suite, or full pytest suite was run.

## Focused controls and negative gate

```text
gtimeout 120s env -u LC_ALL uv run pytest -q -n0 \
  tests/python/test_package_build_exec.py::test_execute_build_actions_uses_compile_commands_as_action_graph \
  tests/python/test_package_build_exec.py::test_execute_build_actions_replays_one_meson_target_into_fresh_objects \
  tests/test_numpy_head_gate.py::test_package_executor_dry_plan_replays_only_multiarray_umath_closure \
  tests/python/test_package_abi_mode_labels.py::test_libpython_off_cpython_abi_fixture_rejects_with_pcc_pkg_004 \
  tests/python/test_package_import_path.py::test_package_site_rejects_cpython_extension_abi_for_no_libpython
```

Result: `5 passed in 3.16s`.

The two final tests keep CPython-ABI artifact rejection as an independent
negative gate; a rejected foreign artifact cannot earn a pcc-native claim.
The broader package/NumPy focused control also returned `24 passed, 7 skipped`
before the final formal gate.
