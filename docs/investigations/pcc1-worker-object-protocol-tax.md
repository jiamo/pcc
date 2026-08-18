# pcc1 deferred-worker object-protocol tax (nested_walk 78.7%)

## Goal

Reduce the per-module cost of a pcc1 deferred codegen worker (currently
~24s for `class_gen`) so the Stage2 prediction
(`docs/goal/evidence/HARNESS-P0-STAGE2-MEMORY-SAFE-DEFAULT/002-*.md`, 1302.8s)
can fit the 600s/8GiB contract. Task-list owner: item 4 (native data plane /
GC-root-frame tax / allocator high-water).

## Mode

Repro mode (new). Related predecessors:
`granule-span-lookup-radix.md` (the granule lookup itself is already radix,
DONE_STRONG — the remaining lever is CALL COUNT, not lookup cost),
`pcc1-stage2-emit-throughput-and-memory.md`,
`self-backend-short-lived-emit-worker-fanout.md`.

## Reproducer / canary `[CONFIRMED]`

Fast loop (~24s, no Stage2 needed): replay one deferred worker item from the
retained v5 plan state with any current pcc1.

```bash
# manifest: copy of v5 worker_0.manifest with lines 1 (result tsv) and
# 2 (pco output dir) rewritten to a scratch dir and the trailing assigned
# index set to 87 (pcc.py_frontend.codegen.class_gen).
gtimeout 120s env -u LC_ALL PCC_PY_FRONTEND_JOBS=1 \
  PCC_DIRECT_INDEXED_NATIVE_OBJECT=0 PCC_GC_BACKEND=0 \
  PCC_PYTHON_IR_PASSES=off PCC_RUNTIME_HIGH=py \
  /usr/bin/time -lp <pcc1> --pcc-python-multi-codegen-worker <manifest>
```

- v11 pcc1 baseline: 23.4–24.1s wall over 3 runs, ~2.1 GiB RSS, 315.6e9
  instructions, result OK, emits `module_87.ll` (sha b7059795c85ccfcf…).
- Do NOT set `PCC_DIRECT_INDEXED_KERNEL_EMIT=1` standalone: the worker fails
  with "direct indexed kernel output requested without capture" (the capture
  is plan-driver state). The frontend-only replay is the dominant cost anyway.
- Byte-compare `module_87.ll` against the stash to prove semantic neutrality
  of a candidate.

## Measured attribution `[CONFIRMED]`

`scripts/pcc_flamegraph.py cpu <pid> 15` on a live replay (12,570 samples):

- `hoist_free_names.compute_free_names`'s nested `walk` subtree =
  **78.7% of the whole worker**.
- `PCC_HOIST_PROFILE_PATH` counters: only **12 calls** (4 cache hits) for the
  whole module → **~2.4s per cache-miss call**, i.e. the cost is per-walk
  per-node, not call-count.
- Leaves inside the subtree are a diffuse object-protocol tax, no single
  owner: granule/provenance ~12.4% (`pcc_gc_granule_is_object_start` 8.8,
  `pointer_is_managed` 2.3+1.3), per-call frame/root bookkeeping ~12.5%
  (`frame_enter`/`note_frame_enter`/`frame_leave`/`note_frame_leave`/
  `frame_roots_disabled_fast`/`store_root`), refcount protocol ~8%
  (`py_incref_prepare`/`py_decref_prepare`/`py_decref_finish`/`py_decref`),
  pin/unpin 2.7%, `pcc_gc_config_ensure` 2.1%, TLS 1.4%.
- Suspicious on GC0: `pcc_gc_backend4_finish_remap_retirement` at 1.5% of the
  subtree **while PCC_GC_BACKEND=0** — a backend-4 retirement check appears on
  the default backend's per-op path.
- Exception machinery in the hot path: `_py_exc_alloc` callers are 263/282
  `nested_walk`. Mechanism confirmed in source: `hasattr(x, "n")` lowers to
  `py_obj_getattr` (call_expression_lowering.py:1788-1862), which on miss
  ALLOCATES an AttributeError (message format + TLS store) that the hasattr
  lowering immediately `py_clear_exception`s. `walk`'s `_is_call_node` does
  up to three hasattr probes per non-Call node and `_call_ident` uses
  `getattr(expr, "ident", None)` — one full exception lifecycle per miss.
  Direct self ≈ 4.7% (`exc_alloc` 2.3, `tls_exc_get/set` 1.7, `obj_getattr`
  0.5, `dealloc_exc` 0.2) plus the alloc/free GC tax it drags.

## Proposal No.1 — dict membership index for the walk's name sets `[DENIED as a speed fix]`

Replaced the per-Name `name_in(local_scope, …) + name_in(builtins_ns, …)`
linear scans with a prebuilt `resolved_scope` dict
(pcc/py_frontend/codegen/hoist_free_names.py). Hoist behavioral + shape tests
green (15), emitted `module_87.ll` byte-identical, but the canary did NOT
move: 23.79s (baseline band 23.4–24.1s). Interpretation: most Name lookups
short-circuit early in the list (params/assigned names are scanned first), so
the theoretical nodes×names quadratic was not the paid cost. The change is
retained as structural debt removal (byte-identical, neutral; the v12 Stage1
built with it measured 155.12s vs v11's 161.62s — single pair, host-side,
not claimed as a proven win). **Do not re-propose list→dict membership in
this walk as a performance fix.**

## Candidate queue (each needs its own [CONFIRMED]/[DENIED] verdict)

1. **Exception-free attribute probe** `[CONFIRMED, small]` — implemented
   2026-08-31: `py_obj_getattr_maybe` in C
   (src/py_obj_ops_dispatch.c) + port (py/py_obj_ops_dispatch.py) with the
   identical probe order to `py_obj_getattr` and NULL terminals instead of
   `_raise_attribute_error`; declared in runtime_abi and py_runtime.h;
   `hasattr` (call_expression_lowering) and 3-arg `getattr`
   (builtin_type_attr_lowering) lower through it, 2-arg getattr still raises.
   Semantics pinned by `tests/python/test_hasattr_getattr_probe.py` (one
   binary, GC0..4 runs; plus a strict-xfail pinning the PRE-EXISTING distinct
   bug that a bare 2-arg getattr AttributeError escapes an enclosing
   `except AttributeError`). class_gen IR diff vs baseline is exactly 112
   `py_obj_getattr`→`py_obj_getattr_maybe` call-site swaps plus the declare.
   Measured on adjacent alternating v12/v13 pairs (user CPU): 23.28/23.23/
   24.85 vs 22.55/22.65/22.52 — candidate wins 3/3, median **−3.1%**, and
   with lower variance. Verdict: real but below the 5% Amdahl bar — the
   AttributeError construction was a minor part of the miss path; the
   remaining per-probe cost is the lookup itself (MRO walk, capi probes).
   Also fixed en route: repo-root `pcc1` was a stale 2026-06-24 binary that
   failed `test_pcc_self_host_getattr_default.py` against the current
   backend analyzer ("managed root state disagrees at block join
   'err.exit'"); rebuilt via the documented command, 19 tests green.
2. **GC0 per-op check hoisting** `[DENIED as a speed fix]` — mechanism
   located: `pcc_gc_note_object_freeing` runs on EVERY free and, on all
   backends, allocated a 48-byte remap-finish struct and called the six-way
   `pcc_gc_backend4_finish_remap_retirement` fan-out at every exit, although
   its only writers sit under the forwarding-backend (3/4) checks — on 0/1/2
   the struct is all-null by construction. Implemented the symmetric gate in
   both mirrors (`moving` in py/py_gc_backend.py, `pcc_gc_backend_uses_
   forwarding()` at the C `done:` exit). Semantics: finalizer/resurrection/
   trashcan gates green on GC0..4 (29 each), emitted module_87.ll
   byte-identical to v13. Canary (alternating v13/v14 pairs, user CPU):
   21.88/22.19/22.03 vs 21.97/21.64/21.80 — median −1.0%, within noise.
   Retained as neutral null-work removal; **do not re-propose retirement/
   config-check gating as a performance fix.** (`pcc_gc_config_ensure`
   already had an initialized fast path; its 2.1% is call frequency from
   inlined per-op sites and moves nothing when the callee is this cheap.)
   En route, HEAD's write-barrier ratchet
   (tests/python/test_gc_codegen_write_barrier.py) was red against the
   already-landed `pcc_gc_store_ptr_plan_*` migration of py_dict.c/py_set.c
   and their ports; updated the expected shapes to the plan-commit API
   (barrier discipline intact), 15 tests green.
3. **Per-call frame/root protocol** (~12.5%) — root elision for small leaf
   helpers; `scripts/pcc_root_elision_sizing.py` exists for sizing; read its
   contract and the prior root-elision investigations first.
4. **Refcount elision for borrowed reads** (~8%) — compiler pass; highest
   semantic risk (ownership bugs are this repo's worst class).

Amdahl note: these four together bound roughly 30–40% of the worker. Reaching
the Stage2 600s contract (needs ~2.2x on the lane) likely also requires the
deeper native-data-plane representation work; do not claim the contract from
any one candidate.

**Escalation rule now armed:** candidate 1 landed below 5% (−3.1%). Per the
convergence guardrail, at most ONE more sub-5% adjacent candidate may be
tried (candidate 2's GC0 per-op checks, bounded ~3.6%); after that, work must
move to the architectural owners — the per-call frame/root protocol
(candidate 3, ~12.5%, via the root-elision program) and the native-data-plane
representation of the frontend AST/analysis structures.

## Update 2026-08-31 — metadata-traversal skip `[CONFIRMED, −48.9%]`

The escalation to a representation-level owner found a surgical form first:
the walk's generic reflection loops traversed EVERY dataclass field,
including `span` (SourceSpan) and `ty` (structural type) — ~2 metadata
dataclasses plus ~7 scalars dragged through the full per-node dynamic
gauntlet for every semantic node, a ~3x hidden multiplier on visited
objects. Skipping span/ty in the three loops plus a scalar fast-bail at the
top of `walk` (hoist_free_names.py) measured, on adjacent alternating
v14/v15 pairs (user CPU): 21.66/21.68/21.72 → **11.09/11.12/11.07**
(−48.9%, 3/3, tight variance), with `module_87.ll` byte-identical and all 15
hoist gates plus multi-file/bootstrap-baseline gates green. Free-name
semantics are unchanged by construction (span/ty cannot contain a Name).

Refreshed offline Stage2 prediction from seven single-module v15 replays
across the size spectrum: per-module ≈ 1.85s + 32.9s/MB → 224-module lane at
jobs=2 ≈ 331s → **whole Stage2 ≈ 561s ≤ 600s** — the contract is predicted
feasible for the first time (evidence 004).

En route, the first authorized capped Stage2 attempt (v7) failed closed in
0s: the stage2 runner replays the stage1 receipt env, and v15's host
`PCC_BOOTSTRAP_STAGE1_PY_FRONTEND_JOBS=10` tripped bootstrap.sh's
worker-budget guard (v5/v6 never saw this because their stage1 was also
throttled to 2). Fixed by adding the missing
`PCC_BOOTSTRAP_STAGE1_PY_FRONTEND_JOBS=auto` override to
`_stage2_environment_overrides` (run_pcc_stage_ab.py) — the compiled stage
derives its own width; no compiled admission widened. Focused gates 13
passed.

## Update 2026-08-31 — slice A landed; full-cost canary established

- **Slice A (deferred lane scheduler) implemented**: `_run_codegen_batches`
  in `scripts/run_pcc_deferred_link.py` is now a sliding window with live
  aggregate-RSS admission (`PCC_WORKER_TREE_BUDGET_BYTES`, default 8 GiB,
  1 GiB driver reserve, 2 GiB launch reserve) and a SIGSTOP/SIGCONT pressure
  ladder that always keeps one worker runnable (pressure misreads can only
  serialize, never deadlock; an exhausted window admits one
  unconditionally). Widths are caps, not schedules; wave-straggler
  serialization is gone. Contract tests:
  `tests/python/test_deferred_link_window.py` (6 passed: width cap,
  fail-closed stop, pressure-serialization without deadlock,
  suspend/resume ladder, budget parsing, live-RSS read); adjacent gates 57
  passed. Real-world proof deferred to the next authorized Stage2.
- **Full-cost canary**: the standalone worker replay needs BOTH
  `PCC_DIRECT_INDEXED_KERNEL_CAPTURE=1` (capture, generation_lowering) and
  `PCC_DIRECT_INDEXED_KERNEL_EMIT=1`; EMIT without CAPTURE fails with
  "direct indexed kernel output requested without capture". class_gen full
  cost with v15 pcc1: **35.95s wall / 4.32 GiB RSS** = frontend 11.1s +
  emit ~24.9s (+2.1 GiB). The emit half is now the dominant time/RSS owner,
  matching v8's lane data (evidence 005).
- **Emit-phase attribution** (16s flamegraph of the emit window): owners
  `build_direct_indexed_function` 13.2%, `emit_function` 9.0%, precise
  stackmaps 8.4%, `emit_typed_initializer` 5.8%, compute/materialize 5.5%,
  verify 3.9% — all over the same protocol-tax leaves (granule 10.0%,
  load_ptr 4.5%, store_root 3.6%, pin/unpin 4.8%, TypeDesc.__eq__ native
  adapter 1.6%). This is the territory of the existing IN_PROGRESS
  `PERF-P0-PROVEN-NATIVE-DATA-PLANE-PARALLEL-EMIT` row; new emit candidates
  must route through it, with this canary as the measurement loop.
- Open lead for the frontend side: the span/ty metadata-skip pattern likely
  applies to the OTHER generic AST walkers (vthread_effect_analysis was
  6.1% cumulative; hoist_analysis/boxing share the reflection helpers) —
  same verification recipe as the −48.9% slice.

## Status

OPEN — **escalation rule TRIGGERED**: two consecutive sub-5% candidates
(candidate 1 −3.1%, candidate 2 −1.0%/noise). Per the convergence guardrail,
no more adjacent micro-candidates.  The earlier allocation-window root-store
elision is already explicitly `[DENIED]` by
`S-P1-ALLOCATION-POINT-ROOT-ELISION` (1.022x, candidate removed), so the
candidate-3 wording above is historical sizing, not permission to retry it.
The selected architectural owner is the native-data-plane representation of
the builder-to-emitter boundary; its v15 full-cost profile and native final-
order proposal are recorded in
`pcc1-frontend-direct-indexed-kernel-plane.md`.
Current baseline: v13/v14 pcc1 canary user CPU ~21.6–22.2s (down from 23.4–
24.1 at v11/v12), module_87.ll sha stable with 112 `_maybe` call sites.
Evidence:
`docs/goal/evidence/HARNESS-P0-STAGE2-MEMORY-SAFE-DEFAULT/002-*.md`, `003-*.md`.

## Update 2026-09-02 — code-review pass over the session's changes

A high-effort review of the 17 files this line touched returned 7 findings;
verified outcomes:

- **hasattr leak [CONFIRMED, FIXED]**: every `py_obj_getattr(_maybe)` edge
  returns an OWNED reference (audited: instance/class getattr,
  `py_type_builtin`, field reads all incref; bound-builtin probes fabricate),
  and the hasattr lowering never released the found object — pre-existing,
  not introduced by the probe swap. Fixed: present branch now `py_decref`s
  the probe result (call_expression_lowering.py). Regression: a compiled
  4M-iteration `hasattr(lst, "pop")` loop measured **2.365 GB** max RSS with
  the pre-fix compiler and ~35 MB fixed
  (tests/python/test_hasattr_getattr_probe.py, threshold 160 MB).
- **getattr builtin ownership [CONFIRMED, FILED]**: the reviewer flagged the
  3-arg default as the leak; source audit shows the opposite polarity — the
  classifier treats `getattr(...)` results as borrowed, so the OWNED hit-edge
  result leaks (dict.get precedent), while the miss edge returns the
  BORROWED default (a bare decref would over-release). Needs the full
  native-module edge audit → `BUG-P1-GETATTR-BUILTIN-RESULT-OWNERSHIP`.
- **span/ty skip soundness [ratcheted]**: all 66 py_ast dataclasses annotate
  `span: SourceSpan` and `ty: Type`, never Expr; pinned by
  `test_span_ty_fields_can_never_carry_expressions`.
- **deferred worker deadline [CONFIRMED, FIXED]**: the window lanes had no
  per-worker bound; added `PCC_DEFERRED_WORKER_TIMEOUT_S` (default 900s,
  clock frozen during SIGSTOP), kill + fail-closed on expiry, with a hung
  worker test (window suite now 7 passed).
- **stage_ab CLI exception coverage [FIXED]**: main() now also catches
  KeyError/RuntimeError (dynamically-loaded tools raise CompileABError
  subclasses) so failures exit through the clean error path.
- **deferred-plan state hygiene (cp -R nesting; env-restore materializing
  unset vars) [CONFIRMED, FILED]**: another session's in-flight area →
  `BUG-P2-DEFERRED-PLAN-STATE-PERSISTENCE-HYGIENE`.

Post-fix gates: hoist/multi-file/bootstrap-baseline/getattr-default 62
passed; probe pin tests 2 passed + strict xfail; window suite 7 passed.

## Update 2026-09-02 — coordinator decomposition; site-roots cache; contract wall

- **Lane simulation with the landed admission window** (full-cost fit from
  five v15 replay points: cost ≈ 0.94s + 142.7s/MB, rss ≈ 0.87G + 12.6G/MB):
  current deferred caps give lane ≈ 470s (the window alone removes v8's
  wave waste, 792→470), proposed wider caps (heavy 3 / medium 4 / small 6)
  only ≈ 439s — the big modules' 4–6 GiB RSS makes admission serialize them
  regardless of caps.
- **Coordinator (checkpoint) measured end to end** via a standalone repro
  (v15/v17 pcc1 + defer env; it reproducibly dies at the end with the opaque
  `PCC-PY-COMPILE-001 [python-frontend] __init__` — the same empty-message
  diagnosability defect that bit the bare isolated-pcc1 runs; phases up to
  the failure are complete). `--profile-json` phase totals:
  **multi_frontend_export_parallel 83.9s** (58%, runs at the compiled ≤2
  export contract), expand_recursive_stdlib 16.5s, closure collect 11.9s,
  native-ext ports 7.9s, site-ABI 3.6s.
- **Site-roots resolution cache `[CONFIRMED, small]`**: every
  `resolve_pcc_native_extension_path` call re-ran the 13-env-var package
  environment resolution plus an environment.json open/read/parse
  (kernel `read` was 34% of a mid-scan sample window).
  `package_environment_fingerprint()` + `_SITE_ROOTS_CACHE` in
  pipeline_packages cache the RESOLUTION only; isdir/isfile/listdir probes
  stay per-call (fresh installs still found; env changes re-resolve —
  tests/python/test_package_site_roots_cache.py, 3 passed; both files pass
  the no-libpython closure check; package-env tests 18 passed).
  Coordinator wall to the same failure point: 147.63 → 143.00s (−3%; the
  34% was window-relative, my extrapolation was sloppy). Worker canary
  unchanged (11.19s), module_87.ll byte-identical.
- **Where the 600s contract now stands** (whole ≈ checkpoint 143 + lane 470
  + link ~50 ≈ 663s): the remaining ~10% cannot be closed without one of
  (a) re-deriving the pinned checkpoint/export ≤2 width from measured
  export-worker peaks (board-contract change — human call; export at
  width 4 would cut ~40s), (b) a further ~2x on the emit half (the
  parallel-emit ledger's 0.1–3%-per-candidate territory), or (c) re-deriving
  the 600s value itself (~700s fits today). Deferred-cap widening alone
  (+31s) does not reach it.

## Update 2026-09-03 — slice A's window admitted bursts; owner correction

Re-reading `build/inline-edge-stage2-capped-v2/stage2-process.samples.tsv`
(0.25 s samples) instead of the summary numbers relocates the 8 GiB trip:
the coordinator peaked at 7.15 GiB tree (7.03 GiB single) around 120-152 s
and exited under the cap; every non-small lane finished under 6.3 GiB tree;
at 553 s the width-4 small lane admitted worker_27/28/32/33 in one poll and
ten seconds later they summed 8.23 GiB (2.75 + 2.56 + 1.58 + 1.34, all still
growing) -> MEMORY_LIMIT at 564 s.  The v12 arm shows the same kill-time
shape (four concurrent codegen workers ~8.6 GB).  The "coordinator live
6.5 GiB is the owner" attribution on the board was wrong; the coordinator is
a wall-time owner, not the memory-trip owner.

### Code Change

`scripts/run_pcc_deferred_link.py::_run_codegen_batches` admitted on
`live + 2 GiB <= 7 GiB` and re-polled immediately after each launch; a fresh
pcc1 worker reads ~0 RSS, so the whole width filled at once and the 2 GiB
reserve covered one worker's growth, not four.  Now each running worker is
charged `max(live, floor)` with an AST-derived floor for the heavy/medium/
small lanes (`min(0.9 GiB + 1.2 GiB/AST-MB, 3.5 GiB)`; serial/paired keep the
2 GiB default), admission needs `charged + floor(new) <= 7 GiB`, the ladder
stops all runnable workers but the oldest in one poll, and per-lane admission
stats land in `<plan>.result.json`.  Tests: window suite 11, self-link tool 5
(fixture fixed), adjacent harness 39 — all green.

### CONFIRMED (mechanism) — Stage2 proof pending authorization

The mechanism is confirmed from the receipt and the source; the fix is
unit-proven.  A real capped Stage2 has not run.  Note for that run: 31/224
modules were done at 564 s, so the whole stage projects to ~1100 s+ and a
600 s timeout will fire before the memory fit is observed; the wall gap is
this investigation's original problem, not a regression from the fix (the
floor-charged small lane models ~550 s versus ~380 s at the unsafe width 4).
Evidence: `docs/goal/evidence/PERF-P0-STAGE-RESOURCE-ENVELOPE-PARITY/002-cap-trip-owner-is-deferred-lane-burst-admission.md`.

## Update 2026-09-04 — Step 10 pcc1 transfer `[CONFIRMED, sub-1% on representative workers]`

The detailed steps 1-10 and per-operation tables live in evidence 001. Fresh
frozen Stage1 v12 completed under the 8 GiB process-tree guard: pcc1
`939b761e...`, build wall 165.88s, tree CPU 808.70s, peak tree RSS 4.88 GB,
libSystem-only and function canary 42. The focused Step-10 GC0..4/shape packet
passed 23 tests.

The two retained Stage2-v4 workers both succeed with the v12 pcc1:

```text
cli_bootstrap       ~684.7 B -> 679.064 B instructions (about -0.8%), 60.10s, 6.63 GiB
exception_lowering  ~203.6 B -> 203.541 B instructions (about -0.03%), 18.49s, 3.15 GiB
```

This confirms the host-green pin/root change transfers correctly, but its
compiler-workload share is too small to close the Stage gap. Do not extend the
same adjacent helper family. Evidence:
`docs/goal/evidence/PERF-P0-PCC1-WORKER-OBJECT-PROTOCOL-TAX/002-step10-pcc1-transfer.md`.
The next proposal must address a cross-row structural owner: first measure the
already-landed GC tracking-node pool, then choose between that owner and the
~12% refcount provenance walk with full C/port and GC0..4 proof.

## Update 2026-09-04 — bounded node pool retained; five-module reuse `[DENIED]`

The tracking-node freelist measures about 5% on the allocation row and 3.4%
on a call-returning-object row, but was unbounded. It is now capped at 4096
nodes (~160 KiB), with C/port-equal count/drain APIs; bounded versus unbounded
adds only 0.3-0.55% instructions on the allocation row. Strict closure,
LLVM/self objects, GC0..4, pthread contention and the bounded reuse/drain
canary pass. A stale state-registry test was corrected to include the two
globals deliberately owned by the separate relocation-selector object rather
than moving or duplicating their definitions.

The exact old five-module batching probe was also rerun because ownership,
slab and release mechanisms had materially changed. Fresh processes take
23.96s / 1.244GB; one batch takes 21.55s / 2.469GB. CPU improves only about
2.5%, RSS remains 1.98x and exceeds the registered 1.5x/2GB lines. The prior
batch/recycle denial stands; smaller batches are not a new mechanism.

Evidence:
`docs/goal/evidence/PERF-P0-PCC1-WORKER-OBJECT-PROTOCOL-TAX/003-bounded-tracking-pool-and-worker-batch-denial.md`.
