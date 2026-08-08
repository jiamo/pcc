# Investigation: entry-alloca hoist fix (call-ret GC root slots) was semantically right but made stage2 quadratic — 282s -> 5245s under pcc1, plus a leftover-children hour-long shell

## Status
resolved (pending the in-flight gc4 bootstrap gate re-run as the final
measurement) — the hoist itself is correct and stays; the insertion
mechanics were rewritten from per-call `position_before(instr)` (linear
rescan) to a per-function O(1) numeric cursor, and marshal's overflow-slot
scan was de-quadratified the same way.

## Problem Description
After `unary_call_lowering._call_user` switched the call-return GC root
slot from a call-site `builder.alloca` to `_alloca_in_entry` (fixing a real
stack-overflow bug: a body-positioned alloca re-executes every loop
iteration and LLVM stack is only reclaimed at function exit — 1M-iteration
loop probe segfaults pre-fix, passes post-fix with an exact 1,000,000
`__del__` canary count), the GC4 bootstrap gate went from 9:50 to: pytest
FAILED at 22:42 on its stage watchdog while the detached bootstrap children
kept grinding — stage2 wall 5,246s (user 1,728s) vs 282s earlier the same
day. The user noticed the orphaned hour-long shell; killing it left no
survivors (`ps` clean).

## Repro
Stage2-scale only (host constants hide it: class_gen solo compiles in
~1.5s on host either way). The pcc1-executed stage2 is the measurement:
`profile/stage2.time` real_s=5246 vs 282 for the prior tree.

## Test [CONFIRMED]
- The failed gate's `profile/stage2.time`: real_s=5246.168, user_s=1728.444
  (33% of one core — mostly slow boxed scans under PCC_GC_BACKEND=4).
- Leftover children confirmed: pytest printed its final FAILED summary while
  `bootstrap.sh` + `pcc2 --pcc-self-backend-*` kept running for ~70 more
  minutes (the AGENTS.md leftover-children hazard, observed live).

## Root cause (two stacked multipliers)
1. `_alloca_in_entry` cached the first non-alloca INSTRUCTION and called
   `builder.position_before(cached_instr)` per alloca.
   `IRBuilder.position_before` does `for i, rec in enumerate(blk._instrs)`
   — a linear identity-compare scan. Each rooted call adds one alloca to
   the entry prefix, so call N scans past N-1 records: O(N^2) per function,
   with hundreds of rooted calls in the compiler's own big functions.
2. Under pcc1 self-host, the cache never even hit:
   `getattr(self, "_entry_alloca_insert_before_function", None)` can return
   the DEFAULT for instance attrs that ARE set (the documented
   `_skip_program_main` / marshal `_stash_overflow_slot` boundary), so
   every call also re-ran the first-non-alloca scan. Host CPython (stage1)
   had a working cache + C-speed scans — which is why stage1 stayed ~4min
   and only stage2 exploded.
   `marshal._stash_overflow_slot` had the same per-call prefix scan and
   turned quadratic too once the prefix held one alloca per rooted call.

## Fix (landed)
- `core_helpers._alloca_in_entry`: per-function NUMERIC cursor
  (`_entry_alloca_insert_index`, initialised in layer1_init, direct
  attribute reads per the marshal discipline — no getattr-with-default);
  repoint via raw `_block`/`_pos` save-restore (marshal precedent), bump
  the cursor per emitted record. Restore adjusts the caller's `_pos` only
  when it was inside the entry block at/after the insertion point.
- `marshal._stash_overflow_slot`: `position_at_start(entry)` instead of the
  first-non-alloca scan (ordering among entry allocas is irrelevant; each
  executes once); same-block restore adds the +1 shift unconditionally for
  positioned callers.
- `host_contract.L1_CODEGEN_HOST_ATTRS`: `_entry_alloca_insert_before_instr`
  -> `_entry_alloca_insert_index` (attribute renamed).

## Gates
- `tests/python/test_call_root_slot_loop_stack.py` (the 1M-loop + exact
  canary regression for the original hoist) — passed.
- `test_pipeline_and_codegen_host_contract_do_not_drift` — passed.
- Multi-file batteries — 133 passed.
- `tests/python/gc/test_pcc_bootstrap_full_gc4.py` re-run in flight; its
  `profile/stage2.time` is the accept/reject measurement for this fix
  (expect ~300s-class, reject if it stays 1000s+).

## Notes
- The stack-scan probe that exposed the recursion cycle for the sibling
  GC4 trashcan bug also applies here: a guard-page crash whose deepest
  frame is an innocent prologue needs the full-stack scrape, not `bt`.
- 25 other direct `self.builder.alloca(` sites remain in
  `pcc/py_frontend/codegen/` (6 files): any of them positioned inside a
  loop-reachable block is the same stack-growth bug class as the one the
  hoist fixed — worth one focused audit slice.
