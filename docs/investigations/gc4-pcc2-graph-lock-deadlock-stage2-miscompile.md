# Investigation: pcc2 under GC4 deadlocks on the minor graph lock — stage2-only, pcc1 is fine (suspected pcc1 codegen miscompile)

## Status
resolved — root cause CONFIRMED (self-host-only export-descriptor
degradation: `-> None` encodes as `("dyn",)` under pcc1, and the
plain-function extern paths ignored the schema `returns_none` bool that
survives the round trip). Fix landed in
`native_modules._extern_user_function_return_ir_type` +
`extern_func_info_lowering._extern_info_to_funcdef`; focused regression
green (`tests/python/test_extern_returns_none_abi.py`, 4 passed).
`tests/python/gc/test_pcc_bootstrap_full_gc4.py` passed twice on 2026-08-07:
once at the fix state (1 passed, 25:41 cold) and once at the final tree
state including the unrelated fallback-surface fixes (1 passed, 15:21).
The 4-line smoke that previously hung forever compiles in 0.56s under
PCC_GC_BACKEND=4. Evidence:
docs/goal/evidence/2026-08-07-gc4-graph-lock-deadlock-returns-none-fix.md.

## Problem Description
The five-GC bootstrap gate for backend 4 never completes: stage2 (pcc1 -> pcc2)
compiles fine (~66s warm), but the post-stage smoke compile — `pcc2` compiling
a 4-line `def main(): return 0` file under `PCC_GC_BACKEND=4` — hangs forever.
Four retries on 2026-08-06 (20:38/21:55/22:23/23:07) all died there, each
leaving a `stage-smoke.*` dir in `build/bootstrap-pytest-self-gc4/`. The
earlier "fix" of raising `_BOOTSTRAP_STAGE_TIMEOUT_S` from 900 to 2400 only
masked this. GC0/1/2 pass; GC3 passes but its stage3 is ~4.8x slower than
GC1/2 (separate issue, see gc-frame-index-entry-pool-perf.md Update
2026-08-07).

## Repro
```bash
printf 'def main() -> int:\n    return 0\n\nmain()\n' > /tmp/smoke.py
PCC_GC_BACKEND=4 ./build/bootstrap-pytest-self-gc4/pcc2 \
  --ir-scaffold=on --backend self --python-libpython off /tmp/smoke.py -o /tmp/smoke_bin
# hangs within ~2-8s at 100% CPU; same binary under PCC_GC_BACKEND=0 finishes in 0.4s.
# CRITICAL CONTROL: pcc1 (host-python-built, same source, same runtime archive)
# compiles the same file under PCC_GC_BACKEND=4 in 2.6s. Only pcc1-compiled
# pcc2 hangs => stage1-vs-stage2 codegen divergence (pcc1 miscompiles something).
```

## Test [CONFIRMED]
Observed repeatedly under the command above; hang stack (native, lldb attach):
```
pcc_thread_safepoint <- pcc_py_gc_minor_graph_lock(+spin) <-
user_py_gc_backend__object_graph_lock <- pcc_gc_note_object_freeing <-
py_decref <- pcc_gc_release <- ...hoist_nested_funcdefs <- generate ... main
```
Full 17-frame stack contains NO ancestor holding the graph lock.
Gate: `tests/python/gc/test_pcc_bootstrap_full_gc4.py` (currently red/hanging).

## Evidence chain (all instruction-level, each eliminating one hypothesis)
- Deadlock state: `g_pcc_py_gc_minor_graph_lock == 1` while this thread's TLS
  `g_tls_pcc_py_gc_minor_graph_lock_depth == 0` — "locked but nobody holds it".
- Single-threaded for the whole run (thread-count polling + no pthread_create).
- `pcc_py_gc_minor_graph_lock/unlock` disassembly in pcc2 is correct
  (TLV thunk usage, ldaxr/stlxr CAS, release stlr in a split block after ret)
  and is instruction-identical to pcc1's copy.
- TLV descriptors: dyld converted classic 24B layout to compact u32-offset
  form correctly at load (offsets 0/4/8/16, no aliasing); `_tlv_get_addr`
  fast path preserves registers per the special ABI.
- `pcc_thread_safepoint` is the no-thread kernel no-op (`ret`) — the spin does
  no GC work.
- No signal handlers; no CMS/relocation worker threads under GC4.
- Only lock/unlock reference the lock word and the TLS depth (whole-binary
  adrp+add scan); no duplicate symbol/implementation; neighbors distinct.
- Clearing the word via lldb resumes progress; it re-hangs every <=6s —
  systematic, deterministic (identical counters across runs).
- Debug-instrumented rebuilds (PCC-DEBUG-GRAPHLOCK counters in
  freestanding_runtime_high_substrate.py, temporary) at hang:
  acq=375884, rel=375883, fast=27535, mirror=0, unlock_noop=0, tls_drift=0,
  split=0 (unlock local-vs-stored depth never diverged), TLS depth=0.
  => exactly one CAS acquire never released; every decrement-to-zero unlock
  DID run its release branch; the depth returned to 0; no early-return unlock
  fired while the mirror said "held". The combination is impossible for the
  verified machine code of lock/unlock alone => some pcc1-generated CALLER
  code corrupts the protocol exactly once per ~400k ops (deterministic input
  pattern), consistent with a stage2 miscompile of a caller, not of
  lock/unlock themselves.
- lldb-from-start perturbs addresses (ASLR off) and moves/hides the event;
  attach-based polling cannot hit the ~ms window before acquire #375884.

## Proposals
- No.1 Gate-stall capture: debug gate (g_dbg_gl_gate_at/g_dbg_gl_gate) stalls
  the process at acquire #~375600; attach, arm watchpoints on the lock word
  and TLS depth (filtered to non-lock/unlock writers), release the gate, log
  the last ~300 acquire/release/depth writes with stacks.        [pending]
- No.2 Diff pcc1-vs-pcc2 codegen for the caller family on the hot path
  (note_object_freeing / py_decref / pcc_gc_release / backend4 zpage +
  forwarding paths) once No.1 names the write site.               [pending]

## Notes
- pcc0-built pcc1 vs pcc1-built pcc2 differ in 4021 functions after
  normalization (legal for the fixed point, which binds stage2+ only:
  pcc2==pcc3 held on 2026-08-06 for gc0..3), so binary diffing alone cannot
  isolate the miscompile; the gate capture is the discriminator.
- All PCC-DEBUG-GRAPHLOCK instrumentation must be removed from
  freestanding_runtime_high_substrate.py before any completion claim.

## Update (2026-08-07): fatal acquire localized to pcc_gc_store_root's GC4 critical section; NOT a stage2 miscompile of lock/unlock; WARNING — instrumentation was swept into commit 27b290cb

Session results (full forensic log in the session scratchpad review dir):

- Gate technique landed: a debug gate in the instrumented substrate stalls the
  process at acquire #375600 (fatal is #375884, deterministic natively across
  3 runs), enabling attach + exact-ordinal breakpoints.
- The fatal (never-released) acquire is taken by `pcc_gc_store_root`
  (py_obj.py:519) called from hoist_nested_funcdefs during codegen, on the
  backend-4 path. After letting that store_root call run to completion under
  the debugger: TLS depth == 1, rel unbumped, word == 1 — i.e. **a callee
  inside the critical section performed one nested (fast-path) lock without a
  matching unlock**; store_root's own final unlock then decremented 2 -> 1 and
  never released. store_root's compiled code is instruction-identical between
  pcc1 and pcc2 (125 instrs, normalized diff empty), so this is NOT a
  stage1-vs-stage2 miscompile of that function; the pcc1-fine/pcc2-hangs
  asymmetry is address-pattern-dependent triggering (which binary's allocation
  addresses exercise the leaking branch).
- Sources audited clean (balanced) so far: pcc_gc_store_root,
  pcc_py_gc_minor_graph_lock/unlock (asm level, both binaries),
  pcc_gc_note_relocation_read + _note_relocation_read_unlocked,
  pcc_gc_note_slot_write_barrier (null-owner branch), pcc_gc_note_store,
  note_frame_leave/enter family, plus a whole-tree static scan for
  return-while-locked shapes (no hits). The leaker is deeper in the
  critical-section call tree (py_decref dealloc cascade under GC4 — zpage
  free / forwarding retirement paths are the prime remaining suspects) or a
  conditional lock/unlock skew a linear scan cannot see.
- Debugger-perturbation caveat recorded: under lldb-from-start (ASLR off) the
  ordinal moves; attach-at-gate keeps it stable; heredoc-fed lldb does NOT
  wait at breakpoints (batch `-o` mode does) — several early null results were
  tooling artifacts, not evidence.

**WARNING / follow-up for the tree owner:** commit 27b290cb (2026-08-07 02:20,
"pcc_gui: pcc-Python GUI core + Metal render surface (B)") swept in the
TEMPORARY instrumented freestanding_runtime_high_substrate.py, including a
baked-in debug gate that makes ANY gc3/gc4 process freeze forever at graph-lock
acquire #375600. The working tree now holds the cleaned substrate (plus the
index-table Fibonacci hash fix); committing the current worktree version of
that file removes the hazard.

Next step for the remaining leak: rebuild the gated substrate (backup copy in
the session scratchpad: substrate_instrumented_backup.py), stop at the fatal
acquire via the gate + ignore-count recipe above, then `thread step-inst-over`
through store_root's critical section reading g_dbg_gl_mirror after each call
to bisect the leaking callee (a run of this shape produced the mirror trace
successfully; one clean repetition on the fatal branch names the function).

## Update (2026-08-07, later): mechanism fully identified — cross-module `-> None` return-type mismatch feeds a garbage pointer into py_incref, which increments the graph-lock TLS depth

Root mechanism (proven end-to-end with a crash trap, not inferred):

1. `hoist_lowering.__HoistLoweringPass__hoist_nested_funcdefs` calls
   `hoist_analysis.write_hoist_profile(...)`, whose source signature is
   `-> None`. In the bootstrap (multi-module) build the CALLEE is emitted as
   `define void` (no value in x0 at `ret`), but the CALLER's module declares it
   as returning a pointer and consumes x0:
   `bl write_hoist_profile ; stur x0, [fp-0x5d8]`.
2. The `return hoisted` that follows roots that stack slot:
   `pcc_gc_store_root(&slot, x0_garbage)` → `py_incref(garbage)`.
3. The garbage is deterministic: the callee's last act is
   `pcc_gc_frame_leave` → `pcc_gc_note_frame_leave` → `pcc_py_gc_minor_graph_lock`
   → dyld `_tlv_get_addr`, which returns **the thread's TLS block address in x0**.
   The TLS block base is exactly `g_tls_pcc_py_gc_minor_graph_lock_depth`.
4. `py_incref` accepts it (`_ptr_can_have_header` passes; tag/flags read from
   adjacent TLS words land in the valid window) and calls
   `pcc_refcount_incref(o)` = `store_i64(o, 0, load_i64(o,0)+1)` — i.e. it
   **increments the graph-lock depth from 1 to 2 without any lock call**.
5. `pcc_gc_store_root`'s own unlock then decrements 2 → 1 and skips the
   release store, so `g_pcc_py_gc_minor_graph_lock` stays 1 forever with no
   owner. The next acquirer spins in `pcc_thread_safepoint` (a no-op in the
   single-thread kernel) → permanent 100% CPU hang.

Evidence: crash trap in `py_incref` comparing `o` against the TLS base fired
with the stack `py_incref <- pcc_gc_store_root <- hoist_nested_funcdefs`; an
8192-entry lock-event ring showed the last events as `201(acquire,d=1),
102(fast,d=2), 401(unlock,d=1), 800(barrier), 900(incref)` with **no** ring
event for the 1→2 increment that the deadlock state requires — i.e. the
increment came from outside lock/unlock, matching (4). Callee/caller
disassembly in both `pcc1` and every `pcc2` variant confirms void-definition
vs pointer-consumption.

Fix attempt #1 (landed, real hardening, but NOT sufficient):
`native_modules._extern_user_function_return_ir_type` matched None returns with
a bare `isinstance(ret_ty, NoneType)`. Under pcc1 self-host the decoded
NoneType can come from a separately compiled copy of the types module, so
identity matching fails for a semantically identical None — the same boundary
`user_function_decl_lowering._is_none_return` already documents and guards by
stable type NAME. Added the name-based fallback there. Verified by rebuilding
stage1 from host and then pcc2 with the fixed pcc1: **GC4 smoke still hangs**,
and the caller still consumes x0, so this declaration path is not the one that
produced the mismatch (keep the fix: identity-only matching is wrong regardless).

Negative controls (both clean, so the trigger needs the real shape):
- single-module void-call + return-local: IR correct (`call void`, return value
  re-loaded from the root slot).
- two-module `from mod_a import sink` via `scripts/pcc_multi.py`: caller emits
  `bl sink; bl py_err_occurred` with no x0 consumption; runs fine on GC0+GC4.
  The real case uses a package-relative import (`from .hoist_analysis import
  (...)`) inside a large module — that form is the next thing to bisect.

Next steps (in order):
1. Find the declaration path that wins for the relative-import form: instrument
   every `user_<mod>_<fn>` declarer (`native_modules._bind_native_cross_module_export`,
   `native_modules._declare_extern_user_function`, `user_function_decl_lowering`)
   to log symbol + return IR type, then compile pcc/py_frontend/codegen/
   hoist_lowering.py in a bootstrap-shaped multi-module run and read the log.
   Prime suspect: the `existing = self.module.globals.get(sym)` reuse branch —
   whichever path declares the symbol FIRST wins silently, so a ptr-returning
   declaration emitted before the void-typed one is never corrected.
2. Make the mismatch impossible instead of merely unlikely: when a declaration
   for `sym` already exists with a different signature, fail closed
   (PCC-PY-COMPILE-001) rather than reusing it.
3. Independent runtime hardening (defense in depth, worth doing anyway): make
   `py_incref`/`py_decref` reject pointers that are not inside a GC-owned
   region, so a stray pointer can never corrupt a lock word. Today only
   `_ptr_can_have_header` + a tag-window heuristic stand between a garbage
   pointer and arbitrary memory corruption.

Instrumentation status: all PCC-DEBUG-GRAPHLOCK code has been removed from the
working tree (`py_obj.py` / `py_gc_backend.py` restored from HEAD, substrate
rewritten clean). The earlier warning about commit 27b290cb still applies:
HEAD's substrate is the instrumented one and must be replaced by the current
worktree version.

## Update (2026-08-07, session 3): HEAD freeze warning corrected; fresh host stage1 from the current worktree is CLEAN at the deadlock call site — the "fix #1 insufficient" verdict was itself unsound (stale-cache or disasm misread)

Correction to the 27b290cb warning above: verified against HEAD directly
(`git grep` for the gate constant and `PCC-DEBUG` across `HEAD -- pcc/`, plus a
full read of HEAD's lock/unlock functions), **HEAD contains only the
counter-version instrumentation — no debug gate, no acquire-#375600 freeze**.
The gated substrate variant only ever existed in the working tree during the
forensic session (backup: session scratchpad `substrate_instrumented_backup.py`)
and was never committed. HEAD's committed instrumentation is harmless-but-noisy
debug counters (racy global i64 bumps, "remove before commit" markers, minor
overhead); committing the cleaned worktree file still removes it, but nothing
in HEAD freezes gc3/gc4.

Today's evidence chain (current worktree = index-table Fibonacci hash fix +
`_extern_user_function_return_ir_type` NoneType-by-name fix + clean substrate):

1. Minimal repro of the suspected trigger shape — package-relative import
   (`from .mod_a import (_NS, other, sink)`), multi-name import list,
   `-> None` callee with an unannotated param, compiled via
   `scripts/pcc_multi.py --backend self --python-libpython off --emit-llvm`,
   both default-jobs and `PCC_PY_FRONTEND_JOBS=2` — IR **clean**:
   `declare external void @user_pkg_mod_a_sink` + `call void`, no x0
   consumption. The import form alone is not the trigger.
2. Full stage1-shaped host builds of `pcc/__main__.py` (`--backend self
   --python-libpython off`), twice: once with `PCC_PY_FRONTEND_JOBS=1`, once
   with the exact gate env (`PCC_RUNTIME_CC=pcc PCC_RUNTIME_HIGH=py
   PCC_PYTHON_IR_PASSES=off`, jobs=auto multi-worker). In BOTH binaries the
   two `write_hoist_profile` call sites inside `hoist_nested_funcdefs`
   disassemble to `bl write_hoist_profile ; bl py_err_occurred ;
   stur x0,[fp-0x2e0] ; ldur ; cmp #0 ; cset ; branch` — the standard
   post-call **err-flag spill + test**, not return-value consumption.
   **Reading only `bl` + `stur x0` makes the benign err-check look like the
   miscompile — that two-instruction window misread is exactly how this
   session initially re-"confirmed" the bug.** Full-window disasm is
   mandatory before claiming x0 consumption.
3. Combined `--emit-llvm` IR of the same stage1-shaped compile: the symbol has
   exactly one `declare external void`, two `call void` sites, one
   `define external void`, and a correct boxing adapter. No ptr-returning
   declaration exists anywhere in the closure.
4. Implication: the earlier "fix attempt #1 landed, rebuilt stage1 + pcc2,
   GC4 still hangs, caller still consumes x0" verification is unsound — it
   was either performed against stale cache layers (shared-stage1 pcc1 /
   stage2_3 cache / IR-keyed object cache; see
   reference_bootstrap_gate_cache_layers) or the same two-instruction disasm
   misread. The mechanism chain (garbage ptr -> py_incref on the TLS lock
   word) remains proven for the OLD artifacts; what is retracted is the claim
   that the CURRENT worktree still produces the mismatch.

In flight: fresh `pcc1 -> pcc2` rebuild from the current worktree, then the
4-line GC4 smoke (the deterministic hang repro), then
`tests/python/gc/test_pcc_bootstrap_full_gc4.py` as the formal gate.

## Update (2026-08-07, session 3 continued): root cause CONFIRMED — self-host-only `-> None` descriptor degradation to ("dyn",); fix = consume the schema `returns_none` bool in the plain-function extern paths

Fresh-chain verification first (current worktree, before today's fix): the
fresh host-built pcc1 (clean at the call site, verified by disasm above)
compiled a fresh pcc2, and that pcc2 **still hung** the 4-line GC4 smoke
(gtimeout 60s, 100% CPU, rc=124). So the earlier "fix #1 insufficient, still
hangs" verdict was correct after all — what was wrong was only the
HEAD-freeze overclaim and this session's initial stale-cache suspicion.

The decisive split: pcc2's `hoist_nested_funcdefs` disassembles to
`bl write_hoist_profile ; stur x0,[fp-0x5d8]` (immediate x0 consumption, no
err-check) — byte-matching the original forensics — while pcc1's same call
site is the benign void+err-check shape. **The mismatch is created only when
the frontend RUNS UNDER pcc1** (stage2 codegen), never under host CPython.
That is why every host-side probe in this file (minimal repros, emit-llvm IR,
stage1 disasm) was clean: the bug is unreachable from host runs.

Root cause: when pcc1 scans sibling exports, `encode_type`'s isinstance chain
sees foreign node identity and falls through to the `("dyn",)` catch-all for
the `-> None` return annotation — exactly the boundary
`pipeline._export_returns_none`'s docstring documents. The export schema
already carries a plain bool `returns_none` that survives the round trip, and
`class_gen`'s method plans already consume it — but the plain-function extern
paths did not:

- `native_modules._extern_user_function_return_ir_type` (feeding both
  `_bind_native_cross_module_export` and `_declare_extern_user_function`)
  decoded the degraded descriptor → `DynType` → declared the sibling extern
  as returning a pointer.
- `extern_func_info_lowering._extern_info_to_funcdef` reconstructed the
  caller-side FuncDef with `return_ty` dyn, keeping call lowering coherent
  with the wrong declaration (value consumption + rooting of x0 garbage).

Fix (landed): both sites now check
`"returns_none" in info and bool(info["returns_none"])` FIRST (same idiom as
class_gen line ~312; avoids `dict.get` which mis-lowers under pcc1). Fix #1
(NoneType-by-name) is retained as real hardening but could never fire here:
the descriptor arrives as `("dyn",)`, not as a foreign-identity NoneType.

Regression: `tests/python/test_extern_returns_none_abi.py` (4 tests, green)
pins both consumers against the degraded `("dyn",) + returns_none=True` shape
and the no-bool fallback.

Deliberately NOT changed in this slice: `type_infer.py` (~line 3861) still
decodes the raw descriptor for the imported FuncType's return, so under pcc1
the inferred call-expression type degrades to dyn — precision loss only, no
ABI impact now that declaration + FuncDef are void-coherent.
