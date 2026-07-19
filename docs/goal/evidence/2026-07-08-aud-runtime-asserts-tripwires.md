# Production-safe runtime assertion tripwires (first slice)

Date: 2026-07-08

Task: `AUD-P0-RUNTIME-ASSERTS`

Scope:
- Added a production-safe C-kernel tripwire macro and a first, small set of
  collector / object-graph invariant checks. C-kernel safety only (no Python
  semantics), so no pcc-Python port mirror is required.
- Incremental hardening slice; the open boundary (tripwires still sparse) stays.

Changed files:
- `pcc/py_runtime/src/py_internal.h` — `PCC_RT_TRIPWIRE(cond, msg)` macro +
  `pcc_runtime_tripwire_fail` declaration.
- `pcc/py_runtime/src/pcc_runtime_log.c` — fatal sink `pcc_runtime_tripwire_fail`
  reusing the existing `pcc_runtime_log_event` entrypoint, then `abort()`.
- `pcc/py_runtime/src/py_obj.c` — 2 refcount tripwires in `py_decref`.
- `pcc/py_runtime/src/py_gc_backend.c` — 1 header-tag-sanity tripwire in
  `py_obj_visit_slots`.
- `pcc/py_runtime/src/py_obj_gc.c` — 1 node-integrity tripwire in
  `py_gc_recompute_reachability`.

Design (production-safe):
- The macro is gated by `PCC_RUNTIME_TRIPWIRES`. When undefined (the default /
  production build) it expands to `((void)0)` — `cond` is not evaluated, zero
  cost, zero behavior change. The only always-present addition is the unused
  (in default build) sink function.
- Tripwire sites and invariants: `py_decref` refcount > 0 before decrement and
  new refcount >= 0 after (catches decref-of-dead and the threaded double-free
  race); `py_obj_visit_slots` type_tag >= 0 (corrupt header); cycle-collector
  node `obj != NULL` before deref. Deliberately NO `refcount > 0` on the incref
  path, because tracing backends (tricolor / CMS) legitimately keep refcount-0
  objects alive via marking.

Gates (owner-run builds; both succeeded with `-Wall -Wextra`, no errors):
- Required: `gtimeout 240s make -B -C pcc/py_runtime libpy_runtime.a`
  (default, tripwires OFF) -> exit 0.
- Additional (proves the guarded expressions actually compile, since the OFF
  build does not compile them): `CPPFLAGS='-DPCC_RUNTIME_TRIPWIRES' gtimeout 240s
  make -B -C pcc/py_runtime libpy_runtime.a` -> exit 0.
- Working-tree archive restored to the default (tripwires-off) production build
  after verification.

Result: DONE_WEAK.

Claim: a production-safe tripwire mechanism exists and a first set of 4
collector/object-graph invariant checks compile in both the default (inert) and
`-DPCC_RUNTIME_TRIPWIRES` (active) configurations; the default runtime archive
build is unchanged.

## Update 2026-07-08 — second batch (7 more tripwires, 11 total)

Added 7 more production-safe tripwires (same inert-by-default macro), covering
relocation/forwarding, generational promotion, and remembered-set invariants:
- `py_gc_backend.c` (6): instance owner `cls` has type_tag PY_TYPE_CLASS before
  the n_fields slot walk (directly catches the recurring freed/over-released
  class -> n_fields 119->0 corruption); backend-4 relocation read-barrier
  source/target type_tag match; backend-4 remap heal-slot old/new type_tag match
  before `*slot = to`; two young->old promotion guards (PY_FLAG_GC_OLD clear
  before set) at both promotion write points; backend-3 remembered-owner
  non-NULL before deref.
- `py_obj_gc.c` (1): cycle-reachability node obj is not a tagged int before the
  `py_header(obj)->refcount` deref (pairs with the first-slice NULL guard).
Four invariants were deliberately SKIPPED as not universally true at their site
(zpage zombie-at-alloc, relocation-read UNKNOWN path, numeric n_fields bound,
index-table must-hit) — recorded to avoid false trips.

Gates (owner-run; both exit 0):
- `gtimeout 240s make -B -C pcc/py_runtime libpy_runtime.a` (default, OFF) -> 0.
- `CPPFLAGS='-DPCC_RUNTIME_TRIPWIRES' gtimeout 240s make -B ...` (armed, compiles
  all 11 guarded conditions) -> 0.
- Working-tree archive restored to the default (tripwires-off) build.
`git diff --check` clean.

Open boundary: collector and runtime object-graph tripwires are still sparse —
this is now 11 checks at the decref / slot-visit / cycle-reachability /
relocation-forwarding / generational-promotion / remembered-set hot spots.
Broader coverage (relocation/forwarding barriers, per-backend generational and
zpage invariants, scheduler/continuation roots, native-handle lifetime) and any
runtime behavior of the enabled build under a fault-injection test remain open.
The enabled build is opt-in via `-DPCC_RUNTIME_TRIPWIRES` (no Makefile default
change), with `PCC_LOG=runtime` surfacing the failure message before `abort()`.
