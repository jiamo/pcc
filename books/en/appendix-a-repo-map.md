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
| `pcc/py_runtime/src/*.c` | C runtime (objects, GC, threads, exceptions, ...) |
| [pcc/py_runtime/src/py_gc_backend.c](../../pcc/py_runtime/src/py_gc_backend.c) | The five GC backends |
| `pcc/py_runtime/py/*.py` | pcc-Python runtime ports (mirrors of C; for self-hosting) |
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
| [pcc/py_runtime/src/py_capi_shim.c](../../pcc/py_runtime/src/py_capi_shim.c), `py_extension_loader.c`, `py_cpy_handle.c` | C-API shim, extension loading, CpyHandle |

## Methodology documents (Chapter 18)

| Path | Role |
|---|---|
| [AGENTS.md](../../AGENTS.md) | Repository rules and north star (Project Intent) |
| [codex-goal-prompt.md](../../codex-goal-prompt.md) | Goal contract and work protocol (§0.10 claim-hygiene table) |
| [docs/current-goal-state.md](../../docs/current-goal-state.md) | Current goal audit and routing |
| [docs/debugging-playbook.md](../../docs/debugging-playbook.md) | Debugging playbook (12 techniques) |
| [docs/investigation-workflow.md](../../docs/investigation-workflow.md) | Investigation workflow (three modes and template) |
| [docs/investigations/INDEX.md](../../docs/investigations/INDEX.md) | Index of 280+ investigations |
| [tests/](../../tests) | Unit, parity, and integration regressions |
| [projects/lua-5.5.0/](../../projects/lua-5.5.0) | Real-program stress target |
