# Hostless installed-runtime receipt source and focused evidence

Mode: host pcc unit/contract tests. This is not current-pcc1 or fixed-point
evidence.

The installed wheel marker is now a version-2 completion receipt binding the
exact runtime archive, production provenance manifest, and C-API inventory.
`pipeline_runtime_archive` validates those three digests using the compiled
`os._pcc_sha256_file_hex` primitive in pcc1 (and `hashlib` only in host-pcc
mode). Runtime selection and native-link inventory reads accept that receipt
before considering the host-Python provenance subprocess. Legacy v1 markers
are not hostless completion evidence.

Added required gate sources:

- `tests/python/test_package_runtime_archive_install.py`
- `tests/integration/test_pcc1_hostless_distribution.py`

The integration source installs a built wheel into a fresh venv, then guards
`python*`, `pip*`, and host `pcc`, installs a pure wheel and a synthetic
pcc-native C-extension sdist through the installed pcc1, compiles/runs both in
strict self/no-libpython mode, checks build/runtime provenance, and verifies an
unsupported owned Meson shape publishes no partial environment.

Focused commands completed:

```text
gtimeout 180s env -u LC_ALL uv run pytest -q -x -n0 tests/python/test_runtime_archive_isolation.py tests/python/test_package_runtime_archive_install.py tests/python/test_package_uv_environment.py
24 passed in 6.65s

gtimeout 90s env -u LC_ALL uv run pytest -q -x -n0 tests/test_runtime_archive_consumers.py::test_hatch_force_includes_the_verified_manifest
1 passed in 1.66s

gtimeout 90s env -u LC_ALL uv run pytest -q -x -n0 tests/python/test_fallback_baseline.py::test_capi_export_anchor_nm_fallback_keeps_native_stdout_contract tests/python/test_runtime_substrate_spike.py::test_pcc_python_archive_requires_valid_provenance_before_wheel_shortcut
2 passed in 0.40s
```

The integration file passed `py_compile`. Its pytest collection was aborted as
soon as the repository's collection hook began a current-pcc1 preflight; all
spawned collection/bootstrap children were explicitly terminated. The actual
integration gate remains open until shared source is stable and one deliberate
current-pcc1 run is authorized by the normal final sequence.
