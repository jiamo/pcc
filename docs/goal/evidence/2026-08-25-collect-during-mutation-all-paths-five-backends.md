# Collect-during-mutation: dict and set, all three paths, all five collectors

This closes the callback-mutation surface.  Two classes exist, and they now
have different but complete answers.

## Movement

Only backend 4 can move a container.  Generational oldify gates on
`pcc_gc_relocate_copy_supported_tag`, which omits DICT/SET/LIST/TUPLE, and
backends 0/1/2 do not relocate at all.  So the existing
COLORED_RELOCATING-only probes are the whole reachable surface, not a gap —
recorded in `2026-08-25-only-backend4-moves-containers.md`, and guarded by a
source-contract test that fails and names the new probe obligation if a
container tag is ever added, on the C gate and the strict mirror alike.

## Collection

A user callback that drives a full mark/sweep needs no relocation, so it
applies to every collector.  Three probes, each parametrized over both mirrors
and all five `PCC_GC_KIND_*` values:

```text
collect_during_insert                         10 passed
collect_during_update_and_delete              10 passed
collect_during_set_add_update_discard         10 passed
```

What each checks after the callback's collect:

```text
dict insert   fresh value alive (via __del__), identity intact, settled entry
              kept, len, root balance
dict update   both source entries land with correct values across a collect
              taken mid-walk
dict delete   tombstone committed, key absent, survivor intact, and the
              displaced value finalized EXACTLY once -- neither leaked nor
              double-freed
set add       element present, len and fill published
set update    all three members present after a collect mid-walk
set remove    element absent, both survivors intact
```

Every probe carries a control arm: an unreachable two-dict cycle holding a
`__del__` instance, which refcounting alone cannot reclaim.  If it is never
finalized the probe returns its own code instead of 0, because a collector that
collected nothing makes every other assertion in the probe vacuous.

**Correction (same day, third review round):** as written here that control was
weaker than claimed.  The insert probe built its cycle *after* the armed insert
and checked it after eight further collects, so it showed only that some collect
swept; the set and dict probes checked one control at the end, across three and
two armed phases, so no sweep could be attributed to a particular callback.  Each
armed phase now builds and checks its own control immediately.  See
`2026-08-25-third-review-probe-integrity-fixes.md`.

## The two mistakes that shaped these probes

**A probe that collects nothing passes.**  The first insert version drove
`pcc_gc_step` in a loop and passed on both arms; the control showed nothing had
been collected.  `pcc_gc_step` does not complete a tracing cycle —
`pcc_gc_collect` is the entry point that marks and sweeps.  Without the control
this would have been filed as "backend 1 is safe".

**A probe that does not root its own pointers invents findings.**  With a real
collect in place, backend 4 reported `fresh entry lost from the dict` at
`len == 2`, which reads exactly like a post-relocation hash failure.  The probe
held its key and value in plain C locals and the collect it drove relocates,
so it was looking up a moved key.  The runtime was never wrong.  Every pointer
in all three probes is now a registered scheduler root, reloaded through
`pcc_gc_load_ptr` after each armed operation.

## Gates

```text
-k collect_during_insert                                    10 passed
-k collect_during_update_and_delete                         10 passed
-k collect_during_set_add                                   10 passed
-k "dict or set_add or set_update or collect_during
    or generational_promotion_declines"           49 passed in 35.91s
dict + set parity, PCC_GC_BACKEND=0..4               23 passed each, ~33s each
```

No arm deadlocked on any backend, which independently confirms that
`py_dict_set`, `py_dict_update`, `py_dict_del`, `py_set_add`, `py_set_update`
and `py_set_remove` all run user callbacks outside the graph lock.

## Nonclaims

- List and tuple mutation paths are not probed for this class.
- Concurrency is single-threaded here: backend 2's worker is exercised only
  through `pcc_gc_collect` on the mutator thread, not a genuine race.
- No bootstrap, stage or fixed-point gate was run.
