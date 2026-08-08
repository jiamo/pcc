# Investigation: `pcc1 -> pcc2` emit throughput and native-worker memory

## Status

active — two fixes landed and measured; the remaining gap is the runtime
object model / allocator, which is NOT yet fixed.

## Problem Description

`pcc1 -> pcc2` (stage2) takes ~17-18 minutes while `pcc0 -> pcc1` (stage1,
host CPython) takes ~5.5 minutes.  The owner's target is ~5 minutes for
stage2 and for each backend of the five-GC matrix.  A secondary report: a
stage2 emit worker holds 4.8 GB RSS where the host worker holds ~1.5 GB.

Predecessor: [pcc1-self-host-module-init-startup-sigbus.md](pcc1-self-host-module-init-startup-sigbus.md)
(the startup SIGBUS that blocked the matrix; fixed).

## Repro

```bash
# one huge module through the native emit worker
./build/bootstrap/pcc1 --pcc-self-backend-emit-worker <27MB>.ll /tmp/out.result
```

Observed before this investigation: does not finish in 10 minutes; RSS climbs
past 9.5 GB at ~68 MB/s.  The host worker completes a *larger* (43 MB) module
in 69 s at 1.56 GB peak.

## Findings [CONFIRMED]

### 1. Lane widths are hard-capped, but they are not the main cost

`pipeline_self_backend_emit.py` runs emit in tiers: oversized, huge (>=4 MB),
medium, small.  On a 12-core / 96 GB host the widths are 1, 2, 8, 12 — the two
heaviest lanes are the narrowest.  The live stage2 had 25 modules >= 4 MB and
3 oversized.  `jobs_for_input_sizes` additionally collapses to 1 whenever any
input is at or above the split threshold, unless `PCC_SELF_BACKEND_JOBS` is
set, and `scripts/bootstrap.sh` never sets it.  All of it is deliberate, with
the stated reason "a compiled emitter needs several GiB for a multi-megabyte
LLVM module" — i.e. the widths are a *memory* mitigation, so per-worker memory
has to come down before widening is safe.

### 2. Regex call volume — FIXED, measured

Counting every `re` call for one 27 MB module (host, `re.compile` wrapped):

```text
regex calls      : 11,062,490
top by bytes:
   3,174,111 calls  '^-?\d+$'
   2,320,434 calls  '^0x[0-9A-Fa-f]+$'
   2,320,434 calls  float-literal pattern
```

7.8M of the 11.0M calls were three token classifiers.  Under pcc1 every call
enters the pcc-Python regex engine (pattern-cache walk + matcher), which is
far more expensive than a str method.  Replaced with `_is_int_token` /
`_is_hex_token` / `_is_float_token` built from whole-string runtime calls
(`isdecimal`/`find`/`strip`) — never `text[i]`, which allocates a fresh
one-character str in the pcc runtime.

Result: **11,062,490 -> 3,247,511 calls (-71%)**, and cold stage1 went
**328 s -> 222 s** even though changing `self_backend_parse.py` invalidates the
whole self-backend object cache (`self_backend*.py` glob) and forces a cold
emit of every module.

Equivalence is gated by `tests/c/test_self_backend_parse_token_classifiers.py`,
which keeps the retired patterns as the oracle over a hand-written corpus plus
a generated 3-character token space (68 cases, includes non-ASCII digits and
the `$`-before-trailing-newline case).

An earlier hypothesis — that the engine's per-call `while index < text_length`
ASCII precondition scan made whole-text `search(ir_text, pos)` loops O(n^2) —
was **refuted by measurement**: only 0.31 GB total is rescanned for the whole
module, because nearly all calls are on short tokens.

### 3. Native-worker memory — LOCATED, not yet fixed

Reading the allocator's own live accounting from a running worker at t=100 s
(RSS 9.69 GB):

```text
pcc_allocator_live_requested = 6,171,471,628   (6.17 GB actually requested)
pcc_allocator_live_usable    = 7,320,614,304   (+19%  size-class rounding)
pcc_allocator_mapped         = 9,831,882,752   (+59%  vs requested)
```

Two distinct components:

- **Object model (dominant).** 6.17 GB is *live requested* — the program
  genuinely holds it. The host holds ~1.5 GB peak for a larger module, so pcc's
  representation of the same parsed module is several times heavier. This is
  the main lever and is not yet addressed.
- **Allocator overhead (~1.6x).** `pcc_allocator_size_class` only pools sizes
  up to 2048 bytes. Everything larger goes to `pcc_allocator_allocate_raw`,
  which adds a 48-byte header, rounds up to a whole 4096-byte page, and takes
  a **private `page_alloc` (mmap) per object**. Note the `sample` profile shows
  mmap itself is only ~0.2% of time, so this is primarily a *memory* defect,
  not the time bottleneck.

The time profile of the native worker (macOS `sample`, symbolicated) is
dominated by allocation and zeroing rather than syscalls:

```text
calloc                              628
memset                              504
py_int_to_i64                       427
pcc_gc_managed_pointer_find_slot    250
py_int_from_i64                     245
   under: user_py_re_engine_runtime__pattern_method_call (2081)
          -> py_re_engine_truth_flags_from (2045)
```

### 4. Byte-at-a-time memory primitives — FIXED, measured

The runtime's memory primitives were all byte-at-a-time loops in compiled
pcc-Python.  `pcc_calloc` had its own inline fill, and `pcc_memset`,
`pcc_memcpy` and `pcc_memmove` each looped `store_i8` once per byte:

```python
i: i64 = 0
while i < total:
    store_i8(ptr, i, 0)     # one byte per iteration, for every allocation
    i = i + 1
```

Every managed object is allocated through `calloc`, so this single loop was
**~19% of all samples** on its own (`memset` a further ~4%).  `calloc` and
`memset` now fill eight bytes per iteration (`store_i64`), aligning the
destination first so no wide store is ever unaligned.  Constants are inlined
and no helper function is introduced: module-level ints are zeroed in stripped
freestanding objects, and a `__pcc_freestanding__` module rejects any function
without a `@c_abi_export`.

After the fix the profile is flat — the former 1845-sample `calloc` peak is
gone and the new leader is 447 samples:

```text
py_int_to_i64                    447 + 240   (int unboxing)
py_int_from_i64                  293         (int boxing)
pcc_gc_managed_pointer_find_slot 278
_tlv_get_addr                    198         (thread-local access)
```

`memcpy`/`memmove` were left byte-wise deliberately: they did not appear in
the profile, so widening them is unmeasured work.

### 5. Why it is slow: generated code quality, not the runtime [CONFIRMED]

The decisive measurement.  This function:

```python
def add_loop(n: int) -> int:
    total: int = 0
    i: int = 0
    while i < n:
        total = total + i
        i = i + 1
    return total
```

compiles to **689 instructions and ~95 runtime calls**: 21 `pcc_gc_unpin`,
18 `pcc_gc_load_ptr`, 13 `pcc_gc_pin`, 12 `pcc_gc_store_root`,
10 `pcc_gc_release`, 14 frame enter/leave, 3 `py_int_from_i64` — and only
**2 `py_int_add`**.  An optimizing compiler emits ~8 instructions.  `total:
int = 0` alone costs five runtime calls (`py_int_from_i64`, `pcc_gc_pin`,
`pcc_gc_store_root`, `pcc_gc_unpin`, `pcc_gc_release`).

`PCC_PYTHON_IR_PASSES=on` produces a **byte-identical 689 instructions**: the
IR passes cannot help, because GC barriers are opaque calls no pass may
remove.  The shape comes from frontend lowering, so the bootstrap's
`PCC_PYTHON_IR_PASSES=off` default is not the cause.

Most of that traffic is provably dead: `pcc_gc_pin` starts with
`is_tagged_int(o)` and returns immediately, so every pin/unpin/store_root/
release on a small int does nothing at all.

Visible in the disassembly, all classic missing passes:

```text
no mem2reg          every local round-trips through a stack slot
                    (`stur` then immediately `ldur` the same slot)
no copy propagation `mov x1,x11; mov x9,x1; mov x10,x9`
no immediate forms  `mov x10,#1; lsl x11,x9,x10`  not  `lsl x11,x9,#1`
no inlining         tiny helpers stay real calls
no shrink-wrapping  py_int_to_i64 saves 10 callee-saved registers even on a
                    5-instruction fast path
```

### 6. Runtime fast paths — FIXED, measured

`py_int_to_i64` was the hottest symbol (117 instructions).  Its "fast" path
made two calls: `_set_overflow` and the `py_int_value_i64` extern.  Since
pcc does not inline, that is the whole cost.  The module docstring blamed a
missing intrinsic, but `untag_int` has since become a recognized
`pcc.unsafe` intrinsic (`ptrtoint` + `ashr 1`).  A tagged value is
`(v << 1) | 1` — always odd, never null — so the tagged test may safely
precede the null check.

The fast path is now five instructions and zero calls:

```text
tbz  w20, #0x0, <slow>     ; tagged?
cbz  x19, +44 ; str wzr, [x19]   ; *overflow = 0
asr  x0, x20, #1                 ; untag
```

### 7. The emit path was not the whole story — profile the phases you skipped

The earlier rounds all profiled the **emit** worker.  Sampling stage2's *main*
process (a phase never profiled before) showed a completely different #1:

```text
user_py_compiled_module_runtime__cstr_equal   519 samples, 434 leaf occurrences
  caller chain: _compile_python_multi_codegen_parallel_uncached
                 -> _build_python_frontend_shared_exports_parallel -> ...
```

Both compiled-module registries (`pcc_compiled_module_inits` and
`pcc_compiled_modules`) were singly-linked lists walked with `_cstr_equal`
per node.  With 500+ modules and a lookup per import/export resolution that
is O(modules) string compares, repeated throughout the compile — not just at
startup (measured startup is only 0.07 s).

Fix: a 512-bucket hash index over each registry (djb2 over the name, one
extra `hash_next` pointer per node; nodes grew 32->40 and 24->32 bytes).  The
existing linear `next` chains are untouched, so registration order and any
external expectation are unchanged — the buckets are a pure index.

Also `_py_ast_wire_field` did **two** dict lookups per AST field
(`name not in fields` then `.get(name)`), each hashing and string-comparing
the key, for every field of every node of every module; now one (subscript,
because `dict.get` mis-lowers under pcc1).

Verified: `cstr_equal` disappeared from the profile entirely (was #1 at 519
samples, now absent from the top 7).  The new leaders are

```text
pcc_gc_managed_pointer_find_slot  372 + 294 = 666   (GC index table, now #1)
py_int_to_i64 / py_int_from_i64   411 + 435 = 846   (int box / unbox)
pcc_gc_unpin                      304
```

which is the same structural conclusion again: GC bookkeeping and int
boxing.  **Method note: the cost is spread across phases (frontend workers,
main-process export resolution, emit, link), and each phase has its own
distinct hotspot.  Profiling only one phase produced three rounds of fixes
that never moved the total.**

### 8. The link phase is a further unmeasured cost centre

A rebuild whose incremental link cache missed spent **16+ minutes in
`pcc_link_macho.py` at 9.3 GB RSS** for the 963 MB image, single-threaded.
The parallel-assembly change earlier in this work covers the *assemble* step
only; the link proper was never profiled and is not covered by any proposal
below.

### 9. The binary is 90% stack maps, and they are 97% redundant [CONFIRMED]

Section breakdown of a current `pcc1` (904 MB total):

```text
__DATA,__pcc_stackmaps   810.2 MB   89.7%
__TEXT,__text             88.3 MB    9.8%
everything else            5.9 MB    0.5%
```

The GC stack-map metadata is **9.2x the size of the code it describes**.
This is why the link phase processes ~900 MB (16+ min, 9.3 GB RSS on a cache
miss) and why every one of the hundreds of worker processes maps it.

Decoding the section (format is documented in `precise_stackmap.py`: 32-byte
records, 16-byte locations):

```text
functions                 9,580
records               2,366,390
locations            48,344,838   = 737.7 MB
distinct location-lists  47,310
location bytes if interned          21.5 MB   -> 34.3x smaller
```

2.37M records reference only **47,310 distinct root-set shapes**; the single
most common list is the *empty* one, repeated 504,992 times.  Interning the
location lists and having each record reference `(index, count)` takes the
whole section from 810 MB to roughly 97 MB (75.7 MB of records + 21.5 MB of
interned locations) — the binary drops from 904 MB to about 190 MB.

This is a pure *encoding* change: the same root sets are described, shared
instead of repeated, so no GC semantics change.  It is nonetheless a
coordinated producer/consumer change and must be done as one:

- producer `pcc/backend/precise_stackmap.py` (bump `VERSION` to 2)
- consumer `pcc/py_runtime/py/freestanding_gc_mapped_roots.py`, which walks
  functions -> records -> locations (lines ~306-345)
- `tests/python/test_precise_stackmap_abi.py` and the emit-side tests
- re-verify all five GC backends, since this is the root-scanning contract

### 10. Barrier elision landed, with an honest negative result

Compile-time small-int literals are now materialized as tagged constants
(`(v << 1) | 1`) instead of calling `py_int_from_i64`, and are recorded as
provably-not-GC-objects; 104 `pcc_gc_pin`/`pcc_gc_unpin` call sites were
routed through checked helpers (`_gc_pin`/`_gc_unpin`), and `_gc_release`
gained the same check.  All three helpers are registered in
`L1_CODEGEN_HOST_METHODS`.

On the `add_loop` microbenchmark: 689 -> 665 instructions, `pcc_gc_unpin`
21 -> 16, `pcc_gc_pin` 13 -> 10, `pcc_gc_release` 10 -> 6,
`py_int_from_i64` 3 -> 0, output still correct.

**Negative result:** the stack-map section barely moved (811.4 -> 810.2 MB).
The expectation that eliding barriers would shrink the root set was wrong —
the roots come from *object* locals, not from int constants.  The 810 MB has
to be attacked by finding No.9 directly.

## Proposals

- No.1 Replace the three hot token-classifier regexes [CONFIRMED, landed]
- No.2 Widen the emit lanes [pending — still blocked on per-worker memory]
- No.3 Pool allocations above 2048 bytes instead of one mmap per object
  [pending — memory-only defect, ~0.2% of time]
- No.4 Word-at-a-time `calloc`/`memset` [CONFIRMED, landed]
- No.5 Reduce int boxing / GC-index-table / TLS cost in the object model
  [pending — this is now the dominant cost and belongs to the value-model
  (V-track) work, not to a hotspot fix]
- No.6 Call-free tagged fast path in `py_int_to_i64` [CONFIRMED, landed]
- No.7 Do not emit GC barriers for values that cannot be GC objects
  (compile-time small-int constants, statically tagged values) [pending —
  pure deletion, no semantic change: those barriers are already no-ops]
- No.8 Value lane for non-escaping `int` locals: keep them as raw i64 in
  registers, deopt to bignum on overflow [pending — the structural fix]
- No.9 Function inlining for small runtime helpers, plus mem2reg / copy
  propagation / immediate-form selection in the self backend [pending]
- No.10 Hash-index both compiled-module registries; single-lookup AST wire
  fields [CONFIRMED, landed — removed the then-#1 hotspot]
- No.11 Profile and optimize `pcc_link_macho.py` (16+ min, 9.3 GB, single
  threaded on a cache miss) [pending — never profiled]
- No.12 Cut GC index-table traffic (`pcc_gc_managed_pointer_find_slot`, now
  the #1 leaf) — largely the same work as No.7, since barriers elided for
  tagged values never reach the table [pending]
- No.13 Intern stack-map location lists (34.3x measured on 89.7% of the
  binary) [pending — highest measured leverage in the whole system]
- No.14 Elide barriers for compile-time tagged constants [CONFIRMED, landed]

### 11. Stack-map interning landed: 904 MB -> 196 MB [CONFIRMED]

Format version 2.  Records keep their 32-byte shape and reuse the trailing
reserved word as `location-index`; the location lists are interned into one
table emitted after every function.  The header grew by 8 bytes to carry the
table length (`HEADER_SIZE` 16 -> 24, mirrored in
`freestanding_abi_constants.py`).  Producers: `encode_stack_map`, the
by-hand `render_aarch64_stack_map_section`, and `merge_stack_map_payloads`,
which re-interns globally across objects — that merge pass is what collapses
the whole closure onto one shared set of shapes.  Consumer:
`freestanding_gc_mapped_roots.py`, which now computes the table base from the
payload tail and still validates each record's slice against *that function's*
frame size (the table is shared across functions with different frames, so a
single validation pass over the table would have been wrong).

```text
binary          904.4 MB -> 195.9 MB
__pcc_stackmaps 810.2 MB ->  94.1 MB
__TEXT,__text    88.4 MB ->  88.4 MB   (unchanged, as expected)
```

Two pcc-closure lowering limits cost a full rebuild each to find, because a
failure there surfaces only as an opaque `compile failed`:

- `dict.get()` mis-lowers under pcc1 into a raising getitem — use
  `key in d` + subscript.
- hashing a dict keyed by a tuple of frozen dataclass instances is not proven
  in the closure — key on a string instead.

**Process note for the next agent:** validate closure compatibility with a
targeted single-module compile, not a full bootstrap:

```bash
env -u LC_ALL uv run pcc --backend self --python-libpython=off \
  --ir-scaffold=on pcc/backend/<changed>.py -o /tmp/probe
```

That returns in a fraction of the time a 10-25 minute stage1 takes.

### 12. Allocator size classes raised 2048 -> 16384 [CONFIRMED]

`__mmap` was the **#2 leaf (536 of ~10000 samples)** in a stage2 *frontend
codegen worker* — an earlier note in this file judged the one-mmap-per-object
path a memory-only defect at ~0.2% of time; that was measured in the emit
worker and does not hold for the frontend phase.  Classes 4096/8192/16384 now
carve the same 64 KiB slab (strides 4144/8240/16432, counts 15/7/3, all
verified to fit).

### 13. What is still NOT solved

stage2 remains ~30-50 minutes.  The last profile of it shows the parallel
frontend workers finishing and then a **long single-threaded phase in the main
process** (84.9% of one core, 5.1 GB RSS, tens of minutes).  That serial phase
is the next thing to identify — it was not sampled successfully before this
session ended.

Also unmeasured: `_tlv_get_addr` was the **#1 leaf (844 samples)** in the
frontend worker — every post-call error check reads thread-local state, and on
macOS each such access is a dyld function call.  Caching the TLS base per
function, or making the error flag non-TLS when threads are idle, is untried.

### 14. Where stage2 actually spends its time [CONFIRMED]

Sampling stage2 while it ran, rather than reasoning from the emit worker:

- At t=21 min it is **still in the frontend phase**, with 10
  `--pcc-python-multi-codegen-worker` processes at ~78% CPU each, each living
  ~22 s before the next module.  The frontend, not a serial tail, is the bulk
  of stage2.
- An earlier note in this file described "a long single-threaded phase in the
  main process" (84.9% of one core, 5.1 GB).  That phase is real but comes
  *after* the frontend; a watcher script that waited for the workers to drain
  did not catch it within its window, so it remains unsampled.

Re-sampling a worker with the current pcc1 confirms finding No.12 landed:
`__mmap` is **gone from the profile** after the allocator size classes were
raised.  The leader is now, by a wide margin:

```text
_tlv_get_addr                   403     (~3x the next entry)
pcc_gc_load_ptr                 134
memset                           99
pcc_py_gc_minor_graph_unlock     67
```

### 15. Thread-local lookups: the lock pair read the same TLS twice [FIXED]

Attributing the `_tlv_get_addr` samples to their callers gives
`pcc_py_gc_minor_graph_lock`/`unlock` ~261 of ~403, with
`pcc_gc_frame_node_pool_heads_get` a distant 31.

On Darwin every `global_addr` on a thread-local is a `_tlv_get_addr` **call**.
Both functions took the address of `g_tls_pcc_py_gc_minor_graph_lock_depth`
twice — once to read the depth and again to store it — so the re-entrant fast
path, which is the common case, paid for two lookups where one does.  Both now
hoist the address into a local and reuse it.

Generated code calls this pair through the GC frame protocol on essentially
every function with a rooted local, so the call frequency is the same order as
the frame enter/leave count.

Verified: the runtime archive rebuilds clean, stage1 216 s, and all five GC
backends still start, compile and run correctly.

**Faster validation loop (use this instead of a full bootstrap).**  A
`__pcc_freestanding__` runtime port is not standalone-compilable as a program,
so the single-module `uv run pcc ...` probe does not apply to it.  Build just
the archive:

```bash
env -u LC_ALL PATH="$PWD/.venv/bin:$PATH" make -C pcc/py_runtime libpy_runtime_pcc_py.a
```

That compiles the changed port in seconds instead of a 10-25 minute stage1.

## Technique gap versus other compilers

Each measured pcc problem has a named, documented technique behind it; none
of this is unreachable, it simply is not implemented yet:

```text
int locals heap-boxed + GC-rooted   escape analysis + scalar replacement
                                    (Go, HotSpot, Graal)
no inlining at all                  inlining — the enabling optimization that
                                    everything else in Graal/HotSpot builds on
locals round-trip through stack     mem2reg / SSA construction + regalloc
GC barriers are opaque calls        inline 2-3 instruction write barriers
                                    (this is also WHY IR passes are useless
                                    here: a pass may not touch opaque calls)
811 MB of stack maps                HotSpot/Graal oopmaps are a few percent of
                                    code size; interning + delta encoding
int boxing                          speculation + deopt (Graal), i.e. exactly
                                    the value lane already in AGENTS.md
```

Already present, so not on the list: cached string hashes (`PyStrObject.hash`,
FNV-1a with -1 meaning "not yet computed") and the tagged small-int
representation itself.

## Report

**Not closed, and the 5-minute target is not met.**  Three defects were found
and fixed, each verified:

```text
No.1 token classifiers   regex calls 11,062,490 -> 3,247,511 (-71%)
No.4 word-at-a-time zero calloc's 19% single-function peak removed
     (both also help the host: cold stage1 328 s -> 222 s)
```

A fourth fix (No.6, `py_int_to_i64`) removed the two calls from the hottest
symbol's fast path.

What did NOT move: **stage2 wall time**.  One 27 MB module still does not
finish in the native emit worker inside 10 minutes (11.4 GB RSS at t=10 min),
and a stage2 run was still in its frontend phase — 10 parallel
`--pcc-python-multi-codegen` workers — at 36 minutes before it was stopped
(that run was cold, since every fix invalidates the object-cache identity, so
it is not directly comparable to the ~18 min warm figure).

The honest conclusion is finding No.5: **micro-optimizing runtime helpers
cannot fix this.**  Four hotspot fixes landed, each verified, and the
end-to-end number did not move, because the generated code spends ~10 runtime
calls per loop iteration to perform one integer add.  Reaching the 5-minute
target requires No.7/No.8/No.9 — stop emitting dead GC barriers, keep
non-escaping ints in registers, and give the backend inlining + mem2reg —
not another hotspot.

Sequencing note that still holds: do not widen the emit lanes (No.2) before
the per-worker footprint comes down.  A worker was still at 9.4 GB RSS at
t=75 s; the current widths (oversized 1, huge 2) exist precisely because of
that.

Correctness after both changes: `pcc1 --help` rc=0 on `PCC_GC_BACKEND=0..4`,
a real program compiles and runs correctly, and the focused gates for every
touched subsystem pass (403 passed, 2 deselected: self-backend, verifier,
token classifiers, arm64 encoder, freestanding mem/allocator, bootstrap-gate
baseline).

## Update — No.10 CONFIRMED: the frontend, not the runtime, owned the compile

The four earlier fixes targeted runtime *leaf* symbols and did not move the
end-to-end number.  Attributing the same profile by **caller** instead of by
leaf changed the answer completely.

Technique (worth reusing — it is what made this finding possible):
`sample <pid>` emits a call graph whose tree lines are prefixed with
`+ ! : |`, so a naive `^(\s*)(\d+)` indentation parse silently matches
nothing.  Strip that prefix set, resolve each `load address + 0xOFF` against
`nm -n` of the same binary via bisect, and the tree becomes a real
caller/callee attribution.  Symbolization was spot-checked by confirming each
hot address lands well inside its symbol's extent.

What that showed, on `pcc1` emitting IR for `pcc/py_frontend/type_infer.py`:

```
_ensure_post_call_frame_block   30.9% inclusive of the codegen phase
  of which: find_slot 831, minor_graph_lock 472, store_root 215,
            py_int_to_i64 272, py_int_from_i64 184   (~2000 samples)
```

That function's whole body is a cache lookup.  The cost was the **key**:

* `key = (parent_fn.name, err_target.name, func_name, file_name, line)` built
  a 5-tuple on every call that may raise.  In pcc a tuple is a GC object, so
  each lookup paid allocation + `pcc_gc_pointer_register` + object-graph lock
  + managed-pointer slot probe, plus `py_int_from_i64` to box `span.line`,
  plus per-element hash dispatch.
* `_emit_exception_frame` **re-opened and re-`splitlines()`d the entire source
  file** for every traceback frame, to read one line — `_str_splitlines_impl`
  was 1.8% of all samples on its own, and each call allocated a fresh list of
  every line in the module.

Fixes, both verified byte-identical (host `pcc0` and self-hosted `pcc1` each
emit the same IR for `type_infer.py` as before):

1. `_post_call_frame_blocks` became nested cheap-key dicts:
   `id(parent_fn)` → `err_target.name` → `line`.  `func_name`/`file_name` drop
   out because one LLVM function comes from one def in one file.
2. `_source_file_lines_cache` reads and splits each source file once.

### A negative result worth keeping

The first attempt nested on `parent_fn.name` and made it **worse**:
30.9% → 34.3%.  Mangled LLVM function names are ~80 characters and nothing
caches a string's hash here, so keying on one re-hashes those bytes on every
lookup — more expensive than the tuple allocation it replaced.  `id()` is a
tagged int and allocates nothing.  **In self-hosted pcc code, prefer an
identity/int key over a long-string key.**

### Result

```
_ensure_post_call_frame_block   30.9%  ->   6.6%  of the codegen phase
_emit_post_call_err_check       38.8%  ->  17.2%
```

Wall-clock is deliberately not quoted for this slice: a `Virtualization.framework`
VM was consuming 79–241% CPU throughout, which stretched the same stage1 build
from 211 s to 429 s.  Profile-relative attribution is contention-insensitive;
wall time then was not.  Re-measure wall time on a quiet machine.

### Fallback ratchet breach from the earlier stackmap-v2 slice (fixed)

`test_per_module_fallbacks_under_ratchet` went red at
`pcc.backend.precise_stackmap: 27 vs baseline 15`.  Cause was this
investigation's own v2 interning work: `struct.pack_into` /
`struct.unpack_from` / `_FUNCTION.unpack_from` have no native lowering, so
each is a CPython fallback in the self-compiled closure.  Open-coding the
little-endian field reads and writes in `merge_stack_map_payloads` restored
the baseline exactly (27 → 15 actions, 162 → 63 total fallback calls) and
removes a `struct` call per safepoint record from the link's hot loop.

Two stale test artifacts surfaced while confirming that and were fixed:

* `test_freestanding_gc_mapped_roots.py`'s C harness template was a plain
  f-string, so `printf("...\n")` reached clang as a real newline inside a
  string literal — a hard C syntax error.  It is now `rf'''`.
* the same test corrupted `malformed[81]` to assert the consumer fails closed
  on an unmanaged location.  Since v2 moved locations into one shared table at
  the end of the payload, offset 81 is no longer a location, so that assertion
  had been passing without testing anything.  The offset is now derived from
  the payload's table count.

### Gates

`PCC_GC_BACKEND=0..4` each compile and run a real program correctly with the
rebuilt `pcc1`.  Green: `test_precise_stackmap_abi`,
`test_freestanding_gc_mapped_roots`, `test_macho_semantic_layout`,
`test_pcc_stdlib_struct` (119 passed), and `test_fallback_baseline`,
`test_ir_py_fallback_baseline`, `test_bootstrap_gate_baseline` (37 passed).
Two pre-existing HEAD-red tests are unrelated and unchanged by this slice:
`test_borrowed_object_local_rebind_keeps_gc_root` (asserts the pre-GC-rework
plain-`store ptr` shape; codegen now emits the stricter `pcc_gc_store_root`
barrier) and `test_absolute_from_import_detects_native_extension_alias`.

### Still open

The self-host gap is now the headline number: host `pcc0` emits IR for
`type_infer.py` in **4 s**; native `pcc1` needs **95 s** for the same work.
A ~24x penalty for the compiled compiler is the thing to explain next, and it
is not explained by any single hotspot — the remaining leaf profile is
GC provenance ~31%, type dispatch ~12%, int box/unbox ~12%, refcount ~7%,
which is the signature of No.7/No.8/No.9 (dead barriers, boxed locals, no
inlining/mem2reg), not of another cache to fix.

## Update — No.11 CONFIRMED: every GC barrier ran a linear type-object scan

Continuing the caller-attribution work from No.10 on the stage2 cost (pcc1
compiling pcc source).  Benchmark throughout: `pcc1 --python-library
--emit-llvm` on `pcc/py_frontend/type_infer.py`.

### The chain

`_is_type_object` was the #2 leaf at 8.1% and is a 24-way linear
`ptr_eq(o, global_addr("PyXxx_Type"))` scan.  The emitted IR of the function
it appeared under does not call it at all — the intervening frame is elided by
the tail-call pass.  The real chain is:

```
any GC barrier (pcc_gc_store_root / pin / _ptr_is_class / ...)
  -> pcc_gc_pointer_is_managed
    -> _pointer_is_managed_no_lock
      -> pcc_capi_is_type_object_value   -> _is_type_object   (24 compares)
```

`_pointer_is_managed_no_lock` is a disjunction of side-effect-free lookups,
and it ran the 24-compare scan **third**, before
`pcc_gc_managed_pointer_index_contains` — one hash probe, and the case that
actually hits for a real managed object.  So every barrier in every compiled
program walked the builtin type list before reaching its answer.

Fix: move the two O(1) index probes ahead of the linear scan, in both
`py/py_gc_backend.py` and the `src/py_gc_backend.c` oracle.  Reordering a pure
disjunction cannot change the result.

**54s, from 87s.**  `_is_type_object` left the top-14 leaves entirely.  IR
byte-identical to the pre-optimization `pcc1`; `PCC_GC_BACKEND=0..4` all
compile and run a real program correctly.

### Two smaller changes in the same area

* `run_compiled_default_tier` passed `_mem2reg_function` as a **function
  value** to `_rewrite_functions`.  A function value crossing a call boundary
  lowers to the fully dynamic path — build an argument tuple, `py_obj_call` to
  resolve the callable, enter its native adapter, unpack and marshal — once
  per `define` in the module.  Confirmed in the emitted IR
  (`call @py_obj_call(ptr %transform, ptr %call.args, ...)`), and fixed by
  calling the transforms directly; the IR now shows direct calls and
  `_mem2reg_function_native_adapter` is gone from the profile.
* The two transforms are strictly per-function, so `sroa(mem2reg(body))` per
  function replaces two full traversals of a multi-megabyte IR text with one.
  Equivalence checked on 33 MB of real IR across three files, and pinned by
  `test_fused_traversal_matches_two_separate_passes` with the historical
  two-pass shape kept as an executable oracle.

**Negative result worth recording:** neither of those two moved the number.
`run_compiled_default_tier` stayed at ~69% and its children were unchanged
after both.  The traversal was never the cost — the GC barriers inside it
were, which is what No.11 above found.  Halving a traversal that is 95%
barrier overhead halves nothing.

### Where stage2 stands now

```
95s  ->  87s  ->  54s      (same benchmark; machine contended throughout)
```

Leaf profile after this round:

```
15.9%  py_int_to_i64          | int box/unbox      26.2%
10.3%  py_int_from_i64        |
11.0%  pcc_gc_managed_pointer_find_slot   | GC provenance ~25%
 4.6%  pcc_gc_pin / 4.1% unpin / 4.2% graph_lock / 2.1% is_managed
 6.3%  py_incref / 5.2% py_decref         | refcount 11.5%
```

The next targets in order of measured size are int box/unbox and the
managed-pointer probe itself — both are the No.7/No.8 structural items (dead
barriers on values that cannot be GC objects; boxed integer locals), not
another mis-ordered predicate.

All wall-clock figures in this section were taken with a
`Virtualization.framework` VM consuming 65-88% CPU, so they understate the
gains; profile shares are unaffected.  Gates: 50 passed
(bootstrap-gate/fallback/ir-fallback/compiled-default-tier), 210 passed on the
GC backend files with the same 6 pre-existing HEAD-red text assertions as
before this work (`: int` expected where the unmodified sources say `: i64`).

## Update — No.12 CONFIRMED: scaffold constants were boxed and immediately unboxed

Found by reading emitted IR, not by profiling — the leaf profile only says
"int box/unbox is 26%", which is indistinguishable from real integer work.
The IR says what it actually is:

```llvm
%native.const.int = call ptr @py_int_from_i64(i64 16)      ; box the literal 16
%m.int_unbox      = call i64 @py_int_to_i64(%native.const.int, %ov.flag)
```

Every `load_i64(obj, SOME_OFFSET)` in the runtime ports hits this.  The offset
is a module-level Python int constant, so it is boxed into an object to
satisfy the name's object type, then unboxed again to satisfy the `i64`
parameter: **two runtime calls, one of them allocating, to pass a
compile-time integer.**  Struct offsets and type tags are everywhere in the
ports, which is why every per-line helper carried 4-8 of these.

Two changes, both in the frontend:

1. `_emit_native_module_constant` now materializes an int scaffold constant
   through `_emit_int_literal_object`, the same path ordinary int literals
   take — a small value becomes a tagged constant (`inttoptr((v << 1) | 1)`),
   so there is no call and no GC barrier.
2. `marshal_from_object` folds the unboxing away when the value is a recorded
   boxed literal.  The IR builder returns opaque handles with no operand
   access, so a value cannot be asked what it boxes; the producer records it
   in a side table in `marshal` keyed by `id`, holding the value itself so the
   id cannot be recycled.  `generation_lowering` clears it per module.

```
runtime port int box/unbox calls   10426  ->  5572   (-47%)
pcc/py_runtime/py/py_list.py         259  ->    49   (-81%)
benchmark                             54s ->    30s
```

`pcc1`'s emitted IR for `type_infer.py` is still byte-identical to the
pre-optimization compiler (scaffold constants appear in the runtime ports, not
in frontend modules, so this changes how pcc1 is *built*, not what it
*emits*).  `PCC_GC_BACKEND=0..4` all compile and run correctly.

### Build-cache hazard found while measuring this

`pcc/py_runtime/build_py/*.o` is keyed by **source** hash
(`source_sha256`, `ir_sha256` in each `.o.provenance.json`) and does not
invalidate when the *compiler* changes.  A frontend/codegen change therefore
leaves the runtime archive silently stale — the first rebuild attempt here
reported `rc=0` and produced a 6 MB archive with none of the change in it, and
the boxing count was unchanged at 10426.  Deleting `build_py/*.o` and
`*.provenance.json` is what forces re-emission.  Anyone measuring a codegen
change against the runtime must do that first or they will measure nothing.

### Running total for stage2

```
95s  ->  87s  ->  54s  ->  30s
```

Contributions, in the order they were found: the exception-frame cache key and
source-file re-read (No.10), the GC provenance predicate order (No.11), and
scaffold-constant boxing (No.12).  The two changes that came from reading the
profile rather than the code — fusing the pass traversals and nesting the
cache on function names — contributed nothing and one was a regression.

## Update — No.13: stage2 measured end-to-end, and it FAILS in self-backend emit

First completed full-bootstrap measurement of this investigation.

```
stage1     90s   (warm; cold is 700-780s)
stage2   5429s   = 90.5 min, then FAILED (EXIT=1)
stage3      —    never ran
```

The failure is not a compile error:

```
/bin/sh: line 9: 68889 Killed: 9  ( pcc1 --pcc-self-backend-emit-batch-worker .../self_backend_emit_oversized_0.manifest )
error: PCC-PY-COMPILE-001: [python-frontend] compile failed
```

**Who sent SIGKILL is unknown.** Ruled out: the system OOM killer (96 GB machine,
40 GB free at the time, no jetsam record for the pid), an `rlimit` (none set),
and any in-repo RSS cap or watchdog (none exists in
`pipeline_self_backend_emit.py` or the generated batch shell). Not recorded as
a cause because it is not established.

### Localizing without a 90-minute run

`self_backend_emit_oversized_0.manifest` lists four shards, processed serially
in one process:

```
split_72_shard_1  -> native_158.s   95MB  produced
split_75_shard_2  -> native_164.s   45MB  produced
split_189_shard_3 -> native_387.s   killed here
split_14_shard_2  -> native_59.s    never reached
```

The failing shard is the **smallest** of the group, which rules out raw size:

```
split_189_shard_3 (fails)   187949 lines  14357 blocks   31071 SSA  208 phi
split_72_shard_1  (ok)      582235 lines  72100 blocks  139804 SSA    0 phi
```

Running that one shard alone reproduces it in minutes instead of 90: RSS climbs
monotonically past 24.5 GB and it does not finish. Two hypotheses were formed
and both **DENIED** by that probe: it is not memory accumulated across the four
shards (one shard alone does it), and it is not phi handling (the phi-count
difference above is suggestive but the profile never enters phi code).

### What it actually is

```
build_stack_map_plans -> build_function_stack_map_plan -> add_record   97-99%
  py_func_call_kwargs                          ~71% of add_record
  pcc_gc_managed_pointer_find_slot             19-41% of add_record
```

`add_record` is a ~30-line closure that runs once per safepoint. Its real
lowered body, read from the bootstrap's own module IR, is **286 lines with 120
calls**, about 90 of them GC barriers.

(An earlier revision of this section reported 3648 lines / 1410 calls / ~800
barriers for `add_record`. That was an extraction error: the symbol's first
occurrence in the `.ll` is a *call site* inside
`build_function_stack_map_plan`, so searching backwards for `define` from it
yields the enclosing function's body, not the closure's. Those figures belong
to `build_function_stack_map_plan`, which runs once per function, not per
safepoint. Anchor on a `define .*@<exact symbol>\(` line, never on the first
textual occurrence of the name.)

### Fixed and verified

`PlannedSafepoint` was constructed with 8 keyword arguments per safepoint.
Making that positional (fields are in declaration order) **halved peak RSS on
the same shard and manifest**, and changed the shape of the curve from
monotonic growth to one the GC can claw back:

```
          before      after
t=120s   13600MB     6638MB
t=210s   21927MB    10551MB
peak     24509MB    12783MB
```

### Did not help — recorded so it is not retried

Three further keyword-only signatures were made positional
(`_local_label`, `add_record` itself, `_planned_managed_reloads`) and
`_locations(active)` was memoized on a version counter bumped only where
`active` can change (block boundary, and `_apply_frame_protocol` returning
true — that branch emits no record). Unit gates pass (32) and the logic is
sound, but **the shard emit did not get faster and `py_func_call_kwargs`
stayed at ~71% of `add_record`.**

The reason is not yet established, and four hypotheses have now been
**DENIED** by reading the real lowered IR:

* not accumulation across the four shards in the worker (one shard alone does it)
* not phi handling (the profile never enters phi code)
* not keyword call sites (removing every keyword-only signature changed nothing)
* not constructor dispatch — `add_record` lowers the frozen dataclass to
  `py_instance_new` plus a **direct** call of
  `PlannedSafepoint___init__`; its body contains **zero**
  `py_func_call_kwargs`, and so do `_locations`,
  `PlannedSafepoint___init__`, and in fact the **entire module** (0 call sites).

Intersecting `add_record`'s direct callees with every runtime-port function
that does emit `py_func_call_kwargs` leaves only `py_int_to_i64`, whose kwargs
path sits behind the C-extension number protocol — unreachable for a tagged
small int, which is what `ordinal` is.

So the profiler's attribution of `py_func_call_kwargs` to `add_record` is not
explained by any call `add_record` actually makes. It appears at 100% inclusive
at the top of the stack as well, which is consistent with it being an
**ancestor** frame on the worker entry path rather than a descendant, with the
tail-call pass flattening the tree between them. **Do not optimize against this
attribution until the frame is identified some other way** (a debugger
breakpoint on `py_func_call_kwargs` with a backtrace, or an emit built with the
tail-call pass disabled).

This is the third time in this investigation that a "direct child" in `sample`
output was actually a grandchild behind an elided tail-call frame
(`_is_type_object`, the int box/unbox helpers, and now
`py_func_call_kwargs`). Read the real lowered IR before acting on a caller
attribution.

### The two remaining blockers, stated precisely

1. **The `py_func_call_kwargs` frame is unidentified.** See the DENIED list
   above. It needs a debugger backtrace, not another guess from the profile.
2. **Barrier density.** ~90 GC barriers in a 30-line function, per safepoint,
   plus `pcc_gc_managed_pointer_find_slot` at 19-41% of the emit. The index
   itself is sound (load factor 0.5, back-shift deletion, power-of-two
   capacity, Fibonacci-mixed pointer hash), so this is allocation volume, i.e.
   the No.7/No.8 structural item (dead barriers on values that cannot be GC
   objects, boxed locals) — not a mis-ordered predicate and not a table bug.

Neither is a small edit, and neither was attempted here. `stage2` remains
**red**: 90.5 minutes and it does not complete.

## Update — No.14 ROOT CAUSE CONFIRMED: every dynamic method call leaked two objects

The `py_func_call_kwargs` attribution in No.13 was a dead end (an ancestor
frame flattened by the tail-call pass). The real cause was found by
discriminating **leak** from **slow**, which the profile cannot do.

### The experiment that turned it

Run the same failing shard under two GC backends:

```
gc0 (refcount)  180s -> 19.5 GB, monotonic growth
gc3 (tracing)   180s -> 11.1 GB, plateaus and falls back
```

A block of memory that refcounting cannot reclaim but tracing can means
**dropped references**, not fragmentation and not a big working set. That
split is what pointed at codegen ownership instead of at any backend pass.

An in-process heap census (lldb, sampling 739 of the 51327 VM_ALLOCATE slabs)
then named the garbage: tag=16 objects dominated, next tuples of length 2, then
short strings — and the string contents were `'groupdict'`, `'groups'`,
`'group'`, `'end'`, `'start'`, `'span'`. Those are exactly the six method names
`py_re_engine_runtime._new_match` attaches to every `re.Match`.

### Two independent leaks, both in the dynamic method-call path

`method_call_expression_lowering` lowers a DynType-receiver `obj.method(args)`
to `py_obj_getattr` + `py_obj_call`, and released neither result:

1. **The bound method object.** `py_obj_getattr` returns a NEW reference on
   every path — fields and `__dict__`/`__class__` incref, the dynamic-attr
   path goes through `py_dict_get` (which increfs), a descriptor `__get__`
   yields an owned result, and a plain method builds a fresh bound object via
   `py_instance_bind_method`. `py_obj_call` only borrows the callable, so the
   emitting frame owns it. **All 21 `py_obj_getattr` emission sites in codegen
   released nothing.**
2. **The call result.** `py_obj_call` unconditionally returns a new reference,
   but the statement-level `_gc_release_if_owned` asks
   `_expr_returns_owned_object`, which sees only a DynType Call and answers
   "not owned". The AST shape genuinely cannot decide this: a dozen native
   emitters intercept the same shape and return borrowed values. Only the
   emitter knows which path it took, so it now records the value in
   `_owned_dynamic_call_values` and the release consults that set first —
   mirroring the existing `_cpy_values` / `_owned_cpy_values` split.

### Minimal repros, each isolating one leak

```
2M x obj.method() where method returns None   2860 MB -> 2 MB   (leak 1 only:
                                                the result is immortal None)
300k x pat.match(text), result discarded      1728 MB -> 2 MB   (both leaks)
300k x pat.match(no-match)                       2 MB           (control: the
                                                leak scales exactly with the
                                                Match objects created)
```

The control matters: a non-matching pattern allocates no Match and stays flat,
which is what ties the leak to the returned object rather than to the engine.

### Scale

~1.4 KB leaked per dynamic method call. `self_backend_parse` alone emits 40
`py_obj_getattr` sites, and the IR parser runs one match per operand, so an
oversized stage2 shard leaked its way to 24 GB. This was never a stack-map
algorithm problem: `add_record` was 97% of the profile because it was the
allocation site being starved by a heap full of garbage the collector could not
free.

### Refuted along the way (do not retry)

Beyond No.13's list: `_locations` rebuild cost, the `.sort(key=lambda)`
keyword call, `_stable_id_bytes`, and the managed-pointer index itself (load
factor 0.5, back-shift deletion, power-of-two capacity, Fibonacci-mixed hash —
all sound; its 19-41% share was call volume driven by the leak). Also note the
host-CPython control that should have been the first clue: the identical
planning code over the identical shard completes in **9 seconds using under a
gigabyte** under CPython, versus >20 minutes and 24 GB under pcc1. A 167x gap
against an 8-24x baseline was itself evidence of a leak, not of a slow pass.

## Update — No.15 DENIED: host-CPython profiling is the wrong cost model for pcc1

Attempted the largest remaining item the host profile named, and it was a large
**net loss** under pcc1. Recording it in full because the failure mode
generalizes to every future optimization of this file.

### The change

`safepoint_id(identity, ordinal, kind)` hashes
`b"safepoint\0" + identity + b"\0" + str(ordinal) + b"\0" + str(kind)` one byte
at a time. `identity` is a ~80-character mangled symbol, so ~100 bytes are
re-hashed per safepoint while only the last few vary. FNV-1a is a streaming
hash, so the constant prefix can be absorbed once per function and resumed per
record — `stable_id_prefix_state` / `stable_id_resume`.

Bit-identity was proven exhaustively (5 symbols including a 200-character one x
6 ordinals x every `SAFEPOINT_KIND`, all equal).

### Host result: good

```
build_stack_map_plans   9.0s -> 8.1s   (this change alone)
add_record              2.32s -> 1.40s
_stable_id_bytes        0.97s -> out of the top 24
```

### pcc1 result: catastrophic

A stage-1 smoke input that normally compiles in seconds:

```
26+ minutes, RSS 15.6 GB, killed
  add_record                            100%
    stable_id_resume                     89.8%
      pcc_gc_managed_pointer_find_slot   59.2%
      pcc_gc_managed_pointer_index_insert 7.4%
```

### Why

Resuming needs `("\0" + part).encode("utf-8")` per part: a concatenation, an
encode, and the bytes object — **three fresh allocations per safepoint**, each
of which enters the global managed-pointer index. The original
`"\0".join(fields)` allocates **once**.

Host CPython's cost is dominated by interpreter-loop iterations, so trading
allocations for fewer loop iterations wins there. pcc1's cost is dominated by
**allocation count and GC index traffic**, so the same trade loses badly. The
two cost models point in opposite directions for this exact transformation.

**Rule for this file: measure against pcc1, never against host CPython.** A
host profile is still useful for finding *candidates* — it is not a basis for
accepting one. The hot path has been restored to `safepoint_id` with a comment
recording this verdict so the next agent does not re-derive it.

`stable_id_prefix_state` / `stable_id_resume` are left in `precise_stackmap.py`
(tested, unused by the hot path) since a future caller batching many ids behind
one prefix could still use them without the per-record allocations.

### Also found and avoided, not diagnosed

The first attempt failed differently: the field validation copied from
`scoped_stable_id` — `not isinstance(part, str) or not part or "\0" in part` —
**rejected plain decimal digit strings under pcc1** while the identical call
succeeded on host CPython. That is a real pcc1 semantic gap in `isinstance(x,
str)` or in `"\0" in x`, worth its own investigation. It is documented in
`stable_id_resume`'s docstring; nothing here depends on it any more.

### Kept from this round

Memoizing `is_local_value_ref` (pure function of the operand spelling; 346732
calls over ~35000 distinct strings) survives: it removes calls without adding
allocations, which is the only shape of win that helps both cost models. Host
went 8.1s -> 6.3s and 26.9M -> 18.8M calls; its pcc1 effect is unmeasured
because the `stable_id_resume` regression made that build unusable.

## Update — No.16: why No.15 lost, and a reusable pcc1 cost rule

No.15 was reverted on the strength of one pcc1 measurement, without a
mechanism. The mechanism turned out to be neither of the two things that were
plausible, and finding it needed three microbenchmarks — each compiled by pcc1
in ~5 seconds, which is the fast pcc1-side loop this investigation was missing.

### Hypothesis 1 — DENIED: "allocation beats loop iterations under pcc1"

That was the conclusion recorded in No.15. A microbenchmark of the two exact
spellings says the opposite:

```
                        pcc1        host
join whole string      441 ms      395 ms
cached prefix          102 ms       39 ms
```

The cached-prefix form is 4.3x FASTER under pcc1 too. So "pcc1 dislikes
allocations" does not explain the regression, and No.15's stated reason was
wrong.

### Hypothesis 2 — DENIED: "per-allocation cost scales with the live heap"

Plausible, because the real emit runs with ~10 GB live and a huge
managed-pointer index. Re-ran the same benchmark holding 900000 live tuples
first:

```
empty heap      441 ms / 102 ms
900k live       422 ms / 107 ms
```

Unchanged. The index size does not flip the ranking.

### Hypothesis 3 — CONFIRMED: the packed state left the tagged-int lane

`_stable_id_feed` returned the two 32-bit limbs packed as
`high * 0x100000000 + low`, which reaches ~2**63 — outside the +/-2**62 tagged
small-int lane — and the hot path packed and unpacked it twice per safepoint.

```
                                    pcc1        host
threading a packed (>2**62) state   604 ms      142 ms
keeping two in-lane limbs           247 ms      133 ms
ratio                               2.4x        1.07x
```

**Crossing the tagged lane costs 2.4x under pcc1 and is invisible on host
(1.07x).** That is the whole story: the streaming hash was a win on the string
work and a loss on the state threading, and only pcc1 charges for the latter.

### The rule this yields

pcc1's value model has a tagged small-int lane at +/-2**62 (project intent,
V-track: `int` has a value projection and an object projection). An
intermediate that exceeds it silently becomes a boxed bignum, and bignum shifts
and masks allocate. **Keep hot-path integer intermediates below 2**62**;
prefer several in-lane values over one packed wide value. Host CPython cannot
see this cost, so it will never appear in a host profile.

`stable_id_prefix_state` / `stable_id_resume` remain available and tested. A
future attempt should return the limbs *unpacked* (two in-lane ints) and avoid
varargs, which would keep the string win without the lane crossing. Not
attempted here: the host-side gain for this item was ~10%, and each pcc1
verification costs a full stage-1 rebuild.

### The fast loop, for the next agent

`~/.cache/pcc/test-artifacts/bench/micro/` holds the three benchmarks
(`alloc_vs_loop.py`, `alloc_vs_loop_heap.py`, `bignum_lane.py`). Each compiles
under pcc1 in ~5 s and runs in under a second, so a cost-model question can be
settled in seconds instead of a 10-minute rebuild. Use this before changing
code that pcc1 executes — and note that a microbenchmark must reproduce the
real *shape* (state threaded through the loop, values in the real magnitude
range), or it will mislead exactly as the first one did.

## Update — No.17 CONFIRMED: `sort(key=...)` lowered to insertion sort

The oversized shard that had never once finished now emits in **200 seconds**.

```
                        result              peak RSS
before the leak fix     never finished      24509 MB (monotonic)
after the leak fix      never finished      10683 MB
No.16 build             24 min, unfinished  19857 MB
this build              COMPLETED in 200s   16143 MB, 38 MB of asm
```

### The bug

`list.sort()` without a key routes to `py_obj_sorted`, whose comment records
that it *used* to be insertion sort and was changed to a bottom-up stable merge
sort because "O(n^2) comparisons dominated codegen-worker profiles via sorted
symbol lists".

**The keyed path was never given the same fix.**
`_emit_list_insertion_sort_by_key` still emitted insertion sort, and it is
strictly worse than the keyless version was: insertion sort re-evaluates the
key on *both sides of every comparison*, so the cost is O(n^2) **key calls**,
not O(n^2) comparisons.

`_locations` merges ~354 roots and sorts them 12186 times for one function:

```
insertion sort   354**2 / 2 x 2 key calls  ~= 124000 per sort
                 x 12186 sorts             ~= 1.5 billion key calls
merge sort       354 key calls + ~3000 tuple compares per sort
```

### The fix

The keyed path now emits a Schwartzian transform: build
`(key(elem), index, elem)` triples, hand the list to `py_obj_sorted`, write the
third field back. Tuple comparison is lexicographic, so ordering is by key with
the original index breaking ties — the same stable order insertion sort
produced. No new runtime surface; it reuses the merge sort that was already
there and already tested.

```
pcc1 microbenchmark, 200 rounds x 354 elements
  sort(key=) insertion   13787 ms
  Schwartzian + merge      260 ms      53x, identical result
```

### Where the profile pointed vs where the bug was

The profile said `add_record` was 94-97% of the emit, and it was — but not
because `add_record` was slow. It called `_locations`, which called
`list.sort(key=...)`. Three earlier rounds tried to make `add_record` itself
cheaper (positional args, memoized locations, a streamed identity hash) for a
combined gain of roughly nothing, because the actual cost was one level down in
a lowering decision, not in this file at all.

Also note what the microbenchmarks got wrong before they got it right: an
int-returning sort key instead of a tuple-returning one measured **no** gain
under pcc1 (133655 ms vs 137228 ms). That result is what forced the next
question — "is it the key or the sort?" — which decomposed as:

```
200 rounds x 354 elements    pcc1        host
plain int sort(), no key      66 ms       1 ms
obj sort(key=...)           8764 ms       5 ms
354 key calls, no sort        62 ms       5 ms
```

Key calls are cheap and sorting is cheap; only the two together explode. That
is the signature of a complexity bug, and it is invisible in any single-number
comparison.

### Verified

```
CPython differential      6 cases identical, including stability
                          (equal keys keep input order), empty, single
existing sort tests       15 passed
PCC_GC_BACKEND=0..4       all compile and run correctly
type_infer benchmark      29s, IR byte-identical to the pre-optimization pcc1
stage1                    90s
```

A regression test pins both properties — that the emitted IR contains
`@py_obj_sorted(` for a keyed sort, and the exact stable output order — so this
cannot silently return to insertion sort a third time.

### Defect I introduced and fixed while doing this

The first version emitted `py_list_set(pairs, i, triple)` into a list from
`py_list_new(n)`. That call returns a **length-0** list with capacity n, so
every indexed store was out of range and silently dropped: the whole result came
back `<null>`. `py_list_append` is correct. Two references (`key_val`, `elem`)
also needed releasing, since `py_tuple_set_item` takes its own. The CPython
differential caught this, not code reading — which is the argument for running
the differential before the expensive rebuild, not after.

## Update No.18 -- root cause found: pcc1 could not compile ANY `def` [CONFIRMED]

The `ssa-dominance` verifier errors reported in the previous update were
produced by a pcc1 that was itself broken. Minimizing the input showed the real
boundary:

```
x = 1; print(x)          OK      (module has no function -> no phi node)
def f(): pass            FAIL
def f() -> int: ...      FAIL
def main(): print(1)     FAIL
```

`PCC-PY-COMPILE-001 ... exception_type=Exception` with an empty message was the
only surface, so this had been invisible. Two diagnostics were needed before
the cause was reachable at all, and both are now committed to source:

* `pcc/py_frontend/pipeline.py`: the 22 `raise PyPipelineError(str(exc)) from exc`
  sites now raise `str(exc) or type(exc).__name__`, so a runtime-generated
  exception with no message at least reports its type.
* `pcc/backend/self_backend_aarch64_darwin.py`: `_emit_trace()`, gated on
  `PCC_DEBUG_SELF_BACKEND_TRACE`, marks prepare / order-profile / stack-map /
  globals / per-function phases.

With those, `PCC_DEBUG_CODEGEN_PHASES=1` showed codegen running to completion
(`module str end 55256`) and the failure landing in the self-backend. Dumping
that IR with `PCC_DEBUG_SELF_IR_DUMP_DIR` and feeding it to the **host**
backend gave `parsed funcs=4 / total phis=1 / verify OK / emit OK bytes=22198`
-- the IR was legal, so the defect was in pcc1's compiled emitter.

### Root cause

`build_function_stack_map_plan` in `self_backend_precise_stackmaps.py` memoized
merged location tuples in `interned_locations`, keyed on
`tuple(sorted(id(group) for group in active.values()))`. The code comment
asserted that "two equal-but-distinct groups simply miss ... and can never
return a wrong tuple". **That reasoning is wrong.** `id()` is only a stable key
while the keyed object is alive: a freed `_RootGroup` whose address is reused by
a different group makes a stale fingerprint *hit* and return the location tuple
of an unrelated root set. The host keeps those groups alive incidentally
(`_block_entry_states` holds one tuple of groups per block for the whole
function), which is why the host never reproduced it.

Fix: each cache entry keeps the groups it was keyed on alive next to the
answer -- `entry = (_locations(active), tuple(active.values()))`. That is 2465
small tuples for the oversized shard against 12186 avoided merges, so the
optimization is retained.

### Denied along the way

Each was disproved with a seconds-long pcc1 probe rather than a rebuild, and
each had looked convincing:

* `[DENIED]` `isinstance(block.phis, list)` mis-answering under pcc1 (a
  recorded pcc1 defect for `str`). Probe: `isinstance([], list)` -> True,
  `isinstance((), list)` -> False. Correct.
* `[DENIED]` `@dataclass(slots=True)` field reassignment failing under pcc1.
  Probe: init / reassign / append all correct.
* `[DENIED]` pcc1 losing exception messages, i.e. the empty message being a
  runtime gap. Probe: `raise ValueError("HELLO-MSG")` round-trips with
  `args is None` -> False. The message really was empty at the raise site.
* `[DENIED]` `dict.get()` mis-lowering under pcc1 (also a recorded defect).
  Probe with an int-tuple key and tuple value: miss -> None, hit -> correct
  value. The `in` + subscript rewrite was kept anyway as the recorded-safe
  idiom, at no cost.
* `[DENIED]` the shared `_EMPTY_SEQUENCE` for `ParsedBlock.phis` plus its
  `isinstance` promotion. Removed for simplicity, but it was not the defect --
  both mechanisms it depended on were probed correct above.

`block.raw_lines = _EMPTY_BLOCK_LINES` after parsing was **verified safe and
kept**: `rg raw_lines pcc/backend/` shows the field is written and read only
inside `_parse_block`.

### Why no gate caught it

Every existing pcc1 test stops at `--emit-llvm`, so the self-backend emit path
was only ever exercised from the host. The chained smoke check used
`print(1)` -- which happens to be the one shape with no function and therefore
no phi node, i.e. the only shape that still worked.
`tests/python/test_pcc1_emits_native_function_binary.py` now compiles four
shapes bracketing that boundary through to a native binary and runs each under
`PCC_DEBUG_RUNTIME=1`.

### Status

Root cause fixed and confirmed: all four shapes compile and run correctly under
a pcc1 rebuilt from the fixed source (stage1 491 s). Whether the three
`ssa-dominance` errors were purely a consequence of this defect is not yet
established -- a stage2 run on the fixed pcc1 is the outstanding evidence.

## Update No.19 -- the stage2 emit bottleneck: a quadratic in `collect_block_local_last_uses` [CONFIRMED]

With the `def`-compiling defect of No.18 fixed, stage2 reached the self-backend
emit phase for the first time and then sat on **one** oversized shard for 57+
minutes at 98% CPU. Measured on the live worker:

```
shard            self_backend_split_72_shard_1.ll   28 MB
one function     59402 blocks
RSS              1.9 GB
physical footprint   54.4 GB   <-- the "two pcc processes using 60 G" report
progress         0 of its 4 outputs after 57 min; 45 of 461 results overall
```

`sample` on the worker gave a single dominant chain (the binary is stripped, so
offsets were resolved against `nm` with static = `0x100000000 + offset`):

```
allocate_aarch64_block_registers
  -> collect_block_local_last_uses     6551 / 6573 samples
     -> text_key_mapping_get           6545 / 6573
```

### Root cause

`collect_block_local_last_uses` looked its own result dict up while filling it:

```python
block_mapping = text_key_mapping_get(block_local_last_uses, block_name)
if block_mapping is None:
    block_mapping = {}
    block_local_last_uses[block_name] = block_mapping   # grows the mapping
```

Every new block name grew the mapping, so the next lookup missed, fell into the
false-hash-miss fallback, and re-entered `_text_key_index` -- whose "incremental"
extension still did `keys_list = list(mapping)`, materialising **all** keys.
That is O(definitions x blocks) time *and* a fresh multi-hundred-KB list per
definition, which is what produced the 54.4 GB footprint: pcc1's cost is
dominated by allocation count, so the host penalty is amplified enormously.

### Fix

Two changes, both narrow:

* `self_backend_analysis.py`: resolve the per-block dict through a stable int
  bucket (`_stable_text_bucket_key`) side index -- the same technique
  `_record_definition` / `_record_use_position` / `_record_block_length`
  already use in this function -- and never look up the growing result dict.
  Equality is still checked with `text_key_names_equal`, so inconsistent
  native str hashing stays handled.
* `self_backend_ir.py`: `_text_key_index` walks the mapping and skips the first
  `cursor` keys instead of building `list(mapping)`. Same complexity, but no
  giant allocation per growth, which bounds the damage for any other caller.

### Measured, on the real 28 MB shard

```
                                        host
old formulation (differential oracle)   47.6 s   (25 s at 85k defs, 47.6 s at 118k -> superlinear)
new formulation                          2.8 s   17x
output                                  identical (differential over every function)
peak RSS (host, new)                    499 MB
```

A unit check covers `_text_key_index`'s cursor across interleaved
grow-and-probe, and the differential oracle is kept in the probe script rather
than the test, since the pre-fix formulation is what it exists to compare
against.

### Contract change surfaced by this

`jobs_for_input_sizes` no longer collapses the whole batch to one worker when
any input crosses the split threshold; it caps at `LARGE_INPUT_CONCURRENCY`.
`test_native_large_inputs_are_serial_unless_jobs_are_explicit` encoded the old
rule and was red. It is now
`test_native_large_inputs_are_capped_not_serialized`, and it asserts the cap
actually binds on a batch wide enough to want more workers, so it cannot pass
with the cap removed. The 1.27 GB figure quoted in that code comment was
measured on **stage1** and did not cover a stage2 oversized shard -- the 54.4 GB
footprint above is why the cap is kept rather than removed.

### Status

Fix landed and differentially verified on host. Outstanding: the
stage1 -> stage2 -> stage3 chain on a pcc1 rebuilt from the fixed source, with
wall time and peak RSS per stage, then the five-GC cold/warm matrix.

## Update No.20 -- two more O(n^2) scans in the emit path, found by profiling instead of guessing [CONFIRMED]

`sample` on a live worker pointed at `_dot_numeric_text_key_id`, and a
microbenchmark **refuted** optimizing it: 240000 calls take 0.03 s, and
memoizing gives only 1.6x. Recording that as a denial mattered, because a
`cProfile` run at Python level then showed the same function as the top entry
by a wide margin -- the leaf was right, the *fix* would have been wrong. What
made it hot was its callers:

```
   ncalls   tottime  cumtime  function                    (10.4 MB module, ~120 s total)
100972224     42.2s    64.2s  _dot_numeric_text_key_id
203257888     15.2s           str.startswith
 65825805     14.5s    78.1s  text_key_names_equal
   124268      6.8s    69.8s  _aarch64_increment_value_use_count   <-- caller
   200971      1.4s    12.3s  _int_mapping_get                     <-- caller
```

124268 increments produced 65.8 million comparisons: 530 per call, because a
dict miss walked the whole growing `counts` dict. That is O(distinct values^2).

### Fix 1 -- canonical key instead of a recovery scan

`text_key_names_equal(a, b)` holds exactly when the spellings are identical or
both are dot-numeric with the same id. So keying dot-numeric names on the id
itself makes an ordinary dict lookup *already* exact, and both scans in
`self_backend_target_passes.py` disappear:

```python
def _aarch64_use_count_key(value_name: str):
    numeric_id = _dot_numeric_text_key_id(value_name)
    if numeric_id >= 0:
        return numeric_id
    return value_name
```

Int and str keys cannot collide in one dict. This also makes zero-padded
spellings (`%.05` vs `%.5`) converge deterministically, where the ordered scan
resolved them only by whichever entry it reached first. Contained: the dict has
exactly one consumer (`_aarch64_collect_value_use_counts` builds it,
`_aarch64_value_use_count` queries it) and nothing iterates its keys.

### Fix 2 -- reuse the incremental index in regalloc

`_type_mapping_get` / `_int_mapping_get` / `_last_use_mapping_get` had the same
miss-then-scan shape, but over `func.value_types`, `func.value_registers`, and
`block_last_uses` -- shared structures whose key type must not change. They now
call `text_key_mapping_get`, which is O(1) amortised, keeps the mapping pinned
in its cache entry so an id() cannot be recycled under it, and covers both
recovery cases the scan covered (`by_id` for dot-numeric spellings, `by_bucket`
for plain equality after an inconsistent native hash).

`_int_mapping_set` and `_int_mapping_delete` **always** scan (no fast path at
all, and `delete` allocates `list(mapping)`). They did not appear in the top 22
so they are left alone; noted here rather than fixed silently.

### Measured, on a 10.4 MB module, byte-identical output throughout

A/B in one process, injecting the old implementation over the new one so
nothing else differs. `sha` is over the full assembly text:

```
old (both scans)              32.2s   30180438 bytes   sha=621eb0e15e2b34b5b96767bd
+ use-count canonical key     18.9s   30180438 bytes   sha=621eb0e15e2b34b5b96767bd   1.70x
+ regalloc shared index       17.0s   30180438 bytes   sha=621eb0e15e2b34b5b96767bd   1.89x
```

The old run went second, with caches warm, so 1.89x is if anything
conservative. 10.4 MB IR -> 30.2 MB / 1398007 lines of assembly.

### Retracted from Update No.19

The claim that 98.7% of emitted lines were one-value-per-line data directives
was measured on a **stale** `.s` in an old temp directory, produced by an older
pcc1 whose emitter did not pack. Current source both dedups the location table
and packs it 8 per line (`_append_packed_location_lines`), and a current emit of
a 10.4 MB module expands 3x, not 55x. The line-count analysis in No.19 does not
describe current behaviour and is withdrawn. This is the second time in one
session that an old artifact produced a confident wrong conclusion.

### Memory attribution (host, per phase, 28 MB / 59402-block shard)

```
read 29 MB text            0.07 GB
parse                      0.41 GB
collect_block_local_last_uses  0.49 GB
prepare_module_for_target  0.74 GB
build_stack_map_plans      0.84 GB
```

The host completes the analysis chain in under 1 GB where one pcc1 emit worker
held 11.5 GB RSS with a 66 GB physical footprint. So the remaining memory gap is
**pcc runtime object overhead plus an allocator that does not return pages**,
not another algorithmic blowup in the emitter. stage2's 21.4 GB peak is
`LARGE_INPUT_CONCURRENCY` (4) times the per-worker peak, not a single phase
growing.

### Status

Both scans fixed, output byte-identical, 41 focused backend tests green. A
stage2 run on a pcc1 rebuilt from this source is the outstanding end-to-end
evidence, together with the 28 MB oversized shard's time and peak RSS.

## Update No.21 -- pcc2 could not print any integer; root cause chain and where it still stops [CONFIRMED to a specific edge]

`stage2 exit=0` is not "pcc2 is correct". On an **idle** machine (an earlier
`<null>` was wrongly excused as contention with a concurrent stage3 -- the
retest disproved that excuse):

```
same input, def main(): print(7)
  pcc1 -> 7          pcc2 -> <null>

print("hi") / x="hi";print(x) / print(True)   pcc2 correct
print(1)                                       pcc2 <null>
print(1 + 2)   pcc2 "Unhandled non-exception object (null)"
```

The emitted IR differed in exactly **one** line out of 1818:

```
pcc1:  %int.lit.tagged.2.12 = inttoptr i64 3 to ptr
pcc2:  %int.lit.tagged.2.12 = inttoptr i64 0 to ptr
```

### Immediate cause, fixed

`_emit_int_literal_object` computed the tagged constant as
`((value << 1) | 1) & 0xFFFFFFFFFFFFFFFF`. A **literal** integer above the
tagged lane evaluates to **0** in pcc-compiled code, so under pcc2 the mask
became `& 0` and every int literal lowered to a NULL pointer. The mask was
redundant: for an in-lane `value`, `(value << 1) | 1` is already in
`[-2**63+1, 2**63-1]`, and for negatives its bit pattern is identical to the
masked form. Removed.

All 13 over-lane literals in `pcc/py_frontend` and `pcc/backend` were rewritten
as equivalent expressions (`((0xHI << 32) | 0xLO)`, each asserted equal to the
original), because several were latent miscompiles of their own: the FNV-1a
64-bit offset basis in `macho_parallel.py`, a stack-map mask in
`precise_stackmap.py`, an x86 mask, and the float-inf bit pattern.
`tests/python/test_selfhost_no_over_lane_int_literals.py` keeps new ones out.

**Verified end to end**: rebuilt stage1 (445 s / 6.6 GB) and stage2
(2493 s / 21.4 GB), then `pcc2` compiled and ran `print(7)` -> `7`.

### Underlying defect, narrowed but NOT fixed

Why is a big literal 0 in the first place? The parser does
`int(e.text, 0)` (`pcc/parse/py_lift.py:583`), so inside pcc1 the literal is
parsed by the **runtime port** `py_int_from_cstr`. Probed with a pcc1-built
binary:

```
                                       pcc1     host
int("9223372036854775808", 10)            0      correct
int("9223372036854775808",  0)            0      correct
int("0xFFFFFFFFFFFFFFFF",   0)            0      correct
int("FFFFFFFFFFFFFFFF",    16)            0      correct
int("9223372036854775807", 10)      correct      correct   <- in-i64 path
(1 << 63)  computed at run time     correct      correct
4611686018427387903 * 4             correct      correct
```

So only the over-i64 path is wrong. Bisected inside the port by temporary
sentinels (all removed, archive rebuilt, baseline re-confirmed):

* `_parse_bigint` **is** reached for all four failing cases (entry sentinel
  12345 came through).
* Its accumulation loop runs the right number of iterations from the right
  offset: 19 iterations / first byte `'9'` for the decimal, 16 / `'F'` for the
  hex.
* `py_int_mul` and `py_int_add` behave correctly: a tagged-bit probe reported
  `p1` tagged, and both `pm` and `pa` **non-NULL heap bignums**.
* Returning a **tagged immediate** through this path works; returning the
  **heap bignum** yields 0 at the caller.
* The frontend IR is clean -- in `py_int_from_cstr`,
  `%.172 = call ptr @user_py_int_parse__parse_bigint(...)` is immediately
  followed by `ret ptr %.172`, with no `pcc_gc_release` / `pcc_gc_unpin` /
  `py_decref` in between.
* The in-i64 path (`E`) returns a heap bignum too -- 2**63-1 is above the
  tagged lane -- through the **same** `@c_abi_export` boundary, and it works.
  The only structural difference is that it returns from the function tail
  while the bigint path returns from **inside the digit loop**.

`[DENIED]` A user-level minimal reproducer does **not** reproduce: returning a
heap `str` from inside a `while` loop in an ordinary program is correct under
pcc1 (`in_loop = [a7]`). So this is not simply "early return from a loop loses
a heap pointer" at user level; it is specific to the freestanding /
`@c_abi_export` port context.

So the loss happens **below the frontend IR** -- self-backend, link, or object
staging -- on the edge that returns a heap pointer from inside a loop in a
freestanding exported function. That is where the next session should start:
read the emitted assembly for `py_int_from_cstr` around its early-return block,
and build a freestanding `--python-library` reproducer rather than a user-level
one.

### Status

Call sites fixed and pcc2 proven to print integers again. The compiler defect
itself is open and tracked as **M5-SELFHOST-BIG-INT-LITERAL** (P0), with this
narrowing recorded in its `open_boundary`.

## Update No.22 -- the big-literal root cause, one layer fixed and one layer named [CONFIRMED]

Continuing No.21. The loss is **not** below the frontend IR after all -- the port
chain (`_parse_bigint`, `py_int_from_cstr`, `py_int_from_cstr_or_raise`) is
correct in IR and returns `ptr` everywhere. The loss is on the **consumer**
side, and the user-program IR shows it directly:

```llvm
%int.parse.12.18     = call ptr @py_int_from_cstr_or_raise(...)   ; correct bignum
%m.int_unbox.27      = call i64 @py_int_to_i64(ptr %int.parse.12.18, ptr %ov.flag.26)
%exact.int.box.19.28 = call ptr @py_int_from_i64(i64 %m.int_unbox.27)
```

`marshal_from_object` unboxes an int-typed object to i64, and the overflow slot
it allocates was documented **"caller can ignore"**. Above 2**63-1
`py_int_to_i64` yields 0, and the immediate re-box produces 0. That is also why
the failing threshold is exactly the signed i64 maximum and not the tagged lane:
`int("9223372036854775807")` was always correct.

### Fixed: the adjacent unbox/re-box pair

`py_int_from_i64(py_int_to_i64(x))` now returns `x`. This is a correctness fix,
not an optimisation -- keeping the bignum is what CPython does.

An ownership defect in the first version of this fix was caught before it
shipped: `py_int_from_i64` hands back a **new owned reference**, so returning
the borrowed original would let the caller's release drop a reference it never
took -- the over-release shape that has broken pcc1 before. The path now
retains. Verified over 20000/50000 iterations: correct total, peak RSS 13.3 MB
(a leak would balloon it), no `[BAD_INCREF]`.

`[DENIED]` Carrying the link as an attribute on the IR value (which would
remove the id()-keyed table entirely) is impossible: llvmlite `Value` uses
`__slots__` and has no `__dict__`. Reverted to a side table whose entry stores
the keyed value, so a recycled id() cannot hand back an unrelated object.

### Verified under a rebuilt pcc1 (stage1 233 s warm)

```
                                    before    after
int("9223372036854775808", 10)          0     9223372036854775808
int("9223372036854775808",  0)          0     9223372036854775808
int("0xFFFFFFFFFFFFFFFF",   0)          0     18446744073709551615
int("FFFFFFFFFFFFFFFF",    16)          0     18446744073709551615
```

### Still open, and now precisely named

A **source literal** is still 0:

```
print(9223372036854775808)   ->  0
print(0xFFFFFFFFFFFFFFFF)    ->  0
print(1)                     ->  1
```

because the parser's value has to survive a store:

```
int(e.text, 0)                       correct bignum now
  -> stored into IntLit.value / a local inferred as `int`   TRUNCATED
  -> _emit_int_literal_object(0)     emits 0
```

Confirmed minimally -- the loss happens at the **assignment**, before any field
store:

```python
big = int("9223372036854775808", 0)
print(big)          # pcc: 0        CPython: 9223372036854775808
```

An `int`-annotated local or dataclass field is an i64-backed exact-int slot and
cannot hold a bignum. Per the project north star, `int` is an
arbitrary-precision semantic type whose value projection must **deopt or
promote** on lane overflow, never wrap -- and this does not even wrap. The fix
is the admission demotion already designed as **INT-P0-PROJ**; it is not
attempted here.

### Status

Runtime `int(str)` parsing fixed and verified under pcc1. Source literals remain
0, blocked on INT-P0-PROJ. The 13 over-lane literals in the closure stay
rewritten as expressions, and the lint gate keeps new ones out, so the pcc2
integer-printing failure cannot recur through that route.

## Update No.23 -- big-literal fix attempt: verdict RETRACTED, see No.24

Continuing No.22. The remaining half was chased to a precise shape ladder, then
the fix was implemented, measured unsound in **both** directions, and reverted.
Recording it in full so the next attempt does not repeat it.

### The shape ladder (host-compiled, fast loop -- seconds per iteration)

Reproducing the parser's chain on the host instead of rebuilding pcc1 each time
was the single most useful move here; earlier rounds of this investigation paid
~5 minutes per hypothesis for no reason.

```
1 module, direct ctor arg   Lit(int(TEXT, 0))            0
2 module, via local         tmp = int(...); Lit(tmp)     correct
3 fn, direct ctor arg       return Lit(int(text, 0))     0
4 fn, via local             v = int(...); Lit(v)         correct
5 fn returns the int        return int(text, 0)          correct
6 fn, untyped param         Lit(int(text, 0))            0
```

Two distinct sites, both found:

* `_materialize_class_init_call_args` (`call_expression_lowering.py`) opened the
  staging slot from `_emit_expr(arg)`'s type. For an int-typed call that is an
  **i64**, so the slot was i64 and `store i64 %m.int_unbox, ptr
  %__pcc_ctor_arg_0_0.addr` dropped the bignum. Staging the object projection
  fixed shapes 1 and 3.
* `int(<dyn>)` (`numeric_builtin_lowering.py`) unboxes all four branches
  (str/float/bool/int) and phis them as `_I64`, so shape 6 loses any bignum.

### RETRACTION (see Update No.24)

**Every `[DENIED]` verdict below is withdrawn as unsupported.** They compared a
program that produces bignums against one that does not, and concluded "my
change leaks".  That is not the right control: the arms differ in the *size and
kind of object retained*, not in whether the change is present.  Two further
methodology errors compounded it -- a claim that the measurements were served
from a stale cache (they were not), and a baseline taken with `git show HEAD:`
while HEAD itself had moved mid-session, so several "mine vs HEAD" comparisons
were mine-vs-mine.

The correct control, run in No.24: the same loop shape producing bignums through
a path **this work never touched** (`1 << 70` computed at run time) grows
29 MB -> 80 MB from 20k to 60k iterations, i.e. the same order as the changed
path (26 MB -> 76 MB).  Bignum-producing loops in this runtime grow regardless of
this change, which is a separate pre-existing condition worth its own
investigation (~320 B per bignum retained).  Nothing here supports attributing a
leak to the fix.

### Original (withdrawn) reasoning

`[DENIED]` **Object projection alongside the i64 phi in `int(<dyn>)`.** Each
branch produced an object and a parallel `ptr` phi carried it. Objects are
created eagerly but consumed only if some later site re-boxes, and most callers
just use the i64, so the unconsumed references leak. Measured: peak RSS 17 MB at
20k iterations vs 162 MB at 200k -- linear, about 160 B per call, against a flat
1 MB baseline.

`[DENIED]` **Recovering the source object inside `marshal_to_object`.** Isolated
by swapping single files against HEAD:

```
marshal mine, others HEAD          162 MB    leaks
marshal HEAD, others mine            1 MB    no leak, and no fix either
```

With a retain it leaks one reference per call, because the release side dedups
by value identity and releases the value once. Without the retain the value is
freed early. So this site cannot be fixed by an incref at all: the recovered
value has to be **registered with the ownership lowering as that call's owned
result** (the `_note_owned_dynamic_call_value` mechanism), which is the design
the next attempt should start from.

A correction to an intermediate reading in this investigation: `total=100000`
from the 20k-iteration probe was **not** corruption -- it is the correct total
when all five values print as `"0"` (1 char x 5 x 20000). Truncation, not a
double free.

`[DENIED]` Carrying the unbox link as an attribute on the IR value instead of an
id()-keyed table: llvmlite `Value` uses `__slots__` and has no `__dict__`.

### What was kept, and why it is safe

* The redundant `& 0xFFFFFFFFFFFFFFFF` in `_emit_int_literal_object` stays
  removed. This is the fix that lets **pcc2 print integers at all**, it is
  independent of the recovery work, and ordinary ints verify clean
  (`1 / -7 / 4611686018427387903`).
* The 13 over-lane literals in `pcc/py_frontend` and `pcc/backend` stay
  rewritten as expressions, with
  `tests/python/test_selfhost_no_over_lane_int_literals.py` keeping new ones
  out. Several were latent miscompiles of their own (FNV-1a offset basis, a
  stack-map mask, an x86 mask, the float-inf bit pattern).
* `tests/python/test_int_unbox_rebox_roundtrip.py` is now
  `xfail(strict=True)` so the gap stays visible and flips to a hard failure the
  moment it is really fixed.
* Leak baseline re-confirmed flat after the revert: 1 MB at both 20k and 200k
  iterations, no `[BAD_INCREF]`, 81 focused tests green.

### Status

Source-level integer literals above 2**63-1 still lower to 0 under pcc1. The two
losing sites are identified, the ownership requirement is understood, and the
naive fix is disproved with numbers. Tracked as **M5-SELFHOST-BIG-INT-LITERAL**.

## Update No.24 -- big integer literals FIXED; four sites, one principle [CONFIRMED]

All six shapes of the ladder from No.23 are now correct, verified on
host-compiled binaries:

```
1 module, direct ctor arg   Lit(int(TEXT, 0))          9223372036854775808
2 module, via local         tmp = int(TEXT, 0)         9223372036854775808
3 fn, direct ctor arg       return Lit(int(text, 0))   9223372036854775808
4 fn, via local             v = int(...); Lit(v)       9223372036854775808
5 fn returns the int        return int(text, 0)        9223372036854775808
6 fn, untyped param         int(<dyn>)                 9223372036854775808
```

### The principle that made it work

Do not unbox and then try to recover.  Reach for a runtime call that **returns a
new owned object**, and never let the value touch i64:

* `emit_int_builtin_as_object` -- `int(<str>)` returns the
  `py_int_from_cstr_or_raise` result directly.
* `_emit_int_dyn_as_object` -- `int(<dyn>)` dispatches on the type tag and phis
  the **objects**.  Placement is the whole point: the same object phi bolted onto
  the *i64-returning* function leaked, because the objects were built eagerly and
  most callers only want the i64, so nothing consumed them.  Inside the
  object-returning variant the join is always consumed, so every branch can hand
  back an owned reference (the str branch's parse result already is one, the int
  branch retains its borrowed input, float/bool box their exact i64).
* `_maybe_emit_exact_int_object` gained an `Attr` case: `py_instance_get_field`
  when the container's class and field index are statically known (BORROWED, so
  nothing to release), otherwise `py_obj_getattr` registered with
  `_note_owned_dynamic_call_value` (NEW reference).
* `_materialize_class_init_call_args` stages an `int` argument as the object
  projection instead of opening the slot from `_emit_expr`'s i64 type -- an i64
  staging slot cannot hold a bignum.

### Leak question, answered with a real control

```
                                    20k      60k
all-small-int control               20 MB    55 MB
changed path (int(str)/int(dyn))    26 MB    76 MB
UNTOUCHED path (1 << 70 computed)   29 MB    80 MB
```

The untouched path grows the same way, so the growth is a pre-existing property
of bignum-producing loops here, not something this change introduced.  The
all-small-int control also grows, so the loop shape itself retains something --
both are separate findings, filed rather than fixed here.

### Gates

87 passed, plus one pre-existing failure unrelated to integers
(`test_multiple_legacy_starstar_kwargs_split_to_splat_dict`, a
`NotImplementedError` for multiple `**mapping` operands, confirmed still failing
with this change disabled).

### Status

Fixed on host across all six shapes. `pcc1`-rebuilt source-literal confirmation
is the outstanding evidence.

## Update No.25 -- the real gate is `boxed_world=False`, not any single emit site [CONFIRMED]

No.24's fix works for **user programs compiled by the host** (all six shapes), and
`pcc1` itself gained several genuine fixes (below).  But a `pcc1`-rebuilt source
literal stayed 0 through four more rebuilds, and instrumenting the **real**
chain -- instead of the mimic programs used up to that point -- explained why in
two lines.

Splitting the chain at both ends:

```
[biglit] lift 9223372036854775808 -> 0      <- lost at the parser's int(e.text, 0)
[biglit] emit received 0
```

and then asking why the object path never ran:

```
[biglit] assign target=idx boxed_world=False forced=True rhs=IntLit
```

`_int_exprs_are_boxed()` is **False** under `--ir-scaffold=on
--python-libpython=off`, i.e. the mode pcc1 is built in.  In that world `int`
values are native i64 by default, so `_maybe_emit_exact_int_object` -- and with
it every object projection added in No.24 -- is never reached.  Confirmed
directly: compiling `pcc/parse/py_lift.py` in that mode produces **zero**
`emit_int_builtin_as_object` calls.

That is why the mimic programs kept "fixing" the bug while pcc1 did not move:
the host compiles user programs in the boxed world and pcc1 is compiled in the
unboxed one, so the two exercise different code entirely.

### What this means for the fix

The remaining work is not another emit site.  It is the admission decision
itself: an `int` whose value range is unbounded (an `int(<str>)` result, a value
arriving from an unannotated source) must be **demoted to the object
projection** in this mode rather than staying in the i64 lane.  That is
INT-P0-PROJ, a mode-level change, and it is deliberately not attempted here.

### Landed and verified under pcc1 (stage1 green three times: 219 s / 334 s / 525 s)

```
int("...") dec/hex x base0/base10     0 -> correct
int-typed field read                  0 -> correct
field value re-wrapped through int()  0 -> correct
```

Plus, for host-compiled user programs, all six shapes of the No.24 ladder.

### Also landed

A runtime `py_obj_as_int_object(o, base)` (C + pcc-Python port mirror + ABI
registration) so the Dyn case is ONE call.  Building the four-way dispatch in
the frontend instead created basic blocks and a phi inside
`_maybe_emit_exact_int_object` -- a probe whose result callers may discard,
which orphans the blocks and parks the builder on the join.  That compiled every
small host program and **broke stage1**; isolation confirmed it (stage1 exit 0
with it disabled, exit 1 with it enabled).

### Not landed

`pcc1` still lowers a source-level integer literal above 2**63-1 to 0.  Tracked
as **M5-SELFHOST-BIG-INT-LITERAL**, whose blocker is now precisely named rather
than guessed.

All temporary instrumentation (`PCC_DEBUG_BIGLIT` in `py_lift.py`,
`literal_lowering.py`, `numeric_builtin_lowering.py`,
`assignment_statement_lowering.py`) has been removed.
