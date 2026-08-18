# Backend-4 relocation selector — diagnosis, not a fix — 2026-08-25

## Status

**Resolved for the drain symptom.**  The relocation-drain gate is green
(`8 passed in 7.52s`) with **no runtime change** — the defect was in the test's
own C oracle.  The other two symptoms of
`GC-P1-BACKEND4-AGING-MIDSTOP-PROMOTION` are **separate causes** and remain
open; see "Not one cause after all" below.

Sections 1-8 are the investigation as it ran, including two of my own
conclusions that later measurement refuted.  They are kept because the refuted
steps are the expensive part to rediscover.

## Minimal reproducer

The failing gate is
`test_freestanding_gc_relocation_drain.py::test_relocation_drain_matches_c_oracle_for_object_page_and_step_budgets[object-...]`,
which fails because the C oracle binary itself exits 4 at
`pcc_gc_select_relocation_set(8) != 2`.  It reduces to five lines linked against
the ordinary C runtime archive:

```c
pcc_gc_set_backend(PCC_GC_KIND_COLORED_RELOCATING);
PyObject *a = pcc_gc_alloc(128, PY_TYPE_LIST, 0x100 /* PY_FLAG_GC_OLD */);
PyObject *b = pcc_gc_alloc(128, PY_TYPE_LIST, 0x100);
pcc_gc_telemetry_reset();
printf("%lld\n", (long long)pcc_gc_select_relocation_set(8));  /* 0, want 2 */
```

Use this instead of the pytest file; it builds and runs in seconds.

## Measured facts

**1. The objects are registered.**  `pcc_gc_object_id` returns `1` and `2`, so
this is not a missing index entry.

**2. The objects are zpage-allocated, not malloc'd.**  Header flags are
`0x14108` with `PY_FLAG_GC_ZPAGE_ALLOC` set and `PY_FLAG_GC_MALLOC_ALLOC`
clear.  A `PY_TYPE_STR` allocated the same way comes back `0x40108`
(malloc, no zpage) because `pcc_alloc_graph_leaf_tag` routes leaf tags around
the zpage allocator — so the leaf-tag path is *not* the explanation here.  This
refutes the first hypothesis tried.

**3. Every evacuation candidate score is zero, in every configuration.**

```text
two fresh objects            cand=0 small=0 medium=0 page_cand=0 zpage_bytes=0
64 allocated                 cand=0 page_cand=0 frag=0
64 allocated, 48 released    cand=0 page_cand=0 frag=0
after select(8)              cand=0 page_cand=0
```

So it is not "a fresh fully-live page is correctly not worth evacuating" —
fragmenting three quarters of the objects changes nothing.  That refutes the
second hypothesis tried.

**4. The current selector is page-only.**  `pcc_gc_select_relocation_set`
(`py_gc_backend.c:8211`) contains exactly two batch loops:
`pcc_gc_backend4_best_relocation_page_batch_unlocked` to choose a page, then
`pcc_gc_backend4_select_page_objects_batch_unlocked` to take objects from that
page.  There is no object-granularity path.  With no qualifying page, `added`
stays 0 and the outer loop breaks on the first iteration.

**5. The recorded-green expectation did not depend on page candidacy.**  The
drain test's expected output for this case is `1,1,0,0,0,1,1000`, whose fourth
and fifth fields are `pcc_gc_relocation_set_size()` and
`pcc_gc_backend4_evacuation_page_candidate_score()` — both **0**.  So when this
gate was green, `select(8)` returned 2 while page candidacy was zero.  Selection
did not go through page candidacy at all.

## The regression window

`docs/goal/evidence/2026-08-22-gc4-a3b-relocation-object-drain.md` records this
gate green together with file hashes:

```text
tests/python/test_freestanding_gc_relocation_drain.py
  recorded 9e551897e816f073e828874110c5e7e957916add0c77f25b4ed33133f7c7a296
  current  8efe40fc557e3da3132d5783d7ffa7162c20ce7dc007a2f9267995e0d88d9a22
pcc/py_runtime/src/py_gc_backend.c
  recorded c3257d0a58caf93c1a707643f4ca67160cceac539660caca71bfe818a7de55b7
  current  4daf55aab6d59d7938e9dcab0c016a35129d52e7b8e70856963ffff9de506248
```

`py_gc_backend.c` is unmodified in the working tree, so the runtime change
landed between that evidence and now.  Facts 4 and 5 together say what to look
for: an object-granularity selection path that existed then and does not now.

**6. REFUTED BY LATER MEASUREMENT.**  The claim below — that a plain
allocation gets no zpage node — is false.  Direct counting showed `nodes=2`
immediately after two plain `pcc_gc_alloc` calls, and the candidate predicate
*accepted* them (`reason=0`).  `pcc_gc_backend4_zpage_track_alloc_unlocked` is
genuinely dead code, but nodes reach the list by another path, so its deadness
explains nothing here.  The original text follows for the record.

**6 (as originally written, now refuted). The alloc-time zpage-node
registration is dead code.**  The selector scan
walks the `pcc_gc_backend4_zpages` node list.  Nodes enter it from exactly two
places: `pcc_gc_backend4_zpage_link_node_unlocked`, reachable only from
`pcc_gc_backend4_zpage_track_alloc_unlocked`, and
`pcc_gc_backend4_zpage_link_node_preallocated`, the exported owner-payload-span
path used by `pcc_gc_backend4_zpage_register_owner_payload_span`.  The compiler
confirms the first is unreachable:

```text
py_gc_backend.c:4405: warning: unused function
  'pcc_gc_backend4_zpage_track_alloc_unlocked' [-Wunused-function]
```

So a plain allocation gets zpage *memory* (`PY_FLAG_GC_ZPAGE_ALLOC` set) but no
zpage *node*, and only containers that register an owner payload span appear in
the list the selector scans.  This is the strongest lead, but see the
invalidated experiment below — it is **not** yet confirmed.

**7. Two more predicate branches are ruled out.**  `PY_TYPE_LIST` is a
supported relocate-copy tag (`pcc_gc_colored_relocate_copy_supported_tag`
returns 1 at `py_gc_backend.c:6133`), and
`pcc_gc_backend4_evacuation_policy_accept(256)` returns 1 since 256 is under the
small-page limit.  Neither the tag nor the size policy is the blocker.

**8. What is left inside the predicate.**  After facts 1, 2, 3, 7 and the
score arithmetic (the `PY_FLAG_GC_OLD` bonus alone guarantees `score >= 1`), the
only surviving rejection points in
`pcc_gc_backend4_zpage_candidate_snapshot` are `zp->page == NULL` and
`page->pending_alloc_count > 0`.

## Invalidated experiment — do not repeat

Registering an owner payload span to force a node into the list:

```c
void *pa = malloc(256);
int64_t ra = pcc_gc_backend4_zpage_register_owner_payload_span(a, pa, 256);
/* ra == 256, rb == 512 -- registration reports success */
/* select(8) still 0, cand still 0 */
```

The registration returns a cumulative byte total, not a failure, so it did
"succeed" — but a `malloc`-backed base cannot belong to a zpage, so the node's
`page` is NULL and the predicate rejects at `if (page == NULL) return 0;`.  The
experiment therefore says nothing about fact 6 either way.  Confirming fact 6
needs a span whose base lies inside the owner's own zpage, which is not
constructible from outside the runtime — instrument
`pcc_gc_backend4_zpage_candidate_snapshot` to report which branch rejects, or
count the node list directly.

## Resolution — the oracle allocated an object it never published

The trace that ended the search, from an instrumented copy of the runtime
(the repository source was never modified):

```text
[SEL] plan_init -> batch_budget=8
[SEL] batch has_best=1 scan_complete=1 examined=2      <- a page IS found
[SEL] scan_begin -> commit_complete=0                   <- page scan begins
[ONE] snapshot ok, plan=0x16b0ecbf8 added=0             <- predicate ACCEPTS,
[ONE] snapshot ok, plan=0x16b0ecbf8 added=0                the ADD refuses
[SEL] page_objects added=0 examined=3 commit_complete=1
[SEL] loop end added=0 selected=0
```

`pcc_gc_relocation_set_add_preallocated` rejects any object carrying
`PY_FLAG_GC_FRESH_ALLOC`:

```c
if ((flags & (PY_FLAG_GC_RELOCATION_CANDIDATE | PY_FLAG_GC_RELOCATION_TARGET
            | PY_FLAG_GC_PINNED | PY_FLAG_GC_FRESH_ALLOC
            | PY_FLAG_GC_DEALLOCATING)) != 0) return 0;
```

`pcc_gc_alloc` sets that flag for container tags (`py_obj.c:331`), and
`pcc_gc_publish_initialized` clears it (`py_obj.c:380`) once a constructor has
finished initializing the object.  The drain oracle called raw `pcc_gc_alloc`
and never published, so its two lists carried `FRESH_ALLOC` forever — the
`0x4000` bit visible in the `0x14108` measured back in fact 2, whose
significance I missed at the time.

Refusing to relocate an object that was never published as initialized is
**correct**: moving a half-initialized object is not safe.  The runtime is
right and the probe was wrong.

Decisive confirmation, before touching the test:

```text
without publish:  select(8)=0  set_size=0
with publish:     select(8)=2  set_size=2
```

The fix is two `pcc_gc_publish_initialized` calls plus an extern declaration in
the oracle, since the symbol is runtime-internal and not in the public header.
The expectation `select(8) == 2` is unchanged — the probe now builds a valid
object rather than the assertion being weakened.

Gate: `tests/python/test_freestanding_gc_relocation_drain.py` -> `8 passed in
7.52s`.

## A real runtime inconsistency this exposed

`pcc_gc_backend4_zpage_candidate_snapshot` does **not** test
`PY_FLAG_GC_FRESH_ALLOC`, while `pcc_gc_relocation_set_add_preallocated` does.
So the selector will happily choose a page, begin a page scan and walk every
object on it, only to have the add refuse all of them — wasted scan work, and a
failure mode that reports as "selected nothing" with no indication why.  Filed
as `GC-P1-BACKEND4-FRESH-ALLOC-FILTER-DISAGREEMENT`.

## Not one cause after all

The task row grouped three symptoms as "plausibly one cause".  That grouping is
now **wrong**: the aging probe builds its objects with `py_list_new(0)`, a real
constructor that publishes, so it never carries `FRESH_ALLOC` and cannot be
failing for this reason.  Only the drain oracle used a raw allocation.  The
aging (`promotions=0 aged=0`) and task-forwarding (`[0,1,1]`) symptoms need
their own diagnosis.

## Superseded: the fork, and the two facts that were wrong

Exactly one of these is true, and the next slice should decide which before
changing code:

- **The page path regressed.** Selection is supposed to fall back to, or
  additionally cover, objects whose page is not itself a candidate.  Then
  `pcc_gc_backend4_best_relocation_page_batch_unlocked` returning nothing for a
  live zpage is the defect.
- **The object path was dropped deliberately.** Selection is now intentionally
  page-driven, and the drain test encodes a contract that was retired.  Then
  the defect is that the test was not updated in the same change, and the fix is
  to re-express the expectation — *not* to reinstate an object path.

Do not guess between these.  Fact 5 makes the second reading plausible, but a
deliberate retirement should have left a note, and none was found.

## Two hypotheses already refuted — do not retry

- `pcc_alloc_graph_leaf_tag` routing `PY_TYPE_LIST` around the zpage allocator.
  Measured false: the lists are zpage-allocated (fact 2).
- Fresh fully-live pages correctly failing a fragmentation test.  Measured
  false: fragmenting 48 of 64 objects leaves every score at 0 (fact 3).
- `PY_TYPE_LIST` being an unsupported relocate-copy tag, or 256 bytes failing
  the evacuation size policy.  Both measured false from source (fact 7).
- Forcing a node into the scan list with a malloc-backed payload span.  Reports
  success and changes nothing, for the reason given above.

## Nonclaims

- Nothing was fixed and no runtime file was modified.
- The aging (`promotions=0 aged=0`) and task-forwarding (`[0,1,1]`) symptoms
  were not investigated in this slice.  Whether they share this cause is still
  unestablished, and fact 3's "nothing is ever a candidate" is only suggestive.
- No bootstrap, stage, fixed-point or five-GC gate was run.
