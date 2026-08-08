# Self-bootstrap phase-reuse reduced-test slice

Date: 2026-07-26

Task: `PERF-P0-SELF-BOOTSTRAP-PHASE-REUSE`

Mode boundary: this evidence covers the current source tree on Darwin arm64,
self backend, no-libpython bootstrap paths, and the reduced validation set run
before the user asked to stop broad testing. It does not close the task's full
acceptance boundary because the exact complete non-integration and integration
suites did not both finish with final summaries inside 900 seconds.

Previously recorded implementation evidence remains the compiler-level
frontend bundle cache described in
`docs/investigations/pcc-compiler-design-reference-audit.md`: an isolated
current-source pcc1-to-pcc2 stage improved from 373.914 seconds and 5.319 GiB
to 38.209 seconds and 2.689 GiB, and byte-identical staged compiler copies share
one frontend cache key by compiler SHA-256 rather than output path.

Reduced gates run in this session:

- `gtimeout 180s env -u LC_ALL uv run pytest -q -n0 tests/python/test_py_frontend_ir_pass_pipeline.py -k 'cache or profile or deterministic'`
  - Result: 4 passed, 77 deselected in 0.34s.
- `gtimeout 60s env -u LC_ALL uv run pytest -q -n0 tests/python/test_py_frontend_compile_cache.py`
  - Result: 7 passed in 1.98s.
- `gtimeout 900s env -u LC_ALL uv run pytest -q -n0 tests/python/test_self_host_oracle_diff.py::test_000_self_host_oracle_stage_cache_warmup`
  - Result: 1 passed in 0.45s.
- `gtimeout 900s env -u LC_ALL PCC_BOOTSTRAP_FULL_REBUILD=1 uv run pytest -q -m integration tests/python/gc/test_pcc_bootstrap_full_gc0.py tests/python/gc/test_pcc_bootstrap_full_gc1.py tests/python/gc/test_pcc_bootstrap_full_gc2.py tests/python/gc/test_pcc_bootstrap_full_gc3.py tests/python/gc/test_pcc_bootstrap_full_gc4.py`
  - Result: 5 passed in 302.05s.

Broad gate intentionally not claimed:

- `gtimeout 900s env -u LC_ALL uv run pytest` was interrupted after the user
  asked to do fewer tests. It had reached about 48% and had emitted one `F`,
  but there was no final pytest summary or failure traceback. This is not green
  evidence and is not enough to name a root cause.
- `gtimeout 900s env -u LC_ALL uv run pytest -m integration` was not run.

Post-interrupt process check:

- A focused `ps` filter for this repository's pytest/bootstrap/pcc processes
  returned no matches after the interrupted broad suite.

Supported claim: the content-addressed frontend cache and forced-rebuild
five-GC fixed-point matrix are currently compatible with the 900-second
bootstrap budget; the matrix completed in 302.05 seconds with every GC0..4
backend represented.

Not proven: complete non-integration and complete integration suite closure
inside 900 seconds; clean release truth; any semantic claim from the interrupted
non-integration run; or cross-machine performance.
