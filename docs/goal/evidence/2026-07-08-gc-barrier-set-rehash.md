# GC write-barrier audit — py_set rehash move-store (next slice)

Date: 2026-07-08

Task: `AUD-P0-GC-BARRIER-WRITE-AUDIT` (predecessor:
docs/goal/evidence/2026-07-05-gc-barrier-list-reverse.md)

Scope:
- One conservative, sibling-confirmed write-barrier omission fixed: the hash-set
  rehash move-store. Row stays DONE_WEAK — the typed-slot audit is not complete.

Finding: `py_set_rehash` relocates each live key into a freshly allocated
`entries[]` via a raw move-store and skipped the collector slot barrier, while
its direct sibling `py_dict_rehash` calls `pcc_gc_note_slot_write_barrier`
after the identical raw move-store. Pattern-established omission.

Changed files:
- `pcc/py_runtime/src/py_set.c` (~line 151, in `py_set_rehash`): after the raw
  `s->entries[slot].key = k;` move-store, added
  `pcc_gc_note_slot_write_barrier((PyObject *)s, &s->entries[slot].key, k);`.
- `pcc/py_runtime/py/py_set.py` (mirror): added the `pcc_gc_note_slot_write_barrier`
  extern decl and the matching call after the raw store in `_rehash`, so the C
  and pcc-Python runtimes do not drift (mirrors py_dict.py).
- `tests/python/test_gc_codegen_write_barrier.py`: new
  `test_hash_rehash_move_stores_route_through_slot_write_barrier` (source-guard,
  same style as the existing list_reverse case) asserting the barrier is present
  in all four files (py_dict.c, py_set.c, py_dict.py, py_set.py) + the py_set.py
  extern, documenting the dict<->set sibling so mirrors can't drift.

Correctness: uses `pcc_gc_note_slot_write_barrier` (NOT `pcc_gc_store_ptr`)
because this is a ref MOVE (no incref/decref) — identical to `py_dict_rehash`;
using store_ptr would wrongly incref (leak). Breaks backend #3 (generational:
migrated key's new slot never recorded in remembered-set/SATB -> possible missed
young key -> UAF) and loses #4 store-side tracking; no-op on #0.

Candidates SKIPPED as unproven (recorded so a future slice can revisit, not
silently dropped): py_list.c `py_list_concat`/`copy`/`repeat` (fresh young dest;
also `py_list_new` leaves `items` unzeroed so a naive store_ptr would decref
garbage), `py_list_extend` (strongest unproven — asymmetric with the barriered
`py_list_append`; correct fix is a copy needing NULL-init, a different slice),
`memmove`-based list shifts (intra-object bit moves, loads self-heal on #4),
dict/set delete+clear tombstone stores (not owned-pointer stores).

Gates (owner-run):
- `gtimeout 240s make -B -C pcc/py_runtime libpy_runtime.a` -> exit 0.
- `gtimeout 200s env -u LC_ALL uv run pytest -q -n0
  tests/python/test_gc_codegen_write_barrier.py
  'tests/python/test_gc_backend4_production.py::test_backend4_list_mutations_load_forwarded_item_slots'`
  -> `6 passed`.
- `git diff --check` clean.
- Note: standalone single-module pcc compile of the port was NOT used to verify
  py_set.py (known-bad probe per reference_gc_test_stale_port_archive_trap: it
  links a stale port archive and gives false signals); the port change mirrors
  py_dict.py exactly and the GC test harness builds a fresh runtime.

Result: DONE_WEAK.

Claim: the py_set rehash move-store now routes through the collector slot write
barrier in both the C runtime and the pcc-Python port, matching py_dict_rehash,
with source-guard coverage across all four files.

Open boundary: typed-slot inventory is still incomplete — the four skipped
candidates above (esp. `py_list_extend`) plus the rest of the runtime's owned
pointer-slot writes remain to be inventoried, and each real omission needs
backend-sensitive proof. STRONG proof (five-backend pcc0->pcc1->pcc2->pcc3
bootstrap + a set-specific backend-3/4 forwarding behavior test) is the
remaining tail, not run in this slice.
