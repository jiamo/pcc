# Linux x86_64 Self Backend Plan

Related plans:

- `docs/plans/self-backend-translation-plan.md`
- `docs/plans/dual-llvm-backend-compat-plan.md`
- `docs/plans/all-pass-llvm-ir-1to1-master-plan.md`

## LLVM Source Anchors

The x64 backend must use the same source-anchored translation discipline as the
AArch64 track. The local LLVM checkout is currently sparse/incomplete on this
machine, so implementation tasks must first restore the referenced LLVM source
tree or use a checked upstream tag URL before claiming source-anchored work.

Required upstream anchors for Linux `x86_64` work:

- target-independent lowering / legalization:
  - `llvm/lib/CodeGen/TargetLoweringBase.cpp`
  - `llvm/lib/CodeGen/SelectionDAG/SelectionDAGISel.cpp`
  - `llvm/lib/CodeGen/SelectionDAG/LegalizeDAG.cpp`
  - `llvm/lib/CodeGen/SelectionDAG/LegalizeTypes.cpp`
- calling convention mechanics:
  - `llvm/lib/CodeGen/SelectionDAG/CallingConvLower.cpp`
- frame/register/PEI mechanics:
  - `llvm/lib/CodeGen/RegAllocFast.cpp`
  - `llvm/lib/CodeGen/PrologEpilogInserter.cpp`
- x86 target lowering and instruction rules:
  - `llvm/lib/Target/X86/X86ISelLowering.cpp`
  - `llvm/lib/Target/X86/X86InstrInfo.cpp`
  - `llvm/lib/Target/X86/X86FrameLowering.cpp`
  - `llvm/lib/Target/X86/X86RegisterInfo.cpp`
- SysV calling convention details:
  - `llvm/lib/Target/X86/X86CallingConv.td`
  - `llvm/lib/Target/X86/X86ISelLoweringCall.cpp`
- asm/MC emission:
  - `llvm/lib/Target/X86/X86AsmPrinter.cpp`
  - `llvm/lib/Target/X86/X86MCInstLower.cpp`
  - `llvm/lib/Target/X86/MCTargetDesc/X86MCAsmInfo.cpp`
  - `llvm/lib/Target/X86/MCTargetDesc/X86MCTargetDesc.cpp`

Policy:

- do not implement a new SysV ABI or instruction-lowering task from memory,
- every x64 backend task must name the upstream LLVM source file it used,
- if `pcc` implements a narrower subset, record the exact LLVM subsystem being
  narrowed and why.

## Scope

This plan tracks the Linux `x86_64` self backend as a second target for the
same self-backend contract used by the AArch64 Darwin path.

Target identity:

- `self-x86_64-linux-v0`

Current execution environment:

- Linux `amd64` Docker, driven from the macOS arm64 development host.
- MVP output is asm-first: self backend emits GAS/ELF-style assembly, then the
  host Linux toolchain assembles/links.

Non-goals for this phase:

- do not make Linux x86_64 the default backend before AArch64 Darwin promotion,
- do not start a raw ELF object writer,
- do not require cross-running Linux binaries directly on macOS,
- do not claim full LLVM parity from partial-support buckets.

## Status

Snapshot as of `2026-04-27`:

- target dispatch exists for `self-x86_64-linux-v0`,
- `amd64-pc-linux-gnu` is accepted as an alias-style Linux x64 target triple,
- local emitter/unit coverage is green:
  - `env -u LC_ALL uv run pytest tests/test_self_backend.py -k x86_64_linux -q -n0`
  - current result: `29 passed`,
- Docker infrastructure exists:
  - `docker/self-backend-linux-x86_64.Dockerfile`,
  - `scripts/run_self_backend_linux_x86_64_docker.sh`,
  - `tests/test_self_backend_x86_64_linux.py`,
- the c-testsuite Docker harness exists:
  - `scripts/run_self_backend_linux_x86_64_c_testsuite.py`,
  - modes currently include:
    - `llvm-native-exact`,
    - `self-unsupported`,
    - `self-partial`,
- the current Docker-gated x64 self path has already moved past pure smoke:
  - constant-return closure,
  - alias-triple closure,
  - direct-call plus integer-binop closure,
  - partial-support `c-testsuite` bucket through `128` cases, where supported
    cases must match native behavior and unsupported cases must fail explicitly.

Implemented target slice:

- scalar integer/pointer args and returns,
- local scalar `alloca`, `load`, `store`,
- pointer-valued SSA load/store,
- integer binops:
  - `add`, `sub`, `mul`,
  - `and`, `or`, `xor`,
  - `sdiv`, `srem`, `udiv`, `urem`,
- integer/pointer `icmp`,
- `br`, conditional branch, `switch`,
- phi assignment on CFG edges,
- `zext`, `sext`, `trunc`,
- `ptrtoint`, `inttoptr`,
- pointer-to-pointer `bitcast`,
- simple `getelementptr` on alloca/global/pointer-SSA bases,
- scalar/global/data emission:
  - scalar globals,
  - struct/array globals,
  - `zeroinitializer`,
  - string-literal byte arrays,
- direct calls,
- indirect calls through function pointers,
- partial FP support:
  - some `sitofp`,
  - some FP args/calls/returns,
  - some FP binop/fcmp/fneg paths,
- partial aggregate memory support:
  - local aggregate zero/store/load/memcpy-style movement.

Current boundary:

- Linux x86_64 is a real early target slice, not a stub.
- It is not promotion-ready.
- The current broad gate is still a partial-support gate, not a strict
  all-cases-success gate.
- It has not yet proven real workload closure comparable to AArch64 Darwin.

## Promotion Ladder

### X0. Keep The Docker Harness Reliable

Purpose:

- make Linux x64 progress reproducible from the macOS arm64 development host,
- avoid mixing cross-toolchain complexity into normal local macOS tests.

Required gates:

```bash
env -u LC_ALL uv run pytest -m integration tests/test_self_backend_x86_64_linux.py -q -n0
```

Useful direct probes:

```bash
scripts/run_self_backend_linux_x86_64_docker.sh \
  env -u LC_ALL uv run python scripts/run_self_backend_linux_x86_64_c_testsuite.py \
  --mode self-partial --bucket-size 128 --timeout 20
```

Exit criteria:

- Docker build/run is deterministic,
- no stale probe binaries or runaway processes,
- x64 harness failures are real backend failures, not environment drift.

### X1. Split Partial Support From Strict Success

Purpose:

- keep `self-partial` useful for frontier discovery,
- add at least one strict x64 self gate that cannot pass by treating
  unsupported cases as acceptable.

Tasks:

- add a `self-strict-exact` mode to
  `scripts/run_self_backend_linux_x86_64_c_testsuite.py`,
- start with a small exact-success allowlist or prefix bucket,
- assert every selected case compiles, links, runs, and exactly matches native,
- keep `self-partial` for wider frontier scanning.

Initial target:

- `32` strict exact-success cases.

Next targets:

- `64`,
- `128`,
- `256`,
- full `c-testsuite` exact-match subset when the bucket stops exposing major
  ABI holes.

Exit criteria:

- x64 has at least one strict broad-ish self gate,
- partial bucket and strict bucket are separate signals.

### X2. Complete The First SysV ABI Core

Purpose:

- move from "works for simple cases" to "calls across realistic C boundaries are
  predictable".

Tasks:

- GP integer/pointer args and returns remain locked,
- stack args beyond the six GP registers,
- caller/callee-saved register discipline,
- 16-byte call-site stack alignment,
- direct and indirect calls stay covered,
- scalar returns through `rax` / `rdx` where needed,
- floating args/returns through `xmm0..xmm7`,
- no red-zone dependency in MVP,
- always keep a frame pointer until the target is stable.

Known high-risk items:

- aggregate eightbyte classification,
- hidden sret,
- SysV varargs `%al` vector-count rules,
- Linux `va_list` layout.

Exit criteria:

- ABI microtests cover:
  - stack args,
  - FP args/returns,
  - indirect calls,
  - sret,
  - varargs smoke,
  - small aggregate arg/return.

### X3. Expand IR / Instruction Coverage By Frontier Evidence

Purpose:

- do not build a random opcode wishlist,
- close the actual IR shapes that block strict buckets and workloads.

Current backlog:

- broader FP casts and FP ops,
- richer `fcmp`,
- aggregate `insertvalue` / `extractvalue` combinations,
- aggregate `select` / phi / call-result movement,
- LLVM intrinsics that appear in optimized real code,
- vector-shaped IR that currently scalarizes poorly or not at all,
- more global initializer shapes.

Rule:

- every new lowering gets:
  - one focused local emitter test,
  - one Docker strict/partial bucket movement,
  - one real workload movement when applicable.

Exit criteria:

- x64 failures are bucketed by concrete missing LLVM IR shape rather than
  undifferentiated "unsupported backend" errors.

### X4. Bring Up The Real Workload Ladder

Purpose:

- move from corpus snippets to project-shaped evidence.

Order:

1. `zlib`
2. `lz4`
3. `zstd`
4. `pcre`
5. `openssl` smoke
6. `readline` smoke
7. `postgres` libpq slice

First workload target:

- Docker-native Linux x64 `zlib` system-link runtime under `--backend=self`.

Rules:

- keep workloads in Docker,
- use Linux native compiler output as the runtime oracle,
- do not require PIC/PIE in the first phase; continue with `-no-pie` until the
  backend supports position-independent code intentionally,
- shrink every workload failure into a focused local regression.

Exit criteria:

- at least `zlib` and `lz4` pass under Linux x64 self,
- one workload gate is strict enough to prove the self emitter was used.

### X5. Build An x64 Promotion Gate

Purpose:

- mirror the AArch64 promotion workflow without pretending the target is equally
  mature.

Proposed tiers:

- `quick`:
  - local x64 emitter tests,
  - Docker constant/direct-call smoke,
  - small strict c-testsuite bucket,
- `broad`:
  - strict c-testsuite bucket,
  - partial-support frontier bucket,
- `workloads`:
  - zlib/lz4/zstd/pcre as they land,
- `full`:
  - all strict buckets plus current workload ladder.

Implementation target:

- create a Linux x64 sibling to:
  - `scripts/run_self_backend_promotion_gate.py`

Exit criteria:

- one command produces a readable x64 promotion result,
- AArch64 and x64 progress can be compared by gate tier instead of anecdotes.

### X6. Promotion Criteria

Linux x64 can be called "supported experimental" when:

- local x64 emitter tests are green,
- Docker smoke gates are green,
- strict c-testsuite bucket is at least `128` cases and green,
- partial frontier bucket is wider than the strict bucket and has no unexpected
  failures,
- `zlib` and `lz4` pass as real workloads,
- unsupported cases fail explicitly, not by assembler/linker garbage.

Linux x64 can be considered for default-like use only after:

- strict broad gate is no longer a tiny bucket,
- real workload ladder reaches at least `zstd` and `pcre`,
- SysV aggregate ABI and varargs boundaries are intentionally covered,
- LLVM-vs-self mismatch buckets are small and understood,
- `self` can be selected through the same backend contract without x64-specific
  harness exceptions.

## Immediate Task Queue

Recommended next tasks, in order:

1. Add `self-strict-exact` mode to the Linux x64 c-testsuite harness.
2. Add a pytest gate for a `32-case` strict exact-success bucket.
3. Run the existing `128-case` partial bucket and record supported/unsupported
   counts in this plan.
4. Push strict bucket from `32` to `64`.
5. Add stack-arg SysV ABI microtests and implementation.
6. Add FP arg/return SysV ABI microtests and implementation.
7. Start Docker `zlib` self system-link runtime gate.
8. Convert first `zlib` blocker into a focused local x64 emitter regression.

## Commands

Local x64 emitter unit slice:

```bash
env -u LC_ALL uv run pytest tests/test_self_backend.py -k x86_64_linux -q -n0
```

Docker integration harness:

```bash
env -u LC_ALL uv run pytest -m integration tests/test_self_backend_x86_64_linux.py -q -n0
```

Current partial c-testsuite frontier:

```bash
scripts/run_self_backend_linux_x86_64_docker.sh \
  env -u LC_ALL uv run python scripts/run_self_backend_linux_x86_64_c_testsuite.py \
  --mode self-partial --bucket-size 128 --timeout 20
```

LLVM/native Docker baseline:

```bash
scripts/run_self_backend_linux_x86_64_docker.sh \
  env -u LC_ALL uv run python scripts/run_self_backend_linux_x86_64_c_testsuite.py \
  --mode llvm-native-exact --bucket-size 128 --timeout 20
```
