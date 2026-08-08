# Native assembled runtime, Cordis Loader and Session projections

- Schema: pcc.harness.migration.v1
- Sequence: 0001
- PCC change: 4fa327ba782ac7492c4942396824c56714cf8c24
- Upstream range: b67e81ac97647270b3002d78532baf3a5b68cbc3..47f943859bef60e4160492346772ded9b24f765a
- Native-only rationale: not-applicable
- Changed domains: vendor/cordis, packages/bundle, packages/preset, packages/core/session, packages/core/system-prompt, packages/core/tools, packages/core/agent, packages/core/agent-loop, packages/llm, packages/session, packages/settings, packages/credentials, packages/identity, packages/todo, packages/plan, pcc/gui, pcc/self-backend, pcc/python-frontend, pcc/python-runtime
- Tasks: HARNESS-P0-CURRENT-PCC1, HARNESS-P0-NATIVE-GUI-SHELL, HARNESS-P1-PLUGIN-EFFECT-KERNEL, HARNESS-P1-SESSION-EVENT-COMPAT, HARNESS-P1-SESSION-COMPOSITION, HARNESS-P1-PRESET-BUNDLE-SELF-MOD, HARNESS-P1-MODEL-PROVIDER-REGISTRY, HARNESS-P1-SETTINGS-IDENTITY-CREDENTIALS, HARNESS-P1-TODO-PLAN, HARNESS-P0-MIGRATION-LEDGER-GATE
- GUI impact: changed

## Behavior migrated

- Scoped plugin services/events/effects, dependency ordering, missing/cyclic requirement rejection, partial-load rollback, leaf reload and reverse teardown; versioned event-sourced turns, steps, request inputs, chunks, messages, tool calls/results, fork and replay; provider/tool registries; deterministic multi-step agent execution; DeepSeek-compatible SSE request/response codec; validated atomic JSONL sessions; revisioned settings, secret redaction, credential references and anonymous identity; whole-list todo and logged plan state; CLI and PCC GUI use the assembled runtime.
- Cordis Consumers remain pending until injected services resolve in selected private or joined realms, activate when Providers appear, and unload/reload when Provider identity changes or disappears. Active Consumers retain committed bindings during teardown, and Provider withdrawal drains dependent Consumers before Provider resource cleanup.
- The scoped event registry implements emit, parallel, serial, bail and explicitly delegated waterfall modes, including prepend, global and one-shot listener ownership.
- Session logs and anonymous identity files use exclusive creation, flush, file synchronization, owner-only permissions and atomic replacement so separate native Harness processes can resume and fork committed state.
- `ctx.effect()` publishes its wrapper before setup enters plugin code, accepts one disposer or a synchronous iterator of disposers, rolls back yielded disposers in reverse order after setup failure, and completes reentrant disposal without leaking cleanup registrations.
- Context teardown is an explicit unloading phase. Committed services remain readable for cleanup, while new effects, services and listeners are rejected; deterministic graph snapshots expose plugin state, injections, Provider identities/relationships, realms, services and owned effect labels.
- A declarative catalog and Loader mount validated group/plugin entry trees, allow Consumer-before-Provider order, isolate group service realms, reconcile changed suffixes, reverse unmount order, restore the prior stable suffix after update failure and reject static dependency cycles.
- Bundle/Profile/Patch composition applies bundle, profile, home, CLI and launcher layers in explicit order through complete entry replacement, insertion and removal by id; the assembled runtime publishes its static capabilities through this Loader and can dump the resolved entry tree.
- Session events persist timestamps and provider token usage. Whole-log replay derives distinct turns, closed steps, model/tool time, time to first token and decode time/token totals, clamps clock skew and prunes unresolved tool calls at turn end.
- DeepSeek choice-less final usage payloads survive SSE assembly, and the Agent loop logs usage on the authoritative `assistant/message` event so persistence, resume and fork reproduce the statistics.

## PCC facilities

- PCC declarative AppKit GUI support and native command bridge used by the Harness shell.
- Static self-host imports for frontend lane policy and freestanding GC-global classification; fail-closed default-pass handling for dangling SSA definitions; primitive target-final AArch64 stack-map emission without managed dataclass temporaries in the compiler hot path.
- Native `dict.copy()` type propagation and lowering; native IEEE-754 `math.isfinite`, `math.isinf` and `math.isnan`; entry roots for normal class-method object parameters and reassigned boxed-int parameters.
- Native exclusive file creation plus reusable `os.replace`, `os.chmod`, `os.fsync` and `fileno` runtime paths on Darwin and Linux without libpython.
- Native `os.makedirs(path, mode, exist_ok)` applies the requested leaf permissions, and native `json.dumps(..., ensure_ascii=False)` preserves UTF-8 Session records without a CPython fallback.
- Text and binary native file objects implement the iterator protocol in both runtime owners: `iter(file)` retains the file, `next(file)` reuses `readline()`, and EOF becomes `StopIteration`.
- Missing Python return annotations now use the dynamic object ABI across local definitions and cross-module declarations; only explicit `-> None` uses `ret void`, and implicit dynamic fallthrough returns `py_None` rather than the null error sentinel.
- Closed-world class exports preserve same-module constructor types for unannotated `self.field = SomeClass()` fields, keeping nested receivers statically typed and preventing a user-defined `get()` from being specialized as `dict.get()`.
- A static self-host export for the finite read-only GC runtime-query predicate keeps freestanding runtime-archive verification identical in host and native compilers.
- Compiler-source manifests exclude generated runtime provenance, bind the compiler artifact hash to a current PCC source digest, and fail the Harness build when that binding is stale.
- Python-only migration-ledger validation binds implementation commits and the dirty worktree to ordered, exact upstream/task/test/UI records.
- PCC native generator frames and iterator/callable protocols execute Cordis generator effects without libpython; existing dictionaries, lists, JSON, file iteration and epoch wall-clock facilities implement Loader graphs and durable Session statistics without Node or CPython at runtime.

## Verification

- PASS | `gtimeout 180s env -u LC_ALL uv run pytest -x -q projects/harness/tests tests/python/test_harness_gui.py -m 'not integration'` | Passed 37 tests after the assembled-runtime changes.
- PASS | `gtimeout 120s env -u LC_ALL uv run pytest -q -x -n0 tests/python/test_compiled_default_pass_tier.py tests/python/test_precise_stackmap_abi.py` | Passed 35 focused compiled-pass and stack-map tests.
- PASS | `gtimeout 120s env -u LC_ALL uv run pytest -q -x -n0 tests/python/test_precise_stackmap_abi.py` | Passed 25 stack-map ABI tests.
- PASS | `gtimeout 120s env -u LC_ALL uv run pytest -q -x -n0 tests/python/test_freestanding_module.py tests/python/test_pipeline_frontend_workers.py` | Passed 57 freestanding and frontend-worker tests.
- PASS | `gtimeout 300s env -u LC_ALL uv run pytest -q -x -n0 projects/harness/tests tests/python/test_harness_project.py tests/python/test_harness_gui.py tests/python/test_harness_migration_ledger.py tests/python/test_harness_plugin_kernel.py tests/python/test_harness_settings_identity_credentials.py tests/python/test_harness_todo_plan.py -m 'not integration'` | Passed 67 source tests; four integration tests were deselected.
- PASS | `gtimeout 180s env -u LC_ALL uv run pytest -q -x -n0 tests/python/test_py_for_target_representation_join.py::test_repeated_dict_iteration_reuses_one_native_object_target tests/python/test_python_module_imports_parity.py::test_import_math_float_classification tests/python/test_gc_root_precision.py::test_borrowed_class_method_parameters_are_rooted_before_calls tests/python/test_py_typed_int_unboxed.py::test_boxed_method_int_parameter_roots_before_raw_branch_rebind` | Passed all four focused PCC compiler regressions.
- PASS | `gtimeout 2400s env -u LC_ALL projects/harness/bootstrap-pcc1.sh` | Built current-source pcc1 with SHA-256 `124aa91379a824469686f91a1e9c002335fbaa0f3e2b8d16f828a2ca569f30c8` and source digest `193d4a4794fd948df3bc26aa99110b19d0ecad51b0a4003b24930cd4c7a9c087`.
- PASS | `gtimeout 1800s env -u LC_ALL projects/harness/build.sh` | Built `projects/harness/build/harness-core` with the project-local current-source pcc1; the Harness executable SHA-256 is `cceaf58b9823186e8e65bab99c1ff5f01f76601f3bab5e32fbee5c26298dba20`.
- PASS | `gtimeout 60s env -u LC_ALL projects/harness/build/harness-core --self-check` | Printed `HARNESS_RUNTIME_SELF_CHECK_OK`.
- PASS | `gtimeout 60s env -u LC_ALL PCC_HARNESS_GUI_BRIDGE=projects/harness/build/libpcc_gui_metal.dylib projects/harness/build/harness-core --gui-self-check` | Printed `HARNESS_GUI_SELF_CHECK_OK`.
- PASS | `gtimeout 60s env -u LC_ALL projects/harness/build/harness-core 'native cli turn'` | Logged and projected the user and assistant turn from the native runtime.
- PASS | `gtimeout 180s env -u LC_ALL uv run pytest -q -x -n0 tests/python/test_harness_plugin_kernel.py::test_current_pcc1_plugin_lifecycle tests/python/test_harness_settings_identity_credentials.py::test_current_pcc1_secret_redaction tests/python/test_harness_todo_plan.py::test_current_pcc1_logged_plan -m integration` | Passed all three current-pcc1 integration gates.
- PASS | `gtimeout 60s otool -L projects/harness/build/pcc1 && gtimeout 60s otool -L projects/harness/build/harness-core` | Both executables list only libSystem and no libpython or Node runtime.
- PASS | `gtimeout 120s env -u LC_ALL uv run pytest -q -x -n0 tests/python/test_harness_migration_ledger.py` | Passed all five ledger schema and stale-worktree tests.
- PASS | `gtimeout 60s env -u LC_ALL uv run python projects/harness/migration/validate_ledger.py` | Reported the Harness migration ledger complete and current.
- PASS | `gtimeout 300s env -u LC_ALL uv run pytest -q -x -n0 tests/python/test_native_os_durable_files.py` | Passed four native lowering, host semantic and Linux syscall regressions.
- PASS | `gtimeout 300s env -u LC_ALL uv run pytest -q -x -n0 projects/harness/tests/test_plugin_runtime.py tests/python/test_harness_plugin_kernel.py -m 'not integration'` | Passed 16 reactive plugin/effect/event tests; one current-pcc1 integration test was deselected.
- PASS | `gtimeout 300s env -u LC_ALL uv run pytest -q -x -n0 projects/harness/tests tests/python/test_harness_plugin_kernel.py tests/python/test_harness_session_composition.py -m 'not integration'` | Passed 52 assembled source tests; two current-pcc1 integration tests were deselected.
- PASS | `gtimeout 300s env -u LC_ALL uv run pytest -q -x -n0 projects/harness/tests/test_source_provenance.py` | Passed four compiler source/artifact identity tests.
- PASS | `gtimeout 300s env -u LC_ALL uv run pytest -q -x -n0 tests/python/test_freestanding_module.py::test_freestanding_allows_exact_readonly_gc_runtime_abi_import tests/python/test_freestanding_module.py::test_freestanding_readonly_gc_registry_is_a_static_pcc1_import tests/python/test_freestanding_module.py::test_freestanding_runtime_global_registry_is_a_static_pcc1_import` | Passed all three freestanding GC registry tests.
- PASS | `gtimeout 360s env -u LC_ALL uv run pytest -q -x -n0 tests/python/test_pipeline_exports.py tests/python/test_extern_returns_none_abi.py tests/python/test_py_cross_module_class_inference.py -k 'returns_none or gap1_cross_module_class_constructor_returns_typed_instance or unannotated or constructor_initialized_cross_module_field'` | Passed eight return-ABI and cross-module field-type regressions.
- PASS | `gtimeout 300s env -u LC_ALL uv run pytest -q -x -n0 tests/python/test_native_file_readline_seek_tell.py -k file_iteration` and the same gate with `PCC_RUNTIME_CC=pcc PCC_RUNTIME_HIGH=py` | Text and binary file iteration matched Python 3 under both native runtime owners.
- PASS | `gtimeout 600s env -u LC_ALL uv run pytest -q -x -n0 tests/python/test_native_json_sort_keys.py::test_json_dumps_ensure_ascii_false_no_libpython tests/python/test_native_os_makedirs.py tests/python/test_native_file_readline_seek_tell.py -k 'ensure_ascii_false or makedirs or file_iteration'` | Passed five native JSON, filesystem and file-iteration regressions.
- PASS | `gtimeout 300s env -u LC_ALL uv run pytest -q -x -n0 tests/python/test_harness_plugin_kernel.py::test_current_pcc1_plugin_lifecycle tests/python/test_harness_session_composition.py::test_current_pcc1_persist_resume_and_fork -m integration` | Passed both current-pcc1 integration gates, including three-process Session persistence, resume and fork.
- PASS | `gtimeout 300s env -u LC_ALL uv run pytest -x -n0 projects/harness/tests tests/python/test_harness_plugin_kernel.py tests/python/test_harness_session_composition.py tests/python/test_harness_settings_identity_credentials.py tests/python/test_harness_todo_plan.py` | Passed 85 Harness source, Loader, Cordis graph/effect, Session statistics, persistence and assembled host tests; four native integration tests were deselected.
- PASS | `gtimeout 2400s env -u LC_ALL projects/harness/bootstrap-pcc1.sh` | The last completed attempt built pcc1 SHA-256 `bb149a1c2afed76959f86710ab2490d5234e434c0d7840a06202228dd66ed3aa` from source digest `d5e2970a7851f59f57ab16589a1f4058727a5e8bd07804b8747e9be461a5609e`; that snapshot was current immediately after publication.
- PASS | `gtimeout 1800s env -u LC_ALL PCC1=projects/harness/build/pcc1 projects/harness/build.sh` | Built native `harness-core` SHA-256 `d3541162a65acd25e0bf7afca7b9c04c539c3e96da2a35a0add12eb73c5ff5a5` with self backend and libpython disabled.
- PASS | `gtimeout 60s env -u LC_ALL projects/harness/build/harness-core --self-check` | Loader graph, Session statistics, agent/tool replay and Cordis generator-effect assertions printed `HARNESS_RUNTIME_SELF_CHECK_OK`.
- PASS | `gtimeout 900s env -u LC_ALL uv run pytest -x -n0 -m integration tests/python/test_harness_plugin_kernel.py tests/python/test_harness_session_composition.py tests/python/test_harness_settings_identity_credentials.py tests/python/test_harness_todo_plan.py` | Passed all four native plugin, persistence/fork, settings/identity/credential and todo/plan integrations.
- PASS | `gtimeout 60s env -u LC_ALL projects/harness/harness --gui-self-check` | Printed `HARNESS_GUI_SELF_CHECK_OK` with the rebuilt runtime.
- PASS | `gtimeout 60s otool -L projects/harness/build/pcc1 && gtimeout 60s otool -L projects/harness/build/harness-core` | Both executables list only `libSystem`, with no libpython or Node runtime.
- NOT-RUN | `gtimeout 30s env -u LC_ALL uv run python projects/harness/source_provenance.py --verify . projects/harness/build/pcc1 projects/harness/build/pcc1-source.json` | Final HEAD-current acceptance is not claimable: shared PCC work advanced to `4fa327ba782ac7492c4942396824c56714cf8c24` with source digest `537e0be53a20694a5e62401f008273d262db9d5013901b4d2f5d6bbf58f2523e` after three successful bootstrap/build attempts; each completed native binary remains functional but the last compiler manifest names the immediately preceding snapshot.

## GUI evidence

- PENDING | HARNESS-P0-NATIVE-GUI-SHELL | Native self-check passes; same-viewport interaction trace and pixel comparison remain open.

## Remaining boundaries

- Native shell needs recorded same-viewport parity and live persisted session controls.
- Complete upstream event inventory, SQLite provider integration, settings YAML/watch/cross-process locking, credential file watching, full plan interaction/command narration, real PCC HTTP/TLS transport, approvals and remaining capability seams are open tasks.
- Cordis async setup/iterator effects, virtual-thread cleanup concurrency/error aggregation and native-resource leak gates remain open.
- Loader plugin entries do not yet own nested child entry trees; configuration file watching, module HMR, YAML/path policy, shipped Web/Headless profiles, per-session preset switching and a full Fiber reload transaction remain open.
- Session storage remains the tracer-bullet atomic JSONL provider until PCC SQLite lands; the extensible projection registry, telemetry export pipeline and complete upstream event vocabulary remain open.
- Final current-HEAD provenance must be rerun from a quiescent PCC worktree; repeated concurrent source/HEAD movement invalidated three otherwise successful native build attempts.
