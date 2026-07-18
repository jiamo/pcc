# Chapter 19: GPU Kernel IR, Metal, and Accelerator Execution

This chapter fills a line the first eighteen deliberately left alone:
accelerators. It belongs to the same "owning execution" thesis — the goal is not
"run one operator faster" but to make GPU execution **ownable**: with its own
auditable kernel IR, the ability to emit and launch kernels on a real device
from a no-libpython binary, and honesty about every boundary. The test for
whether it drifts off-mission is the same as everywhere else: **TVM / TIRx /
TileLang are references (oracles), never runtime dependencies** — exactly the
"oracle, not owner" rule the self backend applies to LLVM (Chapter 13).

State the honest scope first, because this is the line most easily faked by the
sentence "we support TVM/TileLang." What exists today is **one Metal kernel-IR
path plus real on-device execution of small fixed-shape kernels** (macOS/Metal
only, local, hardware-gated). What does **not** exist: whole-program GPU,
`import tvm` / `import tilelang` runtime execution, and any real distributed
runtime. Every section below repeats what it proves and what it does not.

## 19.1 Boundary and overview

The GPU line has three parts, in decreasing maturity:

| Subsystem | Path | What it is today |
|---|---|---|
| Kernel IR | [pcc/kernel_ir/](../../pcc/kernel_ir/) | Real code: a host/device-split kernel-only IR, `validate_kernel()`, CPU reference oracles, Metal source/`.metallib` finalization, launch packages, real-device launch. |
| GPU-GC | [pcc/gpu_gc/](../../pcc/gpu_gc/) | A **CPU-only research oracle** (`0.0.1-oracle`): an external-resource lifetime seam, not yet wired into the five production GC backends. |
| Distributed | [pcc/dist/](../../pcc/dist/) | **Single-process, no-socket** metadata oracles (session/mesh/collective/sharding/KV). Every network mode reports `SKIPPED_WITH_REASON`. |

Their relationship to the mission is written in AGENTS.md ("Accelerator
execution is an extension of the ownership thesis"): GPU is an extension of the
mission, not a sixth one, and must not displace the self-host → 5-GC → value →
runtime-efficiency spine. This chapter proceeds from most to least mature.

## 19.2 Kernel IR and the host/device split

The center of `pcc/kernel_ir/` is a **kernel-only** IR, kept separate from the
general pipeline of Chapter 2: it describes only what can run on the device.
`validate_kernel()` is this line's claim-hygiene gate — it **rejects PyObject at
the device frontier**: no pcc heap objects, dynamic dispatch, or exceptions are
allowed device-side. That is not a limitation but the precondition for
device-execution ownership: every device instruction must be statically
describable and checkable point-for-point against a CPU reference oracle.

The CPU reference oracle (`cpu_reference.py`) is this line's counterpart to
"native cc / CPython / llvmlite" (Chapter 18, §oracle method): the same kernel
computes a known-good result on the CPU, then the device result is compared
against it point-for-point. Without that layer, "the GPU ran" means only "it did
not crash."

## 19.3 From `@gpu.kernel` to Metal: the canonical route

The user-facing entry is a decorator plus the Metal backend:

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
pcc --gpu-backend=metal vec_add.py -o vec_add   # host executable + .metallib sidecar
```

What matters is this **canonical route**, not ad-hoc AST→Metal translation:

```text
Kernel IR -> validate_kernel() -> TIRx-compatible freeze -> Metal finalize -> launch package
```

The `@gpu.kernel` subset is **deliberately small** (elementwise vector-add
shapes) because each step must be oracle-checkable and claim-leveled. More
complex shapes (tiled/simdgroup GEMM, split-K with atomics, transposed operands,
edge tails) do not use the decorator; they use the library API of §19.5.

## 19.4 On-device execution and claim levels

The on-device path (`metal_source_runtime.py` / `metal_launch.py` /
`metal_invoke.py`) is real: it emits an Objective-C bridge,
`MTLCreateSystemDefaultDevice` + `newLibraryWithSource`, compiles with clang,
loads via `ctypes`, submits a real command buffer, completes a fence, reads back
device output, and compares to the CPU oracle. The buffer/fence ABI lives in
`hmm_fence.py`; the offline `.metal→.air→.metallib` chain in `metal_finalize.py`
/ `metal_package.py`.

But **evidence must be stated by level**. `pcc/kernel_ir/gpu_claims.py` defines a
ladder from `GPU_LEVEL_0` (metadata) to `GPU_LEVEL_6` (five-GC lifetime parity).
Today the on-device results prove small fixed-shape kernels (copy/fill, scalar
tiled GEMM, an opt-in 8×8-simdgroup GEMM at sizes like M=5,N=7,K=3), and:

- **local-only**, **opt-in behind `PCC_GPU_HARDWARE_STRICT=1`**; default CI
  reports `SKIPPED_WITH_REASON` or injected-CDLL ABI validation (Level 3),
  **never a fabricated success**.
- `whole_program_gpu` is hard-coded `false` everywhere — there is no
  whole-program GPU.

The level definitions and route contract live in
[docs/design/pcc-gpu-next-work.md](../../docs/design/pcc-gpu-next-work.md).

## 19.5 TVM / TIRx / TileLang: oracle, not owner

This is the chapter's sharpest claim-hygiene section. pcc does **not import,
link, or execute** TVM, TileLang, or torch — there is no `import tvm` /
`import tilelang` executable statement anywhere on this route. The three usable
seams are compile-time and fail-closed:

- `import_tilelang_source(...)` ([tilelang_import.py](../../pcc/kernel_ir/tilelang_import.py))
  parses a **strict subset** of the TileLang Python DSL (`@T.prim_func`
  matmul shape: `T.Kernel`, `T.alloc_shared`/`T.alloc_fragment`, `T.copy`,
  `T.gemm`, `T.clear`, `T.Pipelined`, split-K spans, layout annotations) into pcc
  Kernel IR via `ast`. Unknown constructs fail closed. Every module is stamped
  `executes_tilelang_runtime=False` — it parses **syntax that looks like
  TileLang**, it does not run TileLang.
- `lower_to_plain_tir(...)` ([tirx_adapter.py](../../pcc/kernel_ir/tirx_adapter.py))
  freezes Kernel IR tile primitives into a plain-TIR shape mirroring TIRx's
  `LowerTIRx`, and enforces a **negative rule**: CUDA-only assumptions
  (cp.async, Hopper/TMA intrinsics) are **rejected** for a Metal target, not
  silently degraded.
- `project_to_tir_shape(...)` ([tvm_oracle.py](../../pcc/kernel_ir/tvm_oracle.py))
  projects a pcc `KernelFunc` into the serialized object shape of a TVM TIR
  `PrimFunc` and compares against goldens — a comparison oracle with **no TVM
  import**.
- `tilelang_compat.classify(...)` reports which TileLang constructs the current
  subset accepts or rejects (inspect-only; CuTeDSL and Hopper/Blackwell
  intrinsics are explicitly out of scope).

So the precise statement of "TileLang support" is: pcc can lower a **hand-picked,
TileLang-*looking*** matmul dialect into its own IR + Metal; it **cannot run
TileLang**. "TVM support" is thinner still: only a projection-and-compare oracle.
Calling either "TVM/TileLang support" is exactly the overclaim this chapter opens
by warning against.

## 19.6 GPU-GC and distributed: CPU oracles today

`pcc/gpu_gc/` (`__version__ = "0.0.1-oracle"`) is a **CPU-only research oracle**:
it borrows the vocabulary of the five production GC backends (Chapters 10–11) to
model GPU object / external-resource lifetime, but is **not wired into** those
backends and is not a moving collector. Its `external_resource` seam is
"production-shaped" but not connected to the C or pcc-Python runtime.

`pcc/dist/` is a **single-process, CPU, no-socket** metadata oracle: it models
session/`DRef` identity, device mesh, deterministic collective semantics,
sharding schedules, and KV-block bookkeeping. Every network mode reports
`SKIPPED_WITH_REASON` — not multi-process, not localhost-TCP, not multi-machine.

## 19.7 Claim boundary (stated plainly)

- **Have:** one real Metal kernel-IR path plus on-device execution of small
  fixed-shape kernels (local, hardware-gated, claim-leveled to Level 4–6),
  checked against CPU oracles, with TVM/TIRx/TileLang as references.
- **Do not have:** whole-program GPU; `import tvm` / `import tilelang` runtime
  execution; external-framework (torch/MLX/MPS) interop; arbitrary shapes/layouts;
  gpu_gc wired into real backends; real distributed transport.
- Toolchain/device absence always reports `SKIPPED_WITH_REASON`, never success.

Corresponding gates:

```bash
env -u LC_ALL uv run pytest tests/kernel -q -n0        # IR/oracle/finalize/package (skips without toolchain)
env -u LC_ALL uv run pytest tests/gpu_hardware -q -n0  # real Metal launch: Level 4/5/6
env -u LC_ALL uv run pytest tests/gpu_gc -q -n0        # GPU-GC metadata/lifetime seam
```

## History and lessons

The lesson on this line is not a single bug; it is **why "oracle, not owner" had
to be written into the architecture.** A GPU compiler's easiest self-deception is
to `import` TVM/TileLang, let the upstream stack run a kernel for you, and then
claim "we support TVM/TileLang" — when in fact you are only their caller and have
gained no execution ownership at all. pcc's choice is to demote TVM/TIRx/TileLang
to references: they define what a correct shape looks like, and pcc owns every
step from kernel IR to Metal. This is the same design discipline appearing for a
third time — the self backend treating LLVM as an oracle (Chapter 13), the value
model treating Valhalla as a projection reference (Chapter 16). The cost is speed
— it can only grow slice by slice; the payoff is that every slice is **its own**,
auditable, claim-leveled execution, not a borrowed "support."
