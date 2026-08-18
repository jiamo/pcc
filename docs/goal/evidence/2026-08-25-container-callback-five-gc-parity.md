# Container callback commit — five-GC parity, and where the coverage stops

The row's open boundary held two items.  One turned out to be already done;
the other is now measured, and measuring it exposed a real gap that is worth
naming precisely rather than promoting past.

## Item 1: strengthen the dict.update probe — already done

The boundary text asked for "a callback that forces retirement of the relocated
source mid-update, or a delete path that compacts entries_used".  The probe
already does both — `pcc_gc_backend4_remap_and_retire_stopped_world` twice,
then `py_dict_del(cur, mutate_key)` on the reloaded source — and the pre-fix /
post-fix differential in `2026-08-25-dict-update-snapshot-and-review-fixes.md`
records `rc=139` versus `rc=0`.  The boundary text was stale, written before
the probe was strengthened.  Removed rather than re-satisfied.

## Item 2: five-GC gates — measured

Before sweeping the env var I checked that it is actually honored, because five
identical no-op runs reported as five-backend evidence is exactly the failure
this repo's evidence rules exist for:

```text
py_gc_backend.c:1101    getenv("PCC_GC_BACKEND")   read at startup
parity harness          subprocess.run(...) with no env=, so the child inherits
```

Both true, so the sweep is real.

```text
PCC_GC_BACKEND=0    23 passed in 32.27s
PCC_GC_BACKEND=1    23 passed in 32.52s
PCC_GC_BACKEND=2    23 passed in 33.23s
PCC_GC_BACKEND=3    23 passed in 32.43s
PCC_GC_BACKEND=4    23 passed in 32.72s
```

At ~33s per backend this was never the long run it was being deferred as; the
whole sweep is under three minutes.  Measuring one backend before assuming is
what showed that.

Substrate dict/set slice, once:

```text
-k "dict or set_add or set_update"    18 passed, 180 deselected in 142.86s
```

## What the sweep does NOT cover

The substrate probes call `pcc_gc_set_backend(...)` in their own C `main()`, so
the env var is overridden there.  Sweeping `PCC_GC_BACKEND=0..4` over the
substrate would run identical work five times.  Per-probe, the dict/set runtime
coverage is:

```text
backend 4  COLORED_RELOCATING          6 probes
backend 0  REFCOUNT_CYCLE              none
backend 1  INCREMENTAL_TRICOLOR        none
backend 2  CONCURRENT_MARK_SWEEP       none
backend 3  GENERATIONAL_MINOR_MAJOR    none
```

The five remaining dict/set entries in that selection are **source-contract**
tests — they assert on the C and strict source text (prepare/commit/finish
ordering), so they pin no backend and exercise no collector.

So the honest position is: the rooted callback-restart contract is proven
against relocation, and the ordinary dict/set semantics are proven under all
five collectors.  What is **not** proven is the callback-restart contract under
backend 1's incremental write barrier or backend 3's generational forwarding
and eager slot rewrite, both of which can plausibly break it in ways relocation
does not.  That is why this row goes to DONE_WEAK rather than DONE_STRONG.

## Nonclaims

- No dict/set callback-relocation probe exists for backends 0, 1, 2 or 3.
- No bootstrap, stage or fixed-point gate was run.
