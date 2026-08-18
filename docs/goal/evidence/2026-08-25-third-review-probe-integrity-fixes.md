# Third review round — probe integrity — 2026-08-25

Six findings; the first two blocked the GC change and are handled in
`2026-08-25-collect-drain-phase-boundary-fixed.md` (revert) and by rebuilding
the archive.  This file covers 3, 4 and 5: three ways my probes were weaker
than the claims I drew from them.

## 3. A scheduler root pointed at a destructed object

`victim_root` was registered before the removal and only retired at the end of
`main`.  The probe *requires* the removed element to be finalized, so from the
removal onward that root pointed at freed memory — and the contains phase plus
several collects scanned the root set in that state.  Every collector reading
taken after the removal came from a corrupted root set.

Retired at the removal now, with the slot nulled, before the root baseline is
taken.

**Re-measured, because a conclusion drawn through a corrupted root set has to
be re-earned rather than assumed:** the four C `remove` arms report *byte-identical*
diagnostics to before (`collect returned 0 total, remove=1 contains=1
victim_final=1`), and the other 16 arms pass.  So the dangling root was not the
source of the earlier diagnosis — but that is now a measured statement.

## 4. The controls did not prove what the task board said they proved

The insert probe built its control cycle **after** the armed insert and checked
it after eight further ordinary collects.  It therefore showed only that *some*
collect swept, never that the callback's collect did.  The set and dict probes
built their control early but still checked it once, at the end, after three or
two armed phases plus extra collects — one control cannot attribute a sweep to
three separate callbacks.

The board's claim that "each probe's control proves the callback collection was
non-empty" was unfounded.  Now each armed phase builds its own control through a
shared `make_control()` and asserts *immediately after that phase* that its own
control was reclaimed:

```text
insert                     1 phase
set add / update / remove  3 phases
dict update / delete       2 phases
```

All six pass, so the six callback collects do each sweep — this time
demonstrated per phase rather than asserted collectively.

## 5. Probes cleared exceptions instead of asserting success

Thirteen `py_clear_exception()` calls sat after operations that must succeed.
A container left in the right shape with an exception still pending is a
failure; clearing it made the test green over that failure.  The worst case was
after `py_list_remove`, where clearing hid a possible `ValueError("x not in
list")` and let the length check pass on a list that had never been touched.

All thirteen are now assertions with their own return codes.

### The baseline fix found a real misalignment

`dict update`'s root baseline was taken after the update phase *and* after the
delete phase's handles were registered, so a root leaked by the update would be
absorbed.  Moving it before the first armed phase immediately failed:

```text
roots leaked: 4 -> 7
```

Not a leak: the old baseline had been absorbing the delete phase's three
legitimate registrations, so the check was never like-for-like.  The delete
handles are now retired before the comparison, which makes a leak in *either*
phase visible.

## Gates

```text
collect_during_insert                                   10 passed
collect_during_set_add                                  10 passed
collect_during_update_and_delete                        10 passed
all collect probes + the tag-gate contract test          47 passed in 40.75s
  (four C remove arms deselected: known-red after the drain revert)
```

## Nonclaims

- The four C `remove` arms are red, by design, after reverting the unsound
  drain fix.  They are `GC-P1-COLLECT-INSIDE-LIST-REMOVE-LEAVES-CYCLE-UNCOLLECTED`.
- Concurrency is still unprobed: `GC-P1-CONCURRENT-TRACER-PROBE-MUST-PROVE-OVERLAP`.
- No bootstrap, stage or fixed-point gate was run.
