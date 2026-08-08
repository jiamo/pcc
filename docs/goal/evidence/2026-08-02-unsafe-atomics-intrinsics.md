# LIBC-P1-PRIMITIVES — ordering-explicit atomic intrinsics landed (atomics half)

Mode: host pcc frontend + compiled-stage scaffold tables; LLVM backend and
self backend (aarch64-darwin) lowering; macOS arm64. This closes the
"no atomic intrinsic / width picks the ordering" half measured in
[2026-08-01-atomics-mirror-gap-measured.md](2026-08-01-atomics-mirror-gap-measured.md).
It does NOT claim the syscall6 intrinsic, the freestanding module
discipline, i8 byte-flag lanes, x86_64-linux self lowering, or the port
re-pointing migration — those stay in the task row's open boundary.

## What landed

`pcc.unsafe` gained nine ordering-explicit intrinsics:

```text
atomic_load_i32/i64(ptr, offset, order)            -> int
atomic_store_i32/i64(ptr, offset, value, order)    -> None
atomic_rmw_i32/i64(op, ptr, offset, value, order)  -> int   (OLD value)
atomic_cas_i32/i64(ptr, offset, expected, desired,
                   order, fail_order)               -> int   (OLD value)
atomic_fence(order)
```

- Orderings are source-level string literals from
  `{relaxed, acquire, release, acq_rel, seq_cst}` mapped to LLVM
  (`relaxed -> monotonic`); rmw ops are literals from
  `{add, sub, and, or, xchg}`. rmw/cas return the OLD value (LLVM
  `atomicrmw`/`cmpxchg` semantics; `*_fetch` shapes are old-value plus
  arithmetic at the call site).
- Fail-closed at compile time: unknown ordering, ordering invalid for the
  operation (release-load, acquire-store, acq_rel/release cas-failure,
  relaxed fence), non-literal ordering, unknown rmw op, and (in the self
  backend) any operand width outside i32/i64.
- Frontend: `pcc/unsafe/__init__.py`, `pcc/py_frontend/type_infer.py`,
  `pcc/py_frontend/codegen/unsafe_lowering.py` (emits `load atomic`,
  `store atomic`, `atomicrmw`, `cmpxchg` + `extractvalue`, `fence`).
- Self backend: `self_backend_parse.py` parses the five atomic instruction
  forms (both raw compat text and LLVM-canonicalized text, optional
  `, align N`), `self_backend_analysis.py`/`self_backend_stackprep.py`
  register defs/uses/slot types (cmpxchg dest is the `{T, i1}` pair read
  by the existing extractvalue path), and
  `self_backend_aarch64_darwin_memory.py` emits `ldr/ldar`, `str/stlr`,
  `ldaxr/stlxr` retry loops (labels via the sanctioned `sanitize_label`),
  `clrex`, `cset`, and `dmb ish`. x86_64-linux self emission fails closed
  on the new kinds (unknown-instruction diagnostic).
- Own-encoder route (`PCC_SELF_OBJ=pcc`): `arm64_encode.py` gained
  `ldar/ldaxr/stlr/stlxr/clrex/dmb ish`, byte-differential against as(1).
- Compiled-stage closure: `ir_scaffold_lowering.py` builder-method tables
  gained `load_atomic` (`ptr`, 3 args) and `store_atomic` (`void`, 4 args)
  — without this the two calls fell back to `py_cpy_getattr/call` inside
  the compiled compiler (19 contextual fallbacks, caught by the
  fallback-baseline gate and fixed to 0).

## Proof

- `tests/python/test_unsafe_atomics.py` — **11 passed**:
  - identical 14-line output on `--backend llvm` and `--backend self`
    (store/load/rmw add/sub/and/or/xchg/cas hit/cas miss at both widths;
    values 41/42/42/100/100/100/100/4/13/-5/-8/-8/9/-1);
  - IR-shape assertions for every ordering and instruction form;
  - aarch64 asm-shape assertions (`ldar/stlr/ldaxr/stlxr/clrex/dmb ish`,
    relaxed stays plain `ldr`);
  - six fail-closed compile diagnostics + non-literal-ordering rejection.
- `tests/python/test_atomic_mirror_gap.py` — **5 passed** after being
  rewritten per its own instructions: premise inverted (the intrinsic
  surface is now pinned), C op-kind/ordering ratchets kept, the known
  remaining gap pinned to exactly the i8 byte-flag ops
  (`test_and_set`, `clear`), helper-mirror drift check kept until the
  ports are re-pointed.
- `tests/python/test_arm64_encode.py` — **4 passed** (as(1) byte
  differential including the six new mnemonics).
- `tests/python/test_py_multi_file_compile.py` +
  `test_py_multi_file_bootstrap_shim.py` — **133 passed**.
- `tests/c/test_llvm_capi_ir_parity.py` + `test_llvm_capi_end_to_end.py` +
  `tests/python/test_self_backend_unreachable_parse.py` — **27 passed**.
- `tests/python/test_fallback_baseline.py` +
  `test_ir_py_fallback_baseline.py` — **27 passed**. Closed-world multi
  compile stays at **0** fallbacks (`fallbacks_total` /
  `fallbacks_total_multi` unchanged); `unsafe_lowering` contextual count
  is back to **0**. Two standalone per-module probe counts were
  recaptured with a `_recapture_log` entry
  (`self_backend_parse` 649→686, `self_backend_aarch64_darwin_memory`
  177→466): standalone probes count single-module-resolution artifacts
  that scale with module size, not real closure fallbacks.
- Bootstrap: `scripts/bootstrap.sh --out-dir build/bootstrap-self
  --backend self` — stage1 rebuilt from current source
  (`PCC_BOOTSTRAP_STAGE_RESULT stage=1`), stage2/stage3 chain and the
  `tests/python/test_bootstrap_gate_baseline.py` self-backend params:
  see the addendum line below recorded when the chain finished.

## Addendum: self chain result

Recorded 2026-08-02 after the chain completed on current source:

- stage1 `PCC_BOOTSTRAP_STAGE_RESULT stage=1 elapsed_ms=15671`
- stage2 `PCC_BOOTSTRAP_STAGE_RESULT stage=2 elapsed_ms=394464`
- stage3 `PCC_BOOTSTRAP_STAGE_RESULT stage=3 elapsed_ms=81339`
- verify: "OK — pcc2 and pcc3 differ only by Mach-O code-signature /
  LC_UUID metadata. Metadata-normalized copies are byte-identical."
- `tests/python/test_bootstrap_gate_baseline.py` — **2 passed,
  2 deselected** (the two llvm-chain params deselect because
  `build/bootstrap-llvm` does not exist on this machine; that predates
  this change and is not a regression).

## Remaining boundary (stays open on the task row)

1. Linux x86_64 `syscall6` intrinsic (musl `syscall_arch.h` ABI; Darwin
   stays named-libSystem by policy).
2. Freestanding module discipline (no heap / no exceptions / no implicit
   boxing / no GC roots / no non-intrinsic calls).
3. i8 byte-flag atomic lanes (`test_and_set`/`clear` mirror).
4. x86_64-linux self-backend atomic lowering (currently fail-closed).
5. Re-pointing the pcc-Python ports off the seven width-ordered
   `pcc_py_atomic_*` helpers onto the intrinsics (5-GC gates required).
