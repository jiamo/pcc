# self-backend torture cluster: phi parallel-copy swap + smin/smax-against-zero peephole

## Status

resolved (2026-06-18)

Two distinct self-backend (aarch64-darwin) codegen bugs surfaced by the
`tests/c/test_gcc_torture_self.py` cluster. Both are confirmed fixed with
focused regressions in `tests/c/test_self_backend.py`. The wider torture-self
suite and the self-host bootstrap gate are the closing gates (see `## Report`).

## Problem Description

`tests/c/test_gcc_torture_self.py` had a cluster of ~40 failing cases under
`--backend self` while the same files passed under `--backend llvm` and native
`cc`. The cluster split into two unrelated root causes:

- **Bug A — fp-cmp family + many loop/compare cases** (`fp-cmp-1..6`,
  `pr57344-*`, `ssad-run`, `usad-run`, `920302-1`, `920501-*`, `pr28982a`,
  `pr45070`, `pr59643`, `20060420`): a NaN comparison program (`fp-cmp-1.c`)
  aborted (`rc=250`) even though each individual fcmp condition mapping in
  `self_backend_aarch64_darwin_ops.py::emit_fcmp_result` is correct.
- **Bug B — `pr78559.c`** (`rc=-6`, SIGABRT): a single distinct case where
  LLVM optimized `fn2` to `p1 << smin(p2, 0)`.

## Repro

```bash
env -u LC_ALL uv run pytest tests/c/test_gcc_torture_self.py \
  -k "fp_cmp or pr78559" -q -n0
```

Asm-level (no build) inspection used throughout:

```python
from pcc.backend.self_backend_dispatch import emit_self_asm
asm = emit_self_asm(open("/tmp/fpc.ll").read())   # IR from `pcc --emit-llvm=`
```

## Test [CONFIRMED]

Before the fixes: `fp-cmp-1` self=250, `pr78559` self=-6, llvm/native=0.
After the fixes: `fp-cmp/cmp` 76 passed, the rest of the cluster 40 passed,
`pr78559` 2 passed. Two new focused regressions pass
(`test_self_backend_phi_parallel_copy_swap_stages_through_temp`,
`test_self_backend_smin_against_zero_keeps_zero_register`).

## Proposals

## No.1 fp-cmp aborts — phi parallel-copy swap is lowered sequentially (lost-copy)

After PRE/GVN, `fp-cmp-1` threads two values through each merge block: a
dnan-carrier (`%.8,%.18,%.23,...`) and an x-carrier (`%.9,%.19,%.24,...`). At a
merge such as `ifend.1 -> ifend.2` the parallel copy is `%.24 <- %.19` and
`%.23 <- %.18`. Slot coalescing put the dnan source `%.18` in the *same
physical slot* (`x29-40`) that is the destination of the x-carrier `%.24`, so
the two copies form a **swap** (`-40:=-8`, `-8:=-40`).

`emit_phi_assignments` (`self_backend_aarch64_darwin_flow.py`) has a fast path
(`can_store_directly`) that emits copies **sequentially** with no temp,
guarded only by an SSA-**name** check (`match.value not in dest_names`). The
names differ here while the slots alias, so the fast path ran: the first store
(`-40 := -8`, i.e. dnan's slot := x) clobbered dnan before the dependent copy
(`-8 := -40`) read it. Both carriers collapsed to `1.0`, so a later
`dnan <= x` became `1.0 <= 1.0` → true → `abort()`. This is the classic
SSA-destruction lost-copy / phi-swap problem.

### Code Change

In `emit_phi_assignments`, extend the `can_store_directly` guard with a
slot-offset hazard check (`_has_slot_swap_hazard`): if any phi source occupies
the same physical slot offset as a *different* phi's destination (excluding the
coalesced self-copy), fall through to the already-correct temp-buffered path,
which stages every source into a scratch frame before writing any destination.

### CONFIRMED

`emit_self_asm` shows the `ifend.1->ifend.2` edge now stages both sources into
`sp+0`/`sp+8` before writing `-40`/`-8`, preserving dnan. `fp-cmp/cmp` 76
passed; the rest of the cluster (pr57344/ssad/usad/920302/920501/pr28982a/
pr45070/pr59643/20060420) 40 passed.

## No.2 pr78559 aborts — `movz wN,#0` dropped while still read by a min/max csel

LLVM lowered `fn2` to `p1 << smin(p2, 0)`. `emit_minmax_intrinsic_call`
correctly emits `movz w10,#0; cmp w9,w10; csel w11,w9,w10,le`. But the
in-emitter peephole `_fold_zero_compare_immediate`
(`self_backend_aarch64_darwin.py`) rewrote `movz w10,#0; cmp w9,w10` to
`cmp w9,#0` and **deleted the `movz` unconditionally**, even though the
following `csel` still reads `w10`. `w10` became undefined → `1 << garbage` →
wrong result → abort. Its sibling `_fold_mov_compare_source` already guards the
drop with `_can_drop_zero_mov_after_store`; the zero-compare fold was missing
that liveness check.

### Code Change

Guard `_fold_zero_compare_immediate` with
`_can_drop_zero_mov_after_store(lines, index + 2, zero_reg)` (and `lhs !=
zero_reg`), mirroring `_fold_mov_compare_source`. When the zeroed register is
read after the compare (the csel case), keep the `movz` and the
register-form compare.

### CONFIRMED

`smin(x,0)` now keeps `movz w10,#0`; `smin(x,7)` is unchanged. `pr78559` 2
passed.

## Report

- **Files changed:**
  - `pcc/backend/self_backend_aarch64_darwin_flow.py` — slot-swap hazard guard
    in `emit_phi_assignments`.
  - `pcc/backend/self_backend_aarch64_darwin.py` — liveness guard in
    `_fold_zero_compare_immediate`.
  - `tests/c/test_self_backend.py` — two focused regressions.
- **Why these are conservative:** Bug A only re-routes aliasing swaps to the
  existing temp path (non-aliasing edges keep the fast path); Bug B only keeps
  an instruction that was being unsoundly dropped. Both strictly add
  correctness; neither can turn previously-correct codegen wrong.
- **Closing gates:** full `tests/c/test_gcc_torture_self.py` (regression count)
  and the self-host bootstrap gate (self backend is the bootstrap backend).
