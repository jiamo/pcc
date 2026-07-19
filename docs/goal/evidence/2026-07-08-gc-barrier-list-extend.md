# GC write-barrier audit — py_list_extend fast-path stores (next slice)

Date: 2026-07-08

Task: `AUD-P0-GC-BARRIER-WRITE-AUDIT` (predecessors:
2026-07-05-gc-barrier-list-reverse.md, 2026-07-08-gc-barrier-set-rehash.md)

Scope: fix the `py_list_extend` owned-slot store omission, matching the proven
`py_list_append` idiom. Row stays DONE_WEAK.

Finding (confirmed real omission): both `py_list_extend` fast paths (list-source
and tuple-source) did `pcc_gc_load_ptr` (borrowed) + `py_incref(v)` + a raw
unbarriered `la->items[la->length++] = v;`, while sibling `py_list_append`
routes its append store through `NULL`-init + `pcc_gc_store_ptr`. The iterator
fallback already delegates to `py_list_append` and was left untouched.

Fix (C `pcc/py_runtime/src/py_list.c` + pcc-Python mirror
`pcc/py_runtime/py/py_list.py`, both fast paths): drop the manual `py_incref`,
NULL-init the fresh capacity slot, then `pcc_gc_store_ptr(a, &la->items[...], v)`
— identical to append.

Refcount accounting proof (no double-incref, no decref-of-garbage):
- Before: load = borrowed (0), `py_incref` (+1), raw store -> net +1.
- After: load = borrowed (0), NULL-init, `pcc_gc_store_ptr` = incref(+1) +
  decref(old=NULL, no-op) -> net +1. Identical; manual incref dropped in all
  four branches. NULL-init before the barriered store means the internal
  `decref(old)` never touches the unzeroed-`items` garbage (the py_list_new
  hazard the prior slice flagged).

Test: `test_list_extend_element_store_matches_append_slot_write_barrier` added to
`tests/python/test_gc_codegen_write_barrier.py` (source-guard, both files):
append template present, extend NULL-inits + barrier-stores in both fast paths,
old raw store + double-incref gone.

Gates (owner-run):
- `gtimeout 240s make -B -C pcc/py_runtime libpy_runtime.a` -> exit 0.
- `gtimeout 200s env -u LC_ALL uv run pytest -q -n0
  tests/python/test_gc_codegen_write_barrier.py
  'tests/python/test_gc_backend4_production.py::test_backend4_list_mutations_load_forwarded_item_slots'`
  -> `7 passed`.
- Five-backend self-host bootstrap regression:
  `pytest -q tests/python/gc/test_pcc_bootstrap_full_gc0..4.py` -> `5 passed in
  354s` (py_list_extend + py_set_rehash barrier changes do not regress the
  self-host on any backend; refcount accounting confirmed correct — no
  leak/UAF/crash). `git diff --check` clean.

Result: DONE_WEAK.

Claim: py_list_extend's fast-path element stores now route through the collector
slot barrier in both the C runtime and the pcc-Python port, matching
py_list_append, verified regression-free across all five GC backends' full
self-host bootstrap.

## Update 2026-07-08 — py_list.c owned-slot store surface fully classified

Completed the classification of EVERY owned-pointer-slot store in
`pcc/py_runtime/src/py_list.c` by reading each site (no code change — this is the
documented backend-sensitive reasoning the prior slice deferred):

- BARRIERED (store into a possibly-OLD existing list -> old->young edge needs
  remembered-set on #3): `py_list_append`, `py_list_extend` (this slice),
  `py_list_reverse` (prior). Correct.
- PROVEN SAFE — raw store into a FRESH young local list (the only `items[...]=v`
  value stores are lines 316,321 concat / 342 repeat / 363 copy / 403,414
  slice): each destination is a `py_list_new(...)` result that has NOT escaped,
  so it is always YOUNG and cannot hold an old->young edge; #3 minor collection
  always scans young objects; the loaded `v` is `pcc_gc_load_ptr`-resolved and
  incref'd (definitely reachable). A raw store is not just safe but REQUIRED
  here: the fresh slots are unzeroed capacity, so a `pcc_gc_store_ptr` would
  `decref` garbage. No barrier needed, no omission.
- PROVEN SAFE — intra-object `memmove` shifts (lines 176,191,446,591,628,648:
  insert/pop/remove/delete_index/delete_range/set_slice): these relocate already
  tracked pointer bits within the same list; they reference no new object and
  create no new cross-gen edge, and #4 stale pointers self-heal on the existing
  load-barrier-on-read model. No barrier needed, no omission.
- NULL clears (the `items[...]=NULL` sites): not owned-pointer value stores.

So the `py_list.c` container mutation surface is now COMPLETE: every owned
pointer-slot store is either barriered (container may be old) or a documented
proven-safe non-omission (fresh-young-list build / intra-object move). Combined
with `py_set` (rehash barriered this session) and `py_dict` (already barriered),
the list/dict/set container mutation surface is fully accounted for.

## Update 2026-07-08 — item (b) done: backend-3/4 forwarding behavior tests

Added 4 focused forwarding-behavior tests proving the barriers are LOAD-BEARING
(would fail if the barrier were absent), verified per-backend by the owner:
- `tests/python/test_gc_backend4_production.py::test_backend4_set_rehash_old_to_young_uses_store_barrier`
  — OLD set + young keys promoted, one more young key triggers rehash; asserts
  `GENZGC_STORE_BARRIERS == 2` (insert + rehash-move) — would be 1 without the
  rehash barrier — plus all 6 keys survive a store-buffer drain.
- `...::test_backend4_list_extend_old_to_young_uses_store_barrier` — OLD list
  extended with 6 young tracked elements; extend is the sole store, so
  store_buffer==6 / GENZGC_STORE_BARRIERS==6 / owner REMEMBERED are all zero
  without the barrier; elements survive a drain.
- `tests/python/test_gc_backend3_barrier_behavior.py::test_backend3_list_extend_old_to_young_remembers_owner`
  — asserts the extend barrier sets PY_FLAG_GC_REMEMBERED on the OLD list (#3
  owner-granularity), preserved across a minor cycle.
- `...::test_backend3_set_rehash_old_to_young_preserves_keys` — end-to-end (the
  test honestly documents that #3's remembered-owner is idempotent so the
  rehash-specific barrier is redundant ON #3, unlike #4).
Owner gates: `PCC_GC_BACKEND=4 ... -> 2 passed`; `PCC_GC_BACKEND=3 ... -> 2 passed`.
Subtlety baked in: elements must be NON-LEAF (young lists/tuples) — leaf str/int
are not graph-tracked so the barrier skips them (a naive test would show 0).

Open boundary: item (b) is DONE. The ONLY remaining item for DONE_STRONG is the
audit SCOPE decision (a): this thread has fully classified + behavior-tested the
list/dict/set CONTAINER mutation surface, but the row title "exact pointer-slot
audit" may also intend non-container runtime owned slots (instance field/__dict__
stores, class metadata, tuple build, exception/generator slots), which have NOT
been swept. If the scope is container-only -> ready for DONE_STRONG; if it is the
whole runtime -> the non-container pointer-slot sweep remains (a larger slice).
This scope call is a task-owner decision, not something to self-narrow.
