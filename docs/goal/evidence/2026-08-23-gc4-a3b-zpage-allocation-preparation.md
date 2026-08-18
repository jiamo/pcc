# GC4 A3b ZPage allocation preparation

Date: 2026-08-23

## Claim

C and strict pcc-Python `pcc_gc_backend4_try_zpage_alloc` no longer allocate
ZPage metadata or backing spans, clear a reusable/fresh page span, or zero the
reserved object range while holding the GC graph lock.

The allocator now detaches one reusable page or creates one fresh private page,
unlocks, resets and fully backs that private page, then reacquires the graph
lock and revalidates the backend plus active-page state.  It publishes only a
fully prepared page.  If another allocator installed a usable active page
first, a detached cached page is restored to the free list under the lock and
a never-published fresh page is freed after unlock.  The chosen object range is
reserved and counted as pending under the lock, so clearing it after unlock
cannot make the page selectable or recyclable before object registration.

An allocation failure frees the still-private page/span and leaves live/free
page metrics unchanged.  No partial page is published.

## RED chronology

The new C/strict source-order contract was genuinely RED on the previous
implementation:

```text
AssertionError: assert 'prepared_page' in strict_alloc
1 failed in 0.09s
```

The former code allocated page metadata under the graph lock and invoked the
span-allocating reset there; it also zeroed the returned object range before
unlock.  The new source test checks each allocator/reset/zero-fill call against
the most recent lock/unlock boundary rather than relying on a single lexical
slice.

The exact strict closure then correctly reported the newly used
`pcc_gc_backend4_free_page_head` global.  That exact expected import was added;
the closure was not widened beyond the real dependency.

## Invalid pthread harness and diagnosis

The first concurrent behavior-preservation attempt linked raw pthreads against
the default nonthreaded runtime archives.  It aborted with `SIGABRT/-6` and an
empty stderr.  LLDB at `malloc_error_break` showed several worker threads in:

```text
pcc_gc_index_rehash_slots
pcc_gc_object_index_insert
pcc_gc_note_object_allocated_sized
pcc_gc_alloc
```

This was a harness error, not a ZPage verdict: `PCC_WITH_THREADS=0` deliberately
provides no concurrent graph-lock exclusion.  The valid regression uses the
content-addressed threaded C and threaded strict archives and calls
`pcc_thread_unregister_current` before each raw worker exits.  The invalid run
is retained as `build/gc4-a3b-zpage-prepare-dynamic.log`, SHA-256
`2c2fd00cf29672ce0f16c6a74eadc90a34a713455782d91da769400f2fd421f1`,
so it is not reused as product evidence.

## Frozen source identity

```text
ff742d97b5055c6109d71d0eef017c99c3447eeb7fd92dd52453a51aa11caf4d  pcc/py_runtime/src/py_gc_backend.c
ed3c9b3917e77d2beed2fa7562c5fa1c9967994b0210fba75751f893abbbf8d1  pcc/py_runtime/py/freestanding_gc_zpage_allocation.py
c0bf74d85fcac5a1120a9ea3249d2198cab0a9ca412c5bb368c032a364057549  tests/python/test_freestanding_gc_zpage_allocation.py
aa948b68b39b59dddb950545b5bb08e39ee26a19346f8f47ac19464e0abf07da  tests/python/test_freestanding_gc_zpage_lifecycle.py
```

## Focused gates

The final packet covered the source order, strict LLVM/self closure, production
strict owner, all three page classes, an impossible 4 EiB span allocation,
16-way true-pthread cold-page publication in threaded C/strict archives, cache
limit reuse, large-page retirement and owner payload-tail reuse:

```text
13 passed in 262.07s
```

Log: `build/gc4-a3b-zpage-prepare-final.log`, SHA-256
`241755b0667d30fff751b930860734c687173804d36868c4cee9c3463dc58571`.
The corrected standalone threaded node also passed in 129.25 seconds; log
`build/gc4-a3b-zpage-prepare-dynamic-v2.log`, SHA-256
`50618c336cef81e5edffc2f0cbcc1face6f23c1b09de29bce7821b94c3c1c2b5`.

Python byte compilation, C syntax with `PCC_WITH_THREADS=0` and `=1`, and
`git diff --check` are green.  No pytest, LLDB, bootstrap or compiler child was
left running.

## Open boundary

This result closes the raw ZPage allocation entry, not every allocation in
object registration.  C `pcc_gc_note_object_allocated_sized` still calls
`pcc_gc_object_node_alloc`, allocation-capable object-index insertion and
`pcc_gc_backend4_zpage_track_alloc_unlocked` under its graph lock.  The strict
mirror prepares the object node earlier, but object-index growth and ZPage-node
plus fallback page/span allocation remain under the same outer lock.  The next
finite A3b slice must preplan those registration resources outside the lock and
commit them allocation-free while preserving index growth races, malloc-backed
fallback admission and failure behavior.

Relocation-reset retirement, GC3 promotion/remembered-owner work, root
callbacks, tripwire/log paths and the remaining bounded-scan audit still block
A3c.  Raw container transactions, collector-owned Backend 4 STW, raw leases,
page/source lifetime and ABA, broad parity, performance and fixed point are not
claimed.
