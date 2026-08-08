# LIBC-P1-PRIMITIVES — x86_64-linux self-backend atomic lowering landed

Mode: self backend x86_64-linux emission; real execution proven inside
the Linux x86_64 Docker harness against the clang-16 oracle. This
closes open-boundary item "x86_64-linux self-backend atomic lowering
(currently fail-closed)". It does NOT claim the freestanding module
discipline, i8 byte-flag lanes, or the port re-pointing migration.

## What landed

`pcc/backend/self_backend_x86_64_linux.py` now translates the five
atomic instruction kinds the shared parser produces, using the
standard x86-TSO mapping (the same shapes clang emits):

- `load atomic` (monotonic/acquire/seq_cst) -> plain `mov`; every
  aligned x86 load is already an acquire load, and seq_cst loads need
  no fence because seq_cst stores lower to the locked `xchg`.
- `store atomic` monotonic/release -> `mov`; seq_cst -> implicitly
  locked `xchg`.
- `atomicrmw` add/sub -> `lock xadd` (sub via `neg`); xchg -> `xchg`;
  and/or -> a `lock cmpxchg` retry loop (commutative `val OP old` keeps
  only rax/r10/r11 live; a miss hardware-reloads rax). Old value is
  returned in all cases (LLVM semantics).
- `cmpxchg` -> `lock cmpxchg` + `sete`, old value stored at pair-slot
  offset 0 and the success flag at `aggregate_member_info(pair, (1,))`,
  matching the existing extractvalue reader.
- `fence` seq_cst -> `mfence`; acquire/release/acq_rel emit nothing
  (x86-TSO provides them; the stack-slot backend never reorders).

Fail-closed edges: operand widths outside i32/i64 raise
`BackendUnavailable` even when the result is unused (the width check
runs before dead-value elision); rmw ops outside add/sub/and/or/xchg
are already rejected by the shared parser.

## Proof

- Docker differential (real x86_64 Linux, clang-16 oracle):
  `tests/integration/test_self_backend_x86_64_linux.py::`
  `test_linux_x86_64_docker_atomics_differential_clang_vs_self_backend`
  — **1 passed**. One IR module with 18 checked steps (both widths:
  store/load at each ordering, rmw add/sub/and/or/xchg old-value
  checks, cmpxchg hit and miss including the extracted success flag,
  seq_cst store visibility, fences) exits 0 through both `cc x.ll`
  (oracle) and `cc x.s` (self backend); a nonzero exit would name the
  first diverging step. Generator shared at
  `tests/python/x86_64_atomics_ir_gen.py`.
- Host regressions: `tests/python/test_unsafe_atomics_x86_64.py` —
  **3 passed**: pinned asm shapes (`lock xadd` both widths,
  `lock cmpxchg` both widths, `xchg` for rmw-xchg and seq_cst store,
  `neg` for sub, `sete` for the cas flag, retry label + `jne`, exactly
  one `mfence` for a seq_cst+acquire fence pair), parser fail-closed on
  a non-set rmw op, width fail-closed on an unused i16 atomic load.
- Combined focused gates — **32 passed** in one `-n0` run:
  `test_unsafe_atomics_x86_64.py`, `test_unsafe_syscall6.py`,
  `test_unsafe_atomics.py`, `test_atomic_mirror_gap.py`,
  `test_arm64_encode.py`, `test_self_backend_unreachable_parse.py`,
  `test_cli.py::test_backend_self_emit_asm_honors_x86_64_linux_target`.
- Commit-level gates — **29 passed, 2 deselected**:
  `test_fallback_baseline.py` + `test_ir_py_fallback_baseline.py`
  (per-module counts unchanged despite the emitter's new imports) +
  `test_bootstrap_gate_baseline.py` (llvm-chain params deselect,
  `build/bootstrap-llvm` absent, pre-existing).
- Five-GC chain status unchanged from
  [2026-08-02-syscall6-x86-64-linux-owned.md](2026-08-02-syscall6-x86-64-linux-owned.md):
  the delta is x86_64-linux-only emission the Darwin chain never
  executes; a fresh chain rides with the port re-pointing slice.

## Remaining boundary (stays open on the task row)

1. Freestanding module discipline (no heap / no exceptions / no
   implicit boxing / no GC roots / no non-intrinsic calls).
2. i8 byte-flag atomic lanes (`test_and_set`/`clear` mirror) — on
   x86_64 these can now reuse the `xchg`/`mov` byte forms once the
   frontend gains the i8 intrinsics.
3. Re-pointing the pcc-Python ports off the seven width-ordered
   `pcc_py_atomic_*` helpers onto the intrinsics (5-GC gates required).
