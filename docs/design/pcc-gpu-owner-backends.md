# First-class optional GPU owner backends

Status: accepted design contract, 2026-07-16.

This document defines how pcc can make Metal GPU execution as first-class as
the host `self` backend while also allowing a controlled TVM/TileLang execution
provider.  It replaces the accidental implication that TVM and TileLang must
remain oracle-only forever.  They are oracle-only in the currently implemented
path; an explicitly selected provider may become an **execution owner** after
it passes the gates below.

## Decision

pcc keeps two kinds of ownership separate:

- **Semantic ownership stays in pcc.**  Kernel IR, the TIRx/plain-TIR freeze,
  validation, packed-argument ABI, buffer/fence lifetime, diagnostics, and
  claim manifests remain pcc contracts.
- **Execution ownership belongs to the selected GPU backend.**  The selected
  backend must compile/package the frozen device program, launch it, wait or
  return a managed fence, and report the actual result.  It may not silently
  delegate to another backend.

This gives pcc control without requiring every implementation line to live in
pcc.  A pinned TVM/TileLang provider can own one selected execution while pcc
still owns what the program means and who owns its resources.

The first modes are:

| Mode | Semantic owner | Execution owner | Runtime dependency |
|---|---|---|---|
| `--gpu-backend=pcc-metal` | pcc Kernel IR/TIRx | pcc Metal finalizer and runtime | Apple Metal |
| `--gpu-backend=tvm-tilelang` | pcc Kernel IR/TIRx | pinned pcc-controlled TVM/TileLang provider | declared provider toolchain |

`auto` is not an owner mode.  Production claims and gates must name an exact
backend.  An unavailable requested backend fails closed with a diagnostic and
`SKIPPED_WITH_REASON` only in a gate that explicitly permits unavailable
hardware/tooling.

Host and device selection are orthogonal.  For example, a pcc1-produced host
launcher can be labeled:

```text
host_backend=self
gpu_backend=pcc-metal
python_libpython=off
```

or:

```text
host_backend=self
gpu_backend=tvm-tilelang
python_libpython=off
provider_process_links_libpython=<true-or-false>
```

The second label does not hide a provider-process dependency merely because
the pcc1 launcher itself does not link libpython.

## What "first-class owner" requires

A GPU backend is an execution owner only when all of these are true:

1. The user selected it explicitly and the result records the same backend as
   the actual backend.
2. It consumes validated pcc Kernel IR or its canonical frozen plain-TIR form;
   it does not execute arbitrary importer Python as a substitute for lowering.
3. It produces the device artifact or runtime module used by the launch.
4. It launches the device work through the common packed-argument contract and
   returns a pcc-managed completion/fence result.
5. It never falls back to `pcc-metal`, TVM, LLVM, CPU, or an interpreter unless
   the user starts a separately labeled compilation with that backend.
6. It emits backend-specific diagnostics and a manifest with versioned,
   inspectable inputs, passes, artifacts, and dependencies.
7. A real device result matches the CPU oracle; higher claims additionally pass
   the pcc1 and five-GC gates.

Source parsing, shape projection, source emission, or a fake/injected launch is
not execution ownership.

## Common driver boundary

The implementation must introduce one narrow `GpuBackendDriver`-shaped
boundary.  Exact Python names may change during implementation, but the
operations and ownership rules may not:

```text
capabilities(target) -> declared feature set
validate(kernel_or_frozen_module, target, schedule=None) -> accepted or fail-closed diagnostic
compile(kernel_or_frozen_module, target, pipeline, schedule=None) -> content-addressed artifacts
package(artifacts, launch_plan) -> provider-neutral package record
launch(package, PccPackedArgs, stream) -> PccFenceToken/result
synchronize(fence) -> completed device result
destroy(package/resources) -> fence-safe release
```

The driver accepts no `PyObject` device arguments.  `PccBufferHandle`,
`PccPackedArgs`, and `PccFenceToken` remain the cross-backend ABI.  A provider
may borrow native handles for a launch; ownership transfer requires an explicit
adapter record and must preserve one-shot/deferred-release rules.

An optional `KernelSchedule` is accepted only with semantic Kernel IR and is
applied through the shared PCC schedule module before plain-TIR freeze. A
schedule binds to the exact input Kernel IR digest, target, selector, and
expected old state. It cannot be applied to already frozen IR, and a rejection
never retries without the schedule or through another owner. The schedule
digest and replay trace are owner-neutral provenance; the produced device
source and runtime remain owned by the selected backend.

Every result manifest records at least:

```text
requested_gpu_backend
actual_gpu_backend
semantic_ir_owner
codegen_owner
runtime_owner
target_triple/device
provider version and build identity
canonical frozen-IR hash
ordered pass-pipeline identity
schedule plan hash when scheduled
artifact hashes
fallback_used=false
launcher links_libpython
provider process links_libpython
claim level and gate result
```

A mismatch between requested and actual backend is an error, never a warning.

## pcc-controlled TVM/TileLang provider

`tvm-tilelang` means a provider pcc can audit and reproduce, not an ambient
`pip install` chosen from the host environment.

- Pin the source/version/build identity and supported target set.
- Keep an explicit allowlisted pass pipeline.  No ambient plugins, arbitrary
  Python callbacks, or import-time registration may silently change it.
- Enter through pcc's strict TileLang source importer or canonical frozen IR.
  Runtime `import tilelang` remains the separate
  `tilelang-package-cpython-compat` claim.
- Hash canonical IR before the provider, and re-import/validate the provider
  output or artifact metadata at the boundary pcc can represent.
- Record every provider pass and diagnostic.  Unsupported CUDA-only or
  non-representable semantics fail closed rather than falling back to the pcc
  Metal path.
- Start with an out-of-process provider so its Python/libpython and native
  dependencies are visible and cannot contaminate the pcc1 no-libpython link.
  In-process or self-hosted provider work is a later, separately gated claim.

The provider may therefore be the **actual execution owner** for its explicit
mode, while pcc remains the semantic, ABI, lifetime, and policy owner.

## Claim labels

These labels are intentionally non-interchangeable:

- `pcc-tilelang-source-subset`: strict source parsing into Kernel IR; no
  upstream runtime execution.
- `tilelang-package-cpython-compat`: runtime package import through the
  compatibility path.
- `gpu-owner=pcc-metal`: the pcc Metal driver compiled and executed the work.
- `gpu-owner=tvm-tilelang`: the pinned provider compiled and executed the work;
  legal only after the execution-owner gates pass.

Unqualified claims such as "pcc supports TileLang/TVM" remain forbidden.  A
claim must name the source/import mode, actual GPU owner, target, device-result
level, pcc1 status, and GC coverage.

## Delivery slices and gates

### O1 — pcc Metal owner driver

Refactor the existing Kernel IR -> Metal finalize/package/launch path behind
the common driver without changing semantics.  The focused gates must prove:

- explicit selection records requested = actual = `pcc-metal`;
- unsupported input fails closed and cannot reach a legacy direct lowering;
- no TVM/TileLang/LLVM/CPU fallback occurs;
- one copy and one GEMM reach `GPU_LEVEL_4_DEVICE_RESULT`;
- the same launcher reaches Level 5 and Level 6 before `DONE_STRONG`.

### O2 — TVM/TileLang compile owner

Add the pinned out-of-process provider, canonical IR interchange, explicit
pass allowlist, artifact hashes, dependency manifest, and negative tests for
ambient plugins, unsupported passes, CUDA-only constructs, and backend
mismatch.  Compile/artifact proof is not yet a device execution claim.

### O3 — TVM/TileLang execution owner

Launch provider-produced Metal work through the common packed-argument and
fence contracts.  A copy and GEMM must match the same CPU oracle and the
`pcc-metal` result.  Tests must force the requested provider to be unavailable
and incompatible and prove there is no fallback.

### O4 — pcc1 and five-GC parity

Run the same provider-selected workload from a pcc1 no-libpython launcher and
under GC0..4.  Record provider-process dependencies separately.  Only this
slice permits the Level-5/Level-6 claims for `gpu-owner=tvm-tilelang`.

## Non-goals of this contract

- It does not make ordinary runtime `import tilelang` pcc-native.
- It does not claim arbitrary TileLang Python, arbitrary TVM passes, CUDA,
  ROCm, training, or whole-program GPU support.
- It does not let TVM objects replace pcc Kernel IR as the durable semantic
  contract.
- It does not make the provider part of `backend=self`; host and device owners
  remain independently labeled.
