# Collect-during-insert, proven on all five collectors

The class the relocation probes could not reach: a user `__hash__` that drives
a full mark/sweep from inside `py_dict_set`, so the container may already be
black when the entry is published.  No relocation is involved, so it applies to
every collector, not only backend 4.

`test_collect_during_insert_keeps_fresh_entry_alive_on_every_backend`,
parametrized over both mirrors and all five `PCC_GC_KIND_*` values:

```text
10 passed, 199 deselected in 11.31s
```

The inserted value carries `__del__`, so a premature free is observed directly
rather than inferred from a corrupted read.  Identity, the pre-existing entry,
`len`, and the scheduler root balance are all checked afterwards.

## Two things the probe got wrong first, and why they matter more than the pass

**It was vacuous.** The first version drove `pcc_gc_step(64)` in a loop and
passed on both arms.  It proved nothing: the control arm — an unreachable
two-dict cycle holding a `__del__` instance, which refcounting alone cannot
reclaim — was never collected.

```text
control cycle was never collected: the tracing sweep did not run,
so this probe is vacuous
```

`pcc_gc_step` does not complete a tracing cycle; `pcc_gc_collect` is the entry
point that marks and sweeps.  Both the callback and the post-insert loop now
use it, and the probe returns its own code rather than 0 if the control is
never finalized, so it can never silently go back to proving nothing.

Without that control the run would have been recorded as "backend 1 incremental
mark mid-insert is safe, 2 passed" — a green result measuring an inert
collector.

**Its "finding" was its own bug.** With a real collect in place, backend 4
reported:

```text
c PCC_GC_KIND_COLORED_RELOCATING collect-during-insert returned 12:
fresh entry lost from the dict
```

`len` was 2 but the lookup missed, which reads like a post-relocation hash
failure.  It was not.  The probe held `key`, `value` and `settled` in plain C
locals, and the collect it drove relocates under backend 4 — so those pointers
were stale and the probe was looking up a moved key.  Registering them as
scheduler roots and reloading through `pcc_gc_load_ptr` makes all ten arms
pass.

The runtime was never at fault, and I nearly reported it as a backend-4
relocation bug.  A probe that drives a collector must root its own pointers to
the same standard as compiled code; the existing backend-4 probes do this and
that is why they do not hit it.

## What this closes

```text
backend 0  REFCOUNT_CYCLE              collect-during-insert   c + strict
backend 1  INCREMENTAL_TRICOLOR        collect-during-insert   c + strict
backend 2  CONCURRENT_MARK_SWEEP       collect-during-insert   c + strict
backend 3  GENERATIONAL_MINOR_MAJOR    collect-during-insert   c + strict
backend 4  COLORED_RELOCATING          collect-during-insert   c + strict
```

Together with `2026-08-25-only-backend4-moves-containers.md` — which showed
that only backend 4 can move a container, so the relocation probes' coverage
was already complete — the callback-mutation surface now has both classes
covered on every collector.

No deadlock occurred on any arm, which independently confirms that
`py_dict_set` does not hold the graph lock across the user callback.

## Gates

```text
-k collect_during_insert                                   10 passed in 11.31s
-k "dict or set_add or set_update or collect_during_insert
    or generational_promotion_declines"                    29 passed in 21.13s
```

## Nonclaims

- Insert only.  The same callback-driven collect during `update` and `del` is
  not probed on the non-relocating backends.
- No bootstrap, stage or fixed-point gate was run.
