# pcc1 self-host miscompile: tuple-unpack corrupts codegen `_str_counter`

## Status

**FIXED** (2026-07-03). This investigation now covers two adjacent pcc1
self-host tuple-unpack failures:

1. the earlier class-lifetime / ownership bug, fixed by immortal user class
   objects; and
2. the later pproxy worker BAD_INCREF crash, fixed in frontend owned-local GC
   root / owned-flag bookkeeping by making both ledgers physical-slot aware
   instead of name-only.

The final pproxy root cause is **frontend root lifetime**, not a GC backend
relocation-retirement design failure for this gate. The runtime forwarding
hardening notes below remain useful defense-in-depth work, but the old
"NOT a frontend bug" conclusion is superseded by the 2026-07-03 IR proof and
green five-GC gates recorded later in this file.

## Fix applied [CONFIRMED]

`py_class_new` now marks user class objects `PY_FLAG_IMMORTAL` (offset 12),
in both the pcc-Python port (`pcc/py_runtime/py/py_class.py`) and the C mirror
(`pcc/py_runtime/src/py_class.c`). This is the correct *structural* fix, not a
mere mitigation: instances reference their class through a **borrowed
(uncounted)** class pointer at `inst+16`, so a class's refcount never reflects
its live instances and any stray release can free a class out from under them.
Immortal classes (matching CPython's long-lived static types, and matching the
already-immortal `object` root here) make that safe.

Verified with a fresh pcc1:
- `left, right = "late", 7; return left` and the exact failing-test snippet
  compile + run correctly on `PCC_GC_BACKEND=0/3/4`.
- `py_class_dealloc` is no longer called at all while compiling a tuple-unpack.
- `tests/python/test_pcc1_python_smoke.py` — 51 passed (incl. the target
  `test_pcc1_if_return_then_tuple_unpack_keeps_codegen_function`).

## Superseded analysis: pproxy gc3/gc4 worker double-free

The subsection below is retained as historical evidence from before the
alloca-keyed frontend diagnosis. Its conclusion that the pproxy crash was "NOT
a frontend bug" is wrong for the current tree: later LLDB + IR evidence showed
that re-bound same-name locals could get fresh allocas while sharing stale
compile-time ownership/root metadata, leaving moving-GC slots unhealed.

`tests/python/test_project_python_proxy.py::test_pcc1_runs_project_python_proxy_test_mode_against_local_http`
still fails under `PCC_GC_BACKEND=4` (parallel frontend worker):
`[BAD_INCREF] tag=1 refcount=-1 flags=0` → `py_decref` → trap. Backtrace is the
**same** `_emit_tuple_unpack_assign + 9312` owned-local release as above.

Key discriminator (reproduced by re-running the crashed worker manifest
directly, `pcc1 --pcc-python-multi-codegen-worker worker_3.manifest`):

- `PCC_GC_BACKEND=0`: exit 0 — **clean**.
- `PCC_GC_BACKEND=3` and `=4`: crash (`refcount=-1`, `flags=0`).

So on gc0 the tuple-unpack's owned-local releases are perfectly **balanced**
(no leak, no crash) — the frontend ownership is correct. In the IR, the
released local (`lhs`/`prev`/`elem_ty`) is **frame-rooted** and read through the
`pcc_gc_load_ptr` **relocation barrier** before release — i.e. the frontend does
the right thing. The double-free is **gc3/gc4 (moving-backend) internal**: a
still-rooted object is prematurely freed / its refcount is mis-accounted across
promotion (gc3) / relocation (gc4), so the later balanced release operates on an
already-freed object (its header reads as garbage `tag=1 refcount=-1`).

This is **not** the class-lifetime bug (that was gc0-fatal and B fixed it) and
**not** a frontend over-release (gc0 proves the frontend is balanced). It lives
in the moving-GC relocation/promotion refcount machinery
(`pcc/py_runtime/src/py_gc_backend.c`), which the HEAD commit explicitly calls
out as in-progress ("backend #4 relocation … still open") and which other
in-flight work is actively reworking. Fix belongs there, in coordination with
that work — the frontend needs no change.

### Attempts (both REVERTED — did not fix; recorded so the next person skips them)

A 5-agent workflow ranked two high-confidence candidates; BOTH were applied
(C + pcc-Python port), rebuilt, and tested against the crashed `worker_3`
manifest — NEITHER stopped the gc3/gc4 crash, so the real extra-decref
mechanism is different/deeper than the reasoning:

1. gc4 `pcc_gc_relocate_copy_unlocked`: `if (refcount <= 0) return NULL;`
   (refuse to relocate a dead/transiently-zero source). No effect on the crash
   ⇒ the crash is not "relocate a refcount==0 source".
2. gc3 `pcc_gc_mark_forwarded_source_inactive`: mark the forwarded source
   `PY_FLAG_IMMORTAL` (immortal-shell, like gc4's relocator) so the promoter's
   `py_decref(child)` no-ops. No effect on the crash ⇒ oldify-frees-source is
   not it either (and note the port's `_generational_oldify_copy` is
   backend-3-only, so it isn't even on gc4's path).

Further ground truth from lldb on `worker_3`:
- Crash object is a **heap** block (no static image), header poisoned to
  `refcount=-1, type_tag=1, flags=0`, fields +16..+40 zeroed — a
  freed-once-then-decref'd-again block, not a live singleton.
- A `py_decref` breakpoint conditioned on "non-immortal bool" never fires
  before the crash ⇒ the object is not a normally-live mortal bool being
  released; it is already poisoned when the frontend's balanced release reaches
  it. So the "extra" event is a collection-path free, not a second frontend
  release.
- Could NOT minimize: a standalone `def gen(pairs): for a,b in pairs: yield a+b`
  (matching the crash bt: for-loop tuple-unpack inside a generator) compiles
  CLEAN on gc0/gc3/gc4. The pproxy trigger needs some additional context
  (specific object graph / collection timing) not yet isolated.

### PINNED (free-ledger instrumentation, definitive)

A 2M-entry native free-ledger (records a `backtrace()` at every
`pcc_gc_free_object_memory`, every `pcc_refcount_decref`→0, every
oldify/relocate NEW-copy, and every gc4-barrier fast-path that returns a
refcount<=0 object; reported at `pcc_debug_bad_incref` keyed by the crash
address) shows the crash object is **NOT** in any refcount path (not freed, not
decref'd-to-0, not a relocation/oldify NEW copy) — but IS caught at the gc4
read barrier:

```
pcc_dbg_record_free
pcc_gc_load_ptr + 384                                  <- gc4 fast-path return
user_..._AssignmentStatementLoweringMixin__emit_assign <- frontend owned-local release
..._emit_stmt <- ..._emit_stmts <- ForLoopLoweringMixin__emit_for_obj_iterator <- ...
```

Root, definitively: in `pcc_gc_load_ptr` (py_obj.py / py_obj.c), the gc4
fast-path `if backend==4 and _gc_backend4_should_check_slot(slot)==0: return v`
returns the raw slot value `v` **without resolving forwarding**. Here `v` is a
**stale** pointer: the object it referenced was relocated (old copy marked
IMMORTAL), then the old shell + its forwarding entry were **retired and the
memory reclaimed/reused/zeroed** (so `v` now has refcount<=0, flags=0 — NOT the
immortal shell anymore), while this frame-root/owned-local **slot was never
remapped** to the new copy. `_gc_backend4_should_check_slot(slot)` returns 0
(no live forwarding for `v` → "no resolve needed"), so the barrier hands the
frontend the stale `v`; the frontend's ordinary owned-local `pcc_gc_release`
then `py_decref`s reclaimed memory → refcount 0→-1 → `pcc_debug_bad_incref`
(`type_tag=1 flags=0 refcount=-1`). gc0 is immune (no relocation/forwarding/
retirement, slots are never stale). Reproduces on gc3 and gc4 (both moving).

Fix location (gc4 relocation retirement): the two-epoch forwarding retirement
(`_backend4_remap_and_retire` / `pcc_gc_backend4_remap_and_retire_unlocked`)
races the mutator. The remap heals frame roots + continuation + scheduler roots
+ live-object referents each remap, then epoch-2 REMOVES the forwarding entry
and parks the old shell's page. Once forwarding is removed, `_gc_forwarding_population()`
can reach 0, so the barrier fast-path stops resolving and returns raw slot values.
A slot that re-acquires an old pointer AFTER retirement (read from a heap field
that wasn't healed — e.g. a freeing object skipped by `_remap_referents`, or a
post-remap store) is then never resolvable, and when the old memory is reused it
becomes a UAF. The frontend needs no change (gc0 proves it balanced).

### Fix approaches TRIED and their outcomes (so the next person skips them)

All applied C+port, rebuilt, tested against the crashed `worker_3` manifest:

1. gc4 relocate `refcount<=0` guard in `pcc_gc_relocate_copy_unlocked` — NO effect.
2. gc3 oldify: mark forwarded source `PY_FLAG_IMMORTAL` in
   `_mark_forwarded_source_inactive` — NO effect.
3. Two-epoch park GRACE (double-buffer `parked_head`/`parked_head_prev`, destroy
   pages two remaps late instead of one) — NO effect ⇒ the stale reader holds
   the old pointer for MORE than the park window.
4. `_backend4_drain_parked_pages` = no-op (NEVER destroy parked pages) — STILL
   crashes ⇒ the old memory is reused NOT via page destroy but via zpage
   reset/recycle (span reset + bump-realloc) or gc3 minor-block recycle, so
   quarantining the whole page is insufficient.

Conclusion: this is a **retirement-vs-mutator timing race**, not a one-site bug.
A robust fix needs one of: (a) keep the forwarding entry resolvable (population
> 0) until it is PROVABLY unreferenced (a scan), so the barrier always resolves
old→new — but then old's memory must not be reused either (else a fresh object
at old's address is mis-forwarded); (b) heal ALL sources of old pointers before
epoch-2 (including freeing-object fields and post-remap stores), which the
current remap does not; or (c) a read barrier that does not depend on
`population > 0`. Each is a redesign of the gc4/gc3 relocation-retirement, which
HEAD flags "backend #4 relocation … still open". This is beyond a safe surgical
patch (multiple reuse paths; wrong change silently regresses the currently-green
gc3/gc4 bootstrap). Recommend the relocation-rework owner take it with this
pinned mechanism.

## Follow-up (optional): the frontend still emits a `pcc_gc_release`
that targets the codegen class in `_emit_tuple_unpack_assign`'s owned-local
cleanup (see below) — now benign under immortal classes. Whether it is a true
over-release (borrowed ref released as owned) or a legitimate release the old
model couldn't survive is a separate, lower-priority ownership-audit item; it
lands in `assignment_statement_lowering.py` / ownership lowering (currently
under other in-flight edits — coordinate before touching).

## Root cause — DEFINITIVE (lldb-proven)

While pcc1 compiles a tuple-unpack, `_emit_tuple_unpack_assign` (as compiled
into pcc1) **over-releases an owned-local that holds a reference to the codegen
instance's own class object**, freeing that class. LLDB backtrace of the free
(gc0, deterministic), caught by a watchpoint on `cls+72` (class `n_fields`)
transitioning 119 -> 0:

```
_platform_memset            (zeroes the freed block)
_xzm_free
pcc_gc_free_object_memory
py_class_dealloc            <-- the 119-field codegen class is deallocated
user_py_obj_dealloc__dealloc_dispatch
pcc_dealloc_with_trash
py_decref
pcc_gc_release
AssignmentStatementLoweringMixin._emit_tuple_unpack_assign + 9312   <-- over-release here
_emit_assign <- _emit_stmt ... <- compile_python
```

Chain of consequences:

1. `pcc_gc_release` on that local drives the codegen **class** object's
   refcount to 0 -> `py_class_dealloc` frees it -> the block is `memset` to 0,
   so `cls+72` (`n_fields`) becomes 0 and `cls+80` (`field_names`) becomes NULL.
2. Every later `self.<attr>` on a codegen instance goes through
   `py_instance_getattr` -> `_lookup_field_index(cls, name)`, which now reads
   `n_fields == 0` and returns -1 -> attribute "not found".
3. Concretely, `_emit_str_literal` does `self._str_counter += 1`; the
   `getattr(self, "_str_counter")` now raises `AttributeError("_str_counter")`
   and returns NULL, so `py_obj_add(NULL, 1)` raises
   `TypeError: unsupported operand type(s) for +` (gc0/gc3) — or, under a
   moving backend, `class_lookup_in_mro` dereferences the relocated/stale class
   pointer and **segfaults** (gc4). This is the same crash as the original
   `test_pcc1_if_return_then_tuple_unpack_keeps_codegen_function` failure.

Why it frees a *class* and stays fatal:

- `py_decref` honours `PY_FLAG_IMMORTAL` (`flags & 1` at obj+12) and the base
  `object` root is immortal (`py_class.py` `_object_root`, sets flags=1), but
  **`py_class_new` (the general user-class builder) does NOT set
  `PY_FLAG_IMMORTAL`** — so ordinary/user classes (incl. the pcc-Python codegen
  classes) are mortal and a single stray over-release can free them.

Established facts (all gc0, deterministic, same-run correlated):

- self is a valid codegen instance (`type_tag = 0x17d = 381`) at
  `_emit_tuple_unpack_assign` entry AND at the failing `_emit_str_literal`
  (same pointer).
- Its class pointer `self+0x10` is unchanged (0x…, same at both points) — the
  class OBJECT (not the pointer) is what gets freed/zeroed.
- The `_str_counter` inline value slot (`self+0x2d0`, field idx 87) still holds
  the correct value at the failing point — only the class-side name→field
  resolution breaks.
- The over-releasing store site is `_emit_tuple_unpack_assign + ~9308`
  (`pcc_gc_load_ptr(slot fp-0xa8)` -> `pcc_gc_release`), the owned-local
  cleanup releasing a local sourced from
  `py_tuple_get(py_instance_get_field(X, 2), i)` (a `<node>.elems[i]`-shaped
  field-access + tuple subscript).

## Fix options (tradeoffs)

1. **Correct/minimal**: fix the ownership mis-tracking in
   `_emit_tuple_unpack_assign` so the `<node>.elems[i]` result is treated with
   the right ownership (borrowed vs owned) and not over-released. Requires
   confirming the `py_tuple_get` / `py_instance_get_field` /subscript ownership
   contract and matching the frontend codegen. (This file currently has other
   uncommitted in-flight edits — coordinate.)
2. **Defensive/robustness**: mark class objects `PY_FLAG_IMMORTAL` in
   `py_class_new` (C + pcc-Python mirror). Makes the over-release non-fatal and
   matches CPython's long-lived static types — but changes collectibility of
   dynamically-created user classes (a GC-semantics change; weigh against the
   north star). Best as defense-in-depth *alongside* (1), not instead of it.

## Symptom

`tests/python/test_pcc1_python_smoke.py::test_pcc1_if_return_then_tuple_unpack_keeps_codegen_function`
is red. The test compiles a tuple-unpack program with **pcc1 running under
`PCC_GC_BACKEND=4`** and expects `late:7` / `early`.

## Test [CONFIRMED]

Minimal repro (`f` contains a tuple-unpack):

```python
def f():
    left, right = "late", 7
    return left
def main() -> None:
    print(f())
if __name__ == "__main__":
    main()
```

Compiling this **with pcc1** (`build/bootstrap-*/pcc1 u2.py --backend self
--python-libpython=off --ir-scaffold=on`):

- `PCC_GC_BACKEND=0` (refcount, non-moving): exit 1,
  `PCC-PY-COMPILE-001: [python-frontend] unsupported operand type(s) for +`.
- `PCC_GC_BACKEND=3` (generational): same `unsupported operand ... for +`.
- `PCC_GC_BACKEND=4` (relocating): **segfault (139)**.

Compiling the **same file with host pcc0** (`uv run pcc ...`): exit 0, runs,
prints `late`. So the frontend *source* is correct — pcc1 (the self-compiled
compiler) diverges from pcc0.

The trigger is the tuple-unpack statement itself, independent of the RHS/body:
`left = "late"; return left + ":"` (no unpack) compiles fine under pcc1; every
tuple-unpack form fails — safe-fresh names (`a, b = "late", 7`), non-safe-fresh
(pre-declared targets), and subscript targets (`d["a"], d["b"] = ...`) all
produce the identical `unsupported operand ... for +` on gc0.

## Root cause (localized)

`unsupported operand type(s) for +` is raised by the runtime `py_obj_add`
(`pcc/py_runtime/py/py_obj_ops_dispatch.py:456`) when an operand is **NULL**.
LLDB backtrace of the failing add (gc0, deterministic):

```
py_obj_add(a=0x0, b=tagged-1)
  <- py_obj_inplace_op                       # self._str_counter += 1
  <- LiteralLoweringMixin._emit_str_literal  (literal_lowering.py:46)
  <- ExprDispatchLoweringMixin._emit_expr_impl
  <- AssignmentStatementLoweringMixin._emit_tuple_unpack_assign
  <- ..._emit_assign <- _emit_stmt ... <- compile_python
```

So while pcc1 runs `_emit_str_literal`, `self._str_counter` reads **NULL**, and
`self._str_counter += 1` → `py_obj_add(NULL, 1)` → the TypeError. Under gc4 the
same stale/garbage read segfaults instead.

Facts established by LLDB (all under gc0, deterministic):

- At `_emit_tuple_unpack_assign` **entry**, `self` is valid
  (`type_tag = 0x17d = 381`, the codegen class) and
  `py_obj_getattr(self, "_str_counter") == 0x1` (tagged int 0 — valid).
- `self` is the **same object** in the nested `_emit_str_literal` call
  (pointer identical; not a wrong-`self`/mis-dispatch bug).
- `_str_counter` lives inline at **`self + 0x2d0`** (found via a
  `py_obj_setattr` marker probe).
- The *first* `_emit_str_literal` reached after tuple-unpack entry still sees
  `_str_counter == 0x1`; a **later** `_emit_str_literal` sees NULL. So a NULL is
  written to (this object's, or an aliased) `_str_counter` slot mid-flow.
- A watchpoint on `self+0x2d0` conditioned on `== 0` did **not** fire before the
  failing read — i.e. the NULL that the failing `getattr` returns is **not** a
  plain store to *this* self's `+0x2d0` slot. Candidate mechanisms not yet
  ruled out: heap corruption of the backing attr storage, or the failing read
  resolving `_str_counter` through a different/aliased object/attrs region.

## What it is NOT

- **Not** the method-param GC-root gap. `_emit_method_body` (class_gen.py) does
  not GC-root method `self`/object params the way `_emit_user_function` does —
  that is a real gap for moving backends — but adding those roots does **not**
  change this test's outcome (fails identically with and without). Kept out of
  the tree for now; worth a *separate* focused fix + gc3/gc4 validation +
  regression test.
- **Not** GC-relocation-specific: gc0 (non-moving) reproduces deterministically.
- **Not** the nested-call-as-argument shape:
  `_store_unpack_target(t, self._emit_expr(elem), ty)` hoisted into a local
  first still fails identically.

## Related in-flight work (do not clobber)

At the time of writing the working tree has **uncommitted** changes (another
agent/user) across `pcc/py_frontend/codegen/class_gen.py`,
`user_function_lowering.py`, and `assignment_statement_lowering.py` that target
the same self-host mislowering class — `_current_entry_block` save/restore
around function/method emission, dict-subclass runtime methods
(`_DICT_SUBCLASS_RUNTIME_METHODS`, `_class_subclasses_dict`), and a
`self.parent.env.pop(...)` → `del` rewrite (`.pop()` mis-lowers under pcc1).
This bug **persists with those changes present** — they are necessary but not
sufficient. Coordinate before editing these files.

## Next steps

1. Watchpoint the backing attr storage (not the inline `+0x2d0` slot) or catch
   the NULL write with a hardware watchpoint on the resolved storage address of
   `_str_counter` at the *failing* `_emit_str_literal`, then `bt` the writer.
2. Confirm whether the failing read resolves `_str_counter` on the same self or
   an aliased attrs region (the `+0x2d0` watchpoint not firing suggests the
   latter).
3. Bisect via CPython idiom-diff: find the exact frontend construct executed
   between tuple-unpack entry and the failing `_emit_str_literal` that pcc1
   lowers with a stray NULL store / bad free.

## Update 2026-07-03 — ROOT CAUSE FOUND AND FIXED (codegen root lifetime, not runtime)

The pinned "barrier fast-path returns stale pointer" mechanism was the
*runtime-visible* half. The actual defect was in the FRONTEND's GC-root
bookkeeping, found via `PCC_DEBUG_BAD_BACKTRACE=1` + lldb at the BAD_INCREF
trap and confirmed at IR level:

```text
crash chain: _emit_tuple_unpack_assign -> pcc_gc_release -> py_decref (stale)
stale value sat in alloca fp-0xa8; lldb walk of pcc_gc_frame_head showed the
alloca NOT registered (23,585 other slots of the same frame were).
IR proof (module dump, function _emit_tuple_unpack_assign):
  %lhs.addr.30  = alloca ptr   ; for-loop unpack binding — frame-entered
  %lhs.addr.156 = alloca ptr   ; scan-loop re-bind      — 5 frame_leave, 0 frame_enter
```

**Mechanism.** GC root bookkeeping (`_gc_rooted_local_names`) was NAME-keyed
while physical `pcc_gc_frame_enter` registration is ALLOCA-keyed. When a local
name is re-bound to a fresh alloca inside one function (comprehension/scope
env save-restore pops the env entry; a later assignment re-creates the slot),
`_ensure_owned_local_gc_root` early-returned on the name: the new alloca was
owned-flag managed (release-on-rebind emitted) but **never frame-registered**.
Moving backends (#3 generational, #4 colored-relocating) heal only registered
slots during relocation remap, so after two-epoch forwarding retirement the
flag-guarded release decref'd a stale pre-relocation pointer into reused
memory (`refcount=-1 type_tag=1 flags=0`). gc0/1/2 never move, hence
gc3+gc4-only. This is why every runtime-side attempt (park grace, never
destroy pages, refcount guards) failed: the unhealed slot is invisible to the
runtime by construction.

**Fix (frontend, per-slot root ledger):**

- `ownership_lowering._ensure_local_gc_frame_root`: registration deduped per
  `(function, alloca)` via `_fn_gc_root_slot_registry` (object identity —
  see drift note below), never per name. A re-bound name's second alloca now
  gets its own entry-block `frame_enter`.
- `_discard_owned_local_gc_root`: compile-time bookkeeping only; no more
  mid-function `frame_leave` (entry-block slots are function-lifetime and
  object-or-null, so staying registered is safe; unregistering mid-loop left
  later iterations unrooted).
- `_emit_owned_local_cleanup`: root leaves driven by the per-slot registry
  (newest-first, once per slot) instead of name→env loops, and each cleanup
  block is recorded in `_fn_gc_root_exit_sites`.
- `_ensure_local_gc_frame_root` retro-patches a newly registered slot's leave
  into every already-emitted exit site, and
  `exception_lowering._ensure_fn_err_exit` back-patches all registry slots
  when the err block is created late — entry enters always run, so every exit
  leaves every slot (the new regression test enforces both invariants).
- `literal_lowering` persistent-thread container temp roots now route through
  the same registry.

**Two follow-on defects the fix flushed out (both fixed):**

1. *Self-backend fixup overflow*: the extra balanced leaves grew huge
   functions past the ±32KB `cbz` range that `_thread_trampoline_branches`
   created by threading a short trampoline hop into a direct far branch
   ("fixup value out of range"). `self_backend_aarch64_darwin.py` now range-
   guards trampoline threading (cbz/cbnz ≤6000 lines, b.cond ≤200000) and
   `_fold_cond_branch_to_fallthrough` (b → b.cond narrows to ±1MB) — far
   targets keep the trampoline.
2. *pcc2/pcc3 byte drift*: the first registry keyed dedup on the alloca's
   VALUE NAME string; name-uniquification timing differs between the host
   compiler and the self-hosted stages, so pcc1 emitted a different
   enter/leave count than pcc0/pcc2 (single drifting symbol:
   `literal_lowering.__emit_cpython_list_ops`, +64B). All dedup is now object
   IDENTITY (`is` scans over small per-function lists) — never value-name
   strings, never `id()` strings.

**Second failure in the same gate (separate bug, also fixed):** with gc3/gc4
no longer crashing, all backends hit
`self backend expected pointer value 'st.addr.117' in
'user_pproxy_verbose__native_lambda_2'` — pproxy/verbose.py:58's
`lambda s: [st.__setitem__(i, st[i] + s) for st in tostat]`. The three native
lambda emitters (`lambda_helpers_lowering._maybe_emit_native_lambda_func`, the
trampoline variant, `lambda_callback_lowering`) swap builder/env/
current_function but did not swap `_current_entry_block`, so the
comprehension target's `_alloca_in_entry` landed in the ENCLOSING function
(cross-function alloca reference; alloca proven to live in
`user_pproxy_verbose___nested_modstat`). All three emitters now save/set/
restore `_current_entry_block`.

**Regression homes:**
- `tests/python/test_gc_root_rebound_local.py` — rebound-local per-slot
  enter/leave balance invariants (fails pre-fix).
- `tests/python/test_native_lambda_comprehension_alloca.py` — lambda-body
  comprehension allocas stay in the lambda (fails pre-fix with the
  BackendUnavailable error).
- `tests/python/test_project_python_proxy.py` pproxy gate now parametrized
  over `PCC_GC_BACKEND` 3 and 4.

**Gates run (all green, clean caches):** worker_3 manifest gc0/3/4 (×2);
pproxy pytest gc3+gc4; full three-stage bootstrap gc0/gc1/gc2/gc3/gc4
(pcc2==pcc3 byte-identical); fallback + ir-py fallback baselines (18);
multi-file compile + bootstrap shim (107); llvm_capi parity (24).
`test_bootstrap_gate_baseline.py` remains SKIPPED for the pre-existing reason
(no populated `build/bootstrap-self` / `build/bootstrap-llvm` trees) — same
skip state as before this fix.

## Addendum 2026-07-03 — owned flags also must be alloca-keyed

A follow-up pproxy matrix still crashed after the root registry became
alloca-keyed. LLDB on the failing worker showed `pcc_gc_release -> py_decref`
on a low 32-bit-looking stale value from `_emit_tuple_unpack_assign`.
The self-hosted IR then showed the final bug:

```text
%lhs.addr.30  = alloca ptr
%lhs.addr.161 = alloca ptr
%lhs.owned.31 = alloca i1   ; shared by both same-name physical slots
```

The second `lhs` alloca had its own storage, but the name-keyed owned flag was
reused from the first `lhs`. A true flag from the old slot could therefore
guard a release of the fresh slot before that slot had a corresponding owned
value, producing the BAD_INCREF / stale-release shape. The frontend fix is to
track `_owned_local_flag_allocas` beside `_owned_local_flag_slots`; a flag is
reused only for the same physical alloca, and same-name re-bound locals get a
fresh false-initialized owned flag.

Additional verified gates after this addendum:

- `tests/python/test_gc_root_rebound_local.py` -> 2 passed.
- Direct full `projects/python-proxy/pproxy/cipherpy.py` serial compile with
  fresh `build/bootstrap-pytest-self/pcc1`: `PCC_GC_BACKEND=0..4` all exit 0.
- Old failing `worker_1.manifest` and `worker_3.manifest` rerun with the fresh
  pcc1: `PCC_GC_BACKEND=0..4` all exit 0.
- `tests/python/test_project_python_proxy.py::test_pcc1_runs_project_python_proxy_test_mode_against_local_http`
  with current pcc1 -> 2 passed (the test parametrizes gc3/gc4).
- Manual pproxy helper matrix with the same gate shape: gc0/gc1/gc2/gc3/gc4
  all passed.
- Broader focused frontend/backend batch -> 129 passed, 4 skipped.
- Fresh `scripts/bootstrap.sh --backend self --out-dir build/bootstrap-pytest-self --stage 3`
  succeeded; pcc2/pcc3 differed only in Mach-O code-signature / LC_UUID
  metadata and were metadata-normalized byte-identical.
- Full gc1..gc4 bootstrap pytest files -> 4 passed in 553.18s.

Recheck on 2026-07-03T10:10+08:00, after a user-provided LLDB transcript
pointed at another attempted low-pointer `pcc_gc_release` catch:

- `tests/python/test_gc_root_rebound_local.py` -> 2 passed.
- `tests/python/test_native_lambda_comprehension_alloca.py` -> 1 passed.
- Direct `projects/python-proxy/pproxy/cipherpy.py` compile with current
  `build/bootstrap-pytest-self/pcc1`: `PCC_GC_BACKEND=0..4` all exit 0.
- `tests/python/test_project_python_proxy.py::test_pcc1_runs_project_python_proxy_test_mode_against_local_http`
  -> 2 passed for gc3/gc4 when `PCC_CURRENT_PCC1` is absolute. A relative
  `PCC_CURRENT_PCC1=build/bootstrap-pytest-self/pcc1` false-fails because
  the test runs the pcc1 subprocess with `cwd=projects/python-proxy`.
- `tests/python/gc/test_pcc_bootstrap_full_gc3.py` and
  `tests/python/gc/test_pcc_bootstrap_full_gc4.py` -> 2 passed in 292.12s.

**Runtime hardening follow-up (recommended, not landed here):** the review
design for gc3/gc4 forwarding lifetime remains valid defense-in-depth —
no-deref relocation-candidate checks in `pcc_gc_release`/`pcc_gc_store_ptr`/
`pcc_gc_store_root`/`_remap_heal_slot` (forwarding-index lookup before any
header read; unknown+unforwarded → skip), plus zpage/minor-block reuse
quarantine gated on live forwarding. Those close the remaining ABA window for
any FUTURE unrooted-pointer bug, but each needs C+port mirroring and its own
5-GC gate pass; land as a separate change.

## Status

FIXED (2026-07-03). Root cause: name-keyed frontend bookkeeping for physical
owned-local storage: first GC-root registration, then owned-flag reuse. Moving
backends could not heal unregistered slots, and shared owned flags could later
release the wrong same-name re-bound slot. Fixed in ownership/exception/
literal/lambda lowering plus self-backend branch-range guards. The pproxy gate
(`test_pcc1_runs_project_python_proxy_test_mode_against_local_http`) passes
on gc3 and gc4; direct worker and manual helper matrices pass on gc0..gc4; full
bootstrap fixed point is green on all five GC backends.
