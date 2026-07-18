# 附录 A 仓库地图

本附录是全书源码引用的总索引。按子系统组织;路径相对仓库根。

## 入口与驱动

| 路径 | 角色 |
|---|---|
| [pcc/pcc.py](../../pcc/pcc.py)、[pcc/cli_core.py](../../pcc/cli_core.py) | CLI 入口 |
| [pcc/cli_bootstrap.py](../../pcc/cli_bootstrap.py) | 自举各阶段(`pcc1`/`pcc2`/`pcc3`)使用的 bootstrap CLI |
| [pcc/api.py](../../pcc/api.py) | C 路径的 `build(...)` / `module(...)` Python API |
| [pcc/project.py](../../pcc/project.py) | 目录源收集、`--sources-from-make`、TU 选择 |
| [scripts/bootstrap.sh](../../scripts/bootstrap.sh) | macOS arm64 三阶段自举入口 |
| [scripts/pcc_multi.py](../../scripts/pcc_multi.py) | 实验性多文件 Python 入口 |

## C 前端(第 3–4 章)

| 路径 | 角色 |
|---|---|
| [pcc/parse/c_parser.py](../../pcc/parse/c_parser.py) | C 解析器(PLY;改语法/词法须升缓存版本号) |
| [pcc/preprocessor.py](../../pcc/preprocessor.py) | 预处理 |
| [pcc/evaluater/c_evaluator.py](../../pcc/evaluater/c_evaluator.py) | C 预处理/解析/IR/优化/执行流水线 |
| [pcc/codegen/c_codegen.py](../../pcc/codegen/c_codegen.py) | C 语义低层化主体(符号性不变式所在) |
| [utils/fake_libc_include/](../../utils/fake_libc_include) | 伪 libc 头(host ABI 失配在此暴露) |

## Python 前端(第 5–6 章)

| 路径 | 角色 |
|---|---|
| [pcc/parse/py_parse.py](../../pcc/parse/py_parse.py)、[pcc/parse/py_lift.py](../../pcc/parse/py_lift.py) | Python 解析与提升 |
| [pcc/py_frontend/py_ast.py](../../pcc/py_frontend/py_ast.py)、`pipeline.py`、`type_infer.py` | AST、流水线、类型推断 |
| [pcc/py_frontend/codegen/layer1.py](../../pcc/py_frontend/codegen/layer1.py) | Layer-1 低层化 facade |
| `pcc/py_frontend/codegen/*_lowering.py` | 低层化 mixin 群(行为主体) |
| `pcc/py_frontend/codegen/native_*.py` | 原生模块低层化(gc、threading、asyncio、os、math…) |
| [pcc/fallback_routes.py](../../pcc/fallback_routes.py)、[pcc/fallback_explainer.py](../../pcc/fallback_explainer.py) | 回退路由与解释 |

## 运行时(第 7–11、14 章)

| 路径 | 角色 |
|---|---|
| [pcc/py_runtime/include/py_runtime.h](../../pcc/py_runtime/include/py_runtime.h) | 公共头:对象头、类型标签、`PCC_GC_KIND_*` |
| [pcc/py_runtime/src/py_internal.h](../../pcc/py_runtime/src/py_internal.h) | 运行时内部布局(如 `PyClassObject`) |
| `pcc/py_runtime/src/*.c` | C 运行时(对象、GC、线程、异常…) |
| [pcc/py_runtime/src/py_gc_backend.c](../../pcc/py_runtime/src/py_gc_backend.c) | 五 GC 后端实现 |
| `pcc/py_runtime/py/*.py` | pcc-Python 运行时端口(C 的镜像;自托管用) |
| [pcc/extern/](../../pcc/extern)、[pcc/unsafe/](../../pcc/unsafe) | Python→C extern 声明;编译器识别的内建 |
| [docs/refs_docs/gc-research/](../../docs/refs_docs/gc-research) | 五 GC 的参照实现(Lua、Go、OCaml、ZGC、CPython) |

## 后端(第 12–13 章)

| 路径 | 角色 |
|---|---|
| [pcc/llvm_capi/](../../pcc/llvm_capi) | 仓库内 LLVM-C builder(llvmlite 为回退与 oracle) |
| [pcc/backend/](../../pcc/backend) | self 后端(AArch64 Darwin、x86_64 Linux 子集) |

## 自举与基线(第 15 章)

| 路径 | 角色 |
|---|---|
| [tests/bootstrap_gate_baseline.json](../../tests/bootstrap_gate_baseline.json) | **自举状态权威基线** |
| [tests/fallback_baseline.json](../../tests/fallback_baseline.json) | **no-libpython 回退棘轮权威基线** |
| `tests/python/gc/test_pcc_bootstrap_full_gc{0..4}.py` | 五 GC 全自举闸(stage1→2→3) |
| [tests/python/test_self_host_oracle_diff.py](../../tests/python/test_self_host_oracle_diff.py) | Python 语义 oracle / pcc1-pcc2 对齐棘轮 |

## 包与扩展(第 17 章)

| 路径 | 角色 |
|---|---|
| [pcc/package/](../../pcc/package)、[pcc/capi_abi.py](../../pcc/capi_abi.py)、[pcc/capi_surface.py](../../pcc/capi_surface.py) | 包路径与 C-API 面 |
| [pcc/py_runtime/src/py_capi_shim.c](../../pcc/py_runtime/src/py_capi_shim.c)、`py_extension_loader.c`、`py_cpy_handle.c` | C-API shim、扩展装载、CpyHandle |

## 方法论文档(第 18 章)

| 路径 | 角色 |
|---|---|
| [AGENTS.md](../../AGENTS.md) | 仓库规则与北极星(Project Intent) |
| [codex-goal-prompt.md](../../codex-goal-prompt.md) | 目标契约与工作协议(§0.10 声明卫生表) |
| [docs/current-goal-state.md](../../docs/current-goal-state.md) | 当前目标审计与路由 |
| [docs/debugging-playbook.md](../../docs/debugging-playbook.md) | 调试手册(12 技法) |
| [docs/investigation-workflow.md](../../docs/investigation-workflow.md) | 调查工作流(三模式与模板) |
| [docs/investigations/INDEX.md](../../docs/investigations/INDEX.md) | 两百余篇调查的索引 |
| [tests/](../../tests) | 单元、对齐、集成回归 |
| [projects/lua-5.5.0/](../../projects/lua-5.5.0) | 真实程序压力目标 |
