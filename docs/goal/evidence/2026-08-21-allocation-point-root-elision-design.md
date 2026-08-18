# Design + pre-registration: allocation-point root elision

Date: 2026-08-21
Row: `S-P1-ALLOCATION-POINT-ROOT-ELISION` (design-stage exit artifact)
Status: DESIGN ONLY — no compiler source change accompanies this file.

## Problem

A `pcc_gc_store_root` is only needed if a GC can run inside the window between
the store and the slot's last reload. On the representative 15.5 MB module
(`fair_same` shape, 804 functions), 11,602 root stores and 12,807 frame
enter/leave calls execute; investigation Update No.53 attributes a distributed
66.2% GC/refcount leaf tax across every stage2 phase.

## Sizing (read-only, both bounds measured 2026-08-21)

```
store_root sites                                    5,601
dead roots (slot never reloaded)                        0
allocation-free windows, generous upper bound       4,001   71.4%
allocation-free windows, sound lower bound          1,600   28.6%
rejection line (from the row)                                5%
```

The sound lower bound is a path-insensitive full-region scan: region R = every
block reachable from the store block that can still reach a reload block, the
store block's full remainder plus every full block of R must contain no call
outside the whitelist. Even this over-conservative form is 5.7x above the
rejection line, so the row proceeds.

Two scan corrections any re-derivation must keep: (1) key windows on the SLOT's
reloads, not the value — root codegen reloads from the slot, so value-keyed
scans are vacuously 100%; (2) block-local scans are structurally blind — every
call is followed by a `py_err_occurred` branch, so all windows cross blocks.

## Window definition (on the precise-stackmap CFG)

The analysis lives in `pcc/backend/self_backend_precise_stackmaps.py`, which
already computes `_block_entry_states` (line 507) and rejects any inconsistency
with "managed root state disagrees at block join" (line 535). The elision fact
is a per-slot, per-edge dataflow bit ALLOC_FREE(slot, program-point):

```
gen   : pcc_gc_store_root(slot, v)        -> ALLOC_FREE(slot) := true
kill  : any call NOT in the whitelist     -> ALLOC_FREE(*) := false
join  : logical AND across predecessors (same join discipline, same walker,
        as _block_entry_states; a disagreeing edge kills the bit)
use   : a store_root whose every reload is reached only through
        ALLOC_FREE(slot)=true program points is elidable — the slot is not
        registered, the store becomes a plain SSA use, reloads read the SSA
        value directly.
```

No new IR metadata; the fact is computed where root plans are already built
(`build_function_stack_map_plan`, line 952) so the stack maps and the elision
can never disagree by construction.

## Whitelist = proof obligation

A call may be treated as non-allocating ONLY if it is on this list, and the
list ships with a test that fails when a listed symbol's implementation gains
an allocation site:

```
pcc_gc_frame_enter / pcc_gc_frame_leave (+ _lifo)   bookkeeping, no alloc
pcc_gc_store_root                                    slot write
pcc_gc_load_ptr                                      read barrier (GC3/GC4 may
                                                     RELOCATE-READ but never
                                                     allocates or collects)
pcc_gc_retain / pcc_gc_release                       refcount adjust; release
                                                     may FREE but never runs a
                                                     collection under GC0..4*
py_err_occurred                                      TLS read
llvm.* intrinsics                                    machine ops
```

(*) The release entry is the sharpest obligation: `py_decref` reaching zero
runs deallocation and `__del__` dispatch, and a finalizer can allocate. The
v1 whitelist therefore EXCLUDES `pcc_gc_release`/`py_decref`. The sizing was
re-run under that exclusion the same day:

```
sound lower bound, v1 whitelist (no release/decref)   1,200 / 5,601 = 21.4%
```

Still 4.3x above the rejection line, so the exclusion costs 7.2 points of
domain and buys the finalizer-safety argument outright — accepted for v1.

## Per-backend safety argument

```
GC0 refcount+cycle   a missed root -> collector cannot see the object during
                     a cycle collection triggered by an allocation; elision is
                     safe exactly when no allocation happens in the window
GC1 incremental      same, plus marking steps piggyback on allocation — the
                     no-allocation proof covers both
GC2 concurrent       marking can run CONCURRENTLY without an allocation in
                     this thread — the v1 design therefore only elides in
                     threads-off builds (pcc_threads_enabled()==0, same guard
                     the graph-lock elision uses); the threaded case needs a
                     safepoint argument and is out of scope
GC3 generational     promotion/forwarding runs at allocation or collection;
GC4 relocating       reads go through pcc_gc_load_ptr which is whitelisted as
                     non-allocating; an elided slot means the value lives in
                     an SSA register — legal only because no relocation can
                     occur without a GC point, which the window proof excludes
```

## Frozen A/B protocol and rejection line

```
inputs      the frozen module98 IR + assembly oracle (existing bundle)
arms        candidate pcc1 (elision on) vs baseline pcc1 (same source, elision
            force-off by env), private snapshots, balanced CB/BC pairs >= 3
acceptance  every focused stackmap/GC gate green on backends 0..4; assembly
            differs ONLY by removed registration/stores; paired median wall
            >= 1.05x with user+sys and instructions improving; RSS <= 1.02x
rejection   first pair below 1.02x may stop and DENY; any GC gate red on any
            backend is an immediate DENY regardless of speed
```

## Explicit non-goals of v1

Threaded builds (GC2 concurrent marking), cross-function windows, elision of
`frame_enter` itself when some slots in a frame survive, and any change to
barrier semantics. Bundling any of these voids the pre-registration.


## Parser-authoritative sizing (supersedes the text scans above)

`scripts/pcc_root_elision_sizing.py` reimplements the count on the real
`parse_self_backend_module` output — the same structures the transform would
consume — with per-window semantics (a re-store to the same slot ends the
window; readers are `pcc_gc_load_ptr` calls carrying the slot; one dirty path
to any read kills; v1 whitelist, release excluded). Contract tests:
`tests/python/test_root_elision_sizing_tool.py`, 6 passed.

```
representative module (fair_same shape)
  store_root sites          9,201    (the text scans' 5,601 was a regex
                                      undercount -- wrong denominator)
  windows with reads        5,601
  elidable, sound LB        2,400  = 26.1%
frozen real stage2 module98 (1,831,588 bytes, fe8c801f763e2b66)
  store_root sites            367
  windows with reads          174
  elidable, sound LB           55  = 15.0%
additional domain not yet counted: 3,600 windows on the representative module
store and are never read before the next store/leave -- dead stores under the
same no-GC-point proof, deliberately excluded from the v1 count.
```

Both workloads clear the 5% rejection line on the authoritative instrument
(26.1% synthetic, 15.0% real stage2 shape). Slot references are fully closed:
module-wide, root slots appear only in store_root / load_ptr / frame_leave
argument positions — nothing else ever takes a slot address, so there is no
escape case the rewrite must chase.

## Implementation attempt at the prepare layer: DENIED — wrong layer, with the mechanism

A first implementation (env-gated transform in `prepare_module_for_target`,
after verify, before stackprep: elided `store_root` became `store null`, each
window read became a bitcast of the stored SSA value) compiled and ran, and
produced `<null>` outputs on the first end-to-end test. Two real lessons:

1. **Plain `load` reads root slots too.** The module-wide "slots appear only in
   store_root/load_ptr/frame_leave argument positions" audit counted CALL
   arguments only; parameter-address slots (`%a.addr.*`) are read by ordinary
   `load` instructions. The sizing tool now counts both.

2. **The transform had a concrete soundness hole, and the deeper mechanism is
   NOT yet established.** The dirty flag was only recorded when a READ was
   reached, so a window with no visible reads skipped the dirty check entirely
   and was elided as a "dead store" even when it was full of allocating calls
   (`user_rooty_main` elided 6/6 — every one of its windows crosses `py_*`
   calls). That alone explains nulled values wherever the slot's content was
   consumed by something the visible-reader scan does not model.

   What consumes it is the open question. The first theory — backend-generated
   safepoint reloads — is WEAKENED by a direct check: `grep store_root
   pcc/backend/*.py` matches nothing, so the backend planner does not key on
   store_root instructions at all. Remaining candidates, none yet established:
   (a) frontend-emitted readers the scan still misses; (b) the slot being the
   liveness anchor for an object whose other references are dropped inside the
   window (a root does not hold a refcount, but on a collecting backend the
   registered slot is what keeps the object out of a sweep triggered inside
   the window — which is precisely the no-GC-point proof, voided by the dirty
   hole above). Per the instrument-first rule, the mechanism must be pinned by
   diffing the emitted assembly of one elided site before v2 is designed
   further; the earlier definitive wording overstated what was measured.

VERDICT for the prepare-layer shape: `[DENIED]`, implementation reverted, the
default path re-verified green. Not a denial of the row: the design already
placed the analysis inside the stackmap planner; the attempt confirmed WHY that
placement is load-bearing rather than aesthetic. v2 must make stackprep itself
treat an elided slot as a plain spill slot — excluded from the frame map, no
safepoint reloads generated, reads resolved to the register copy — so the
"reader" set is closed by construction. That is stackprep/regalloc surgery and
needs its own focused gates from the pre-registered list before another
attempt.

The sizing tool and its 6 contract tests remain valid (they measure windows on
the visible-reader definition and now count plain loads); the 15.0%/26.1%
numbers stand as upper-context for the v2 domain, with the caveat that hidden
safepoint reloads will shrink the truly elidable share for values living
across whitelisted calls.


## Strict-subset sizing closes one v2 shortcut

Windows containing no calls at all (llvm intrinsics only) would be safe under
ANY planner policy. Measured with the tool by swapping the whitelist:

```
representative module   0 / 9,201 = 0.0%
real module98           0 /   367 = 0.0%
```

Empty on both workloads — partly by construction, since a barrier read is
itself a call, and every observed window at minimum crosses other slots'
barrier reads. So there is no planner-independent subset: v2 must make the
no-GC-point proof carry values across whitelisted calls, and the whitelisted
calls' non-collecting property is part of the proof obligation.


## Consumer PINNED by measurement: bitcast aliases of one alloca

The "invisible reader" needed no hidden backend machinery. The frontend
bitcasts ONE root alloca to a FRESH alias per window:

```
%exact.int.lhs.tmp.root.ptr.414.28 = bitcast %exact.int.lhs.tmp.root.27
%container.tmp.root.ptr.428.41     = bitcast %exact.int.lhs.tmp.root.27
%container.tmp.root.ptr.469.78     = bitcast %exact.int.lhs.tmp.root.57
```

`store_root` and the reads each use their own alias, so an analysis keyed on
the spelled name sees window A's store and window B's read as different slots:
A looks read-free ("dead store"), gets nulled, and B's reader — the SAME
memory — reads null. Combined with the transform's own hole (windows with no
visible reads skipped the dirty check), this fully accounts for the `<null>`
outputs; no safepoint-reload theory is required.

Flip-test on the exposing input (`rooty.ll`, 29 store_root sites):

```
without alias canonicalization    7 windows with reads, 0 elidable
with alias canonicalization      17 windows with reads, 7 elidable = 24.1%
```

The sizing tool now resolves bitcast chains to the underlying storage before
any comparison (`_canonical_slots`), contract tests still 6 passed, and the
big-workload numbers are unchanged (26.1% representative / 15.0% real
module98 — aliasing exists there but does not flip verdicts).

## v2 shape, now fully constrained by measurement

Back to a prepare-layer transform, with three obligations the failure taught:

1. alias-closure everywhere a slot name is compared (store/read/window-end);
2. windows with no visible reads are NOT dead stores — they keep the object
   visible to a collection triggered inside the window, so they get the same
   dirty check as read-bearing windows and are elidable only when clean;
3. readers include plain `load`, barrier reads, and alias forms of both.

The domain that survives all three is measured, not hoped: 15.0% on the real
stage2 module, 24.1% on the exposing input, 26.1% on the representative
module.

## v2 whole-closure candidate: CRASHES — the false 5.9x, the minimized repro, and the stash

v2 (alias closure + dirty check on read-free windows + plain-load readers)
passed everything local: closure check, 5 e2e programs with identical outputs,
55 focused stackmap/root-precision gates both flag states, and a full stage1
with the flag on (cache off, rc=0, 257 s).

The A/B then read **8.8 s vs 51.6 s = 5.9x** — and was false: the harness
checked compile rc only, and the candidate pcc1 was SEGFAULTING fast (139) on
the 600-function arm. Amdahl already forbade 5.9x from a <=57% tax; the number
itself was the alarm. The harness lesson is permanent: **a compile A/B must
run the produced binary and compare its output, not just the compiler's rc.**

Crash, minimized and fingerprinted:

```
candidate pcc1 (whole closure built with elision, flag off at runtime)
  50-function input   compiles and runs correctly
 100-function input   SIGSEGV while compiling
 lldb: EXC_BAD_ACCESS code=2 at 0x16f603f80 (stack guard), single-frame bt,
       frame #0 = user_..._pipeline_self_backend_cache__identity_raw + 4
```

A stack-guard fault at function entry with a collapsed backtrace is the
smashed-stack / GC-corruption class, not a plain infinite recursion: the
elision removed a root somewhere in pcc1's own closure that a collection
(triggered only at 100-function allocation volume, never in the small e2e
programs) needed. Small-input health checks cannot gate this class — the
five-GC volume gates exist for exactly this reason and had not run yet.

Also recorded on the way: the object cache serves the OTHER configuration's
objects when a compiler-behavior env flag is not part of the cache identity
(two stage1 builds, flag on vs off, produced byte-identical pcc1). Fixed in
`pipeline_self_backend_cache._identity_raw`, which folds the flag into the
identity. Unrelated pre-existing find: host `-o` on a 400-statement `main`
hits RecursionError in frontend codegen regardless of the flag (`--emit-llvm`
on the same file passes), left un-investigated here.

State: default path restored and re-verified (baseline pcc1 back in
`build/bootstrap/pcc1`, compiles and runs the 600-function arm correctly);
crash inputs and both binaries stashed for the next session:

```
pcc1_base_v2 / pcc1_cand_v2 / cr_100.py  in the session scratchpad
```

## Status

v2 analysis survives every visible-reader test yet the whole-closure candidate
is unsound under allocation volume — one more invisible reader class or an
unproven whitelist entry (pin/unpin/retain shipped without their obligation
tests) remains. Default off, tree safe. Next: lldb watchpoint triage on the
minimized crash per the debugging playbook, then the whitelist obligation
tests before any re-attempt.

## Full exoneration chain: the crash was a replace-all typo, not elision, not a miscompile

Three wrong attributions fell in sequence, each killed by a single-variable
control:

```
theory 1  elision unsound under volume     killed: flag=0 build of the same
                                           source also segfaults on cr_100
theory 2  pcc1 miscompiles the helper      killed: the "miscompiled" bl <+0>
          (call-lowering defect)           is a FAITHFUL compile of the source
                                           -- a replace-all edit had rewritten
                                           the helper's own first line into
                                           `identity = _identity_raw()`
truth     source-level infinite recursion  introduced by my own sed-style
                                           patch; host RecursionError, both
                                           binaries' stack-guard faults, and
                                           the false 5.9x were all one bug
```

With the recursion fixed at the source:

```
baseline pcc1 (flag=0)  compiles cr_100 and the 600-fn arm, outputs correct
candidate pcc1 (flag=1 whole closure)  same inputs, same outputs
compiled artifacts      byte-identical across the two compilers
candidate binary size   180,828,104 vs 184,196,888 = -3.37 MB (-1.83%)
                        the elided roots are real removed code
```

Editing lesson recorded: a replace-all whose pattern also matches inside the
replacement's own definition writes recursion; every sed-style patch needs a
count assertion per intended site, not one global count.

## Final verdict: v2 root elision is SOUND and [DENIED] on the pre-registered bar

With the typo fixed, the honest A/B (three balanced pairs, fresh salted
600-function inputs, each pair verifying the produced binaries RUN and print
identical results):

```
pair0/1/2   base 143.9/144.0/144.8 s   cand 141.2/141.2/140.8 s   outputs equal
paired      base min 143.9 s | cand min 140.8 s | 1.022x
bar         >= 1.05x (pre-registered)  ->  DENY
```

Correctness held everywhere: candidate pcc1 (whole closure built with elision)
compiles the crash-minimised input and the 600-function arm correctly, and its
compiled artifacts are byte-identical to the baseline compiler's. The candidate
binary is 3.37 MB (-1.83%) smaller — the elided roots are genuinely removed
code — it just does not buy wall time.

Why the arithmetic never worked: `pcc_gc_store_root` writes are ~1.9% of
worker self time; the GC tax lives in the managed-pointer index probes,
incref/decref and the graph locks. Eliding even all 26% of provable windows
caps below 1%. The domain measurements were correct; the WRONG number was
implicitly assumed — the cost per elided site.

Disposition per the pre-registered line: transform and wiring removed, default
path rebuilt and re-verified (stage1 rc=0; hc/MIN/rooty/cr_100 all correct;
47 focused gates green). Kept: the sizing tool + focused contract tests, this
design/evidence trail, and the alias-canonicalization knowledge. Successor per
investigation No.53 stays the value-lane track: make common helpers provably
non-allocating / keep exact-int values out of boxed roots entirely — attacking
the count of allocations and barrier probes, not the root stores.

## Claim-boundary correction after review

“SOUND” in the historical verdict above means only that the tested candidate
produced runnable, byte-identical results on its recorded corpus before missing
the performance bar. It is **not** a universal semantic proof for a future
transform or for the retained sizing tool. In particular, a future proposal
must independently close complete alias/cycle handling, ordinary-load readers,
CFG/terminator coverage, root-value freshness at window entry, finalizer-
capable calls, threads, and GC0..4 relocation. The transform is absent; the
tool is diagnostic-only and cannot authorize production elision. This row is
an exhausted negative result, not the active stage2 optimization route.
