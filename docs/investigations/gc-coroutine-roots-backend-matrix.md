# Investigation: suspended heap-frame roots across all GC backends

## Status
resolved

## Problem Description
The user-mode scheduling gate requires suspended coroutine/generator frames to
be traced outside the active native stack. Existing Backend #3 tests check
minor-promotion slot rewriting, but the broader backend 0..4 matrix also needs
a simple correctness gate proving a suspended heap-frame local survives
`pcc_gc_collect(0)`.

## Repro
Run the focused matrix:

```bash
env -u LC_ALL /opt/homebrew/bin/timeout 900s uv run pytest tests/test_gc_coroutine_roots.py -q -n0
```

Expected result: both C runtime and pcc-Python runtime probes print `0:1`
through `4:1`, meaning backend 0, 1, 2, 3, and 4 all preserve a heap-frame local
reachable through a suspended generator object rooted by the scheduler-root
surface.

## Test [CONFIRMED]
The matrix test passes:

```bash
env -u LC_ALL /opt/homebrew/bin/timeout 900s uv run pytest tests/test_gc_coroutine_roots.py -q -n0
```

Observed result: `2 passed in 27.98s`.

## Proposals
- No.1 Add suspended heap-frame backend matrix     [CONFIRMED]
- No.2 Balance owned refs when suspended generator frames store locals [CONFIRMED]

## No.1 Add suspended heap-frame backend matrix
### Code Change
Add `tests/test_gc_coroutine_roots.py`. The probe creates a generator object
with a heap-list frame containing one string local, roots only the generator
through the scheduler-root surface, runs `pcc_gc_collect(0)`, then verifies the
frame local is still readable after each backend 0..4.
### CONFIRMED
The C runtime and pcc-Python runtime variants both pass backend 0..4. This
confirms the current heap-frame generator shape is visible to all selected GC
backends through the scheduler-root surface.

## Report (only when the investigation is closing)
No.1 landed. This is the first cross-backend coroutine-root matrix, but it is
only the minimum suspended heap-frame local test. The full user-mode scheduling
gate still needs task queues, await-chain/future waiters, task completion
collection, Backend #3/#4 moving-reference updates in task objects, and Backend
#2 worker/assist concurrency coverage.

## 2026-06-18 Update: suspended-frame owned-ref balance
`tests/python/test_intent_constraints.py::TestObligation6GCEquality::test_identical_across_backends_gap[generator_frame_held_ref]`
exposed a different suspended-frame bug than No.1. The generator object and
heap-list frame were visible to all collectors, but Backend #0 did not run the
finalizer for a frame-held local after the generator was dropped. Runtime
refcount logging showed the generator and frame list both freed; the contained
instance only decref'd from 2 to 1.

Root cause: generator resume lowering loaded frame slots with `py_list_get`
(new ref) and created owned locals before `yield`, then saved those locals back
into the heap frame without releasing the resume-stack owned refs at suspension.
Tracing backends later collected the leaked unreachable object; refcount backend
#0 correctly required the stack-owned ref to be released.

Fix: generator resume lowering now uses isolated owned-local state, marks
frame-slot loads as owned roots, releases owned locals after saving the heap
frame on `yield`/finish, and retains `yield <local>` values so the caller
receives an owned ref independent of the frame.

Evidence:

```bash
env -u LC_ALL uv run pytest tests/python/test_python_generator_parity.py::test_generator_overwrite_releases_suspended_frame_local_backend0 -q -n0
env -u LC_ALL uv run pytest 'tests/python/test_intent_constraints.py::TestObligation6GCEquality::test_identical_across_backends[generator_frame_held_ref]' -q -n0 -m integration
env -u LC_ALL uv run pytest tests/python/test_python_generator_parity.py -q -n0
env -u LC_ALL uv run pytest -q -m xfail -n0 -rxX
```

Observed: focused backend0 regression passed, promoted 5-GC equality node
passed, generator parity passed (`18 passed`), and the live xfail audit dropped
to 4 xfailed with no XPASS.
