# LIBC-P1-PRIMITIVES — syscall6 owned end-to-end on x86_64-linux (syscall half)

Mode: host pcc frontend + llvm_capi text builder + self backend
(x86_64-linux emission); real execution proven inside the Linux x86_64
Docker harness against the clang-16 oracle. This closes open-boundary
item (1) of the task row ("Linux x86_64 syscall6 intrinsic"). It does
NOT claim the freestanding module discipline, i8 byte-flag lanes,
x86_64-linux self-backend *atomic* lowering, or the port re-pointing
migration — those stay in the row's open boundary.

## What landed

The frontend/type/parse wiring already existed in-tree (declaration in
`pcc/unsafe/__init__.py`, Darwin fail-closed policy + ptrtoint arg
coercion in `unsafe_lowering.py`, the pinned single inline-asm shape in
`pcc/llvm_capi/ir.py::syscall6`, shape-exact parsing in
`self_backend_parse.py` with i64/arity enforcement, defs/uses and i64
dest slots in `self_backend_analysis.py`/`self_backend_stackprep.py`).
What was missing was the actual machine emission and any proof.

- `pcc/backend/self_backend_x86_64_linux.py` now lowers the `syscall6`
  kind per musl `arch/x86_64/syscall_arch.h` `__syscall6`: the seven
  i64 operands materialize into `rax, rdi, rsi, rdx, r10, r8, r9`, one
  `syscall` is emitted, and raw `rax` spills to the dest slot. The
  kernel-clobbered `rcx`/`r11` are safe by construction — every value
  lives in a stack slot in this backend, so no live register state
  crosses the instruction.
- aarch64-darwin self emission keeps failing closed on the kind
  (unknown-instruction diagnostic), and the frontend keeps rejecting
  `pcc.unsafe.syscall6` for any non-linux-x86_64 target by policy.

## Proof

- Docker differential (real x86_64 Linux, clang-16 oracle):
  `tests/integration/test_self_backend_x86_64_linux.py::`
  `test_linux_x86_64_docker_syscall6_differential_clang_vs_self_backend`
  — **1 passed**. One IR module (SYS_write of a 16-byte string via
  `syscall6(1, 1, ptr, 16, 0, 0, 0)`, raw return truncated to the exit
  code) built with `cc` from the `.ll` (oracle) and from the
  self-backend `.s`; both print `pcc syscall6 ok` and exit 16.
- Host regressions: `tests/python/test_unsafe_syscall6.py` —
  **5 passed**:
  - the pinned inline-asm constraint string
    (`={rax},{rax},{rdi},{rsi},{rdx},{r10},{r8},{r9},~{rcx},~{r11},~{memory}`);
  - the x86_64 register-load sequence before `syscall` plus the rax
    spill after it;
  - aarch64 self backend fails closed on the parsed kind;
  - any other inline-asm shape fails closed in the parser;
  - frontend platform policy (raises "Linux x86_64 only" off-target,
    lowers to the asm shape on linux/x86_64).
- Neighboring gates stayed green in one `-n0` run — **29 passed**:
  `test_unsafe_syscall6.py`, `test_unsafe_atomics.py`,
  `test_atomic_mirror_gap.py`, `test_arm64_encode.py`,
  `test_self_backend_unreachable_parse.py`,
  `test_cli.py::test_backend_self_emit_asm_honors_x86_64_linux_target`.
- Commit-level gates: `test_bootstrap_gate_baseline.py` — **2 passed,
  2 deselected** (llvm-chain params deselect, `build/bootstrap-llvm`
  absent on this machine, pre-existing); `test_fallback_baseline.py` +
  `test_ir_py_fallback_baseline.py` — **27 passed** (closure stays at
  0 fallbacks; the frontend was not touched by this slice).
- The full five-GC self chain was last run green on 2026-08-02 (see
  [2026-08-02-unsafe-atomics-intrinsics.md](2026-08-02-unsafe-atomics-intrinsics.md));
  this slice's delta is x86_64-linux-only emission plus tests, which the
  Darwin chain never executes. A fresh chain rides with the next slice
  that touches Darwin-visible behavior (port re-pointing, item 5).

## Remaining boundary (stays open on the task row)

1. Freestanding module discipline (no heap / no exceptions / no
   implicit boxing / no GC roots / no non-intrinsic calls).
2. i8 byte-flag atomic lanes (`test_and_set`/`clear` mirror).
3. x86_64-linux self-backend atomic lowering (currently fail-closed).
4. Re-pointing the pcc-Python ports off the seven width-ordered
   `pcc_py_atomic_*` helpers onto the intrinsics (5-GC gates required).
