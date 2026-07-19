# Five-GC self-host bootstrap: regression of session runtime edits + current-source proof

Date: 2026-07-08

Tasks touched (boundary-narrowing, not closure): `AUD-P0-GC-BARRIER-WRITE-AUDIT`,
`AUD-P0-GC-SLOT-VISITOR`, `AUD-P0-RUNTIME-ASSERTS`.

Purpose:
- Regression-check this session's runtime C / pcc-Python edits against the full
  five-backend self-host bootstrap, and refresh the current-source five-backend
  bootstrap proof that the GC P0 boundaries reference.

Session runtime edits under test:
- `AUD-P0-RUNTIME-ASSERTS`: `PCC_RT_TRIPWIRE` macro (inert by default) + 11
  tripwires in py_obj.c / py_gc_backend.c / py_obj_gc.c.
- `AUD-P0-GC-BARRIER-WRITE-AUDIT`: `pcc_gc_note_slot_write_barrier` added to
  `py_set_rehash` (py_set.c) and its pcc-Python mirror (py_set.py).

Gate:
- `gtimeout 560s env -u LC_ALL uv run pytest -q
  tests/python/gc/test_pcc_bootstrap_full_gc0.py .. _gc4.py` (xdist parallel,
  shared stage1) -> `5 passed in 347.61s`.

Result: the full `pcc0 -> pcc1 -> pcc2 -> pcc3` self-host bootstrap passes under
all five `PCC_GC_BACKEND` values with this session's runtime edits in place. The
tripwire additions are behavior-inert in the default build (confirmed), and the
py_set rehash write barrier does not regress any backend's self-host.

Scope / what this does NOT claim:
- This is a regression pass + a fresh current-source five-backend bootstrap
  proof. It does NOT by itself promote the GC P0s to DONE_STRONG: their open
  boundaries are about INCOMPLETE AUDIT / COVERAGE (GC-BARRIER: typed-slot
  inventory still has unproven candidates such as py_list_extend; GC-SLOT-VISITOR:
  broader value-payload slot families and pcc-Python mirror parity), not merely a
  missing bootstrap run. The bootstrap-proof COMPONENT of those boundaries is now
  satisfied for the current source; the audit-completeness component remains.
