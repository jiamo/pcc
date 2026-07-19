# Investigation: unbounded RSS growth under steady-state churn (all five backends)

## Status
RESOLVED 2026-06-12 (all five backends) — root cause was TWO FRONTEND
OWNERSHIP LEAKS, not allocator/GC bookkeeping and not GC trigger
policy (the earlier attributions below were wrong; see "Root cause"
and "Post-fix re-measure"). Fixed in
`pcc/py_frontend/codegen/ownership_lowering.py` (raw-scaffold
builtin-constructor set missing `str`/`bytes`) and
`pcc/py_frontend/codegen/literal_lowering.py` (dict-literal owned
key/value temps never released after borrowing `py_dict_set`).
Regression: `tests/python/test_dict_literal_temp_release.py` (5-GC
parametrized). Bootstrap pcc1→pcc2→pcc3 + verify green same day.
Post-fix 200k-round re-measure: ALL FIVE backends plateau (12-27MB
for a 2048-object live set over 12.8M ops) and gc1/gc2 auto-step
triggers FIRE (pause_n=1403) — the pre-fix "trigger silence at 8GB"
was an artifact of measuring a leaking compiler. Remaining open item
moved to its own file: the backend-4 exit-time SIGSEGV
(`gc-backend4-churn-exit-list-item-uaf.md`).

## Post-fix re-measure (2026-06-12, churn 200k rounds == 12.8M ops)

```text
        pre-fix                 post-fix
gc0     450 -> 1771 MB          3 -> 12 MB   0.8 B/op  pause_n=0
gc1     3211 -> 8155, p_n=0     3 -> 17 MB   1.1 B/op  pause_n=1403 (sum 294ms)
gc2     3208 -> 8152, p_n=0     3 -> 17 MB   1.1 B/op  pause_n=1403 (sum 445ms)
gc3     3202 -> 8147, p_n=0     3 -> 16 MB   1.0 B/op  pause_n=0
gc4     6038 -> 10829           4 -> 27 MB   1.9 B/op  pause_n=0, exit=139 (separate bug)

growshrink 400 cycles: 14 / 22 / 21 / 26 / 42 MB (was 54 / 229 / 242
/ 238 / 572); gc1 p_n=56, gc2 p_n=99; gc4 exit=139 again.
```

Research observations retained (not defects): gc3 (generational) and
gc4 keep RSS bounded WITHOUT any timed pauses under these workloads —
their reclamation runs through paths `pcc_gc_record_pause` does not
time (minor-arena recycling / zpage lifecycle). Characterizing those
stepping/recycling policies is future G-P3 material, not a leak.
Success metric from proposal No.3 is met: churn RSS plateaus at a
small multiple of the live set on every backend.

## Root cause (2026-06-12)

Allocation-kind decomposition probes (one kind per run, RSS slope per
op on gc0) + macOS `leaks --atExit` with `MallocStackLogging=1` gave
exact per-leak stacks:

```text
base/inst/list/dict(int values)     ~1 B/op   (flat)
strcat  "n" + str(j % 97)           49.3 B/op -> py_str_new leak 1/call
dictstr {"b": "s" + str(j % 13)}    97.7 B/op -> two leaks/op
churn total                        ~145 B/op == strcat + dictstr  ✓
```

1. `_raw_scaffold_object_rhs_is_owned` listed
   `{list, dict, tuple, set, frozenset}` but not `str`/`bytes`
   (the canonical `_expr_returns_owned_object` set HAS them), so in
   modules importing `pcc.extern`, `s = str(x)` skipped owned-local
   management: raw `store` with no release-on-rebind — one leaked str
   per call. IR diff: the concat branch had the `.owned` flag +
   conditional release; the str() branch had a bare store.
2. All three dict-literal emission sites (exact-int branch, general
   branch, splat) called `py_dict_set`/`py_dict_update` — which BORROW
   (balanced `pcc_gc_store_ptr`) — without releasing owned key/value
   temps. The list/tuple literal paths release via
   `_container_store_temp_needs_release`; dict did not. This one is
   NOT raw-scaffold-scoped: an ordinary no-extern module leaked
   1 object per dict literal with an owned value (30000/30000 in
   `leaks`). Fixed by mirroring the list-path release discipline.

After both fixes: churn gc0 slope ~145 -> 2.2 B/op (RSS 3->9 MB over
3.2M ops, plateau); `leaks` 0; dict_plain probe 0 leaks.

## Why the earlier discriminators misled

- The `__del__`-counting Node proved only that NODE instances die —
  the leaked objects were their COMPONENT strs and the per-op dict
  values, which have no finalizer. "dealloc is healthy" was true for
  the class and false for the payload.
- Periodic `gc.collect()` cannot reclaim a refcount leak (rc never
  reaches 0; the objects are still reachable-by-count, just unowned),
  so "collect does not flatten the slope" did not imply "below object
  lifetime".
- gc0 never allocates PccGcObjectNode/index entries at all
  (`pcc_gc_tracks_objects()` is false for backend 0), so the
  bookkeeping-layer hypothesis was structurally impossible for the
  gc0 slope. Verify the tracks-objects gate before blaming
  bookkeeping.

## Problem Description

The first minutes-scale long-run characterization
(`docs/reports/gc-longrun-first-report.md` + the 200k-round follow-up)
shows steady-state churn (fixed 2048-object live ring, continuous
replacement, 12.8M ops) growing RSS WITHOUT BOUND on every backend:

```text
gc0  450 -> 1771 MB (linear, no plateau)   wall 24s
gc1  3211 -> 8155 MB                       wall 65s
gc2  3208 -> 8152 MB                       wall 64s
gc3  3202 -> 8147 MB                       wall 67s
gc4  6038 -> 10829 MB (plateau-ish tail)   wall 149s
pause_n = 0 everywhere (auto-step policies never fired)
```

A long-running service profile cannot survive this; obligation 6's
whole premise is catching exactly this class.

## Attribution chain (2026-06-12, gc0 focus)

1. **Periodic explicit `gc.collect()` does NOT flatten it** — same
   ~141 bytes/op growth slope with a collect every 5000 rounds. So it
   is not "waiting for a collect".
2. **Deallocation is HEALTHY** — a `__del__`-counting Node shows
   `finalized` tracking `created` with gap == LIVE_SET (2048, the
   live ring itself) throughout. No reference leak in the workload
   path.
3. Therefore the retention lives BELOW object lifetime: the
   allocator / per-object GC bookkeeping layer. ~145 bytes/op of
   never-returned memory despite free running. Prime suspects:
   - per-object GC bookkeeping (object index / GC node / identity
     entries) allocated on `pcc_gc_note_object_allocated` and never
     removed (or recycled into an unbounded freelist) on dealloc;
   - `pcc_gc_alloc`/`pcc_gc_free_object_memory` not returning or not
     reusing freed blocks on the refcount backend;
   - on gc1-4, the additional factor that auto-step trigger policies
     never fire under this workload (debt accounting may not see
     these allocations), stacking dead objects on top of the
     bookkeeping growth.

## Repro

```bash
# build once
env -u LC_ALL uv run pcc --python-libpython=off --ir-scaffold=on \
  --backend self benchmarks/python/longrun_churn.py -o /tmp/churn
# linear growth, any backend:
PCC_GC_BACKEND=0 /tmp/churn 50000   # watch the rss column
# discriminators (session log 2026-06-12): periodic-collect variant
# keeps the slope; __del__-count variant shows gap == live-set.
```

## Proposals
- No.1 Instrument/attribute the per-object bookkeeping lifecycle on backend 0 (where does the 145B/op live: index entries, GC nodes, malloc pool?)   [RESOLVED 2026-06-12 — not bookkeeping; two frontend ownership leaks, see Root cause]
- No.2 Audit gc1-4 auto-step debt accounting against this allocation pattern (why pause_n == 0 at 8GB)   [CLOSED 2026-06-12 — pre-fix artifact: post-fix gc1/gc2 triggers fire (pause_n=1403) and all backends plateau; gc3/gc4's untimed-but-bounded reclamation recorded as a research observation, not a defect]
- No.3 Fix slices per finding, each with the smoke gate + five-GC matrix; success metric = churn RSS plateaus at a small multiple of the live set   [DONE all five backends — 12-27MB plateaus at 12.8M ops]

## Follow-up bug found while gating (2026-06-12)

The post-fix longrun smoke run surfaced an intermittent backend-4
exit-time SIGSEGV on the churn shape (stale list item pointer during
exit dealloc). NOT caused by these fixes (fires with no dict literal
and no dynamic strs; no release frames in the backtrace). Tracked
separately: `gc-backend4-churn-exit-list-item-uaf.md`. The smoke gate
`[4-churn]` stays red until that closes.

## Notes

Coordinate with the parallel G-P2 index work before touching the
index/bookkeeping structures — same files. The finalizer-canary
workload independently proves finalizer health, so fixes must not
trade finalizer semantics for footprint (5-GC Production Equality
Rule).
