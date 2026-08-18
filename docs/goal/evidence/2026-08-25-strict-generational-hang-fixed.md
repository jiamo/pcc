# Fixed: the strict generational collector's drain never terminated

`GC-P0-GENERATIONAL-STRICT-HANGS-ON-COLLECT-INSIDE-LIST-OP` is closed.  A
`pcc_gc_collect` driven from inside a list operation hung the strict
pcc-Python generational runtime; it now completes.

## Root cause

`pcc_gc_collect` stops the world, sets `pcc_gc_explicit_collect_active`, and
drains with `for (;;) { if (!pcc_gc_step(1024)) break; }`.

Every GC frame enter and leave published a tracing-work request
(`pcc_gc_cycle_requested_store_release(1)`, four sites in
`freestanding_gc_frame_registry.py`).  That is correct for a mutator — a new
frame's roots really are new work for an incremental marker.  It is wrong for
the collector itself, because **this runtime is compiled pcc-Python, so every
call inside the drain enters and leaves a GC frame.**  The collector therefore
re-armed the request it had just cleared, and completing a cycle immediately
requested the next one.

Measured, with the world stopped and no mutator running:

```text
before the fix
  k=0  req=1 mark=0 gray=0  -> step=29  req=0 mark=1 gray=3
  k=1  req=0 mark=1 gray=3  -> step=3   req=1 mark=0 gray=0   <- req 0 -> 1
  k=2  req=1 mark=0 gray=0  -> step=1   req=0 mark=1 gray=3
  ...  0 -> 3 -> 0 forever, both halves reporting progress

after the fix
  k=0  req=1 mark=0 gray=0  -> step=29  req=0 mark=1 gray=3
  k=1  req=0 mark=1 gray=3  -> step=3   req=0 mark=0 gray=0
  k=2  req=0 mark=0 gray=0  -> step=0                          terminates
```

The three greys per cycle are the three scheduler roots the list scan holds
across its callback (`list`, `query`, `candidate`).

## Why C never hit it

The C runtime requests a cycle on **scheduler-root** mutation
(`pcc_gc_scheduler_root_register_handle` / `unregister_handle`,
`py_gc_backend.c:15446` and `:15462`), not on frame transitions, and its
collector is C so it registers no GC frames and mutates no roots while
draining.  This is a structural difference, not a line-level mirror bug: there
is no corresponding C site to guard, so **no C change was made** rather than
adding a guard that could never fire.

## The fix

The four frame-registry request sites now skip the request while an explicit
collect is active.  The world is stopped for that whole window, so a frame
transition observed there is always the collector's own and carries no
information:

```python
if load_i32(global_addr("pcc_gc_explicit_collect_active"), 0) == 0:
    pcc_gc_cycle_requested_store_release(1)
```

Inlined at each site rather than factored into a helper: freestanding modules
require `@c_abi_export` on every function, and this does not deserve a new ABI
symbol.

## Gates

```text
[pcc_python-GENERATIONAL-contains]   was a 60s hang   ->  1 passed in 1.86s
[pcc_python-GENERATIONAL-remove]     was a 60s hang   ->  1 passed in 1.36s
collect_during_list_op, all strict arms                   10 passed in 12.22s
collect_during_insert / update / set_add /
  generational_promotion_declines                         31 passed in 27.16s
```

## Three wrong turns, for the next reader

- **I guarded the wrong allocator first.**  The recorded fix direction said
  "collector-internal allocations", so I guarded the backend-3 branch of the
  object allocator (`py_gc_backend.py`).  No effect — the re-setter was the
  frame registry.  My original six-site grep had missed it because those sites
  call `pcc_gc_cycle_requested_store_release(1)` rather than
  `store_i32(global_addr("pcc_gc_cycle_requested"), ...)`.  The ineffective
  change was reverted as soon as it measured null, not left in.
- **A replace-all rewrote the helper's own call into a recursive one.**  The
  count assertion caught it before the file was written.  This is the hazard
  AGENTS.md names: never let the pattern overlap the replacement text.
- **Freestanding modules reject module-private functions.**
  `PCC-PY-COMPILE-001: freestanding module functions require @c_abi_export`.

## Nonclaims

- Only the explicit-collect window is changed; ordinary mutator frame
  transitions still publish cycle requests exactly as before.
- `pcc_gc_explicit_collect_active` is a shared global in the strict runtime,
  not thread-local as in C.  With a concurrent collector and a live mutator on
  another thread, this guard would also suppress that mutator's frame requests.
  That cannot happen for an explicit collect, which stops the world, but it is
  the boundary of the fix.
- The C-side finding
  `GC-P1-COLLECT-INSIDE-LIST-REMOVE-LEAVES-CYCLE-UNCOLLECTED` is unaffected and
  still open.
- No bootstrap, stage or fixed-point gate was run.
