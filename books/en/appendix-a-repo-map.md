# Appendix A: Repository Map

This appendix is the master index for source references throughout the book.
Organized by subsystem; paths are relative to the repository root.

## Entry points and drivers

| Path | Role |
|---|---|
| [pcc/pcc.py](../../pcc/pcc.py), [pcc/cli_core.py](../../pcc/cli_core.py) | CLI entry points |
| [pcc/cli_bootstrap.py](../../pcc/cli_bootstrap.py) | Bootstrap-stage CLI used by `pcc1`/`pcc2`/`pcc3` |
| [pcc/api.py](../../pcc/api.py) | `build(...)` / `module(...)` Python API for the C path |
| [pcc/project.py](../../pcc/project.py) | Directory source collection, `--sources-from-make`, TU selection |
| [scripts/bootstrap.sh](../../scripts/bootstrap.sh) | macOS arm64 three-stage bootstrap entry |
| [scripts/pcc_multi.py](../../scripts/pcc_multi.py) | Experimental multi-file Python entry |

## C frontend (Chapters 3–4)

| Path | Role |
|---|---|
| [pcc/parse/c_parser.py](../../pcc/parse/c_parser.py) | C parser (PLY; bump cache version on grammar/lexer changes) |
| [pcc/preprocessor.py](../../pcc/preprocessor.py) | Preprocessing |
| [pcc/evaluater/c_evaluator.py](../../pcc/evaluater/c_evaluator.py) | C preprocess/parse/IR/optimize/execute pipeline |
| [pcc/codegen/c_codegen.py](../../pcc/codegen/c_codegen.py) | Main C semantic lowering (home of the signedness invariants) |
| [utils/fake_libc_include/](../../utils/fake_libc_include) | Fake libc headers (host ABI mismatches surface here) |

## Python frontend (Chapters 5–6)

| Path | Role |
|---|---|
| [pcc/parse/py_parse.py](../../pcc/parse/py_parse.py), [pcc/parse/py_lift.py](../../pcc/parse/py_lift.py) | Python parsing and lifting |
| [pcc/py_frontend/py_ast.py](../../pcc/py_frontend/py_ast.py), `pipeline.py`, `type_infer.py` | AST, pipeline, type inference |
| [pcc/py_frontend/codegen/layer1.py](../../pcc/py_frontend/codegen/layer1.py) | Thin lowering facade |
| `pcc/py_frontend/codegen/*_lowering.py` | The lowering mixins (where the behavior lives) |
| `pcc/py_frontend/codegen/native_*.py` | Native module lowering (gc, threading, asyncio, os, math, ...) |
| [pcc/fallback_routes.py](../../pcc/fallback_routes.py), [pcc/fallback_explainer.py](../../pcc/fallback_explainer.py) | Fallback routing and explanation |

## Runtime (Chapters 7–11, 14)

| Path | Role |
|---|---|
| [pcc/py_runtime/include/py_runtime.h](../../pcc/py_runtime/include/py_runtime.h) | Public header: object header, type tags, `PCC_GC_KIND_*` |
| [pcc/py_runtime/src/py_internal.h](../../pcc/py_runtime/src/py_internal.h) | Runtime-internal layouts (e.g. `PyClassObject`) |
| `pcc/py_runtime/src/*.c` | Transitional/host-C implementations and differential oracles; not member sources for the final production pcc-Python archive |
| [pcc/py_runtime/Makefile](../../pcc/py_runtime/Makefile) | `PY_MODULES`, `FREESTANDING_PY_MODULES`, provenance, and production archive assembly |
| `pcc/py_runtime/py/py_*.py` | Semantic pcc-Python: object, container, exception, and C-API behavior |
| `pcc/py_runtime/py/freestanding_*.py` | Freestanding pcc-Python: allocator, threads, platform/libc-like substrate, and five-GC policy |
| [pcc/py_runtime/py/freestanding_gc_object_slots.py](../../pcc/py_runtime/py/freestanding_gc_object_slots.py) | Unified production object-slot visitation contract |
| [pcc/extern/](../../pcc/extern), [pcc/unsafe/](../../pcc/unsafe) | Python→C extern declarations; compiler-recognized intrinsics |
| [docs/refs_docs/gc-research/](../../docs/refs_docs/gc-research) | Reference implementations for the five GCs (Lua, Go, OCaml, ZGC, CPython) |

## Backends (Chapters 12–13)

| Path | Role |
|---|---|
| [pcc/llvm_capi/](../../pcc/llvm_capi) | In-repo LLVM-C builder (llvmlite as fallback and oracle) |
| [pcc/backend/](../../pcc/backend) | Self backend (AArch64 Darwin, x86_64 Linux subsets) |

## Bootstrap and baselines (Chapter 15)

| Path | Role |
|---|---|
| [tests/bootstrap_gate_baseline.json](../../tests/bootstrap_gate_baseline.json) | **Authoritative bootstrap state** |
| [tests/fallback_baseline.json](../../tests/fallback_baseline.json) | **Authoritative no-libpython fallback ratchet** |
| `tests/python/gc/test_pcc_bootstrap_full_gc{0..4}.py` | Five-GC full bootstrap gates (stage1→2→3) |
| [tests/python/test_self_host_oracle_diff.py](../../tests/python/test_self_host_oracle_diff.py) | Python semantic oracle / pcc1–pcc2 parity ratchet |

## Packages and extensions (Chapter 17)

| Path | Role |
|---|---|
| [pcc/package/](../../pcc/package), [pcc/capi_abi.py](../../pcc/capi_abi.py), [pcc/capi_surface.py](../../pcc/capi_surface.py) | Package path and C-API surface |
| `pcc/py_runtime/py/py_capi_*_runtime.py`, `py_extension_loader_runtime.py` | Production pcc-Python C-API ABI and extension-loader owners |
| [pcc/py_runtime/src/py_capi_shim.c](../../pcc/py_runtime/src/py_capi_shim.c), `py_extension_loader.c` | Host-C oracle/transitional implementations, not production pcc-Python archive owners |

## GUI and application execution (Chapter 20)

| Path | Role |
|---|---|
| [pcc/py_runtime/gui_declarative_contract_v1.json](../../pcc/py_runtime/gui_declarative_contract_v1.json) | Declarative GUI v1 records, state machines, capacities, and error ABI |
| [pcc/py_runtime/py/pcc_gui_kit.py](../../pcc/py_runtime/py/pcc_gui_kit.py) | Canonical reclaimable composition-tree kernel, layout, clipping, hit paths, and render walk |
| `pcc/py_runtime/py/pcc_gui_{components,scheduler,events}.py` | Keyed atomic commit, state lanes, listeners, and effect lifecycle |
| `pcc/py_runtime/py/pcc_gui_{style,commands,app_lifecycle}.py` | Utility compiler/cache, managed command resolution, and webview-free run lifecycle |
| [projects/mac_diff_app/](../../projects/mac_diff_app) | Declarative dual-pane diff canary and AppKit/Metal boundary |
| [docs/design/gui-declarative-absorption.md](../../docs/design/gui-declarative-absorption.md) | React/Tailwind/Tauri mechanism-absorption boundary, nonclaims, and task route |

## Methodology documents (Chapter 18)

| Path | Role |
|---|---|
| [AGENTS.md](../../AGENTS.md) | Repository rules and north star (Project Intent) |
| [docs/goal/goal-prompt.md](../../docs/goal/goal-prompt.md) | Goal contract and work protocol (§0.10 claim-hygiene table) |
| [docs/goal/task-board.yaml](../../docs/goal/task-board.yaml) | Structured task execution queue (`scripts/goal_state.py next` selects work) |
| [docs/current-goal-state.md](../../docs/current-goal-state.md) | Current goal audit and routing |
| [docs/debugging-playbook.md](../../docs/debugging-playbook.md) | Debugging playbook (12 techniques) |
| [docs/investigation-workflow.md](../../docs/investigation-workflow.md) | Investigation workflow (three modes and template) |
| [docs/investigations/INDEX.md](../../docs/investigations/INDEX.md) | Index of 280+ investigations |
| [tests/](../../tests) | Unit, parity, and integration regressions |
| [projects/lua-5.5.0/](../../projects/lua-5.5.0) | Real-program stress target |
