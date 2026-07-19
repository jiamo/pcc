# Investigation: generator bodies iterating CPython-backed iterables — LLVM dominance failure, then runtime SEGV

## Status
active — No.1/No.2 (cpy slot spill + guard), No.4 (native protocol-for frame slot), and No.3-J1 (boxed iterator handle + precise cross-yield guard) landed; No.3-J2 (full cpy-local lifetime, the misc_util unblock) remains.

## No.3-J1 landed (2026-06-10)

`_emit_for_cpython_iter` inside a generator now: (1) boxes the
`py_cpy_iter` handle as a pcc int (`ptrtoint` -> `py_int_from_i64`)
into the persisted `__pcc_for_iter_*` frame slot, unboxing
(`py_int_to_i64` -> `inttoptr`) at the header and after blocks — raw
libpython pointers never enter the frame py_list; (2) registers the
loop target AND tuple-unpack names in `cpy_skip_save_names`, which
`_emit_generator_save_frame` skips while the loop is being emitted
(the after block clears the slots to None and re-arms saves); (3) a
PRECISE static guard rejects — naming the variable — any cpy loop
variable read after a yield suspension (ordered event scan; a yield
sentinel's argument reads precede its "Y"; a yielding nested loop
emits "Y" first because its back-edge crosses the suspension).

Two development findings worth keeping:
- **Tuple-unpack hole**: the first cut only tracked the synthetic For
  target; `for dirpath, dirnames, filenames in os.walk(...)` then
  COMPILED while the unpack names carried raw cpy pointers into frame
  saves (silent heap corruption). The unpack targets of the normalised
  leading assignment are cpy locals and are now detected/skipped too —
  misc_util is correctly REJECTED naming 'filenames'.
- **Bootstrap dialect**: the first detector used host idioms
  (generators, genexprs, dataclass reflection, in-function imports) and
  added 7 contextual fallbacks to self-host-closure
  `for_loop_lowering` (ratchet pins 0). Rewritten with an explicit
  audited AST-field table and list-accumulating recursion; ratchet back
  to 18 passed.

Observed: min10 (`for line in itertools.chain(...): yield line`)
compiles AND runs `a b end` == CPython (first time since the ladder
opened); cross-yield shape fails naming 'line'; misc_util fails naming
'filenames'; generator parity 12 passed (ladder test upgraded from
asserting the guard to asserting the output, + new guard regression);
GC contract 142; fallback baselines 18; five-GC matrix -> 5 passed in
519.17s. HONEST SCOPE: J1 does NOT unblock misc_util /
upgrade_pythoncapi (their locals genuinely cross suspensions) — that
is J2.

## Problem Description

Diagnosing the six numpy modules the per-module sweep could not compile
classified four root causes: unknown decorators (x2:
`_core/numeric.py`, `lib/_npyio_impl.py` — `finalize_array_function_like`),
`Layer 1 cannot coerce ClassType to int` (x1: `_utils/_pep440.py`), a
method positional-arg-count mismatch (x1: `distutils/fcompiler/gnu.py`),
and **`LLVM verifier failed: Instruction does not dominate all uses`**
(x2: `distutils/misc_util.py`, `linalg/lapack_lite/fortran.py`).

The dominance failure minimized to FIVE lines:

```python
import itertools
def gen(fo):
    for line in itertools.chain(fo, ['']):
        yield line
```

Root cause #1: `itertools` has no native lowering, so the loop takes
`_emit_for_cpython_iter`, which kept the `py_cpy_iter` result as a raw SSA
value; the generator transform splits the loop across resume blocks and the
def no longer dominates the `py_cpy_iter_next` / `py_cpy_decref` uses.

## Repro

```bash
# minimal (pre-fix): emits "Instruction does not dominate all uses" x4
env -u LC_ALL uv run pcc --python-library --python-libpython=auto \
  --ir-scaffold=on --emit-llvm=/tmp/min10.ll /tmp/min10.py
# regression (post-fix ladder):
env -u LC_ALL uv run pytest \
  'tests/python/test_python_generator_parity.py::test_generator_for_over_cpython_iterable_compiles_and_runs' -q -n0
```

## Test [CONFIRMED]

The minimal repro produced the verifier failure (observed, x4 violations);
after the slot spill the binary SIGSEGV'd at runtime (observed via the
focused test); the ladder regression now pins the clear compile-time
diagnostic.

## Proposals
- No.1 Slot-spill the cpy iterator in `_emit_for_cpython_iter`   [CONFIRMED — necessary, not sufficient]
- No.2 Clear compile-time diagnostic for cpy iteration inside generators   [CONFIRMED]
- No.3 Frame the cpy iterator slot across generator suspensions (real support)   [pending]
- No.4 Second dominance site: gc-root reload (`pcc_gc_load_ptr` of `call.ret.root`) in native protocol-for inside generators (fortran.py's `for line in pushbackiter:`)   [CONFIRMED]

## No.4 Native protocol-for iterator frame slot

### Code Change

`_emit_for_native_iterator` kept the `__iter__()` result as raw SSA
across the loop header — the exact shape `_emit_for_obj_iterator` (dyn
protocol path) already solves via the persisted `__pcc_for_iter_*`
generator frame slot (`_collect_generator_frame_names` pre-collects one
per `For` statement). Applied the identical idiom: store the iterator
into the frame slot after `__iter__`, reload at the loop header before
the direct `__next__` call. The frame slot is saved into the frame
py_list at yields, so the suspended iterator stays GC-visible and
relocatable.

### CONFIRMED

Observed (2026-06-10): minimal repro (user-class iterator + `for x in
pb: yield x`) failed the LLVM verifier under `--emit-llvm` byte-equal
to fortran.py AND — worse — under `-o` the broken IR slipped past
verification and produced a binary printing WRONG VALUES (1 instead of
CPython's 6). Post-fix: verifier clean, binary prints 6; nested
two-level generator case prints `[11, 21, 22]` == CPython; FULL
`linalg/lapack_lite/fortran.py` compiles (rc=0; blocked sweep 6 -> 3).
Regression `test_generator_native_protocol_for_resumes` added;
`tests/python/test_python_generator_parity.py` -> 11 passed. Battery +
five-GC matrix in `docs/current-goal-state.md`.

## No.1 + No.2 (landed together)

### Code Change

`for_loop_lowering._emit_for_cpython_iter`: the `py_cpy_iter` result is
stored to an entry alloca and re-loaded at the header (`py_cpy_iter_next`)
and after-block (`py_cpy_decref`) uses — fixes the verifier failure (the
`distutils/misc_util.py` dominance errors went to zero). A guard at the
top raises `NotImplementedError("Layer 1 does not support iterating a
CPython-backed iterable inside a generator body yet")` because with the
spill alone the generated binary still **SIGSEGVs at runtime** (the
generator transform does not frame this slot / the cpy object's lifetime
across suspension is unhandled) — a compile-error -> runtime-crash trade
is NOT acceptable, so the cryptic verifier crash became a clear
diagnostic instead of a false capability.

### CONFIRMED

Observed (2026-06-10): minimal repro verifier failures 4 -> 0;
`misc_util.py` dominance errors -> 0; the focused runtime probe SIGSEGV'd
(hence the guard); ladder regression
`test_generator_for_over_cpython_iterable_compiles_and_runs` -> passes
asserting the clear diagnostic; full
`tests/python/test_python_generator_parity.py` -> 10 passed. Battery and
five-GC matrix results recorded in `docs/current-goal-state.md`.

## No.3 design exploration (2026-06-10 evening — no code change yet)

Evidence gathered for the real-support design:

1. **Why the No.1 slot spill SIGSEGV'd**: generator frame save/restore
   moves named locals through a pcc `py_list`
   (`_emit_generator_save_frame` -> `py_list_set` ->
   `pcc_gc_store_ptr`); store barriers and frame-dealloc releases
   dereference the pcc object HEADER. A raw libpython `PyObject*`
   (the cpy iterator AND every cpy loop item bound to a named local)
   placed in a frame slot corrupts the CPython heap. The entry-alloca
   spill alone left the slot un-framed (garbage on resume). So No.3 has
   TWO holes: the iterator handle and the item locals.
2. **Deep-conversion shortcut is semantically DEAD for the target
   modules**: `py_cpy_to_pcc_obj` (exists, recursive) would convert
   `os.walk` items into pcc values — but BOTH remaining blocked modules
   (`distutils/misc_util.py:609,621`, `upgrade_pythoncapi.py`) do
   `dirnames[:] = pruned` in-place pruning, a REAL alias dependency on
   the cpy list identity (os.walk topdown contract). Converting breaks
   pruning silently (wrong traversal = wrong values). Rejected per the
   north star (no semantic weakening).
3. **Decomposition**:
   - **J1 (frontend-only, candidate next slice)**: box the ITERATOR
     handle as a pcc int (ptrtoint -> `py_int_from_i64`) in the
     already-collected `__pcc_for_iter_*` frame slot; unbox at the
     header. Plus a PRECISE static guard: if any loop-target name
     (incl. tuple-unpack names) is read across a yield suspension,
     keep failing with a clear diagnostic naming the variable.
     HONEST SCOPE: J1 does NOT unblock misc_util/upgrade_pythoncapi
     (their dirpath/dirnames/filenames genuinely live across yields);
     it unlocks the common `for line in cpy_iterable: yield f(line)`
     shape (the original min10.py) where items do not cross yields.
   - **J2 (full design, separate slice)**: complete cpy-local lifetime
     management — boxed frame slots for cpy locals, unbox at every
     read point (`_cpy_env_flags`-driven), per-iteration overwrite
     decref, generator-dealloc release of live cpy handles. This is
     what misc_util actually needs; it must NOT weaken the alias
     semantics (items stay raw cpy refs at use sites).
4. Known pre-existing gap in the SAME family (recorded, not new): the
   non-generator cpy loop leaks the iterator on early `return`/raise
   (only the `after` block decrefs), and item refs are overwritten
   without decref. J2 should sweep these together.

## No.3-J2' design sketch (2026-06-10 night — supersedes the J2 outline above)

The J2 cost driver was "unbox at every read point" — `_cpy_env_flags`
has ~49 consumer sites across 16 lowering files. J2' collapses that:

1. New C-only runtime type `CpyHandle` (header + raw `PyObject* cpy_ref`;
   NOT a pcc pointer slot, so it stays out of `py_obj_visit_slots`);
   its dealloc calls `py_cpy_decref(cpy_ref)`. Frame saves/restores,
   GC tracing, relocation, and generator-drop lifetime all work
   because the slot now holds an ordinary pcc object — the
   suspended-generator leak disappears structurally (GC drops the
   frame -> box dealloc -> cpy ref released).
2. Boxing happens ONLY at the few cpy-local STORE points (for-loop
   item bind, tuple-unpack stores, plain assignment store when the
   value is cpy); unboxing happens ONLY at the central Name-LOAD
   helper for `_cpy_env_flags`-marked names — every downstream
   consumer keeps receiving the raw cpy pointer it receives today,
   zero changes at the ~49 sites.
3. Alloca-slot overwrite must decref the OLD box (load-before-store in
   the same store helper) — single-point refcount discipline.
4. Verification items — ALL THREE RESOLVED (2026-06-10 night):
   (i) cpy-local load paths = exactly TWO: the central name-load
   (`name_lowering.py:473` — load + `_cpy_values.add(val)` SSA tag;
   J2' unboxes here) and the lambda OUTER-CAPTURE load
   (`lambda_helpers_lowering.py:500` — loads the slot directly then
   `py_cpy_incref(val)`; under J2' this must unbox the handle before
   increfing the raw ref, or capture the box itself — one known
   adaptation point). Everything else among the ~49 flag mentions is
   flag maintenance (set/pop/save-restore) or SSA-value checks fed by
   the central load — no other slot loads. The collapse holds:
   2 load points + ~3 store points.
   (ii) Mirror surface is SMALL: the pcc-Python port's per-type
   deallocs are all `extern(...)` bridges to C (py_obj.py:64-89,
   incl. `py_dealloc_generic`) — a new tag needs the C dealloc
   function + one switch case per tier + one port extern line; no
   port-side reimplementation.
   (iii) GC_TRACKED policy: the box carries NO pcc pointer slots
   (`cpy_ref` is foreign), so it is an untracked object like
   str/bytes — `py_obj_visit_slots` untouched, relocation moves the
   box body and never interprets the foreign field.
   STATUS: design verified, ready to implement as its own full slice
   (runtime type + abi entries + central unbox + store boxing + lambda
   adaptation + guard relaxation + regressions + 5-GC matrix). Not
   started at day-end by deliberate choice — a new runtime TYPE tag is
   object-model surface and deserves a fresh full turn.
5. FOURTH verification item discovered during tag selection
   (2026-06-10 late): tag 31 is the last free slot below PY_TYPE_USER
   (C enum ends at VIRTUAL_THREAD=30; the port's validity window is
   `tag <= 31 or tag >= 100`), BUT the port's decref defensive check
   (`py_obj.py` ~line 646) puts `tag == 29 or 30 or 31` in a special
   group requiring `flags & 2` (GC_TRACKED?) or
   `pcc_gc_object_is_known(o)` before proceeding — an UNTRACKED
   CpyHandle with tag 31 would be skipped by that check (decref
   no-op -> leak). Resolution options to settle at implementation
   time: (a) read the group's intent (it looks like a
   lifecycle-object anti-forgery guard) and either register handles
   in the object index, (b) set the tracked flag despite having no
   pcc slots (visit_slots still has nothing to visit), or (c) pick a
   tag layout that widens the validity window without joining the
   29-31 group (requires touching the port window check — mirror
   discipline applies). Also note `pcc_dealloc_dispatch`'s default
   branch routes unknown sub-USER tags to `py_dealloc_generic`, which
   would LEAK the foreign ref — the explicit case is mandatory in
   both tiers, and the tracing backends' sweep dealloc entry must be
   confirmed to route through the same dispatch.

Related negative finding recorded the same evening (sweep lever (b)):
the `append` 71 / `__eq__` 72 / `__contains__` 42 cpy markers in the
41-module sweep are DOWNSTREAM of cpy-domain chains rooted at the
numpy C-extension boundary (`builtins.list()` created via cpy;
`asarray().dtype.type.__eq__`) — "nativizing" those methods is
meaningless; the markers are legitimate ABI bridging, not a frontend
lowering gap. Lever (b) is closed as NOT-A-LEVER.

## Notes

`_emit_cpy_iter_loop` (comprehensions) shares the raw-SSA shape but
comprehension bodies cannot yield, so it is not exposed; left unchanged.
The other four blocked-module root causes (decorators, ClassType->int
coercion in `_pep440.py`, the gnu.py arg-count mismatch) are separate
follow-up candidates.

## J2' implementation — runtime half landed (2026-06-10 late, status TESTING)

Runtime-side components are in (frontend wiring is the next slice):

- `PY_TYPE_CPY_HANDLE = 32` (outside the port's 29..31 anti-forgery
  group; both port validity windows widened to <= 32 with comments).
- `pcc/py_runtime/src/py_cpy_handle.c` (C-only helper; Makefile SRCS +
  OBJ_PY_CC_HELPERS + explicit build_py rule): `py_cpy_handle_new`
  (takes ownership), `py_cpy_handle_get` (borrows),
  `py_dealloc_cpy_handle`. Allocation mirrors the str discipline
  (`pcc_gc_alloc(size, tag, 0)`).
- Dealloc dispatch cases added in BOTH C switches
  (`pcc_dealloc_dispatch` in py_obj.c and the tracing sweep's
  `pcc_gc_finalize_unreachable` in py_gc_backend.c — the default
  branches route to py_dealloc_generic which would LEAK the foreign
  ref); the port's decref tail reaches the same C dispatch via the
  `pcc_dealloc_with_trash` extern, so no port-side switch exists to
  mirror.
- `pcc_gc_relocate_copy_supported_tag` whitelists the handle (no pcc
  slots — shallow relocation copy is str-equivalent).
- LINKAGE FIX found by the contract suite (10+10 failures on plain
  `cc` links): py_cpy_handle.o lives in the MAIN archive while
  py_cpy_decref lives in the separate libpython archive (real/stub) —
  direct reference broke `cc ... libpy_runtime.a` links. Resolved
  with a release-function hook: `py_cpy_handle_set_release_fn`,
  registered by `py_cpy_ensure_init` (a process that never inits the
  bridge can never hold a foreign ref, so a NULL hook is safe).

Observed: gc_production_contract + generator parity -> 142 passed;
gc0 full bootstrap single-file -> 1 passed (port-window live-code
representative check); strict-mode smoke binary runs. NOT yet run:
the five-GC matrix — deferred to the frontend-wiring slice's combined
verification (the new type is dead code until the frontend emits it;
the live-code deltas are the port window + dispatch cases, covered by
the gates above pending the matrix).
Next slice: runtime_abi entries + central name-load unbox +
store-point boxing (for item / tuple-unpack / assignment) + lambda
capture adaptation + J1 boxed-int iter slot -> CpyHandle + cross-yield
guard relaxation + regressions + five-GC matrix.

## J2' stage 1 landed — single-name cpy loop targets cross yields (2026-06-10 night, bootstrap-verified)

Frontend wiring completed on top of the runtime half:

- runtime_abi: `py_cpy_handle_new` / `py_cpy_handle_get`.
- `_emit_for_cpython_iter` (generator branch): the ITERATOR box is a
  CpyHandle in the `__pcc_for_iter_*` frame slot (replaces J1's
  boxed-int; the after block drops the local ref and clears the slot
  so the iterator releases at loop exit; early return/raise leaves it
  to the frame drop — the structural-leak fix working as designed).
  The single-name TARGET slot now holds a CpyHandle box per iteration
  (pre-cleared before the header so the first iteration's old-value
  decref sees NULL; each store decrefs the previous box; the AFTER
  block deliberately KEEPS the last box so post-loop reads see the
  final item like CPython). Names are registered in the generator
  ctx's `cpy_boxed_names`.
- Central unbox at the name-load helper and at the lambda
  outer-capture load (both check `cpy_boxed_names`), so every
  downstream consumer keeps receiving raw cpy pointers.
- Tuple-unpack targets KEEP the J1 skip-save + precise guard
  (stage 2); single-name targets no longer skip saves and the
  cross-yield guard no longer applies to them.

Observed: the formerly guarded shape (`yield 1; print(line)`) compiles
AND runs `1 a 1 end` == CPython; min10 unchanged; misc_util still
correctly rejected naming 'filenames' (unpack = stage 2); generator
parity 13 passed (guard test upgraded to a runs-test + new
tuple-unpack guard test); fallback baselines 18 (closure-module edits
clean); GC contract + sorted suite 135; five-GC matrix -> 5 passed in
460.09s. HONEST SCOPE: stage 2 (tuple-unpack boxing through the
assignment store path) is what actually unblocks misc_util /
upgrade_pythoncapi.

## J2' stage 2 landed — flat tuple-unpack cpy targets cross yields; misc_util & upgrade_pythoncapi UNBLOCKED (2026-06-10 night, bootstrap-verified)

The generic assignment path neither tracks element cpy-ness nor boxes,
so `_emit_for_cpython_iter` now emits FLAT single-level all-Name
unpacks ITSELF and consumes body[0]: each element is extracted from
the raw cpy item via the bridge (`py_cpy_from_i64` index +
`py_cpy_getitem`, owned), boxed into a CpyHandle, stored with the same
old-box decref + pre-cleared-slot discipline as the target, flagged
cpy, and registered in `cpy_boxed_names` (central unbox covers the
reads). NESTED tuple targets keep the J1 skip-save + precise guard
(observed still rejecting, naming 'c'). The alias contract that killed
the deep-conversion idea survives intact: reads unbox to the RAW cpy
list, so `dirnames[:] = pruned` mutates the same object os.walk holds.

Observed: flat unpack across yields runs `1 / x x / 1 / y y` ==
CPython; **misc_util.py AND upgrade_pythoncapi.py compile (rc=0) —
the per-module sweep is now 43/43**; parity 14 passed (flat runs-test
+ nested guard test, the enumerate-shaped nested probe turned out to
desugar away from the cpy path and was replaced with a
zip_longest-of-zip_longest shape verified to hit the guard); fallback
baselines 18; GC contract + sorted suite 135; five-GC matrix ->
5 passed in 598.53s (slow end of today's 416-598s band). Known
limits recorded: unpack arity mismatches are not yet checked
(cpy_getitem error path unchecked — same level as the pre-existing
generic unpack), and nested unpacks remain guarded.

## J2' arity check landed (2026-06-10 late night, bootstrap-verified)

The recorded "unpack arity mismatch unchecked" limit is closed for the
cpy flat-unpack path: before element extraction, `py_cpy_len(item)` is
compared against the target count — mismatch raises ValueError (tag 2)
through the standard err route; unsized items (len < 0) conservatively
skip the check. Observed: `for a, b in chain([(1,2,3)])` prints
`arity-error` == CPython (try/except ValueError); first probe
mistakenly used `iter([...])` which routes through the DYN-protocol
loop (not the cpy path) — the dyn unpack path's missing arity check is
a SEPARATE pre-existing behavior, recorded not changed. Regression
`test_generator_cpy_flat_unpack_arity_mismatch_raises`; parity 15;
fallback 18; contract 130; five-GC matrix -> 5 passed in 518.21s.
