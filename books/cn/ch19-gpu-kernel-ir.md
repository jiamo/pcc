# 第 19 章 GPU 内核 IR、Metal 与加速器执行

这一章补上前十八章刻意没碰的一条线:加速器。它属于同一个"拥有执行"的论题——目标不是"把某段算子跑得更快",而是让 GPU 执行也变得**可拥有**:有一份自己的、可审计的内核 IR,能在无 libpython 的二进制里把内核发射并真机启动,且对每一层边界诚实。判定它是否偏离主线的标准和别处一样:**TVM / TIRx / TileLang 只作参照(oracle),绝不作运行时依赖**——这正是 self 后端对 LLVM 用的"oracle,不是 owner"同一条规矩(见第 13 章)。

先把诚实的范围立在最前,因为这条线最容易被一句"我们支持 TVM/TileLang"骗过去:今天真实存在的是**一条 Metal 内核 IR 路径 + 小固定形状内核的真机执行**(仅 macOS/Metal、本机、硬件门控);**不存在**整程序 GPU、`import tvm` / `import tilelang` 的运行时执行、以及真正的分布式运行时。本章每一节都会重复标注它证明了什么、没证明什么。

## 19.1 边界与总览

GPU 线由三块组成,成熟度递减:

| 子系统 | 路径 | 今天是什么 |
|---|---|---|
| 内核 IR | [pcc/kernel_ir/](../../pcc/kernel_ir/) | 真代码:host/device 分裂的内核专用 IR、`validate_kernel()`、CPU 参照 oracle、Metal 源码/`.metallib` 定案、启动包、真机启动。 |
| GPU-GC | [pcc/gpu_gc/](../../pcc/gpu_gc/) | **CPU-only 研究 oracle**(`0.0.1-oracle`):外部资源生命周期缝,尚未接入五个生产 GC 后端。 |
| 分布式 | [pcc/dist/](../../pcc/dist/) | **本机单进程、无 socket** 的元数据 oracle(session/mesh/collective/sharding/KV)。每种网络模式都报 `SKIPPED_WITH_REASON`。 |

这三块与主线的关系写在 AGENTS.md 的"加速器执行是所有权论题的延伸"里:GPU 是使命的延伸,不是第六个使命,也不能挤占"自举 → 五 GC → 值模型 → 长跑效率"的脊柱。本章按成熟度从高到低讲。

## 19.2 内核 IR 与 host/device 分裂

`pcc/kernel_ir/` 的核心是一份**内核专用**的 IR,与第 2 章那条通用编译流水线分开:它只描述能在设备上跑的东西。`validate_kernel()` 是这条线的声明卫生闸——它在 device 边界**拒绝 PyObject**:设备侧不允许出现 pcc 的堆对象、动态分派、异常。这不是限制,而是"设备执行所有权"的前提:设备上跑的每条指令都必须是能被静态描述、能被 CPU 参照 oracle 逐点对照的。

CPU 参照 oracle(`cpu_reference.py`)是这条线的"native cc / CPython / llvmlite"对应物(见第 18 章 §oracle 方法):同一个内核,先在 CPU 上算出已知正确的结果,再拿设备结果逐点对照。没有这层对照,任何"GPU 跑通了"都只是"没崩"。

## 19.3 从 `@gpu.kernel` 到 Metal 的规范路径

面向用户的入口是装饰器 + Metal 后端:

```python
# vec_add.py
from pcc import gpu

@gpu.kernel
def add(a: gpu.ptr_f32, b: gpu.ptr_f32, out: gpu.ptr_f32, n: gpu.u32):
    i = gpu.thread_id_x()
    if i < n:
        out[i] = a[i] + b[i]
```

```bash
pcc --gpu-backend=metal vec_add.py -o vec_add   # 产出 host 可执行 + .metallib 边车
```

关键是这条**规范路径**,而不是临时的 AST→Metal 翻译:

```text
Kernel IR -> validate_kernel() -> TIRx 兼容 freeze -> Metal 定案 -> 启动包
```

`@gpu.kernel` 支持的子集**刻意很小**(逐元素 vector-add 形状),因为每一步都要能被 oracle 对照、被 claim level 标定。更复杂的形状(tiled/simdgroup GEMM、split-K 原子、转置操作数、边尾)不走装饰器,而走 §19.5 的库 API。

## 19.4 真机执行与 claim level

真机路径(`metal_source_runtime.py` / `metal_launch.py` / `metal_invoke.py`)是真的:发 Objective-C 桥、`MTLCreateSystemDefaultDevice` + `newLibraryWithSource`、clang 编译、`ctypes` 加载、提交真 command buffer、fence 完成、读回设备输出、对 CPU oracle。缓冲/围栏 ABI 在 `hmm_fence.py`,离线 `.metal→.air→.metallib` 链在 `metal_finalize.py` / `metal_package.py`。

但**证据必须按级别说**。`pcc/kernel_ir/gpu_claims.py` 定义 `GPU_LEVEL_0`(元数据)到 `GPU_LEVEL_6`(五 GC 生命周期平等)的阶梯。今天真机结果证明到的是小固定形状内核(copy/fill、标量 tiled GEMM、opt-in 8×8 simdgroup GEMM,尺寸如 M=5,N=7,K=3),且:

- **本机 only**、**`PCC_GPU_HARDWARE_STRICT=1` opt-in**;默认 CI 报 `SKIPPED_WITH_REASON` 或注入-CDLL 的 ABI 校验(Level 3),**决不伪造成功**。
- `whole_program_gpu` 处处硬编码 `false`——没有整程序 GPU。

级别定义与路线契约在 [docs/design/pcc-gpu-next-work.md](../../docs/design/pcc-gpu-next-work.md)。

## 19.5 TVM / TIRx / TileLang:oracle 而非 owner

这是全章声明卫生最尖锐的一节。pcc **不 import、不链接、不执行** TVM、TileLang 或 torch——这条路上任何地方都没有 `import tvm` / `import tilelang` 的可执行语句。三个可用的缝都是编译期、fail-closed 的:

- `import_tilelang_source(...)`([tilelang_import.py](../../pcc/kernel_ir/tilelang_import.py))用 `ast` 解析 TileLang Python-DSL 的**严格子集**(`@T.prim_func` matmul 形:`T.Kernel`、`T.alloc_shared`/`T.alloc_fragment`、`T.copy`、`T.gemm`、`T.clear`、`T.Pipelined`、split-K span、layout 注解)成 pcc 内核 IR。未知构造 fail-closed。每个产物都盖章 `executes_tilelang_runtime=False`——它解析的是**长得像 TileLang 的语法**,不是运行 TileLang。
- `lower_to_plain_tir(...)`([tirx_adapter.py](../../pcc/kernel_ir/tirx_adapter.py))把内核 IR 的 tile 原语 freeze 成镜像 TIRx `LowerTIRx` 的 plain-TIR 形状,并执行一条**否定规则**:CUDA-only 假设(cp.async、Hopper/TMA intrinsics)对 Metal 目标是**拒绝**,而不是静默降级。
- `project_to_tir_shape(...)`([tvm_oracle.py](../../pcc/kernel_ir/tvm_oracle.py))把 pcc `KernelFunc` 投影成 TVM TIR `PrimFunc` 的序列化对象形状,对 golden 比较——一个**不 import TVM** 的比较 oracle。
- `tilelang_compat.classify(...)` 报告当前子集接受/拒绝哪些 TileLang 构造(只检查、不执行;CuTeDSL、Hopper/Blackwell intrinsics 明确出界)。

所以"TileLang 支持"的准确说法是:pcc 能把一段**手挑的、TileLang 样式的** matmul 方言降到自己的 IR + Metal;它**不能运行 TileLang**。"TVM 支持"更薄:只有一个投影+对照 oracle。把这两者说成"有 TVM/TileLang 支持"正是本章开头警告的 overclaim。

## 19.6 GPU-GC 与分布式:今天是 CPU oracle

`pcc/gpu_gc/`(`__version__ = "0.0.1-oracle"`)是**CPU-only 研究 oracle**:它借用五个生产 GC 后端(第 10–11 章)的词汇建模 GPU 对象/外部资源生命周期,但**没有接入**那五个后端,也不是一个会搬动的收集器。它的 `external_resource` 缝是"production-shaped"但未接 C 或 pcc-Python 运行时。

`pcc/dist/` 是**本机单进程、CPU、无 socket** 的元数据 oracle:建模 session/`DRef` 身份、device mesh、确定性 collective 语义、sharding schedule、KV-block 记账。每种网络模式都报 `SKIPPED_WITH_REASON`——不是多进程、不是 localhost-TCP、不是多机执行。

## 19.7 声明边界(把话说死)

- **有**:一条真的 Metal 内核 IR 路径 + 小固定形状内核的真机执行(本机、硬件门控、claim-leveled 到 Level 4–6),CPU oracle 对照,TVM/TIRx/TileLang 作参照。
- **没有**:整程序 GPU;`import tvm` / `import tilelang` 运行时执行;外部框架(torch/MLX/MPS)互操作;任意形状/布局;gpu_gc 接入真后端;真分布式传输。
- 工具链/设备缺失一律 `SKIPPED_WITH_REASON`,决不算成功。

对应闸门:

```bash
env -u LC_ALL uv run pytest tests/kernel -q -n0        # IR/oracle/finalize/package(无工具链则 skip)
env -u LC_ALL uv run pytest tests/gpu_hardware -q -n0  # 真 Metal 启动:Level 4/5/6
env -u LC_ALL uv run pytest tests/gpu_gc -q -n0        # GPU-GC 元数据/生命周期缝
```

## 历史与教训

这条线的教训不是某个 bug,而是**为什么"oracle,不是 owner"必须写进架构**。一个 GPU 编译器最容易的自欺是:import 了 TVM/TileLang、让上游帮你跑通一个 kernel,然后宣称"支持 TVM/TileLang"——而其实你只是它们的调用者,一点执行所有权都没拿到。pcc 的选择是把 TVM/TIRx/TileLang 全部降为参照:它们定义"正确的形状长什么样",pcc 自己拥有从内核 IR 到 Metal 的每一步。这和第 13 章 self 后端把 LLVM 当 oracle、第 16 章值模型把 Valhalla 当投影参照,是同一条设计纪律的三次出现。代价是慢——只能一小片一小片地长;收益是每一片都是**自己的**、可审计、可 claim-level 标定的执行,而不是一句借来的"支持"。
