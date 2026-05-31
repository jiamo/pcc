# Investigation: Backend #4 relocation targets must wait for phase reset

## Status
resolved

## Problem Description
Backend #4 now relocates selected objects and records forwarding from source to
target. When the source is freed, `pcc_gc_note_object_freeing()` removes the
forwarding entry. The relocated target then no longer appears as a forwarding
target, so `pcc_gc_select_relocation_set()` can select that target again in the
same relocation phase. A collector loop that waits for idle work can keep
relocating the same logical object chain instead of making phase progress.

## Repro
Run the focused phase-progress gates:

```bash
env -u LC_ALL /opt/homebrew/bin/timeout 900s uv run pytest 'tests/test_gc_backend_relocating.py::test_colored_relocating_targets_wait_for_phase_reset' 'tests/test_gc_backend_relocating.py::test_pcc_python_colored_relocating_targets_wait_for_phase_reset' -q -n0
```

Expected result after the fix: after source forwarding is removed, the moved
target is not selected again by `pcc_gc_step()` until
`pcc_gc_reset_relocation_set()` starts the next relocation phase.

## Test [CONFIRMED]
Before the fix, both focused gates failed: C runtime and pcc-Python mirror
printed `0` instead of `1`, showing that the relocated target could be selected
again after the source forwarding entry was removed.

During diagnosis after the first implementation pass, the probe showed
`forwards_delta=0` but `work=1`; that remaining work was tracing/cycle
bookkeeping, not repeated relocation. The final gate therefore asserts no new
relocation forwarding in the same phase and successful target selection after
explicit phase reset.

## Proposals
- No.1 Mark relocated targets until relocation-set reset     [CONFIRMED]

## No.1 Mark relocated targets until relocation-set reset
### Code Change
Add a Backend #4 target-side relocation phase bit. Mark forwarding targets when
forwarding is installed, skip target-marked objects during relocation-set
selection, and clear target marks on explicit relocation-set reset.
### CONFIRMED
Implemented `PY_FLAG_GC_RELOCATION_TARGET` in the C runtime and the same
`0x2000` bit in the pcc-Python mirror. Forwarding install marks the target,
relocation-set selection skips target-marked objects, allocation clears stale
target/candidate bits, and `pcc_gc_reset_relocation_set()` clears target marks
as the explicit next-phase boundary. The C runtime relocation-set reset,
contains, and size helpers now take the graph lock like the pcc-Python mirror.

Verification run before the user requested no further tests:

```bash
env -u LC_ALL /opt/homebrew/bin/timeout 900s uv run pytest 'tests/test_gc_backend_relocating.py::test_colored_relocating_targets_wait_for_phase_reset' 'tests/test_gc_backend_relocating.py::test_pcc_python_colored_relocating_targets_wait_for_phase_reset' -q -n0
# 2 passed in 28.26s
```

No broader regression gate was recorded after the final patch because the user
asked to stop running tests and proceed with code changes.

## Report (only when the investigation is closing)
No.1 landed. Backend #4 now has a minimal relocation phase-progress guard:
objects copied as relocation targets are protected from immediate reselection
until `pcc_gc_reset_relocation_set()` starts a new explicit phase. This does not
make Backend #4 page-based or production ZGC; it prevents one concrete
same-phase repeated-relocation loop while leaving real page evacuation,
fragmentation control, and phase orchestration as follow-up work.
