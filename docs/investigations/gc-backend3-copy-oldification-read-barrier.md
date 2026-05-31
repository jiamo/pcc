# Investigation: Backend 3 copy oldification read barrier

## Status
resolved

## Problem Description
Continue Backend #3 production work from `goal.md` No.8.  Backend #3 currently
promotes remembered young children by flipping `PY_FLAG_GC_YOUNG` to
`PY_FLAG_GC_OLD` in place.  That is not OCaml-style oldification: a minor-heap
object remains in the minor arena instead of being copied into old/major
storage, and references are not updated through a forwarding pointer.

Reduced target for this slice: when an old list stores a young integer child
and minor arena pressure triggers a minor collection, Backend #3 should create
an old non-minor copy for the remembered child, install a forwarding record from
the minor object, and let `pcc_gc_load_ptr()` lazily update the owner slot to
the old copy.

## Repro
Run the focused C-runtime Backend #3 gate:

```bash
env -u LC_ALL /opt/homebrew/bin/timeout 420s uv run pytest \
  'tests/test_gc_backend_generational.py::test_generational_backend_minor_refill_oldifies_copy_for_remembered_child' \
  -q -n0
```

Expected after the fix: the probe prints `1 1 1 1 0`, meaning a forwarding
copy exists, `pcc_gc_load_ptr()` returns and stores the forwarded object, the
copy is old, and the copy is not minor-arena-backed.

## Test [CONFIRMED]
Observed before the fix:

```bash
env -u LC_ALL /opt/homebrew/bin/timeout 420s uv run pytest \
  'tests/test_gc_backend_generational.py::test_generational_backend_minor_refill_oldifies_copy_for_remembered_child' \
  -q -n0
```

Result: `FAILED` in `3.37s`.  The probe printed `['0', '1', '1', '1', '1']`
instead of `['1', '1', '1', '1', '0']`: `pcc_gc_note_relocation_read(child)`
returned the original child, `pcc_gc_load_ptr()` left the owner slot on that
same original child, and the object was merely marked old while still carrying
`PY_FLAG_GC_MINOR_ARENA`.

## Proposals
- No.1 Copy oldify remembered young children through forwarding/read barrier     [CONFIRMED]

## No.1 Copy oldify remembered young children through forwarding/read barrier
### Code Change
Reuse the existing forwarding/read-barrier substrate to oldify simple
remembered young objects for Backend #3.  This first slice should focus on
supported scalar payloads already covered by the relocation copy surface
(`int`, `float`, `str`, `complex`, `bytes`, `bytearray`) and lazy pointer
updates through `pcc_gc_load_ptr()`.

Landed change:

- `pcc_gc_install_forwarding()` now accepts Backend #3 as well as Backend #4.
- Backend #3 remembered-set scanning now handles remembered owners before the
  generic young-object promotion pass.
- Supported remembered young scalar objects are copied into non-minor old
  storage, registered in the object list, and linked from the minor object
  through a forwarding entry.
- `pcc_gc_load_ptr()` now runs the relocation read barrier for Backend #3 and
  Backend #4, so old container slots lazily update from the minor source object
  to the oldified copy.

This is not full Backend #3 production.  Full OCaml-style oldification still
requires eager slot rewriting for all traced containers/root slots,
cross-domain remembered-set sharing, pcc-Python parity, and a cleanup policy
for forwarded minor objects.

### CONFIRMED
The focused gate now passes:

```bash
env -u LC_ALL /opt/homebrew/bin/timeout 420s uv run pytest \
  'tests/test_gc_backend_generational.py::test_generational_backend_minor_refill_oldifies_copy_for_remembered_child' \
  -q -n0
```

Result: `1 passed in 3.52s`.

Broader regression gates also passed:

```bash
env -u LC_ALL /opt/homebrew/bin/timeout 900s uv run pytest \
  tests/test_gc_backend_generational.py -q -n0
# 10 passed in 61.94s

env -u LC_ALL PCC_GC_BACKEND=3 /opt/homebrew/bin/timeout 900s uv run pytest \
  tests/test_gc_*.py -q -n0 -rxX
# 205 passed in 225.36s

env -u LC_ALL /opt/homebrew/bin/timeout 900s uv run pytest \
  tests/test_gc_*.py -q -n0 -rxX
# 205 passed in 205.60s
```

C runtime and pcc-Python runtime archive rebuilds passed.  One intermediate
Backend #3 full-suite run failed because the repository
`libpy_runtime_pcc_py.a` had just been rebuilt with `PCC_WITH_THREADS=1`, making
`test_threading_substrate_runs_in_no_libpython_binary` observe
`pcc_threads_enabled() == 1`.  Rebuilding the default pcc-Python archive without
`PCC_WITH_THREADS=1` restored the expected no-thread default and the full suite
then passed.

## Report (only when the investigation is closing)
Proposal No.1 landed.  Backend #3 now has a narrow C-runtime oldification slice:
remembered young scalar children can be copied out of the minor arena, followed
through a forwarding record, and lazily written back into the old owner slot by
`pcc_gc_load_ptr()`.

This closes the specific failure captured by this investigation.  It does not
complete Backend #3 production: pcc-Python runtime parity, eager reference
rewriting for all traced slots, suspended-frame/scheduler queue updates,
cross-domain remembered-set sharing, and forwarded-minor cleanup policy remain
separate goal items.
