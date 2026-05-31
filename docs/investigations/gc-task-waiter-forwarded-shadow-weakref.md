# Investigation: task waiter cycles survive colored relocation

## Status
resolved

## Problem Description
`tests/test_gc_coroutine_roots.py::test_task_completion_releases_waiter_cycle_across_backends`
and the pcc-Python runtime variant failed for backend 4.  The probe printed
`4:1:0`: the waiter was live while the task was waiting, but it was still live
after `py_task_set_result(root, py_None)` cleared the logical waiter.

## Repro
```bash
env -u LC_ALL -u LC_CTYPE uv run pytest tests/test_gc_coroutine_roots.py -q -n0
```

Expected before the fix: the two task-completion cases fail, with backend 4
printing `4:1:0`.

## Test [CONFIRMED]
The failure was observed with:

```bash
env -u LC_ALL -u LC_CTYPE perl -e 'alarm shift; exec @ARGV' 900 uv run pytest tests/test_gc_coroutine_roots.py -q -n0
```

After the fix, the same command reports `4 passed`.

## Proposals
- No.1 Resolve old slot values in `pcc_gc_store_ptr`     [DENIED]
- No.2 Resolve and pin weakref targets only              [DENIED]
- No.3 Pin weakref targets and clear forwarded task shadows     [CONFIRMED]

## No.1 Resolve old slot values in `pcc_gc_store_ptr`
### Code Change
Temporarily changed `pcc_gc_store_ptr()` in both C and pcc-Python runtimes to
resolve forwarded old/new slot values before storing.

### DENIED
The task-completion probe still failed with backend 4 printing `4:1:0`.  The
slot write was not the only stale reference; the forwarded source task still
held its copied `waiter` field.

## No.2 Resolve and pin weakref targets only
### Code Change
Temporarily resolved weakref targets through `pcc_gc_note_relocation_read()` and
pinned them with `pcc_gc_pin()`.

### DENIED
The C and pcc-Python task-completion probes still failed with backend 4 printing
`4:1:0`.  Pinning keeps the borrowed weakref target address stable, but the old
forwarded task shadow still retained the waiter.

## No.3 Pin weakref targets and clear forwarded task shadows
### Code Change
`py_weakref_new()` now canonicalizes a target through the relocation read
barrier, then pins it so the weakref's borrowed exact-pointer invalidation model
remains valid under backend 4.

`checked_task()` now resolves forwarded task objects through
`pcc_gc_note_relocation_read()`.  If the original task pointer is a forwarded
source, its copied `coro`, `result`, and `waiter` slots are cleared so the
shadow no longer retains stale strong references.

### CONFIRMED
The focused regression now passes in both C runtime and pcc-Python runtime
forms:

```bash
env -u LC_ALL -u LC_CTYPE perl -e 'alarm shift; exec @ARGV' 900 uv run pytest tests/test_gc_coroutine_roots.py -q -n0
```

Observed result: `4 passed in 52.31s`.

## Report (only when the investigation is closing)
No.3 landed.  No.1 was too broad and did not address the copied task shadow.
No.2 preserved weakref target identity but left the stale task reference in
place.  The final fix keeps weakref targets stable for the current borrowed
pointer design and removes stale strong references from forwarded task source
objects.
