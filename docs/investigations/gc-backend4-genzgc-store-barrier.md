# Investigation: Backend #4 GenZGC store barrier slice

## Status
active

## Problem Description
`#4` was upgraded to use a latest OpenJDK GenZGC reference pack, but the
runtime implementation still behaved mostly as a colored-relocating collector
without the generational store-barrier / remembered-set surface that modern
ZGC requires.

## Repro
Focused C-runtime probe shape:

```bash
env -u LC_ALL -u LC_CTYPE uv run pytest \
  tests/python/test_gc_backend4_production.py::test_backend4_genzgc_store_barrier_remembers_old_to_young_slot \
  -q -n0
```

The probe constructs an old list owner and a young child under
`PCC_GC_KIND_COLORED_RELOCATING`, stores the child through `pcc_gc_store_ptr`,
and checks:

- the owner is marked `PY_FLAG_GC_REMEMBERED`;
- `pcc_gc_backend4_generation_barrier_score()` reports the store barrier;
- `PCC_GC_COUNTER_GENZGC_STORE_BARRIERS` exposes the same telemetry;
- `pcc_gc_backend4_store_buffer_entries()` reports one pending
  store-buffer entry;
- a backend-4 step processes the precise remembered slot, keeps the owner
  remembered while another slot entry is pending, then clears the remembered
  flag when the queue drains;
- repeated writes to the same owner+slot are act-once while the entry is
  pending.
- a separate pre-store interleaving probe calls
  `pcc_gc_note_slot_write_barrier(owner, slot, value)` before the slot write
  is visible and verifies the queued value snapshot is still promoted when
  backend #4 drains the store buffer.

## Findings
OpenJDK `jdk-27+21` ZGC is generational. The reference files that matter for
this slice are:

- `docs/refs_docs/gc-research/zgc/zGeneration.cpp`
- `docs/refs_docs/gc-research/zgc/zRememberedSet.cpp`
- `docs/refs_docs/gc-research/zgc/zStoreBarrierBuffer.cpp`

The first pcc implementation slice should not pretend to be a full GenZGC
port. It only establishes the missing boundary in pcc's shared GC ABI:
backend #4 now observes old-to-young stores, records the remembered owner,
and has public telemetry for that event.

## Patch
Implemented:

- backend #4 allocations default to `PY_FLAG_GC_YOUNG` unless the caller
  explicitly marks them old;
- backend #4 `pcc_gc_step()` has a bounded young-to-old aging path and
  `PCC_GC_COUNTER_GENZGC_YOUNG_PROMOTIONS` telemetry;
- C runtime counter and helper:
  `pcc_gc_backend4_generation_barrier_score()`
- public telemetry counter:
  `PCC_GC_COUNTER_GENZGC_STORE_BARRIERS`
- public store-buffer counter:
  `PCC_GC_COUNTER_GENZGC_STORE_BUFFER_ENTRIES`
- pcc-Python runtime mirror global, telemetry, helper, and write-barrier logic
- backend #4 step hook that processes remembered owners before relocation work
- reference-bearing function-object relocation:
  `PY_TYPE_FUNC` now preserves native entry pointers and owned captures
- reference-bearing iterator relocation:
  `PY_TYPE_ITER` now preserves sequence references and iterator index
- reference-bearing generator relocation:
  `PY_TYPE_GEN` now preserves resume pointers, frame references, state,
  done flag, and send value
- reference-bearing coroutine relocation:
  `PY_TYPE_COROUTINE` now preserves name, native entry, captures, args,
  result, closed flag, and done flag
- reference-bearing task relocation:
  `PY_TYPE_TASK` now preserves coro/result/waiter and done state in both the
  C runtime and pcc-Python mirror
- reference-bearing exception relocation:
  `PY_TYPE_EXC` now preserves exc_class/message/cause/context and copies
  traceback storage instead of sharing the old exception's buffer
- class-object relocation:
  `PY_TYPE_CLASS` now deep-copies metadata arrays, retargets MRO self
  entries, retargets the class-attrs side table, and treats the owned attrs
  dict as a trace/promote/clear edge
- weakref relocation:
  `PY_TYPE_WEAKREF` now retargets the global intrusive weakref list from
  old weakref object to moved weakref object
- unstarted/handle-free thread-wrapper relocation:
  `PY_TYPE_THREAD` now preserves callable/args/result and lifecycle flags
  when no native thread handle is attached
- descriptor relocation:
  `PY_TYPE_PROPERTY`, `PY_TYPE_CLASSMETHOD`, and `PY_TYPE_STATICMETHOD` now
  preserve their owned function slots and are traced/promoted/cleared as
  descriptor shapes instead of being misclassified as user instances
- memoryview relocation:
  `PY_TYPE_MEMORYVIEW` now preserves and traces/promotes/clears its owned
  base object slot
- first page-policy telemetry slice:
  `pcc_gc_backend4_evacuation_candidate_score()`,
  `pcc_gc_backend4_evacuated_bytes()`, and
  `pcc_gc_backend4_page_policy_score()`
- large-object deferral telemetry:
  `pcc_gc_backend4_large_object_defer_score()` and
  `pcc_gc_backend4_large_object_deferred_bytes()`
- large-object reconsideration telemetry:
  `pcc_gc_backend4_large_object_reconsiderations()`
- small/medium page-class candidate telemetry:
  `pcc_gc_backend4_small_page_candidate_score()` and
  `pcc_gc_backend4_medium_page_candidate_score()`
- selected-byte telemetry:
  `pcc_gc_backend4_evacuation_candidate_bytes()`,
  `pcc_gc_backend4_small_page_candidate_bytes()`, and
  `pcc_gc_backend4_medium_page_candidate_bytes()`
- page-pressure telemetry:
  `pcc_gc_backend4_page_pressure_score()`
- store-buffer drain telemetry:
  `pcc_gc_backend4_store_buffer_drain_batches()` and
  `pcc_gc_backend4_store_buffer_drained_entries()`
- store-buffer backlog telemetry:
  `pcc_gc_backend4_store_buffer_incomplete_drains()`
- evacuation backlog telemetry:
  `pcc_gc_backend4_evacuation_incomplete_batches()`
- store-buffer act-once telemetry:
  `pcc_gc_backend4_store_buffer_duplicate_skips()`
- store-buffer sizing telemetry:
  `pcc_gc_backend4_store_buffer_high_water()` and
  `pcc_gc_backend4_store_buffer_owner_fanout_high_water()` and
  `pcc_gc_backend4_store_buffer_owner_count_high_water()`
- bounded store-buffer batch telemetry:
  `pcc_gc_backend4_store_buffer_batch_capacity()`,
  `pcc_gc_backend4_store_buffer_max_batch_size()`, and
  `pcc_gc_backend4_store_buffer_full_batches()`
- remembered-set slot telemetry:
  `pcc_gc_backend4_remembered_set_entries()`,
  `pcc_gc_backend4_remembered_set_duplicate_skips()`, and
  `pcc_gc_backend4_remembered_set_high_water()`
- page-keyed remembered-set telemetry:
  `pcc_gc_backend4_remembered_page_entries()`,
  `pcc_gc_backend4_remembered_page_slot_entries()`, and
  `pcc_gc_backend4_remembered_page_high_water()`
- minimal ZPage ownership telemetry:
  `pcc_gc_backend4_zpage_count()`,
  `pcc_gc_backend4_zpage_capacity_bytes()`,
  `pcc_gc_backend4_zpage_fragmentation_bytes()`, and
  `pcc_gc_backend4_zpage_large_pages()`,
  `pcc_gc_backend4_zpage_used_bytes()`, and
  `pcc_gc_backend4_zpage_fragmentation_per_mille()`,
  `pcc_gc_backend4_zpage_policy_score()`
- fragmentation policy telemetry:
  `pcc_gc_backend4_evacuation_efficiency_per_mille()`,
  `pcc_gc_backend4_fragmentation_backlog_bytes()`, and
  `pcc_gc_backend4_fragmentation_policy_score()`
- page-policy threshold telemetry:
  `pcc_gc_backend4_small_page_limit_bytes()`,
  `pcc_gc_backend4_medium_page_limit_bytes()`, and
  `pcc_gc_backend4_large_defer_limit_bytes()`
- cross-thread medium-buffer telemetry:
  `pcc_gc_backend4_store_buffer_cross_thread_medium_flushes()` and
  `pcc_gc_backend4_store_buffer_cross_thread_medium_flushed_entries()`
- young/old population telemetry:
  `pcc_gc_backend4_young_object_count()`,
  `pcc_gc_backend4_old_object_count()`,
  `pcc_gc_backend4_young_bytes()`, and
  `pcc_gc_backend4_old_bytes()`
- page-class live population telemetry:
  `pcc_gc_backend4_small_page_object_count()`,
  `pcc_gc_backend4_medium_page_object_count()`,
  `pcc_gc_backend4_large_page_object_count()`,
  `pcc_gc_backend4_small_page_live_bytes()`,
  `pcc_gc_backend4_medium_page_live_bytes()`, and
  `pcc_gc_backend4_large_page_live_bytes()`
- focused production test in `tests/python/test_gc_backend4_production.py`

The store-buffer implementation is an owner+slot queue, with free-hook
unlinking in both the C runtime and pcc-Python mirror. `pcc_gc_store_ptr()`
uses the slot-aware barrier, so backend #4 can process the exact pointer slot
instead of rescanning the whole owner. The old `pcc_gc_note_write_barrier`
entrypoint remains as a no-slot compatibility fallback for direct runtime
users such as class metadata updates.

Backend #4 also relocates pcc-native function objects now. The copied
`PY_TYPE_FUNC` object preserves its native entry pointer and takes a fresh
owned reference to the captures tuple rather than relying on raw `memcpy`
ownership. The focused regression calls the moved function after the read
barrier updates the root slot, proving that the moved closure still observes
its captures.

Backend #4 also relocates iterator objects. The copied `PY_TYPE_ITER` object
takes a fresh owned reference to the sequence object and preserves the current
index. The focused regression moves an iterator, follows the sequence through
the read barrier, and proves the moved iterator still reads the expected
element.

Backend #4 also relocates generator objects. The copied `PY_TYPE_GEN` object
preserves the native resume pointer, frame slot, state, done flag, and
send-value slot with fresh owned references for the pointer fields. The
focused regression moves a generator object and verifies the moved frame and
send value are still readable.

Backend #4 also relocates native coroutine shell objects. The copied
`PY_TYPE_COROUTINE` object preserves its name pointer, native entry pointer,
captures tuple, args tuple, result slot, and closed/done state. The focused
regression moves a coroutine shell and verifies all owned slots and state
fields survive the read-barrier update.

Backend #4 also relocates task shell objects. The copied `PY_TYPE_TASK`
object preserves coro/result/waiter slots and the `done` state. The
pcc-Python mirror previously preserved the three pointer slots but missed the
done field; it now mirrors the C runtime. The focused regression moves a task
shell and verifies all slots plus done survive relocation.

Backend #4 also relocates exception objects. The copied `PY_TYPE_EXC` object
takes fresh owned references to exc_class/message/cause/context and copies
the traceback frame buffer into new storage, so the moved exception does not
share a heap-owned traceback array with the old address. The trace, promote,
and clear paths now treat exc_class as an owned edge too. The focused
regression verifies the exception class slot, the three exception payload
slots, and the copied traceback shape survive relocation.

Backend #4 also relocates class objects. The copied `PY_TYPE_CLASS` object
deep-copies the bases, MRO, methods, and field-name arrays, rewrites copied
MRO self entries to the moved class address, retargets the C runtime
class-attrs side table from old class pointer to new class pointer, and
preserves the owned attrs dict edge in trace/promote/clear. The focused
regression verifies class-level attrs remain visible after the root slot
follows forwarding and that the moved class still points at the same attrs
dict identity.

Update 2026-05-14: the class attrs side table had drifted from the ABI after
`PyClassObject.attrs` became an explicit slot. The side table now acts only as
a lookup index; the class object owns the attrs dict through offset 104, C
relocation takes a fresh owned reference for the moved class, and both C and
pcc-Python deallocation clear that owned slot. This keeps the reference edge
visible to #4 trace/promote/clear and avoids relying on a pinned side table as
an implicit root. Because the side table is no longer an owning pinned root,
class attr lookup now refreshes the cached side-table pointer through
`pcc_gc_load_ptr((PyObject *)cls, &cls->attrs)` before returning it; this
prevents attrs-dict relocation from leaving the lookup table with a stale
pre-forwarding pointer. The focused class-attrs regression now relocates the
class first, then relocates the attrs dict itself, and finally requires
`py_class_getattr()` to refresh the moved class's attrs slot to the forwarded
dict.

The class relocation payload itself also reads `src->attrs` through the #4
read barrier before copying it. This keeps C and pcc-Python relocation
behavior aligned when the attrs dict was forwarded before the class shell is
copied. A focused regression now relocates the attrs dict first and then the
class, requiring the moved class to hold the forwarded attrs pointer.

The same rule applies to borrowed class metadata. Class relocation now copies
`bases`, `mro`, `methods[i].func`, and `del_method` through the #4 read
barrier instead of raw `memcpy`/direct loads. A focused regression forwards
the base class and method object before relocating the derived class, then
requires the moved metadata arrays to reference the forwarded stable-id
targets rather than the old addresses.

Runtime class metadata reads now follow the same rule. `py_class_lookup`,
`py_super_lookup`, `py_isinstance`, and the pcc-Python mirror resolve MRO
entries, instance class slots, and method function slots through
`pcc_gc_load_ptr` before returning or comparing them. A focused regression
forwards a method object without relocating the class and requires
`py_class_lookup()` to update the method slot to the forwarded target.
Class/object parameters are also resolved through `pcc_gc_note_relocation_read`
before lookup/comparison; a focused `py_isinstance()` regression passes the
old forwarded class pointer as the target class and requires the comparison to
still succeed.

Metadata writes now use slot-aware borrowed barriers as well. `py_class_add_method`
keeps borrowed ownership, but passes the exact `methods[i].func` or
`del_method` slot to `pcc_gc_note_slot_write_barrier` so backend #4 can retain
owner+slot precision in the store buffer. A focused regression covers an old
class receiving a young method object.

Instance field and dynamic-attribute reads now also use #4 load barriers.
`py_instance_get_field`, instance getattr/setattr/delattr helper paths, and
dataclass copy paths resolve `inst->cls`, field slots, and dynamic `__dict__`
slots through `pcc_gc_load_ptr` before returning or copying them. A focused
regression forwards an instance field value first and then requires
`py_instance_get_field()` to update the field slot to the forwarded target.
The instance shape predicate also accepts a forwarded class header without
writing the slot; the public read path then updates `inst->cls` through
`pcc_gc_load_ptr`. A focused regression forwards the class slot first and then
requires `py_instance_get_field()` to refresh the instance's class pointer.

Public class/instance entrypoints now also resolve forwarded class parameters.
`py_instance_new(old_forwarded_cls)` stores the moved class in the new instance
instead of embedding the stale source address, and `py_class_add_method` writes
metadata on the moved class when called with an old class pointer. Focused
regressions cover both forwarded-argument shapes. The class attrs API follows
the same rule: `py_class_setattr/getattr/delattr/__dict__` resolve the class
parameter before touching the attrs side table or slot, with a focused
regression covering `py_class_setattr(old_forwarded_cls, ...)`.
`py_class_new` also resolves base-class array entries before copying them and
uses the resolved base array for C3 linearization, so constructing a derived
class from an old forwarded base pointer records the moved base in both
`bases` and MRO. C3 candidate/tail comparisons also resolve MRO entries before
comparing or copying them, so a forwarded grandbase inside an existing base's
MRO is not copied into a newly constructed derived class.

List item reads are now part of the same reference-updating surface.
`py_list_get()` in both C and pcc-Python uses `pcc_gc_load_ptr` on the item
slot before incref/return. A focused regression forwards a list element first
and then requires `py_list_get()` to return the forwarded target and refresh
the list slot. Non-destructive list copy/read operations now follow the same
rule for concat/repeat/slice/extend/contains/index/count; a focused concat
regression requires both source and output list slots to reference the
forwarded item after copying.

Tuple item reads now follow the same reference-updating rule. `py_tuple_get`,
tuple slice/concat, and tuple cycle-detection item walks use `pcc_gc_load_ptr`
before returning, copying, or recursively inspecting tuple elements. Focused
regressions cover forwarded tuple elements through `py_tuple_get()` and
`py_tuple_concat()`.

Class attrs creation also now writes the first attrs dict through
`pcc_gc_store_ptr`. Without that barrier, an old class receiving its first
class variable could point at a young attrs dict without entering backend #4's
store buffer. A focused regression covers this old-class first-write case.

Backend #4 also relocates weakref objects. The copied `PY_TYPE_WEAKREF`
object preserves the borrowed target and owned callback while retargeting the
global intrusive weakref list from the old weakref node to the moved weakref
node. The focused regression invalidates the target after relocation and
verifies the moved weakref is still found and cleared.

The weakref unlink path now verifies list membership before mutating the
global head. This protects relocation failure cleanup and forwarded-source
deallocation from accidentally unlinking the moved weakref node.

Backend #4 also relocates descriptor shell objects. The copied
`PY_TYPE_PROPERTY` object preserves `fget`/`fset`/`fdel`, and
`PY_TYPE_CLASSMETHOD` / `PY_TYPE_STATICMETHOD` preserve their owned `func`
slot. The trace, promotion, and clear paths now handle descriptor tags before
the user-instance branch, so tags `PY_TYPE_USER + 1..3` are no longer treated
as `PyInstanceObject` layouts.

Backend #4 also relocates memoryview shell objects. The copied
`PY_TYPE_MEMORYVIEW` object preserves its owned `base` slot, and the trace,
promotion, and clear paths now treat that base as a real reference-bearing
edge.

Backend #4 also relocates handle-free thread wrappers. The copied
`PY_TYPE_THREAD` object preserves callable, args, result, and started/joined/
finished flags when the native handle is NULL. Relocation selection skips
thread wrappers that still have a native handle, because the native thread
entry may hold the old wrapper address until join/detach protocol work
exists. A focused selector probe now asserts that a thread wrapper with a
non-NULL handle is not selected and cannot be copied through the public
relocation API.

The same native-handle rule is now locked for `PY_TYPE_FILE` and thread sync
wrappers (`Lock`, `RLock`, `Event`, `Condition`, `Semaphore`). These objects
contain `FILE*`, mutex, and condition/semaphore state owned by the platform
substrate, so #4 must skip them until a dedicated quiesce/rebind protocol
exists.

The queue entry now retains an owner+slot+value snapshot. The value snapshot
closes the mutator/collector interleaving where the store barrier records the
slot before the actual pointer store is visible: a concurrent backend #4 step
can drain the entry and still promote the young value instead of reading a
stale NULL or old slot value. The side-table mutation is protected by the GC
graph lock in both the C runtime and the pcc-Python mirror. Pending-entry
dedupe is keyed by owner+slot+value, not just owner+slot, so repeated writes
of the same value remain act-once while different young values written to the
same slot each keep their own promotion snapshot.

Backend #4 now also exposes store-buffer drain batches and drained-entry
counts. This gives the default-GC matrix a visible batching pressure signal
before the runtime grows a true OpenJDK-style `zStoreBarrierBuffer` medium
path. It is still a linked-list drain seam, not the final batched buffer
implementation.

Backend #4 now bounds a remembered-root drain to a fixed store-buffer batch
capacity. A single `pcc_gc_step()` can have a larger work budget, but the
store-buffer phase drains at most one backend-4 batch before yielding to
other GC phases. `pcc_gc_backend4_store_buffer_batch_capacity()`,
`pcc_gc_backend4_store_buffer_max_batch_size()`, and
`pcc_gc_backend4_store_buffer_full_batches()` expose that policy so the
default-GC matrix can distinguish "many small drains" from "capacity-sized
backlog drains". This is still not OpenJDK's per-mutator medium-path buffer;
it is the bounded batch seam that lets pcc tune and validate that later path.

Backend #4 now also has a mutator-local medium store-buffer path. The C
runtime keeps a thread-local 32-entry buffer before publishing entries to the
global owner+slot+value queue and registers each mutator buffer with the GC.
`pcc_gc_step()` flushes all registered C mutator medium buffers under the
graph lock before draining a remembered-root batch; thread exit unregisters
the current buffer after flushing it so the registry does not keep stale TLS
addresses. The pcc-Python mirror keeps the same API and single-mutator
semantics with a runtime-high linked medium list. Pending entries are counted
as barrier work when they enter the medium buffer. Telemetry exposes medium
capacity, pending entries, flush count, flushed-entry count, and full-flush
count. C-runtime telemetry also separates cross-thread medium flushes from
current-mutator flushes so the matrix can prove that a collector phase
published work from another mutator. The pcc-Python mirror exports the same
cross-thread accessors as zero-valued stubs until its threaded object
registry exists. This is closer to OpenJDK's `ZStoreBarrierBuffer` shape,
but still not the final per-page remembered-set bitmap design.

A threaded C-runtime probe now proves the phase-boundary path directly: a
worker thread leaves five old-to-young stores in its TLS medium buffer, then
the main thread calls `pcc_gc_step()`. The step flushes the worker's registered
medium buffer, drains the remembered entries, and increments the cross-thread
medium-flush counters.

Incomplete-drain telemetry counts drain batches that leave store-buffer
backlog behind because the current GC step budget was too small. This is the
policy signal for choosing a future batch size / flush budget.

Evacuation incomplete-batch telemetry counts relocation batches that move at
least one selected candidate but leave the relocation set non-empty because
the current GC step budget was too small. This gives the default-GC matrix a
separate signal for page-evacuation backlog instead of conflating it with
store-buffer backlog.

Duplicate owner+slot+value enqueue attempts now increment
`pcc_gc_backend4_store_buffer_duplicate_skips()`. This gives the later
remembered-set / bitmap work an act-once pressure signal: if duplicate skips
are high, a true remembered-set bitmap should reduce repeated barrier work.
It is not itself a bitmap implementation.

The runtime also records store-buffer high-water entries. This is a sizing
signal for the future true batched buffer: the matrix can compare peak
pending entries against drained batches and duplicate skips before choosing a
buffer capacity / flush policy.

`pcc_gc_telemetry_reset()` treats store-buffer pending entries as live state,
not disposable telemetry. Reset reseeds pending-entry count, global
high-water, owner fanout high-water, and distinct-owner high-water from the
current linked-list buffer, while clearing epoch-only counters such as drain
batches and duplicate skips.

By contrast, store-buffer clear is a live-state transition: it empties the
buffer and explicitly resets pending-entry count plus global / owner-shape
high-water metrics to zero. This keeps backend switches from leaking stale
store-buffer shape telemetry into later measurements.

Backend #4 also groups remembered slots by slot-address page. This is not a
real `ZPage` bitmap, but the C runtime now keeps a 512-bit bitmap per 4096
byte slot page, with each `PyObject **slot` mapped by page-local offset. The
default-GC matrix can now see how many slot pages contain dirty references,
how many dirty slots are represented across those pages, and the
remembered-page high-water. This is the bridge from the current owner+slot
linked side table toward a ZPage-integrated remembered-set bitmap. The
pcc-Python mirror exports page counters as zero-valued stubs until its
threaded object registry exists.

The page-keyed remembered set now has a predicate/clear API:
`pcc_gc_backend4_remembered_page_contains_slot(slot)` and
`pcc_gc_backend4_remembered_page_clear_slot(slot)`. The C runtime answers from
the per-page slot bitmap and clearing a slot removes the corresponding
remembered-set entry as well, so the owner+slot side table and page bitmap do
not drift apart. The pcc-Python mirror exposes the same ABI using its
remembered-slot list as the backing representation. A focused probe verifies
that two slots on the same page set independent bits, a middle unset slot stays
clear, and clearing each slot decrements both page-slot and remembered-set
state.

Reset also treats the relocation set as live evacuation debt. Pending
relocation candidates reseed candidate count, candidate bytes, and
small/medium page-class breakdown after `pcc_gc_telemetry_reset()`, so a
measurement boundary does not hide work that was already selected but not yet
evacuated.

Backend #4 now exposes fragmentation-policy telemetry derived from the page
selector seam: evacuation efficiency reports evacuated-bytes over selected
candidate-bytes in per-mille units, fragmentation backlog reports selected
but not-yet-evacuated bytes plus large-object deferred bytes, and
fragmentation policy score combines backlog bytes with incomplete evacuation
batches. This does not replace a real ZPage fragmentation policy; it makes
the current selector's debt visible to the default-GC matrix.

The small-page limit, medium-page limit, and large-object defer cutoff are
also exported as telemetry/ABI. This keeps future tuning data-driven: the
matrix can report both the thresholds being used and the resulting backlog /
efficiency instead of depending on hard-coded magic numbers in tests or docs.

Large-object deferral now has an explicit reconsideration signal. When
`pcc_gc_telemetry_reset()` clears `PY_FLAG_GC_LARGE_DEFERRED`, backend #4
counts how many deferred large objects became eligible for a later selector
pass. The object may still be deferred again under the current policy, but
the matrix can now distinguish "never reconsidered" from "reconsidered and
deferred again because the threshold still rejects it".

Backend #4 now also exposes young/old population telemetry. The exported
object-count and byte-count accessors scan the active GC object list under
the graph lock and classify objects by `PY_FLAG_GC_YOUNG` /
`PY_FLAG_GC_OLD`. The aging regression now proves the counts move from one
young plus one explicit-old object to two old objects after bounded aging.
This is only observability for the current flag-based generation model; it
is not a full GenZGC heap-generation policy.

Backend #4 now also exposes active page-class population telemetry. The
small/medium/large object-count and live-byte accessors classify active GC
object nodes with the same threshold seam used by relocation selection. This
gives the default-GC matrix heap-shape data even when a page class has not
been selected for evacuation in the current epoch. It remains a thresholded
object-list model, not a `ZPage` allocator or page-table implementation.

Backend #4 now exposes minimal ZPage ownership telemetry. The C runtime and
pcc-Python mirror maintain a backend-local ownership side table at allocation,
free, and forwarded-source retirement, assigning an estimated page capacity by
size class: small objects reserve 4096 bytes, medium objects reserve 65536
bytes, and large objects round up to a 65536-byte multiple. This yields a
consistent page count, capacity bytes, fragmentation bytes, and large-page
count without rescanning the object list for these ZPage metrics.
The same seam also exposes used bytes and fragmentation per-mille so the
matrix can compare heap shapes across different total sizes without relying
only on absolute fragmentation bytes. It now also exposes fragmented-page
count, separating one large fragmented page from many fragmented pages with the
same total bytes. Relocation-set selection now enumerates this ownership table
and picks the highest-fragmentation eligible ZPage before applying the existing
object eligibility and large-object defer policy. This still does not mean
allocation already comes from a real `ZPageAllocator`.
`pcc_gc_backend4_zpage_policy_score()` combines ZPage ownership fragmentation
bytes, fragmented-page count, dirty-page count, remembered-slot pressure,
evacuation backlog bytes, and incomplete evacuation batches. It is the policy
seam for future selector work, not the final fragmentation policy.
The ZPage ownership table now also records remembered-slot pressure per owner:
unique old-to-young remembered-set inserts increment the owning ZPage's
`remembered_slots`, clear/remove/reset decrements it, and
`pcc_gc_backend4_zpage_policy_score()` includes this pressure. The same table
also exposes `pcc_gc_backend4_zpage_dirty_pages()`, a dirty-owner count that
separates "one owner with many dirty slots" from "many owners with one dirty
slot". This still is not a real ZGC per-page remembered-set bitmap, but it
closes the previous gap where remembered slots and synthetic ZPage ownership
were two unrelated side tables. Relocation selection also includes this
owner-local remembered-slot pressure in the candidate score, so two equally
fragmented synthetic ZPages prefer the one with dirty remembered slots instead
of relying only on list order. Zero-benefit synthetic ZPages are no longer
selected: if a page has no fragmentation and no remembered-slot pressure, the
selector treats it as ineligible instead of moving an object only because it is
present in the ownership list.

`pcc_gc_reset_relocation_set()` is the opposite live-state transition: it
clears the relocation set and explicitly resets candidate count, candidate
bytes, and small/medium page-class shape. This keeps page-policy telemetry
from leaking stale selected-candidate debt after the runtime intentionally
drops the relocation set.

Owner fanout high-water records the maximum number of pending slots attached
to a single owner. That is the bridge from the current linked-list
store-buffer to a real remembered-set bitmap: high fanout owners need bitmap
or card-style compression more than low-fanout owners.

Distinct-owner high-water records how many old owners are pending at once.
Together with owner fanout, this separates two different remembered-set
shapes: many owners with one slot each versus one owner with many dirty slots.

Backend #4 now also keeps a remembered-set side table keyed by unique
owner+slot. The store-buffer still keeps owner+slot+value snapshot entries
for correctness, but the remembered-set side table models the GenZGC bitmap
shape: rewriting the same dirty slot with a different young value increments
duplicate-skip telemetry instead of growing the slot set. Telemetry reset
reseeds live remembered-set entries and high-water, while clearing duplicate
skips as an epoch-local pressure signal.

This is still not a full OpenJDK `zStoreBarrierBuffer` / `ZRememberedSet`
port: pcc implements a linked owner+slot side table rather than per-page
bitmaps and still lacks cross-thread phase-boundary flushing for every
mutator buffer.

Backend #4 also has a first page-policy boundary now: relocation selection
uses small/medium page-class limits and skips large objects until pcc has a
real ZPage-style allocator / evacuation policy. The public page-policy score
records selected candidates plus evacuated bytes. Large-object skips are
counted separately through `pcc_gc_backend4_large_object_defer_score()` and
`pcc_gc_backend4_large_object_deferred_bytes()` so the default-GC matrix can
see how often and how many bytes the current selector defers instead of
silently treating it as success. A deferred-large-object flag makes this
idempotent per object; repeated selector passes do not inflate the same
large object's deferral cost. Small and medium candidates are counted
separately so the matrix can tell whether evacuation pressure comes from many
small objects or fewer medium objects; candidate bytes are also tracked so
the matrix can compare object count against evacuation volume. This is
intentionally only the selector / telemetry seam; it is not a complete
`zPageAllocator` or
`zRelocationSetSelector` port.

`pcc_gc_backend4_page_pressure_score()` reports selected candidate bytes plus
large-object deferred bytes. This keeps the older page-policy score stable
while giving the default-GC matrix a bytes-oriented pressure signal that does
not hide large-object work skipped by the current selector.

The large-object deferred flag is scoped to a telemetry epoch.
`pcc_gc_telemetry_reset()` clears the flag on live objects as well as the
counters, so repeated measurement windows can observe the same still-live
large object again without accumulating duplicate counts inside one window.

Dict lookup now uses relocation read barriers for both key and value slots.
`py_dict_lookup()` loads `DictEntry.key` through `pcc_gc_load_ptr()` before
hash/equality matching, `py_dict_get()` loads `DictEntry.value` the same way
before returning it, and rehash compaction copies barrier-resolved key/value
slots into the new entries buffer. The pcc-Python mirror follows the same
slot addresses. The focused backend #4 probe relocates both a dict key and
value after insertion and proves `py_dict_get()` with the forwarded key
returns the forwarded value, not the stale old address.
The same entry helpers now cover dict traversal and destructive paths:
delete/clear, keys/values/items, and update resolve live key/value slots
before handing them to decref/list/tuple/update operations. A focused probe
relocates a dict key and value, then proves `py_dict_keys()` and
`py_dict_values()` return forwarded objects and rewrite the underlying
entry slots.

The backend #4 telemetry counters also now keep internal storage names
separate from public ABI accessor names. In the C runtime, static counters
that back functions such as `pcc_gc_backend4_evacuated_bytes()` use a
private `_count` suffix. In the pcc-Python mirror, `global_addr(...)`
storage follows the same private naming and `py_substrate.py` defines the
matching globals. This avoids self-host link/codegen failures where an
internal telemetry cell was declared with the same symbol as an exported
function.

Set lookup and traversal now use relocation read barriers for live key
slots. Both the C runtime and pcc-Python mirror skip NULL/tombstone slots,
but load real `SetEntry.key` values through `pcc_gc_load_ptr()` before
contains/equality checks, rehash moves, update/intersection/difference,
subset/items traversal, and remove. The focused backend #4 probe relocates
a set element after insertion and proves `py_set_contains()` with the
forwarded key rewrites the table slot away from the stale address.

The object comparison/hash layer now also participates in relocation read
barriers instead of bypassing the container APIs. `py_obj_cmp_threeway()`,
`py_obj_eq()`, tuple hashing, and set-specialized `py_obj_sorted()` load
list/tuple/dict/set slots through `pcc_gc_load_ptr()` before recursive
compare/hash/contains operations. Memoryview byte comparison resolves the
base object through the same barrier. The pcc-Python mirror uses matching
helpers for memoryview bases and dict/set entry traversal. A focused backend
#4 probe relocates elements already stored inside tuple/list/dict/set
containers and proves the lower-level object comparison path rewrites stale
slots to their forwarded addresses.

The native JSON dumping helper now follows the same rule. `json_dump_value()`
loads list elements and dict key/value entries through `pcc_gc_load_ptr()`
before recursively emitting JSON. A focused backend #4 probe relocates
strings already stored in a list and dict, calls `py_json_dumps()`, verifies
the emitted JSON text, and checks the list/dict slots were rewritten to the
forwarded addresses.

The print/format helper now resolves sequence slots too. List repr, tuple
repr, and `py_print_many()` load their element slots through
`pcc_gc_load_ptr()` in both the C helper and pcc-Python mirror before
formatting. A focused backend #4 probe relocates strings already stored in a
list and two tuples, prints them, verifies the stdout text, and checks the
sequence slots were rewritten to forwarded addresses.

The native os.path sequence helpers now resolve sequence slots before
coercion. `py_os_path_join()` in both the C runtime and pcc-Python mirror
loads list/tuple path parts through `pcc_gc_load_ptr()`, and the shared C
`py_os_path_commonpath()` helper does the same. A focused backend #4 probe
relocates strings already stored in a list and tuple, calls join and
commonpath, verifies the resulting strings, and checks the original
sequence slots were rewritten to forwarded addresses.

`str.join()` now also resolves list items through relocation read barriers
in both the C runtime and pcc-Python mirror. The first sizing pass and the
second copy pass load list slots with `pcc_gc_load_ptr()`. A focused backend
#4 probe relocates strings already stored in the join list, calls
`py_str_join()`, verifies the joined string, and checks the list slots were
rewritten to forwarded addresses.

Iterator sequence slots now resolve through relocation read barriers too.
`py_obj_next()` and `py_dealloc_iter()` in both the C runtime and
pcc-Python mirror load the iterator's `seq` slot with `pcc_gc_load_ptr()`
before using or releasing it. A focused backend #4 probe relocates the
sequence object already captured by an iterator, calls `py_obj_next()`, and
checks the iterator slot was rewritten to the forwarded sequence address.

Weakref callback slots now use the normal strong-reference barrier path.
The weak target remains a borrowed weak pointer resolved with
`pcc_gc_note_relocation_read()`, but the callback is stored with
`pcc_gc_store_ptr()` and loaded with `pcc_gc_load_ptr()` before callback
dispatch or deallocation in both the C runtime and pcc-Python mirror. A
focused backend #4 probe relocates the callback function already stored in a
weakref, invalidates the target, verifies the callback ran, and checks the
weakref callback slot was rewritten to the forwarded function address.

Exception object strong-reference slots now use the GC slot ABI as well.
`exc_class`, `message`, `cause`, and `context` are stored with
`pcc_gc_store_ptr()` and loaded with `pcc_gc_load_ptr()` in constructors,
setters, accessors, and deallocation in both the C runtime and pcc-Python
mirror. A focused backend #4 probe relocates the message/cause/context
objects after they are installed in an exception, calls the public exception
accessors, verifies the accessors return forwarded objects, and checks the
exception slots were rewritten away from stale addresses.

Function capture slots now resolve through relocation read barriers at call
and deallocation time. `py_func_call()` in both the C runtime and
pcc-Python mirror loads the `captures` tuple with `pcc_gc_load_ptr()` before
invoking the native entry adapter, and `py_dealloc_func()` does the same
before releasing it. A focused backend #4 probe relocates the captures tuple
after it is installed in a function object, calls the function, verifies the
captured value is still returned, and checks the function slot was rewritten
to the forwarded captures tuple.

Thread wrapper object slots now use read barriers on the synchronous/unstarted
path. `py_threading_thread_invoke()` loads `callable` and `args` with
`pcc_gc_load_ptr()`, `py_threading_thread_main()` loads `result` before
returning it, and thread deallocation loads `callable`, `args`, and `result`
before releasing them in both the C runtime and pcc-Python mirror. A focused
backend #4 probe relocates a thread's args tuple before synchronous
`Thread.start()`, verifies the target receives the forwarded args, and checks
the thread args slot was rewritten.

Module static attribute slot fallback now resolves forwarded values as well.
`py_module_attr_value_or_default()` treats its `PyObject **slot` argument as
an owner-less root/slot and returns `pcc_gc_load_ptr(NULL, slot)` rather than
the raw pointer. A focused backend #4 probe relocates the object held in a
module-style static slot, calls the helper, verifies it returns the forwarded
object, and checks the slot was rewritten.

Generic object dispatch now also respects forwarding for class and exception
slots. `py_obj_type_name()` and `py_type_builtin()` load exception
`exc_class` and instance `cls` through `pcc_gc_load_ptr()`, and generic
exception attribute dispatch loads `value`, `__cause__`, and `__context__`
through the same barrier in both the C runtime and pcc-Python mirror. Focused
backend #4 probes relocate an exception's class/message/cause/context and an
instance's class after installation, then verify generic type/attribute
dispatch returns forwarded objects and rewrites the stale slots.

Memoryview base slots now use the same strong-reference slot ABI. Both
`py_memoryview_new()` and the pcc-Python mirror initialize `base` through
`pcc_gc_store_ptr()`, while byte access/decode/len paths and deallocation load
the base through `pcc_gc_load_ptr()`. A focused backend #4 probe relocates the
bytes object behind a memoryview, calls `py_bytes_getitem()` through the
memoryview, verifies the byte result, and checks the memoryview's base slot was
rewritten to the forwarded object.

Exception TLS implicit context chaining now resolves forwarded current
exceptions before storing `__context__`. `py_raise()` uses a relocation read
for the TLS current exception, updates the TLS slot if it had been forwarded,
loads the new exception's existing context through `pcc_gc_load_ptr()`, and
stores the implicit context through `pcc_gc_store_ptr()` in both C and the
pcc-Python mirror. The same mirror path now loads instance exception classes
through the barrier during normalization. A focused backend #4 probe relocates
the currently pending exception, raises a second exception, and proves the
second exception's `__context__` points at the forwarded object rather than the
stale address.

Container deallocation now barrier-loads reference slots before releasing them.
`py_dealloc_list()`, `py_dealloc_tuple()`, `py_dealloc_dict()`, and
`py_dealloc_set()` in both C and the pcc-Python mirror call
`pcc_gc_load_ptr()` on their live item/key/value slots before `py_decref()`.
A focused backend #4 probe relocates an object already stored in list, tuple,
dict, and set containers, releases the containers, and asserts the dealloc path
emitted relocation read barriers instead of raw-decrefing stale slot values.

List mutation paths now follow the same rule. `pop`, `remove`, `clear`, slice
deletion/replacement helpers, and `reverse` load existing list item slots
through `pcc_gc_load_ptr()` before comparison, return, release, or movement.
Slice replacement writes use `pcc_gc_store_ptr()` for replacement values in
both C and the pcc-Python mirror. A focused backend #4 probe relocates an item
already stored in lists, then exercises `pop`, `remove`, `clear`, and
`reverse`, verifying the operations return or retain the forwarded object and
leave no old addresses.

GC callback-list removal now uses backend #4 read barriers as well. The custom
callback equality path resolves forwarded callback objects and loads function
captures through `pcc_gc_load_ptr()`, and `py_gc_callbacks_remove()` loads the
list item slot through `pcc_gc_load_ptr()` before comparison/removal in both C
and the pcc-Python mirror. A focused backend #4 probe relocates a callback after
it has been appended to the global GC callback list, removes it via the
forwarded callback root, and asserts read-barrier telemetry plus an empty
callback list.

The optional libpython bridge now also treats memoryview base as a moving-GC
slot. `py_cpy_from_pcc_obj()` loads `PyMemoryViewObject.base` through
`pcc_gc_load_ptr()` before recursively converting it to a CPython object, so
compatibility-mode conversion does not read a stale backend #4 forwarded base.

Unhandled-exception printing now loads exception PyObject slots through backend
#4 read barriers. `py_exc_print_unhandled()` and its pcc-Python mirror load
`exc_class`, `message`, `cause`, and `context` through `pcc_gc_load_ptr()`
before formatting chained exceptions or headings; the traceback frame buffer
remains a malloc-owned non-PyObject array. A focused backend #4 probe relocates
an exception message already stored in the exception, calls
`py_exc_print_unhandled()`, and checks that the exception slot was rewritten to
the forwarded message.

Exception matching now resolves forwarded class inputs and MRO entries. The C
runtime and pcc-Python mirror route exception `exc_class` slots and class
`mro[i]` entries through backend #4 read barriers during `py_exc_matches()`.
A focused probe relocates a base class after it has already been stored in a
derived class MRO and verifies that matching against the forwarded base still
succeeds and rewrites the MRO entry.

User dunder dispatch now loads instance class slots through backend #4 read
barriers. The C runtime and pcc-Python mirror read `PyInstanceObject.cls` via
`pcc_gc_load_ptr()` before looking up `__str__`, `__hash__`, `__iter__`,
`__next__`, or `__del__`; the C finalizer path also reads cached
`del_method` through `pcc_gc_load_ptr()` and records a slot write barrier when
caching a newly found finalizer. A focused probe relocates an instance's class
before calling `py_user_str_dispatch()` and verifies the instance `cls` slot is
rewritten to the forwarded class.

Function relocation-copy now loads its `captures` source slot through backend
#4 read barriers. The previous relocation payload path copied
`PyFuncObject.captures` directly, so relocating a function after its capture
tuple had already forwarded could preserve the stale tuple address in the new
function object. Both C and pcc-Python runtime mirrors now use
`pcc_gc_load_ptr()` for this source slot; a focused probe relocates the
captures tuple first, then relocates the function, and verifies the moved
function points at the forwarded tuple.

Class method-table metadata is explicitly not a moving-GC object slot.
`PyClassObject.methods[i].func` and `PyClassObject.del_method` contain raw
generated callable/code pointers, unlike descriptor objects such as
`PyClassMethodObject.func`. Class lookup and backend #4 class relocation-copy
now read/copy those metadata pointers raw, and class trace/promotion skips
them. Bases, MRO, and attrs remain barrier-managed object slots. This fixes a
pcc1 backend #4 crash where method lookup tried to apply the relocation read
barrier to a text/code address.

A regression asserts the C runtime and pcc-Python mirror keep method-table
metadata out of the backend #4 slot protocol, and the pcc1 backend #4 smoke
compile passes with the rebuilt stage binary.

ZPage policy now carries generation age pressure. The synthetic backend #4
ZPage owner table exposes young-page and old-page counts through the C runtime,
pcc-Python mirror, telemetry counters, and Python frontend ABI:

```text
PCC_GC_COUNTER_GENZGC_ZPAGE_YOUNG_PAGES = 100
PCC_GC_COUNTER_GENZGC_ZPAGE_OLD_PAGES = 101
pcc_gc_backend4_zpage_young_pages()
pcc_gc_backend4_zpage_old_pages()
```

`pcc_gc_backend4_zpage_policy_score()` now includes old-page pressure in
addition to fragmentation, evacuation backlog, remembered slots, dirty pages,
and fragmented pages. This is still not a real GenZGC page allocator, but it
prevents the selector/policy surface from being purely fragmentation/dirty
page based while the synthetic ZPage layer is being replaced.

The relocation selector now consumes the same age pressure: when two
otherwise-equivalent synthetic ZPages have the same fragmentation and
remembered-slot score, an old-page owner gets an extra score point and is
selected first. This keeps the selector path aligned with the exposed ZPage
policy score rather than leaving age pressure as telemetry-only state.

Validation for this slice:

```text
tests/python/test_gc_backend4_production.py::test_backend4_genzgc_zpage_ownership_telemetry
tests/python/test_gc_backend4_production.py::test_backend4_genzgc_zpage_tracks_generation_age_pressure
tests/python/test_gc_backend4_production.py::test_backend4_public_telemetry_symbols_are_wired
3 passed in 6.69s

tests/python/test_gc_backend4_production.py
84 passed in 272.92s

make -B -C pcc/py_runtime libpy_runtime_pcc_py.a
success

tests/python/test_pcc_bootstrap_full.py::test_full_three_stage_bootstrap_self
1 passed in 63.57s

tests/python/test_fallback_baseline.py tests/python/test_ir_py_fallback_baseline.py
17 passed in 58.95s
```

Validation after wiring age pressure into the selector:

```text
tests/python/test_gc_backend4_production.py::test_backend4_genzgc_zpage_tracks_generation_age_pressure
tests/python/test_gc_backend4_production.py::test_backend4_genzgc_selector_prefers_old_zpage_age_pressure
tests/python/test_gc_backend4_production.py::test_backend4_genzgc_selector_uses_zpage_remembered_pressure
tests/python/test_gc_backend4_production.py::test_backend4_genzgc_selector_skips_zero_benefit_zpage
4 passed in 13.53s

tests/python/test_gc_backend4_production.py
85 passed in 277.64s

make -B -C pcc/py_runtime libpy_runtime_pcc_py.a
success

tests/python/test_fallback_baseline.py tests/python/test_ir_py_fallback_baseline.py
17 passed in 58.99s

tests/python/test_pcc_bootstrap_full.py::test_full_three_stage_bootstrap_self
1 passed in 64.15s

tests/python/test_pcc_bootstrap_full.py::test_full_three_stage_bootstrap_self
1 passed in 65.51s
```

2026-05-15 update: synthetic ZPage remembered-card pressure is now wired.

The C runtime now has a separate `PccGcZPage` object behind the owner mapping
node. The current allocation policy still creates one synthetic page per object,
but page state is no longer stored directly on the owner mapping. `PccGcZPage`
stores a 64-card remembered bitmap, per-card refcounts, and a
`remembered_cards` population count. The constants are explicit: the existing
page-keyed remembered table exposes 512 slot bits, and each synthetic ZPage card
covers eight remembered-slot bits. Unique remembered slots now update both
owner-level remembered-slot pressure and synthetic ZPage card pressure without
making `remembered_cards` collapse to `remembered_slots`. The public surface exposes
`pcc_gc_backend4_zpage_remembered_cards()` and
`PCC_GC_COUNTER_GENZGC_ZPAGE_REMEMBERED_CARDS = 102`.

The policy and relocation selector now consume the same signal:

- `pcc_gc_backend4_zpage_policy_score()` includes remembered cards in addition
  to remembered slots, dirty pages, fragmented pages, and old-page pressure.
- `pcc_gc_backend4_zpage_relocation_score()` adds remembered-card pressure so
  dirty-card owners are preferred even before the real page allocator lands.
- `pcc_gc_backend4_zpage_remembered_card_ratio_per_mille()` exposes card
  density as read-only telemetry. It is intentionally not folded into the
  relocation selector yet; selector behavior still uses absolute slot/card/page
  pressure until enough matrix data exists to tune a density policy.
- The pcc-Python mirror keeps a private ZPage card pressure count. It does not
  implement the C-side card bitmap/refcount table or the eight-slots-per-card
  grouping because the mirror still lacks a real pointer-page substrate. For
  now, the mirror preserves the ABI/policy/selector surface needed for
  self-host; C runtime tests are the source of truth for card grouping.
- `test_backend4_genzgc_zpage_tracks_owner_remembered_slots` now covers both
  adjacent remembered slots that share one card and a slot at index 8 that
  crosses into a second card. Clearing one slot from a shared card must not
  drop the card until the last slot in that card is cleared.
- The public telemetry wiring test pins
  `PCC_GC_COUNTER_GENZGC_ZPAGE_REMEMBERED_CARDS = 102`, so the metric remains a
  stable ABI counter rather than a name-only source marker.
- The same wiring test pins
  `PCC_GC_COUNTER_GENZGC_ZPAGE_REMEMBERED_CARD_RATIO_PER_MILLE = 103`.

Validation is pending for this slice; required gates remain:

```text
tests/python/test_gc_backend4_production.py::test_backend4_genzgc_zpage_tracks_owner_remembered_slots
tests/python/test_gc_backend4_production.py::test_backend4_public_telemetry_symbols_are_wired
tests/python/test_gc_backend4_production.py
make -B -C pcc/py_runtime libpy_runtime_pcc_py.a
tests/python/test_pcc_bootstrap_full.py::test_full_three_stage_bootstrap_self
tests/python/test_fallback_baseline.py tests/python/test_ir_py_fallback_baseline.py
```

Validation after remembered-card/refcount/density telemetry:

```text
tests/python/test_gc_backend4_production.py::test_backend4_genzgc_zpage_tracks_owner_remembered_slots
tests/python/test_gc_backend4_production.py::test_backend4_public_telemetry_symbols_are_wired
2 passed in 3.58s

tests/python/test_gc_backend4_production.py
85 passed in 277.61s

make -B -C pcc/py_runtime libpy_runtime_pcc_py.a
success

PCC_BOOTSTRAP_PROFILE_DIR=/tmp/pcc-bootstrap-profile \
tests/python/test_pcc_bootstrap_full.py::test_full_three_stage_bootstrap_self
1 passed in 63.80s

tests/python/test_fallback_baseline.py tests/python/test_ir_py_fallback_baseline.py
17 passed in 59.20s
```

## Remaining Risk
This is not full modern GenZGC parity.

Still open:

- full young/old heap policy for backend #4 beyond default-young allocation,
  bounded aging, population telemetry, and synthetic ZPage young/old pressure;
- pcc-Python threaded mirror flushing once the mirror has a real threaded
  object registry;
- full ZPage-integrated remembered-set bitmaps equivalent to
  `zRememberedSet.*`; current remembered slots/cards are attached to synthetic
  ZPage owners for pressure accounting, and the C runtime has a synthetic
  64-card bitmap/refcount table, but this is still not backed by a real ZPage
  allocator;
- page allocator / evacuation policy equivalent to `zPage*` and
  `zRelocationSetSelector.*` beyond the current small/medium selector seam
  and page-class live telemetry;
- broader reference-bearing relocation coverage beyond list/tuple/dict/set/
  instance/Class/Task/Func/Iter/Gen/Coroutine/Exc/WeakRef/handle-free Thread
  and scheduler queue root/free update;
- native-handle thread-object relocation requires a thread-runtime protocol
  because the native thread entry may still hold the old wrapper address;
- budget tuning for store-buffer and evacuation backlog based on matrix data;
- broader pcc1/self-backend and threaded stress evidence.
