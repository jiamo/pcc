# Investigation: Backend 3 frame root slot rewrite

## Status
resolved

## Problem Description
Continue Backend #3 production work from `goal.md` No.8 after
`docs/investigations/gc-backend3-non-list-slot-rewrite.md`.

Remembered owner slots now use slot-aware oldification for C runtime and
pcc-Python runtime mirrors.  Native frame roots are still only used by the
tracing mark cycle (`pcc_gc_seed_roots` / `_seed_roots`), and Backend #3 minor
promotion does not scan frame root slot addresses.  If a live local/root slot
points at a young scalar object, minor refill can promote or mark the object
without rewriting `slots[i]` to the forwarded old copy.

This is the stack/native-frame version of the same OCaml oldification rule:
`refs_docs/gc-research/ocaml/minor_gc.c` scans roots with `oldify_one`, passing
the slot address so the root is updated in place.

Reduced target for this slice: in both `libpy_runtime.a` and
`libpy_runtime_pcc_py.a`, Backend #3 minor collection should rewrite a registered
frame root slot to the forwarded old copy.

## Repro
Run the focused C-runtime gate:

```bash
env -u LC_ALL /opt/homebrew/bin/timeout 900s uv run pytest \
  'tests/test_gc_backend_generational.py::test_generational_backend_minor_refill_rewrites_frame_root_slot_to_oldified_copy' \
  -q -n0
```

Run the focused pcc-Python-runtime gate:

```bash
env -u LC_ALL /opt/homebrew/bin/timeout 900s uv run pytest \
  'tests/test_gc_backend_generational.py::test_generational_backend_pcc_python_runtime_minor_refill_rewrites_frame_root_slot_to_oldified_copy' \
  -q -n0
```

Expected after the fix: each probe prints `1 1 1 0`, meaning a forwarded old
copy exists, `slots[0]` was rewritten to it, `slots[0]` no longer points at the
minor source, and the forwarded copy is not minor-arena backed.

## Test [CONFIRMED]
Observed before the fix:

```bash
env -u LC_ALL /opt/homebrew/bin/timeout 900s uv run pytest \
  'tests/test_gc_backend_generational.py::test_generational_backend_minor_refill_rewrites_frame_root_slot_to_oldified_copy' \
  -q -n0
```

Result: `FAILED` in `3.74s`.  The probe printed
`['0', '1', '0', '1']` instead of `['1', '1', '1', '0']`: no forwarding copy
was installed, `slots[0]` still points at the minor source, and the value is
still minor-arena backed.

```bash
env -u LC_ALL /opt/homebrew/bin/timeout 900s uv run pytest \
  'tests/test_gc_backend_generational.py::test_generational_backend_pcc_python_runtime_minor_refill_rewrites_frame_root_slot_to_oldified_copy' \
  -q -n0
```

Result: `FAILED` in `24.53s`.  The pcc-Python archive probe printed the same
`['0', '1', '0', '1']` result.

During the proposal run the refill helper was strengthened from four short
string allocations to six 96-byte runtime allocations.  The original failure
proved the root-slot rewrite invariant was not met, but the strengthened helper
is the deterministic gate that forces a Backend #3 minor refill before checking
the slot value.

## Proposals
- No.1 Scan frame roots with slot-aware Backend #3 promotion     [CONFIRMED]

## No.1 Scan frame roots with slot-aware Backend #3 promotion
### Code Change
Before Backend #3's object-list young sweep, iterate registered frame root
nodes and call the same slot-aware oldification helper for each root slot
described by the frame map.  Mirror the same logic in
`pcc/py_runtime/py/py_gc_backend.py`.

The frame-root scan does not add to Backend #3's step `processed` count.  Frame
roots can remain registered across many steps, so counting them as progress
would make `gc.collect()` believe every step did useful heap work forever.

This closes native frame roots only.  Suspended generator/coroutine/task frame
objects and scheduler queues remain separate No.10-No.12 work.

### CONFIRMED
Implemented `pcc_gc_promote_frame_roots()` in the C runtime and
`_promote_frame_roots()` in the pcc-Python runtime mirror.  Both scan
registered native frame maps and pass each root slot address through the
Backend #3 slot-aware young oldification helper before the normal object-list
promotion loop.

Focused gates:

```bash
env -u LC_ALL /opt/homebrew/bin/timeout 900s uv run pytest \
  'tests/test_gc_backend_generational.py::test_generational_backend_minor_refill_rewrites_frame_root_slot_to_oldified_copy' \
  -q -n0
```

Result: `1 passed in 3.72s`.

```bash
env -u LC_ALL /opt/homebrew/bin/timeout 900s uv run pytest \
  'tests/test_gc_backend_generational.py::test_generational_backend_pcc_python_runtime_minor_refill_rewrites_frame_root_slot_to_oldified_copy' \
  -q -n0
```

Result: `1 passed in 25.42s`.

Regression for the processed-count edge:

```bash
env -u LC_ALL PCC_GC_BACKEND=3 /opt/homebrew/bin/timeout 900s uv run pytest \
  'tests/test_gc_api.py::test_gc_collect_works_when_disabled' -q -n0
```

Result: `1 passed in 25.98s`.

Broader gates:

```bash
env -u LC_ALL /opt/homebrew/bin/timeout 900s uv run pytest \
  tests/test_gc_backend_generational.py -q -n0
```

Result: `17 passed in 152.42s`.

Runtime archives rebuilt successfully for C runtime, default pcc-Python
runtime, `PCC_WITH_THREADS=1` pcc-Python runtime, and final default pcc-Python
runtime restore.

```bash
env -u LC_ALL PCC_GC_BACKEND=3 /opt/homebrew/bin/timeout 900s uv run pytest \
  tests/test_gc_*.py -q -n0 -rxX
```

Result: `212 passed in 338.64s`.

```bash
env -u LC_ALL /opt/homebrew/bin/timeout 900s uv run pytest \
  tests/test_gc_*.py -q -n0 -rxX
```

Result: `212 passed in 328.98s`.

## Report
Proposal No.1 landed.  Backend #3 now eagerly rewrites registered native frame
root slots to forwarded old copies in both runtime implementations.  This
extends the previous remembered-owner slot rewrite work from heap-owned slots to
active native stack/root-map slots.

This does not close Backend #3 production.  Remaining reference-update work
still includes suspended generator/coroutine/task frames, scheduler queues,
class/borrowed metadata policy, cross-domain remembered-set sharing,
forwarded-minor cleanup, and broader pcc-Python threaded object-index/list
synchronization.
