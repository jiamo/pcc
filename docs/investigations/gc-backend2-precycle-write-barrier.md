# Investigation: Backend #2 write barrier starts mark before root seeding

## Status
resolved

## Problem Description
`PCC_GC_BACKEND=2` fails live-root preservation and related finalizer /
resurrection tests even though Backend #1 now passes the same GC suite. The
likely cause is Backend #2's unconditional store barrier: it grays any white
stored value and sets `pcc_gc_mark_active = 1` even when no mark cycle has
begun. The next tracing step then skips `_begin_mark_cycle()`, so frame/module
roots are never seeded before white objects are cut into sweep candidates.

## Repro
Focused failure:

```bash
env -u LC_ALL PCC_GC_BACKEND=2 /opt/homebrew/bin/timeout 180s uv run pytest 'tests/test_gc_effectiveness.py::test_collect_preserves_root_reachable_subgraph' -q -n0
```

Expected after the fix: the test passes, preserving `live_root[0] is keep`
while still reclaiming the dead cycle.

## Test [CONFIRMED]
Observed failing before the fix:

```bash
env -u LC_ALL PCC_GC_BACKEND=2 /opt/homebrew/bin/timeout 180s uv run pytest 'tests/test_gc_effectiveness.py::test_collect_preserves_root_reachable_subgraph' -q -n0
```

Result: `FAILED`; output was `False`, `True` instead of `True`, `True`.

Broader baseline before this proposal:

```bash
env -u LC_ALL PCC_GC_BACKEND=2 /opt/homebrew/bin/timeout 700s uv run pytest tests/test_gc_*.py -q -n0 -rxX
```

Result: `188 passed, 11 failed in 168.88s`.

## Proposals
- No.1 Gate Backend #2 write barrier on an active mark cycle     [CONFIRMED]

## No.1 Gate Backend #2 write barrier on an active mark cycle
### Code Change
For Backend #2, shade stored white values only when `pcc_gc_mark_active != 0`.
The concurrent marker still needs the barrier during an active cycle, but a
store before root seeding must not force the collector to skip `_begin_mark_cycle()`.
Mirror the same rule in the pcc-Python runtime backend.

### CONFIRMED
Backend #2 now only shades through the store barrier when a mark cycle is
already active. The same condition is mirrored in `py/py_gc_backend.py`.

Focused repro:

```bash
env -u LC_ALL PCC_GC_BACKEND=2 /opt/homebrew/bin/timeout 180s uv run pytest 'tests/test_gc_effectiveness.py::test_collect_preserves_root_reachable_subgraph' -q -n0
```

Observed result after the change: `1 passed in 23.50s`.

pcc-Python runtime mirror rebuild:

```bash
env -u LC_ALL /opt/homebrew/bin/timeout 360s make -B -C pcc/py_runtime PCC='uv run pcc' PYTHON=/Users/jiamo/my/pcc/.venv/bin/python3 libpy_runtime_pcc_py.a
```

Observed result: passed.

Full Backend #2 GC gate:

```bash
env -u LC_ALL PCC_GC_BACKEND=2 /opt/homebrew/bin/timeout 700s uv run pytest tests/test_gc_*.py -q -n0 -rxX
```

Observed result: `199 passed in 166.70s`.

## Report (only when the investigation is closing)
No.1 landed. Backend #2 no longer lets a pre-cycle store force
`pcc_gc_mark_active = 1` before roots have been seeded. This fixed the
live-root preservation failure and collapsed the broader Backend #2 GC suite
from `188 passed, 11 failed` to `199 passed`.

The concurrent barrier test was updated to make the cycle active under an
explicit stop-the-world window before storing the white child; its wait loop now
uses `pcc_thread_safepoint()` so the worker can complete mark termination
without deadlocking on the main thread. The abstraction-surface test now records
the intended split: pre-cycle store does not shade, active-cycle worker tests
cover barrier shading.

Remaining Backend #2 production work is not semantic coverage of
`tests/test_gc_*.py`; it is multi-mutator TSan validation and concurrent
sweep/allocation safety proof.
