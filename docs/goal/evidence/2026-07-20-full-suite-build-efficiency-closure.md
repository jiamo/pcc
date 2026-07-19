# Full-suite build-efficiency closure

Task: `TEST-P0-FULL-SUITE-BUILD-EFFICIENCY`

Date: 2026-07-20

## Claim

The current test tree no longer rebuilds the same mutable repository runtime
archive for each case or xdist worker. Heavy build sites are structurally
inventoried, identical runtime and self-host artifacts are content-addressed
and atomically published, nested pytest and duplicate frontend/GC dimensions
are bounded, and the configured worker count remains the explicit fixed value
of six. No semantic probe, GC backend, frontend mode, or integration corpus was
removed to obtain the timing result.

This evidence proves the finite task boundary and the two complete local suite
commands. It does not claim that a cold five-GC bootstrap is a low-memory
workload: the integration run briefly reached roughly 18 GiB when two complete
bootstrap chains overlapped, then fell as their workers exited. It also does
not turn a dirty local result into release/CI truth.

## Implemented consolidation

- `tests/runtime_build_cache.py` owns immutable, content-addressed C,
  threaded-C, pcc-Python runtime, self-host oracle, and self-backend object
  artifacts. Inter-process locks, staging directories, and atomic rename keep
  xdist readers away from partial products.
- Runtime-consuming tests use the shared fixtures instead of linking or
  rebuilding `pcc/py_runtime/libpy_runtime.a`. The remaining copied-runtime
  variants are real compile configurations (refcount strategy, concurrent,
  tripwire), cached at module/file scope and source-copied without build
  products.
- Self-host oracle stages and full-bootstrap stage results are source- and
  plan-addressed. A failed or partial build cannot publish a success manifest.
- The GC meta matrix keeps one finite inner pytest per frontend/backend slice,
  eliminates frontend-independent duplicate dimensions, and retains the GC4
  production contract under both `llvm` and `self` meta modes.
- Compiler-heavy pytest stays at `-n 6 --dist=loadgroup`; the outer width is
  exported to pcc so automatic inner work does not multiply it silently.
  Full GC chains retain independent scheduling behind a bounded resource
  lease and a single cold GC0 cache warmer.
- Repeated fixture builders are grouped only where the artifact is shared;
  stateless GC probes, Csmith seeds, and real corpora remain independently
  schedulable. The external C/GCC corpora and full three-stage GC chains remain
  integration tests rather than being deleted or weakened.
- `tests/test_test_infrastructure_efficiency.py` scans the full Python test tree
  and fails on an unreviewed `copytree` plus forced build, nested pytest site,
  mutable repository runtime archive consumer, shared-archive deletion,
  worker-specific skip, stale static-method catalogue, or lost scheduling
  contract.

## Compiler/runtime defects exposed while restoring the gates

- Sparse numeric SSA spellings in the self backend were decoded through dense
  Python lists. A single large suffix made one compiler worker grow past 20
  GiB and later roughly 50 GiB. Dictionary-backed sparse caches reduced the
  isolated zlib culprit from about 1.08 GiB to 128.5 MiB without changing its
  IR or test. See
  `docs/investigations/self-backend-sparse-ssa-cache-memory-explosion.md`.
- Native function signature construction unconditionally released a default
  after `py_tuple_set_item`, including borrowed module globals. pproxy's
  repeated `DUMMY` defaults consumed the global's retained references and
  triggered GC4 `BAD_INCREF`. The release now follows the existing container
  temporary ownership predicate; literals/new objects are still released and
  borrowed values are not. The pproxy GC3/GC4 real-project gate passes.
- The final suite also caught and retained focused fixes for GC3 allocation
  provenance, the pcc1 extension exception catalogue, UTF-8 source reads, and
  function-scoped DSE accounting. None was hidden with skip/xfail or a reduced
  parameter matrix.

## Required gates

- `gtimeout 150s env -u LC_ALL uv run pytest -q -n0 'tests/python/test_gc_backend_under_env.py::test_gc_backend_subset_under_frontend_backend[frontend=llvm-gc=4]' 'tests/python/test_gc_backend_under_env.py::test_gc_backend_subset_under_frontend_backend[frontend=self-gc=4]'`
  — **2 passed in 29.09s**.
- `gtimeout 60s env -u LC_ALL uv run pytest -q -n0 tests/test_test_infrastructure_efficiency.py`
  — **17 passed in 0.29s**.
- `gtimeout 900s env -u LC_ALL uv run pytest`
  — **9459 passed, 26 skipped, 1 warning in 875.73s**. A complete pytest
  summary was produced before the watchdog; no compiler/pytest children
  remained.
- `gtimeout 1800s env -u LC_ALL uv run pytest -m integration`
  — **4551 passed, 11 skipped in 1502.17s**. This included the complete
  five-backend three-stage bootstrap set and the real external corpora; no
  compiler/pytest children remained.

## Adjacent ownership regression gates

- `tests/python/test_gc_root_precision.py` plus the generated-method catalogue
  check — **14 passed in 34.57s**.
- pproxy under the rebuilt current-source pcc1, GC3 and GC4 — **2 passed in
  57.08s**.

## Open boundary

Empty for this task. Future reduction of the measured cold-bootstrap memory
peak is a pcc compiler-memory optimization, not evidence that this completed
test-artifact consolidation silently omitted a required mode or probe.
