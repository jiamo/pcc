# 01 · System Overview

pcc turns C **or** Python source into native code. The two frontends are separate subsystems that converge on a common backend layer.

## End-to-end data flow

```mermaid
flowchart TD
    subgraph Entry
      A["pcc.py / cli_core.py / api.py<br/>cli_main → execute_cli"]
      A --> B{"path ends in .py?"}
    end

    subgraph "C frontend (mature)"
      B -->|no| C1["project.py<br/>collect TranslationUnits"]
      C1 --> C2["c_evaluator.py<br/>_system_cpp preprocess"]
      C2 --> C3["c_parser.py (PLY)<br/>→ C AST"]
      C3 --> C4["c_codegen.py<br/>LLVMCodeGenerator"]
      C4 --> IR
    end

    subgraph "Python frontend (experimental)"
      B -->|yes| P1["py_parse.py + py_lift.py<br/>→ pcc AST (py_ast)"]
      P1 --> P2["type_infer.py<br/>infer_module"]
      P2 --> P3["codegen/layer1.py<br/>L1CodeGen (≈90 mixins)"]
      P3 --> IR
    end

    IR["LLVM IR text"] --> X{"backend / mode"}
    X -->|llvm| L1["llvmlite or llvm_capi"]
    X -->|self| S1["pcc/backend self-emit"]
    L1 --> EXE
    S1 --> EXE
    EXE["object · executable · MCJIT"]
    P3 -. link .-> RT["libpy_runtime*.a"]
    EXE -. C path .-> SCC["system cc / MCJIT"]
```

## Three subsystems, one repo

```mermaid
flowchart LR
    subgraph CCOMP["1 · C compiler"]
      direction TB
      ca["pcc/parse · pcc/lex · pcc/ast"]
      cb["pcc/codegen/c_codegen.py"]
      ce["pcc/evaluater/c_evaluator.py"]
    end
    subgraph PYCOMP["2 · Python compiler"]
      direction TB
      pa["pcc/parse/py_*"]
      pb["pcc/py_frontend/*"]
      pc["pcc/py_frontend/codegen/*"]
    end
    subgraph RUNT["3 · Runtime"]
      direction TB
      ra["pcc/py_runtime/src/*.c"]
      rb["pcc/py_runtime/py/*.py (mirror)"]
      rc["5 GC backends"]
    end
    subgraph SHB["Shared backends"]
      direction TB
      ba["pcc/llvm_capi (LLVM-C)"]
      bb["pcc/backend (self)"]
      bd["pcc/passes · pcc/ssa · pcc/ir_passes"]
    end
    CCOMP --> SHB
    PYCOMP --> SHB
    PYCOMP -.links.-> RUNT
```

## The five layers

| Layer | Primary paths | Responsibility |
|---|---|---|
| Entry surfaces | `pcc/pcc.py`, `pcc/cli_core.py`, `pcc/cli_launcher.py`, `pcc/api.py`, `pcc/cli_bootstrap.py` | CLI UX, Python API, bootstrap-stage CLI |
| Build orchestration | `pcc/project.py` | collect files, infer source sets, make-driven builds, TranslationUnits |
| Frontends | `pcc/evaluater/`, `pcc/codegen/`, `pcc/py_frontend/`, `pcc/parse/` | parse + lower C / Python to LLVM IR |
| Optimization / lowering | `pcc/passes/`, `pcc/ssa/`, `pcc/ir_passes/` | High/Mid/Low/Backend passes; SSA mid-tier |
| Backends + runtime | `pcc/llvm_capi/`, `pcc/backend/`, `pcc/py_runtime/` | emit native code; runtime objects + GC |

## Entry & dispatch (where it all starts)

- `pcc/cli_launcher.py:11` `main()` → `cli_main(argv)`.
- `pcc/cli_core.py:1111` `cli_main()` parses args; `execute_cli()` decides **C vs Python** by checking whether the input path ends in `.py` (`cli_core.py:905`).
- `pcc/cli_bootstrap.py:11` is a separate **Python-only** CLI used by the self-hosted `pcc1/pcc2/pcc3` binaries; it delegates C/project inputs back to a host `pcc` (`PCC_HOST_PCC`) and adds `-m <module>` (pip/pytest) support.

## Repo map (orientation)

| Path | What it is |
|---|---|
| `pcc/pcc.py`, `pcc/cli_core.py` | CLI entrypoints |
| `pcc/api.py` | `build(...)` / `module(...)` Python API (C) |
| `pcc/project.py` | source collection, TU selection, make-driven builds |
| `pcc/evaluater/c_evaluator.py` | C preprocess / parse / IR / optimize / execute coordinator |
| `pcc/codegen/c_codegen.py` | core C semantic lowering (most C bugs live here) |
| `pcc/parse/`, `pcc/lex/`, `pcc/ast/`, `pcc/ply/` | C parser pieces (PLY) |
| `pcc/parse/py_parse.py`, `pcc/parse/py_lift.py` | native Python parser + lift to pcc AST |
| `pcc/py_frontend/` | Python frontend (type infer, pipeline, codegen) |
| `pcc/py_frontend/codegen/` | ≈90 lowering mixins + native module lowering |
| `pcc/py_runtime/src/*.c`, `pcc/py_runtime/py/*.py` | runtime (C + pcc-Python mirror) |
| `pcc/llvm_capi/` | in-repo LLVM-C IR builder (llvmlite fallback) |
| `pcc/backend/` | LLVM-free self-backend |
| `pcc/package/`, `pcc/package_compat.py` | package install / extension-ABI gating |
| `utils/fake_libc_include/` | fake libc + `Python.h` shim headers |
| `tests/`, `projects/`, `benchmarks/` | regression, real-project, perf |

See the per-subsystem docs for the internals of each box above.
