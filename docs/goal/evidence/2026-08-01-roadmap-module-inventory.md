# Roadmap / test-only module inventory

Date: 2026-08-01

Task: `AUD-P2-DEAD-ROADMAP-MODULE-INVENTORY`

The row's claim risk was that an audit had named "dead or test-only" modules
without an importer-level classification, so nothing could be acted on and
nothing could be ruled out. This inventory replaces the impression with a
measured one. **No module is deleted by this row.**

## Method

Every top-level module under `pcc/` (79 files) was classified by who
imports it:

- **production** — imported by another module under `pcc/` or by `scripts/`,
  or referenced from a non-Python caller that ships (shell scripts,
  Makefiles, CI workflows). Documentation-only mentions do not count.
- **test-only** — no production importer; imported only from `tests/` or
  `benchmarks/`.
- **docs-only** — referenced only from Markdown.
- **no-importer** — referenced from nowhere.

The scan matches `from .m`, `from pcc.m`, `import pcc.m`, `from ..m`,
`import m` for Python callers and `pcc.m` / `pcc/m.py` for non-Python ones,
so `python -m pcc.<module>` uses in shell scripts are counted as production
(this is what makes, for example, `bootstrap_cache_identity` production
rather than test-only).

## Result

```text
production    35
test-only     44
docs-only     0
no-importer   0
```

**There is no dead code at the top level of `pcc/`**: every module has at
least one importer, and every test-only module has a test that owns it. The
"dead modules" half of the original audit impression is therefore withdrawn.

## Test-only modules (kept, with their owning test)

These are research/roadmap surfaces and oracles. Each is exercised by the
test(s) that import it; none is reachable from the compiler's production
path, so none can silently affect a build. Keep decision for all of them:
**keep as test-owned** — they are the executable form of the research
threads the north star names (effects, ADTs, virtual threads, GC analysis,
GPU kernels), and deleting them would delete the evidence, not the debt.

- `pcc/runtime_effects.py` (1005 lines, 2 test importers)
- `pcc/gpu_kernel.py` (676 lines, 1 test importer)
- `pcc/virtual_thread_comparison.py` (419 lines, 1 test importer)
- `pcc/functional.py` (192 lines, 1 test importer)
- `pcc/dependency_verdict.py` (163 lines, 13 test importers)
- `pcc/heap_snapshot.py` (139 lines, 1 test importer)
- `pcc/stdlib_status.py` (139 lines, 1 test importer)
- `pcc/util.py` (114 lines, 2 test importers)
- `pcc/c_cache_key.py` (97 lines, 1 test importer)
- `pcc/effects.py` (85 lines, 1 test importer)
- `pcc/virtual_thread.py` (83 lines, 1 test importer)
- `pcc/persistent.py` (66 lines, 1 test importer)
- `pcc/threading_compat.py` (65 lines, 1 test importer)
- `pcc/runtime_log.py` (62 lines, 1 test importer)
- `pcc/gc_backend_capabilities.py` (58 lines, 1 test importer)
- `pcc/refcount_strategy_matrix.py` (57 lines, 1 test importer)
- `pcc/pass_profile.py` (56 lines, 1 test importer)
- `pcc/compiler_hot_objects.py` (54 lines, 2 test importers)
- `pcc/adt.py` (51 lines, 1 test importer)
- `pcc/gc_analyzer.py` (49 lines, 2 test importers)
- `pcc/effects_runtime2.py` (48 lines, 1 test importer)
- `pcc/c_codegen_libc_bridge.py` (47 lines, 1 test importer)
- `pcc/cache_explain.py` (46 lines, 1 test importer)
- `pcc/sealed_adt.py` (44 lines, 1 test importer)
- `pcc/buffer_protocol_runtime.py` (43 lines, 1 test importer)
- `pcc/gc_leak_finder.py` (42 lines, 1 test importer)
- `pcc/functional_result.py` (41 lines, 1 test importer)
- `pcc/tailcall_accumulator.py` (41 lines, 1 test importer)
- `pcc/fallback_routes.py` (39 lines, 1 test importer)
- `pcc/runtime_log_env.py` (39 lines, 1 test importer)
- `pcc/effects_runtime.py` (37 lines, 1 test importer)
- `pcc/gpu.py` (37 lines, 1 test importer)
- `pcc/optim.py` (37 lines, 1 test importer)
- `pcc/varargs_report.py` (34 lines, 1 test importer)
- `pcc/capi_abi.py` (33 lines, 1 test importer)
- `pcc/pass_env_decisions.py` (33 lines, 1 test importer)
- `pcc/adt_exhaustive.py` (31 lines, 1 test importer)
- `pcc/buffer_protocol.py` (29 lines, 1 test importer)
- `pcc/pattern_decision_tree.py` (27 lines, 1 test importer)
- `pcc/trait_protocol.py` (23 lines, 1 test importer)
- `pcc/bench_profile_aggregate.py` (22 lines, 1 test importer)
- `pcc/self_backend_profile.py` (21 lines, 1 test importer)
- `pcc/bench_profile.py` (20 lines, 1 test importer)
- `pcc/c_libc_registry_extra.py` (13 lines, 1 test importer)

Two of them deserve a note rather than a blanket keep:

- `pcc/runtime_effects.py` (1005 lines) and `pcc/gpu_kernel.py` (676 lines)
  are the largest test-only surfaces. They are prototypes with one owning
  test each; if a future slice promotes them to production it must come with
  the gates the corresponding track requires, and if a future slice retires
  them the owning test goes with them. Neither is in scope here.
- `pcc/dependency_verdict.py` has 13 test importers and no production
  importer, which makes it a genuine shared test oracle rather than a
  prototype.

## Production modules (no action)

- `pcc/api.py` (319 lines, 4 test importers)
- `pcc/array_core.py` (3276 lines, 1 test importer)
- `pcc/bootstrap_cache_identity.py` (82 lines, 1 test importer)
- `pcc/bootstrap_profile_report.py` (279 lines, 2 test importers)
- `pcc/build_cache.py` (38 lines, 1 test importer)
- `pcc/c_abi_layout.py` (63 lines, 1 test importer)
- `pcc/c_libc_registry.py` (83 lines, 2 test importers)
- `pcc/capi_surface.py` (4370 lines, 2 test importers)
- `pcc/category.py` (995 lines, 1 test importer)
- `pcc/cli_bootstrap.py` (10901 lines, 11 test importers)
- `pcc/cli_bootstrap_array_core.py` (4840 lines, 1 test importer)
- `pcc/cli_contract.py` (116 lines, 0 test importers)
- `pcc/cli_core.py` (1804 lines, 7 test importers)
- `pcc/cli_launcher.py` (21 lines, 1 test importer)
- `pcc/cli_observability.py` (99 lines, 1 test importer)
- `pcc/compile_observability.py` (221 lines, 1 test importer)
- `pcc/diagnostics.py` (335 lines, 3 test importers)
- `pcc/fallback_explainer.py` (59 lines, 1 test importer)
- `pcc/gc_log.py` (169 lines, 2 test importers)
- `pcc/gpu_backend.py` (115 lines, 1 test importer)
- `pcc/gpu_metal.py` (277 lines, 4 test importers)
- `pcc/ir_diff.py` (112 lines, 1 test importer)
- `pcc/macho_normalize.py` (96 lines, 3 test importers)
- `pcc/package_compat.py` (100 lines, 1 test importer)
- `pcc/package_environment.py` (462 lines, 5 test importers)
- `pcc/package_schema.py` (119 lines, 3 test importers)
- `pcc/pass_explain.py` (31 lines, 1 test importer)
- `pcc/pcc.py` (305 lines, 39 test importers)
- `pcc/preprocessor.py` (771 lines, 1 test importer)
- `pcc/profile_events.py` (143 lines, 3 test importers)
- `pcc/project.py` (1057 lines, 28 test importers)
- `pcc/roadmap_deepwire.py` (446 lines, 0 test importers)
- `pcc/runtime_report.py` (162 lines, 1 test importer)
- `pcc/tailcall_ir.py` (106 lines, 2 test importers)
- `pcc/value_model.py` (334 lines, 2 test importers)

## Supported claim

Every top-level `pcc/` module is classified by importer set with the test
owner recorded; nothing is dead, and the modules the audit called "dead or
test-only" are test-only research surfaces with owning tests. This closes the
classification the row asked for.

## Gate evidence

This row changes no code — it adds one document — so the gate exists to show
the tree is green around it:

```text
tests/python/test_libc_import_baseline.py tests/c/test_c_parser.py -n0
  45 passed, 1 deselected in 18.37s

tests/c + tests/python (the full migrated suite, 6 workers, same tree)
  8126 passed, 60 subtests passed in 1389.11s, no failures
```

The row's literal gate (`gtimeout 300s ... pytest -q -n0 tests`) is not
executable as written: serialized with `-n0` the suite needs well over an
hour, and a 1800-second attempt reached 53% with zero failures before the
watchdog. The gate line is corrected on the row to the parallel form that
actually finishes and produces a final summary; the run above is that form.

## Not proven

- Submodules under `pcc/*/` (py_frontend, backend, kernel_ir, dist, ...) are
  not inventoried here; the row's candidates were top-level modules.
- "Test-only" means no production importer today. It does not judge whether a
  prototype should eventually be promoted or retired — those are separate,
  track-owned decisions.
