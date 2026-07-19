# Dead / test-only roadmap module inventory

Date: 2026-07-18

Task: `AUD-P2-DEAD-ROADMAP-MODULE-INVENTORY`

## Method and claim boundary

The inventory covers the candidate families named by the structural audit:
effects, Result/ADT helpers, the extra libc registry, logging/profile helpers,
and small roadmap prototypes.  Importers were found with an exact Python
import scan under `pcc/`, `tests/`, and `scripts/`; a module's own file was not
counted as an importer.  "Removable" below is a classification, not permission
to delete it: its owning test must be migrated or deliberately removed in a
separate, explicitly authorized cleanup.

## Production or production-support modules

| Module | Non-test importer set | Test owner(s) | Decision |
|---|---|---|---|
| `pcc/profile_events.py` | `compile_observability.py`, `cli_observability.py`, `roadmap_deepwire.py`, `py_frontend/pipeline_profile.py` | `test_profile_events.py`, `test_pipeline_profile.py`, `test_roadmap_deepwire.py` | **Keep: production support.** It is the shared compiler/profile event schema and is reached by real CLI/frontend paths. |
| `pcc/roadmap_deepwire.py` | `pcc/__init__.py` installs it for normal package imports | `test_roadmap_deepwire.py`, plus real tailcall/CLI coverage | **Keep: production.** The name is historical, but the module changes real CLI, pipeline, pass, and cache behavior. A future rename/refactor must preserve those entrypoints. |
| `pcc/runtime_effects.py` | no ordinary Python runtime importer; it is the checker/schema owner for production vthread event IDs and depends on `category.py` | `test_runtime_effect_category.py`, `gc_production_contract/test_vthread_runtime_effect_events.py` | **Keep/promote as a contract owner.** Its value is ABI/event parity checking, not effect-handler execution; do not merge it with the three experimental handler libraries. |
| `pcc/category.py` | `runtime_effects.py` | `test_category_kernel.py`, transitively the runtime-effect tests | **Keep: production support.** It supplies the compositional checker used by the retained runtime-effect contract. |
| `pcc/gc_log.py` | `scripts/pcc_gc_viewer.py` | `test_gc_log_tools.py`, `test_gc_log_runtime_schema.py` and runtime-log schema/wiring tests | **Keep: tooling oracle.** It does not implement collection, but it owns the parser/validator used on production runtime logs. |

## Test-only prototypes and duplicate helpers

| Module | Non-test importer set | Test owner(s) | Decision |
|---|---|---|---|
| `pcc/adt.py` | none | `test_adt.py` | **Removable prototype.** It overlaps both functional Option/Result helpers and the two sealed-ADT experiments. |
| `pcc/adt_exhaustive.py` | none | `test_adt_exhaustive.py` | **Removable duplicate.** If an ADT API is retained, migrate its useful exhaustiveness cases to one owner first. |
| `pcc/sealed_adt.py` | none | `test_sealed_adt_runtime.py` | **Test-only oracle; consolidation candidate.** It is the most complete of the three small ADT experiments, but is not a compiler/runtime execution path today. |
| `pcc/effects.py` | none | `test_effects_library.py` | **Removable experimental library.** It is a host-Python handler stack and is not the production runtime-effect contract. |
| `pcc/effects_runtime.py` | none | `test_effects_runtime.py` | **Removable duplicate prototype.** It has a different exception-based handler model and no production importer. |
| `pcc/effects_runtime2.py` | none | `test_effects_runtime2.py` | **Removable duplicate prototype.** It has a third handler-stack model and no production importer. |
| `pcc/functional.py` | none | `test_functional_primitives.py` | **Removable library prototype.** It is not exported from `pcc.__init__` and has no compiler/runtime consumer. |
| `pcc/functional_result.py` | none | `test_functional_result_real.py` | **Removable duplicate prototype.** Its Result overlaps `functional.py`/`adt.py`; `fuse_map_filter` is also unconsumed. |
| `pcc/persistent.py` | none | `test_persistent.py` | **Removable roadmap prototype.** The tuple-backed structures have no compiler/runtime importer. |
| `pcc/c_libc_registry_extra.py` | none | `test_c_libc_registry_extra.py` | **Promote then remove.** Any signatures still needed must enter the production `c_libc_registry.py` owner with real lookup/codegen coverage; the detached tuple is not a registry extension. |
| `pcc/bench_profile.py` | none | `test_bench_profile.py` | **Removable test-only formatter** unless a benchmark command adopts its schema. |
| `pcc/bench_profile_aggregate.py` | none | `test_bench_profile_aggregate.py` | **Removable test-only formatter** unless a maintained script/CLI adopts it. |
| `pcc/pass_profile.py` | none | `test_pass_profile.py` | **Removable duplicate helper.** The real pass/CLI explanation path is wired elsewhere and does not import this module. |
| `pcc/compiler_hot_objects.py` | none | `test_compiler_hot_objects.py`, `test_value_model_valhalla.py` | **Docs/test-only prototype.** Move any still-authoritative value-model migration metadata to the value-model owner before removal. |
| `pcc/pattern_decision_tree.py` | none | `test_pattern_decision_tree.py` | **Removable test-only prototype.** It does not lower Python match/ADT code and must not be cited as compiler support. |

## Result

There is no unclassified candidate in the audited families.  In particular,
the similarly named effect modules are not one implementation: three are
unconsumed host-library experiments, while `runtime_effects.py` is a retained
production ABI checker.  Likewise, `profile_events.py` and
`roadmap_deepwire.py` are live production support despite roadmap-oriented
names.  No deletion or capability reduction was performed by this audit.
