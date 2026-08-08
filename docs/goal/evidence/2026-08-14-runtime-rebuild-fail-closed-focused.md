# Runtime rebuild fail-closed focused evidence (2026-08-14)

Mode: host-pcc mocked build boundary. No runtime archive was rebuilt and no
pcc1 app was compiled.

```text
gtimeout 90s env -u LC_ALL uv run pytest -q -x -n0 \
  tests/python/test_py_multi_file_bootstrap_shim.py::MultiFileBootstrapShimTests::test_ensure_runtime_passes_absolute_host_python_to_make \
  tests/python/test_py_multi_file_bootstrap_shim.py::MultiFileBootstrapShimTests::test_host_python_prefers_source_root_venv_outside_repo_cwd \
  tests/python/test_runtime_archive_isolation.py::test_runtime_make_never_captures_away_build_diagnostics \
  tests/python/test_runtime_archive_isolation.py::test_runtime_build_failure_is_reported_before_link \
  tests/python/test_runtime_archive_isolation.py::test_runtime_build_rejects_empty_archive_publication
5 passed in 0.31s
```

The runtime build receives an absolute blessed interpreter, streams the make
diagnostic, stops before link on failure, and rejects an empty publication.
The real current-pcc1 bad-PATH app regression and sequential bootstrap remain
open.
