# Dual Backend Plan: keep LLVM(llvmlite) + optional own backend

> 目标（本次决议）：在**不影响现有路径**的前提下，开始支持“可选后端”能力，先上可切换的架构，再分阶段落地自己的 LLVM 后端（可运行，但默认仍走当前 llvmlite 路径）。

---

## 0. 现状锚点

- 已具备：LLVM IR 级 pass 翻译闭环已进入稳定阶段（all-pass 1:1 里程碑），`C` front-end/parser/main pipeline、integration、cache 等仍以 llvmlite 为主。
- 风险：如果直接硬切到“自研后端”会影响 `tests` 稳定性。
- 结论：要保持“主线不变”，只能按**双后端**、**默认关闭新后端**、**可选回退**来推进。

---

## 1. 目标拆解（可选项）

我们要支持两层可选：

1. **编译管线提供者（IR 与执行链）**
   - `llvm`：当前 `llvmlite` 路径（默认）
   - `llvm_capi`：先跑起来的“替代管线”（文本/IR 到 LLVM-C API）

2. **机器码后端（长期）**
   - `builtin`: 继续依赖 LLVM 后端
   - `self`：自研 Python 后端（未来，逐步推进）

> 当前先只要求完成第 1 层的双后端可选，`self` 在第 3 阶段再接入。

---

## 2. 设计原则

- **默认行为不变**：现有测试、默认 CLI 行为都必须继续走当前稳定路径。
- **按 token 估算任务，不按人时。**
  - 这里的 `repo token` 指产出代码/测试/文档的净 token。
  - `working token` 指执行 + 调试 + 回归迭代消耗的 token；通常明显高于 repo token。
- **Feature gating**：所有新后端功能必须通过显式 flag 打开。
- **可回退**：任何新后端都必须有“失败回退到 llvmlite”的策略，不让现网受阻。
- **粒度小**：每个阶段改动只影响少量模块。

---

## 3. 任务包（按里程碑）

### 阶段 A：基础能力（无行为改动）

**目的**：先把“可选后端”这件事变成配置，不改现有语义。

#### A1. 后端选择接口（低耦合）
- 新建 `pcc/backend/` 目录，定义后端协议：`BackendKind`、`BackendConfig`、`BackendSession`。
- 新增 `PCC_BACKEND` env + `--backend` CLI（值：`llvm`, `llvm_capi`, `self`）。
- 当前默认值 = `llvm`。
- 现有 `pcc/pcc.py`/`pcc/evaluater/c_evaluator.py` 只读取该配置。
- **验收**：
  - `env PCC_BACKEND=llvm` 与默认结果完全一致。
  - `--backend=llvm` 不报新警告。
- **token 估算**：
  - `repo token`: `8k-25k`
  - `working token`: `120k-260k`

#### A2. 编译缓存与签名打标（避免污染）
- 在 `_compile_cache_key` / 本地缓存签名中加入：
  - `backend_kind`
  - `backend_semver`（后端能力标识）
  - `backend_config hash`
- 让 `--backend` 模式变化时 cache 必定 miss。
- **验收**：模式切换后不会误用旧 `*.so`/`*.json`。
- **token 估算**：
  - `repo token`: `4k-9k`
  - `working token`: `40k-120k`

#### A3. 开关文档 + 自检测试
- 新增文档章节：`docs/plans/` 内新增一页，写明默认/可选/禁用。
- `tests`：新增 3-5 个轻量测试，仅验证 env/CLI 显式行为，不改原语义。
  - 默认模式仍旧可运行小规模关键回归。
  - 非默认后端在未实现时给出清晰报错。
- **验收**：`pytest tests/test_backend_selector.py`（新）绿。
- **token 估算**：
  - `repo token`: `6k-12k`
  - `working token`: `60k-140k`

---

### 阶段 B：`llvm_capi` 可执行后端（替换 llvmlite runtime 入口）

**目的**：在不改 codegen 主路径的情况下，先让自研后端可以**接管运行时管线**。

#### B1. 后端适配层（对象层 + 执行层）
- 新增 `pcc/backend/llvm_capi_backend.py`，将 `evaluate` 中的：
  - `llvm.parse_assembly`
  - `target_machine.emit_object`
  - `llvm.create_mcjit_compiler` / `ee.get_function_address`
  封装为统一接口。
- 引入 `BackendUnavailable` 明确区分“接口未声明”与“运行失败”。
- **验收**：在 `PCC_BACKEND=llvm_capi` 下，未改代码路径下也可输出与 llvm 默认路径一致的行为边界（当 backend 实现完整）。
- **token 估算**：
  - `repo token`: `20k-45k`
  - `working token`: `250k-700k`

#### B2. 扩展 `pcc/llvm_capi` 的声明面
- 补齐最小所需声明（不必一次齐全）：
  - 运行时初始化、context/module、builder、target machine、parse/verify、对象生命周期。
- 先不追求完整性，允许 `NotImplemented` 兜底。
- **验收**：`llvm_capi` 后端至少覆盖：`parse+verify+emit_object` 的单元路径。
- **token 估算**：
  - `repo token`: `6k-18k`
  - `working token`: `80k-220k`

#### B3. 集成与回退机制
- `PCC_BACKEND=llvm_capi` + 缺失符号时：自动回退 `llvm`（并打 warning + metrics）。
- `self-host` 目标（若开启）允许默认后端强制切到 `llvm_capi`。
- **验收**：
  - 能看到 backend 选择日志（便于审计）。
  - 回退不会污染主 cache。
- **token 估算**：
  - `repo token`: `8k-16k`
  - `working token`: `80k-180k`

---

### 阶段 C：自研机器后端（`self`）第一版（单目标，Asm-first）

> 这一步是你说的“自己的 backend”真正开始。先做最小目标架构，不影响现有主线。

#### C1. Scope 锁定（MVP）
- 先只支持单目标（建议 AArch64-darwin 或 x86_64-linux 两选一）。
- 只处理以下指令范围：
  - 整数/指针算术、比较、分支、call、基本栈分配、局部数组/结构读取写入（仅先前端能生成的子集）。
- 先不做：SIMD、异常、DWARF、复杂重定位。
- **验收**：self 后端可编译 `tests/test_cli` 的核心集子集并产出可运行二进制。
- **token 估算**：
  - `repo token`: `90k-180k`
  - `working token`: `1.6M-4M`

#### C2. 最小后端核（MIR/Lowering）
- 从 LLVM IR 文本做轻量解析或直译到自有中间表示（推荐 MIR）。
- 实现：
  - 线性扫描式寄存器分配
  - 简易 ABI 规则（调用约定/返回值/参数传递）
  - Frame layout（栈桢大小、对齐、callee-save）
  - 直接 `asm` 输出。
- **验收**：对 `while/if/call/return` 的小程序，汇编可执行与行为对齐。 
- **token 估算**：
  - `repo token`: `120k-260k`
  - `working token`: `2.8M-7M`

#### C3. 后端选择与回退
- 增加 `--backend=self` 与 `PCC_BACKEND=self`。
- 未覆盖功能区域按 fallback/报错策略退回到 `llvm_capi`。
- 在测试中加入“能力标签”：一部分测试走默认后端，一部分测试 `self` 下 skip（已明确）。
- **验收**：
  - `self` 可被显式打开。
  - 没有 `self` 覆盖的功能不再静默误编译。
- **token 估算**：
  - `repo token`: `12k-24k`
  - `working token`: `130k-320k`

---

## 4. 当前工作不受影响的保障清单

1. `PCC_BACKEND` 默认值 = `llvm`。
2. 不动现有默认 pass pipeline 和 optimization 顺序。
3. 所有现有 suite gate 仍用默认 backend 跑。
4. 新后端功能必须是开关式：
   - 未启用则不进入任何关键路径。
5. 每个阶段都保留 `rollback`: 切回默认并保持同一 commit 通过。

---

## 5. 与已有计划的关系（对齐）

- 与 `all-pass-llvm-ir-1to1-master-plan` 的关系：
  - 本计划不再重复 pass 翻译；它只是把“当前已翻译的 pass”接到可选后端接口里。
- 与 `llvmcapi-wire-spike-report` 的关系：
  - 这是该 spike 的工程化落地版，变成长期任务化。
- 与 `self-backend-translation-plan` 的关系：
  - 本计划负责把后端做成**可选能力**；
  - `docs/plans/self-backend-translation-plan.md` 负责把“我们自己的机器后端”拆成独立 roadmap；
  - 两者关系是：`β4/llvm_capi` 负责前半段解耦与共享 builder，`self backend` 在这个共享边界之上逐步落地。
- 与 `python-frontend-plan` 的 Phase 6C：
  - 这里是 Phase 6C 的必要底座：同一 artifact/back-end 管线的共享。

---

## 6. 接下来立即可执行任务（建议今天就开）

- `Task 0`：建立 `docs/plans/dual-llvm-backend-compat-plan.md`（本计划）
- `Task 1`：在 evaluator/pcc CLI 增加后端选择开关（`--backend`/`PCC_BACKEND`）
- `Task 2`：增加 cache fingerprint 后端维度
- `Task 3`：新增后端选择自检测试（2~3个）
- `Task 4`：开始 `llvm_capi_backend` 抽象接口的 skeleton（不改旧行为）

> 以上四项可以做到“边做边跑当前完整 suite”：默认路径不变，新增测试可控。

---

## 7. 里程碑判定

- **M0（本周）**：开关和缓存隔离完成，默认行为不变。
- **M1（下阶段）**：`llvm_capi` 可替代 `llvmlite` 运行到 emit_object/基本加载。
- **M2（后续）**：`self` 后端可以选择打开并在最小子集下运行。
- **M3（长期）**：逐步扩展 `self` 覆盖率与目标族。
