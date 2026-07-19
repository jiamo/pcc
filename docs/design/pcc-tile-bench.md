# PCC Tile Bench — Design (First Slice: metadata-only measurement harness)

Status: **first slice landed** — `tests/benchmarks/tile/` is a self-contained
measurement harness over the existing `pcc.kernel_ir` package. It measures
**logical compile-side metrics only**. **No device codegen, no GPU launch, no
`.metallib` produced, no host launch claimed, no TFLOPS/latency number.**

Row: `P-P0-TILE-TVM-BENCH`. This document is the authoritative map for the tile
benchmark harness and its hard claim boundary. It builds directly on the kernel
IR pipeline documented in [`pcc-kernel-ir.md`](pcc-kernel-ir.md) (the
`K-P0-*` cluster) and the research judgment in
`tmp_research/deep-research-tvm.md` (do not integrate whole-TVM/whole-TileLang;
keep a TIRx-compatible middle tier and freeze tile semantics to plain TIR before
any target codegen).

## 1. Why a measurement harness (and not a benchmark)

A GPU/tile lowering path is exactly where a "10x faster than PyTorch" claim is
tempting and exactly where such a claim is easiest to make dishonestly. pcc's
north star (`AGENTS.md` obligation 2) is that **performance must be proven**:
C-like / device-speed claims require IR-shape evidence **plus** a runtime
benchmark on real hardware **plus** a semantics-preserving slow path. This slice
has none of the hardware half. So it deliberately measures only what it can
prove today — the *shape* of the lowering — and marks every resource metric as
`not-measured`.

This is a **measurement target**, not a benchmark result. It answers "does the
kernel IR lower to a deterministic, correctly-split, oracle-matching shape?" It
does **not** answer "how fast is it?" — that question is out of scope until a
real Metal (or other device) runtime path exists.

## 2. What it measures over `pcc.kernel_ir`

For a fixed set of kernel shapes — `vector-add`, `copy`, `fill`, `reduction`,
`gemm` — the harness builds a `KernelModule` via `pcc.kernel_ir.ir`, then:

| Metric | Source | What it proves |
|---|---|---|
| IR node counts (funcs / params / body ops / scalar / buffer) | `ir` module traversal | the built kernel IR has a deterministic, pinned shape |
| host/device split node counts | `target_split.resolve(self, metal)` + `tirx_adapter.lower_to_plain_tir` | the shared front-half is target-neutral; device-side = frozen plain-TIR ops; host-side = launcher stubs; device finalize is scheduled for `device=metal` |
| plain-TIR freeze success | `tirx_adapter.lower_to_plain_tir` marker | tile semantics froze into plain TIR (the `plain_tir_freeze` surface) |
| TVM-oracle golden match | `tvm_oracle.project_to_tir_shape` | the projection is stable/round-trippable and every func is a well-formed PrimFunc-shaped object |
| metal packaging descriptor | `metal_finalize.finalize_metal` | the `.metal -> .air -> .metallib` packaging plan + entry points are describable — WITHOUT producing any of them |

`host=self` is used deliberately for the split so the harness measures the
**first-class self-backend** split; `resolve` guarantees **no silent LLVM
fallback** (a resolution that would fall back raises).

## 3. Mode taxonomy + hard claim boundary

The harness has exactly three modes. The mode a metric was produced under is
part of the claim (`AGENTS.md` obligation 1: compatibility/measurement must be
mode-labeled).

```text
cpu-only          RUNS.
                  Measures logical compile-side metrics ONLY: IR node counts,
                  host/device-split node counts, plain-TIR freeze success,
                  TVM-oracle golden match. No wall-clock, no capacity, no
                  throughput, no TFLOPS. Every resource metric == not-measured.

metal-source-only RUNS iff metal_finalize reports a packaging descriptor;
                  otherwise SKIPPED_WITH_REASON.
                  Measures the emitted device-source DESCRIPTOR metadata +
                  packaging plan (library name, entry points, .metal/.air/
                  .metallib step plan). Produces NO .metallib, runs NO device
                  codegen, makes NO host-launch claim. When the Xcode Metal CLI
                  is absent (the norm on CI / in the pcc sandbox), the descriptor
                  metadata is still inspected but the status is
                  SKIPPED_WITH_REASON — "descriptor described" != "metallib
                  produced".

metal-runtime     ALWAYS SKIPPED_WITH_REASON.
                  No host launch, no GPU execution, no .metallib. This mode
                  exists only to make the absence explicit. launch-latency and
                  TFLOPS are reported as the literal placeholder not-measured.
```

### Hard claim boundary (never weakened)

```text
1. MEASUREMENT TARGET ONLY. This harness reports the LOGICAL IR shape produced
   by pcc.kernel_ir. It is NOT a benchmark result and NOT a collector/backend
   ranking.
2. NO SPEED CLAIM WITHOUT BOTH (a) IR-shape evidence AND (b) a real hardware
   run. This slice has (a) only. Therefore every launch-latency / TFLOPS /
   throughput value is the literal string "not-measured" — never a number,
   never an estimate, never a projection.
3. NO .metallib, NO device codegen, NO GPU launch is performed or claimed. The
   metal-source-only mode measures a DESCRIPTOR; the metal-runtime mode is
   always skipped.
4. MODE-LABELED. Each metric carries the mode it was produced under. A cpu-only
   node count is not a device measurement; a descriptor is not a compiled
   kernel; a skip is not a run.
5. INHERITS the kernel-IR boundary (pcc-kernel-ir.md §4): device IR never sees a
   GC-managed PyObject; --backend=self never silently falls back to LLVM. The
   harness resolves host=self and relies on that enforcement.
```

## 4. Module / class map

Package `tests/benchmarks/tile/` (imports `pcc.kernel_ir.*` only; does not touch
`pcc/__init__.py`):

| Symbol | Kind | Role |
|---|---|---|
| `harness.TileBenchMode` | Enum | `cpu-only` / `metal-source-only` / `metal-runtime` |
| `harness.RunStatus` | Enum | `RUN` / `SKIPPED_WITH_REASON` |
| `harness.KERNEL_SHAPES` | tuple | the five shapes |
| `harness.build_kernel(shape)` | fn | build + `validate_kernel` a shape's `KernelModule` |
| `harness.IrNodeCounts` | dataclass | funcs / params / body_ops / scalar / buffer counts |
| `harness.HostDeviceSplit` | dataclass | shared / host / device node counts + `runs_device_finalize` |
| `harness.ResourcePlaceholders` | dataclass | launch-latency / TFLOPS / throughput — all `not-measured` |
| `harness.CpuOnlyKernelReport` | dataclass | cpu-only per-shape result |
| `harness.MetalSourceOnlyReport` | dataclass | metal-source-only per-shape result |
| `harness.MetalRuntimeReport` | dataclass | metal-runtime per-shape result (always skip) |
| `harness.metal_toolchain_available()` | fn | xcrun + metal probe (mirrors `metal_finalize`) |
| `harness.run_cpu_only_bench(shape)` | fn | cpu-only runner (RUNS) |
| `harness.run_metal_source_only(shape)` | fn | metal-source-only runner (RUN or SKIP) |
| `harness.run_metal_runtime(shape)` | fn | metal-runtime runner (always SKIP) |
| `harness.run_all_modes(shape)` | fn | all three modes for one shape |
| `test_tile_bench.py` | pytest | the gate: real assertions (see §5) |

## 5. Test oracle

Gate command (main runs this; the authoring agent does not run pytest):

```bash
env -u LC_ALL uv run pytest tests/benchmarks/tile -q -n0
```

Each test asserts a *real* invariant, not a shape that merely looks similar:

- cpu-only produces **deterministic, pinned** IR node counts per shape (change a
  builder and the pinned count must change on purpose);
- the host/device split has the correct shared-front-half count, one launcher
  stub per func, device-side == frozen plain-TIR op count, and `device=metal`
  actually schedules the device finalize;
- the plain-TIR freeze carries the freeze marker;
- the TVM-oracle projection is stable/round-trippable and well-formed;
- metal-source-only RUNS-with-descriptor when the toolchain is present and
  SKIPS-with-reason when it is absent, and in **both** cases never sets
  `metallib_produced` / `host_launch_claimed` and never emits a TFLOPS number;
- metal-runtime is **always** `SKIPPED_WITH_REASON` and its reason names the
  `not-measured` placeholder;
- every resource metric across every mode is the literal `not-measured`.

## 6. What is out of scope for this slice (honest TODO)

- real device codegen / Metal source emission / `.air` / `.metallib` production;
- any GPU launch, torch/MPS interop, or wall-clock timing;
- any TFLOPS / latency / throughput number (these arrive only with a real device
  runtime path — the P4 phase of the kernel-IR research roadmap);
- kernel shapes beyond the fixed five (e.g. softmax / attention) — `build_kernel`
  raises `KeyError` on an unknown shape rather than silently accepting it.
```
