# 001 — per-operation cost table: pcc-compiled runtime vs CPython (2026-09-04)

Tool: `scripts/pcc_per_op_cost_table.py --out-dir build/per-op-cost-v1 --n 1000000`
(receipts `part1.json`, `part2.json`, `part3.json`, merged `all.json`).  Each
program isolates one operation in a counted loop; the same source runs as a
pcc binary (`--backend self --python-libpython=off`, host pcc0 compile, the
runtime pcc1 itself executes) and under the project CPython (3.15rc1).
`/usr/bin/time -lp` instructions retired and wall at N and 2N; per-iteration
cost = (2N - N) / N, which cancels startup.  Every pcc output equals CPython's.

"minus loop" subtracts the `int_add` row (the loop skeleton itself:
`total += i; i += 1; i < N`) to expose the marginal cost of the extra
operation; CPython's loop skeleton is 718 instructions (interpreter dispatch),
pcc's is 1288.

```text
op                  pcc instr/it  cpy instr/it  ratio | minus loop: pcc  cpy   ratio | pcc ns  cpy ns
str_strip_split            14492          2664   5.4x |          13204  1946   6.8x |    920    150
try_except_raise            8729          3443   2.5x |           7441  2725   2.7x |    630    180
call_returns_obj            7742          1269   6.1x |           6454   552  11.7x |    480     60
alloc_small_object          6283          1758   3.6x |           4994  1040   4.8x |    440     80
tuple_pack_unpack           6058          1371   4.4x |           4769   654   7.3x |    410     80
str_eq_dispatch             5911           882   6.7x |           4622   164  28.1x |    380     40
attr_store                  4237           384  11.0x |           2948  -333    -- |    260     20
dict_set_str                3939           695   5.7x |           2650   -23    -- |    230     40
dict_get_str                3872           843   4.6x |           2583   126  20.6x |    230     30
str_of_int                  3699          1453   2.5x |           2411   735   3.3x |    240     60
isinstance_class            2852           910   3.1x |           1563   193   8.1x |    170     40
str_concat_small            2481           676   3.7x |           1193   -41    -- |    160     30
list_append_pop             1939           901   2.2x |            651   184   3.5x |    110     50
method_call                 1839          1051   1.7x |            550   334   1.6x |    120     50
list_index                  1767           683   2.6x |            479   -34    -- |    110     20
attr_load                   1578           561   2.8x |            290  -157    -- |    100     20
call_int2                   1556           946   1.6x |            267   229   1.2x |    110     40
int_add_annotated           1306           672   1.9x |           1306   672   1.9x |     90     30
int_add_local_n             1306           684   1.9x |           1306   684   1.9x |     80     20
int_add                     1288           718   1.8x |           1288   718   1.8x |     90     40
for_over_list                678           243   2.8x |           -610  -475    -- |     40      0
```

## Reading

- The loop skeleton alone is 1.8x CPython: pcc lowers `total += i` and `i += 1`
  to `py_int_add` runtime calls and `i < N` to `py_int_cmp`, and wraps each
  call in the root protocol (per loop body: 24 `pcc_gc_load_ptr`, 19 unpin,
  13 store_root, 10 pin, 7 release).  Annotating the locals `: int` or copying
  N into a local changes nothing (1306 instr/it).  No inline tagged-small-int
  lane is in effect for this shape; the "value projection" of `int` the
  north star describes is not what the generated code does today.
- Marginal costs (minus the loop): a user call returning a list 6454 vs 552
  (11.7x), a dict lookup with a str key 2583 vs 126 (20x), an `==` chain over
  8 short strings 4622 vs 164 (28x), `.strip().split(",")` 13204 vs 1946
  (6.8x), tuple pack+unpack 4769 vs 654 (7.3x), object allocation 4994 vs 1040
  (4.8x), isinstance 1563 vs 193 (8x), attribute store 2948 vs ~0 (CPython's
  STORE_ATTR is cheaper than its loop noise).
- Ratios of 5-28x on exactly the operations a compiler is made of (dict
  lookups, string compares, tuple traffic, allocation, calls returning
  containers) are the per-operation gap behind Stage2 = 8.2x Stage1; the
  cheap ops (`call_int2`, `method_call`, `attr_load`) are already close to 1x.

## Claim boundary

Instruction counts are deterministic to <0.1%; nanoseconds are single runs on
a busy machine and are shown for orientation only.  Host pcc0 compiled these
binaries; pcc1 emits the same lowering, so the runtime cost is what pcc1 pays.

## Update 2026-09-04 — per-call protocol fixes, first A/B (steps 1-3)

Dissecting `total += i` showed ~20 runtime calls around 10 inline
instructions: a temporary root frame for the lhs (store_root, frame_enter_lifo,
three reloads, frame_leave_lifo) although the rhs was a plain local load,
operand pins and the `py_err_occurred` probe on the inline fast path, and
`i < N` always calling `py_int_cmp`.  Fixed in
`exact_int_lowering.py`/`binary_op_lowering.py`: no temp root when the rhs is
a bound Name / int / bool literal (`_exact_int_operand_is_gc_quiet`), pins and
error check only inside the slow (bignum) block, and an inline tagged compare.
Ratchet: `tests/python/test_exact_int_loop_protocol_ratchet.py` (IR shape +
GC0..4 semantics incl. tagged -> bignum promotion).

```text
op                pcc instr/it before  after   cpython   before x  after x
int_add                  1288           440      716      1.8       0.61
call_int2                1556           707      947      1.6       0.75
attr_load                1578           982      561      2.8       1.75
list_index               1767          1180      683      2.6       1.7
dict_get_str             3872          3281      854      4.6       3.8
str_eq_dispatch          5911          4210      882      6.7       4.8
for_over_list             678           674      242      2.8       2.8
```

Remaining hot-path protocol per exact-int assignment: `pin(result);
store_root(slot, result); unpin(result); release(result)` -- an
ownership-transferring root store would make it one call (next step).
Gates: 40 int semantics tests (typed overflow, dyn tagged binop, typed
unboxed, store_root tagged fast path) green; `test_py_typed_int_unboxed.py::
test_typed_list_i64_runtime_helpers_match_c_fast_path` was red at HEAD
(efdd3810) from a brittle regex and is fixed in the test.

## Update 2026-09-04 — step 4: ownership-transferring root store

`pcc_gc_store_root_take(slot, value)` (py_obj.py + py_obj.c mirrors, header,
ABI) moves the caller's reference into the root slot; codegen's exact-int
assignment no longer emits `pin; store_root; unpin; release`.  Moving backends
commit through the existing plan and release the caller's reference through
the slot (never through a possibly relocated SSA pointer).

```text
op                 before   step1-3   step4   cpython   ratio now
int_add             1288      440      370      716       0.52
call_int2           1556      707      637      950       0.67
attr_load           1578      982      912      561       1.6
list_index          1767     1180     1105      683       1.6
dict_get_str        3872     3281     3182      848       3.8
str_eq_dispatch     5911     4210     4048      882       4.6
alloc_small_object  6283       --     5616     1759       3.2
tuple_pack_unpack   6058       --     4791     1372       3.5
attr_store          4237       --     3661      385       9.5
```

Hot path of the exact-int loop is now 3 root reloads + 2 transfer stores per
iteration; the loop ratchet pins load_ptr<=9 / pin<=5 / unpin<=11 /
store_root==0 / take<=4 / lifo==0.  Gates: 71 int/ownership tests + ABI
chunking/attrs + C-mirror run on GC0/3/4.

## Update 2026-09-04 — step 5: typed attribute store by slot

`p.v = i` on a class-hinted receiver lowered to the string-keyed
`py_obj_setattr(p, "v", i)` while the read side already used
`py_instance_get_field(p, idx)` and `self.x = v` used
`py_instance_set_field`.  `attr_store_lowering._typed_instance_field_slot`
now resolves the slot (class hint + declared field, no `__setattr__` in the
MRO) and stores through `py_instance_set_field`; slot reads are also
recognised as GC-quiet operands so `total += p.v` opens no temp root frame.

```text
op            before   after   cpython   ratio now
attr_store     3661     597      384       1.6   (was 9.5x)
attr_load       912     660      561       1.2
method_call    1217*   1217     1050       1.2   (*post step 4)
```

Found en route (pre-existing, not changed): a user-defined `__setattr__`
override is not executed by the generic store path (`4 []` vs CPython
`4 ['v', 'v']`); the fast path guards on it so a future runtime fix is not
bypassed.  Tests: `tests/python/test_typed_attr_store_fast_path.py` (IR shape
+ GC0..4 semantics incl. subclass through a base-typed variable).

## Update 2026-09-04 — steps 1-5 transferred to pcc1 (Stage1 v9, sha b0122381)

Source-frozen Stage1 v9 (all per-call protocol fixes, transfer store, typed
slot store) built under the 8 GiB cap: rc 0, wall 178.9 s, canary 42,
libSystem-only.  Replays of the recorded Stage2 v4 workers, v9 vs v8 pcc1:

```text
worker                         v8 instr   v9 instr   change   v8 wall  v9 wall  RSS
exception_lowering (native obj)  252.6 B    233.4 B   -7.6%     20.0s    18.8s   2.93 GiB (same)
cli_bootstrap (serial, .s)       892.2 B    808.4 B   -9.4%     72.3s    66.3s   6.14 GiB (same)
```

Outputs are no longer byte-identical to the v4/v8 products because the
compiler's own lowering changed (str +=, len(), int protocol, slot stores);
semantics are held by the Stage1 canary, 71 int/ownership tests, 17 class/
field tests and GC0..4 runs.  The compiler's hot operations are the remaining
3-5x rows (dict lookup, str compare chains, tuple traffic, allocation, calls
returning containers), which is why the int-loop gains transfer only ~8-9%.

## Update 2026-09-04 — step 6: dict rows (owned-key leak, dead result roots, numeric hash, GC0 dict fast path)

Profile of `dict_get_str` (pcc_profile.py, self time): `granule_is_object_start`
12.8% (refcount provenance walk from every py_incref/py_decref),
`_dict_rooted_op` 9.5%, pin/unpin/store_root/frame protocol ~20%, `py_obj_hash`
2.8%.  Reading the loop IR and the runtime found four defects:

1. **Ownership bug**: `d[keys[i]]` (and `d[keys[i]] = v`, `obj[keys[i]] = v`)
   never released the NEW key reference returned by the inner `py_list_getitem`
   (`_emit_exact_container_subscript_load_object` Dict branch, the dict/obj
   store branches).  A key with `__del__` was never finalized; CPython prints
   `del 0`, pcc printed nothing.  Fixed; error-edge releases added.
2. **Dead protocol**: every exact-container subscript parked its result in a
   temporary root frame (store_root, frame_enter_lifo, load_ptr,
   store_root(null), frame_leave_lifo) and left it before any call that could
   move the object.  The root is now replaced by a pin around the operand
   releases and emitted only when an owned key/receiver is actually released.
   Empty `[]`/`{}`/`()` literals and `xs.append(<borrowed>)` also opened
   container roots for nothing; skipped.
3. **Unbox call on a tagged index**: `marshal_from_object` always called
   `py_int_to_i64`; the tagged case is now an inline shift with the call as the
   bignum slow block.  The Dyn `+` path checked `py_err_occurred` after the
   inline tagged fast path; moved into the slow block.
4. **Runtime**: `py_obj_hash` hashed floats by raw IEEE bits (port) while the C
   mirror special-cased integral floats — `d[1.0]` missed `d[1]`,
   `{1: a, 1.0: b}` held two keys, `hash(0.5)` differed from CPython.  Both
   runtimes now implement CPython's `long_hash`/`_Py_HashDouble` (mod 2**61-1);
   verified against CPython's `hash()` on 20k random doubles.  `py_dict_get` /
   `py_dict_set` gained a GC0 fast probe for str / tagged-int keys (no roots,
   locks, plans or 128-byte memsets); bool/float-vs-int and user `__eq__`
   collisions still take the rooted path.

```text
op             before   after   cpython   ratio now
dict_get_str    3182    1674     845       2.0   (was 3.8x)
dict_set_str    2950    1556     688       2.3   (was 4.3x)
list_index      1105     880     683       1.3   (was 1.6x)
str_eq_dispatch 4048    2369     881       2.7   (was 4.6x; indirect)
```

Tests: `tests/python/test_dict_subscript_owned_key_released.py` (canary on
GC0..4, loop ratchet lifo==0 / store_root==0 / unbox only in slow block /
err checks only beside runtime calls), `tests/python/test_dict_numeric_hash_and_gc0_fast_path.py`
(hash values vs CPython, dict semantics across str/int/bool/float/bignum/None/
user keys, KeyError, del+tombstones, growth; GC0..4 and the C mirror on
GC0/3/4).  15 neighbouring ratchet/unbox tests green; one stale assertion in
`test_py_for_target_representation_join.py` updated (tagged literal store
replaced the `py_int_from_i64` box since step 4).

Found en route, not changed: `hash(tuple)` uses the pre-3.8 algorithm (values
differ from CPython; equal tuples still hash equal); bignum hash truncates to
i64; `py_incref`/`py_decref` validate provenance with a 3-level radix walk on
every call because pointer-typed port locals may hold raw pointers (the
architectural owner of the remaining ~13%).

## Update 2026-09-04 — step 7: static str literals were the slow provenance path

`str_eq_dispatch` profile after step 6: `_is_type_object` 12.2%,
`_pointer_is_managed_no_lock` 5.2%, forwarding/index lookups ~5%.  Static
`.pystr.obj.N` literals live in the data segment, so the allocator's granule
check cannot vouch for them and every `py_incref`/`py_decref`/pin of a literal
(`if op == 'add'`, `d['key']`, `x == 'ret'`) walked the whole locked provenance
chain, including the linear builtin-type scan, before answering "not managed".
Each module now emits `_pcc_py_static_literals_<mod>()` (guarded, one
`pcc_gc_pointer_register` per pooled literal) and calls it first from `main` /
`_pcc_py_module_top_<mod>`; the chain then hits the managed-pointer index probe.

```text
op                before   after   cpython   ratio now
str_eq_dispatch    2369    1595     881       1.8   (was 4.6x at step 0)
isinstance_class   1462*   1462     911       1.6   (*first measured here)
str_concat_small   1119*   1119     676       1.7
```

Remaining literal cost: granule miss + graph lock + hash probe + unlock per
touch (~10% of the row).  Owner for the rest: a contiguous per-module static
object pool with a lock-free range/radix check, or heap-interning literals at
module init so they become granule objects.  Test:
`tests/python/test_static_str_literal_provenance_registration.py`.
`tests/fallback_baseline.json`: marshal's standalone ceilings raised
(actions 76→108, py_cpy total 310→886) for the inline unbox IRBuilder calls;
multi-file strict closure unchanged at 0 (recapture log entry).

## Update 2026-09-04 — step 8: dealloc path and a borrowed-parameter over-release

Profiles of `alloc_small_object`, `tuple_pack_unpack`, `call_returns_obj`: the
free path dominated — `py_user_del_dispatch` ran `py_class_lookup(cls,
"__del__")` (string hash + MRO dict probes) on every instance free,
`_ptr_is_instance` re-proved provenance for the object being freed,
`pcc_gc_note_object_freeing` probed an always-empty identity index under the
graph lock, `pcc_gc_note_alloc` called `pcc_gc_config_ensure` per allocation.

Fixed (port + C mirror): a process-wide `pcc_class_del_defined_count`
(incremented when a class installs `__del__` in its body or through the
runtime class-attribute store; never decremented) gates the finalizer lookup;
`_dealloc_ptr_is_instance` skips the radix probe; `pcc_gc_index_py_remove` /
`pcc_gc_index_remove_slot` return early on an empty index; `note_alloc` reads
the config globals directly.  Test:
`tests/python/test_dealloc_finalizer_gate_semantics.py` (class-body and
inherited finalizers on GC0..4 and the C mirror).

**Ownership bug found en route** (`tests/python/test_container_literal_borrowed_int_param_not_released.py`):
`_container_store_temp_needs_release` treated every `int`-typed element as a
freshly boxed temporary, so `def pair(a: int): return [a, a]` released the
borrowed parameter after each append.  A no-op for tagged small ints, an
over-release for a bignum: the caller's object was freed while the list still
held two uncounted references and the program died silently.  Now any name
whose slot holds an object pointer is a borrowed load; list/tuple/dict
literals and `list.append` share the fix.

```text
op                  before   after   cpython   ratio now
alloc_small_object   5364    4774     1742      2.7   (was 3.2x)
tuple_pack_unpack    4791    4765     1370      3.5
call_returns_obj     7058    7027     1269      5.5
```

The remaining owners are structural and shared by every object-producing row:
the refcount provenance radix walk in `py_incref`/`py_decref` (~12% self
time), a malloc'd 40-byte GC-tracking node plus hash-index insert/remove per
container or instance allocation (CPython: intrusive `PyGC_Head`),
`pcc_gc_free_object_memory` re-noting a free the dealloc path already noted
(lock + granule + finish struct twice), and the codegen pin/unpin/load_ptr
protocol around every runtime call.  Pre-existing semantic gap recorded:
`Cls.__del__ = f` after class creation lowers to a compile-time `.classattr`
global store the runtime class never sees, so such a finalizer never runs.

## Update 2026-09-04 — step 9: steps 6-8 transferred to pcc1 (Stage1 v11, source v21 sha 63b4f24e)

Stage1 v10 (source v20) built and passed its canary, but replaying the recorded
Stage2 v4 workers with the v10 pcc1 failed in the self IR verifier
(`phi-predecessors` in exception_lowering, `ssa-dominance` on `m.int.bits` in
`cli_bootstrap._fnv1a_update_u64`): the inline tagged unbox in
`marshal_from_object` named its blocks `m.int.unbox.fast/slow/join` with no
per-function uniqueness.  The host builder renames duplicate block labels;
the self-hosted compiler does not, so any function with two unboxes produced
two blocks with one name.  Host-green, pcc1-red, invisible to every host test
and to the Stage1 canary (one unbox per function).  Fixed by suffixing the
block names with the function's block count (value names were already
deduplicated on both sides).  Stage1 v11 rebuilt (rc 0, 183 s, canary 42,
peak tree RSS 5.04 GB under the 8 GiB cap).

Recorded Stage2 v4 worker replays (`replay_worker.py`, receipt env,
matching stage runtime bundle; manifest line 1 is the `codegen_worker.v4`
header, lines 2-3 are rewritten):

```text
worker                         v8 instr  v9 instr  v11 instr  v9->v11  v8->v11  v9 wall  v11 wall  RSS
exception_lowering (native obj) 252.6 B   233.4 B   203.6 B   -12.7%   -19.4%   18.1 s   16.1 s   3.10 GiB
cli_bootstrap (serial, .s)      892.2 B   809.0 B   684.7 B   -15.4%   -23.3%   64.5 s   56.5 s   6.62 GiB
```

Both workers wrote their two outputs; memory envelope unchanged.  The per-op
work of steps 6-8 therefore transfers at 13-15% of pcc1 worker instructions
on top of the 8-9% from steps 1-5.  Remaining owners are the structural ones
listed under step 8.  Pre-existing red not from this session:
`test_freestanding_gc_state.py::test_gc_state_storage_types_are_registered_in_runtime_abi`
(runtime_abi registers two backend4 i64 globals that
freestanding_gc_state.py does not define; identical at HEAD).

## Update 2026-09-04 — step 10: generic binop pins and the for-target quartet (host-green, NOT yet transferred)

`for_over_list` profile: `pcc_gc_pin`/`pcc_gc_unpin` 24% of samples.  The
generic (non exact-int) BinOp path in `expr_dispatch_lowering` pinned the lhs
before lowering the rhs, pinned the rhs, pinned the result and unpinned all
three around every binop, including ones whose inline tagged fast path never
leaves the function.  Now: a GC-quiet rhs needs no lhs pin across its
evaluation; when `_emit_binop_value` routes to an inline tagged-int route
(`_binop_route_defers_pins`: boxed int/bool operands, or DynType with only
int/bool/dyn operand types) the pins are deferred into the slow block
(`slow_pins`, both routes, fallback calls pinned around themselves); the
result pin is emitted only when an owned operand is released afterwards.
The for-loop target store (`_for_store_owned_target`, 9 callers) replaced its
pin / store_root / unpin / release quartet with one `pcc_gc_store_root_take`.

```text
op              before   after   cpython   ratio now
for_over_list     683     525     244       2.1   (was 2.8x; wall now 30 ns = CPython)
dict_get_str     1674    1639     850       1.9
str_eq_dispatch  1595    1585     882       1.8
```

Ratchet: `tests/python/test_generic_binop_pins_only_around_slow_calls.py`
(every `pcc_gc_pin` in main sits in a block with its `py_int_*`/`py_obj_*`
slow call; GC0..4 semantics incl. bignum mixed ops).  Shape tests updated to
the new owners: `test_py_for_target_representation_join.py` (transfer store),
`test_native_dyn_tagged_int_binop.py` (`_emit_binop_value_routed` owns the
four DynType call sites).  Host contract: `_binop_route_defers_pins`,
`_emit_binop_value_routed` registered; static method table regenerated.

Step 10 has NOT been built into a Stage1 yet (last transfer: Stage1 v11 =
steps 6-9).  Next session: Stage1 v12 from a fresh frozen source, then the two
worker replays, before any further per-op work.  Session paused here at the
human's request.
