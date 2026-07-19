# AUD-P1-NUMPY-CAPI-PROVIDER-SPLIT Evidence

Date: 2026-07-07

Task: `AUD-P1-NUMPY-CAPI-PROVIDER-SPLIT`

Changed files:
- `utils/pcc_numpy_capi_provider/pccnpapi.c`
- `utils/pcc_numpy_capi_provider/pccnpapi_impl/*.inc`
- `tests/python/test_package_build_exec.py`
- `tests/python/test_pcc_native_extension_loader.py`
- `docs/goal/task-board.yaml`

Implementation:
- Replaced the 11,820-line public `pccnpapi.c` body with a 16-line routed entrypoint.
- Moved the unchanged implementation into seven included shards under
  `utils/pcc_numpy_capi_provider/pccnpapi_impl/`.
- Kept `pccnpapi.c` as the single compiled translation-unit entrypoint so
  existing package build paths still compile one public source file.
- Updated provider tests that previously copied only the C file so they compile
  from the provider path or copy the whole provider directory.

Gates:
- Mechanical equivalence check:
  - Reconstructed all seven implementation shards after removing the new shard
    header comments and compared them with `HEAD:utils/pcc_numpy_capi_provider/pccnpapi.c`.
  - Result: `split equivalence: OK`; old bytes = 380661, new bytes = 380661.
- `gtimeout 180s env -u LC_ALL uv run pytest -q -n0 tests/python/test_package_build_exec.py::test_execute_build_actions_builds_reusable_numpy_capi_provider_with_include_dirs tests/python/test_pcc_native_extension_loader.py::test_pcc_native_extension_numpy_capi_provider_minimal_array_metadata`
  - Result: 2 passed in 10.21s.
- `gtimeout 120s env -u LC_ALL uv run pytest -q -n0 tests/python/test_package_build_exec.py::test_pcc1_build_exec_builds_reusable_numpy_capi_provider_without_host_python`
  - Result: 1 passed in 0.84s.
- `gtimeout 240s env -u LC_ALL uv run pytest -q -n0 tests/python/test_pcc_native_extension_loader.py`
  - Result: 63 passed in 43.36s.

Claim:
- The reusable pcc NumPy C-API provider has been split into routed
  implementation shards without changing the single-entry translation-unit
  build contract.
- Existing native-loader provider coverage and both host/pcc1 package-build
  provider smokes still pass.

Open boundary:
- This is a maintainability-only refactor. It does not claim broader NumPy
  C-API semantic coverage, real no-host `import numpy`, pcc-native accelerator
  package execution, or whole-program GPU execution.
