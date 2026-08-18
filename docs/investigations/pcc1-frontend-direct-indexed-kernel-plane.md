# Investigation: pcc1 frontend rebuilds text that the self backend parses back into the same indexed kernel

## Status

active

## Problem Description

The accepted No.89 same-source phase comparison isolates the Stage2/Stage1
gap to two pcc1 heap-heavy workers:

```text
phase                              Stage1       Stage2       ratio    S2-S1 gap
export/summary                      67.617s      69.924s      1.03x      2.307s
frontend codegen worker             16.160s     109.684s      6.79x     93.524s
native self-backend emit            96.323s     254.655s      2.64x    158.332s
pcc link driver                     63.350s      76.493s      1.21x     13.143s
```

The export/summary phase proves that pcc1 is not uniformly slow.  The 251.856
seconds of codegen+emit gap comes from one architectural round trip:

```text
typed AST
  -> pcc.llvm_capi.ir object/text builder
  -> hundreds of MB of LLVM text
  -> file/process boundary
  -> self_backend_parse
  -> IndexedFunctionSeed / IndexedFunctionKernel
  -> verifier / stackprep / stack maps / regalloc / emit
```

The downstream kernel plane is already complete for the supported AArch64
path: parser construction publishes block/value/type/definition/use/call/PHI/
terminator/instruction records into final dense arenas, and every normal hot
consumer reports zero legacy record projection.  The frontend builder discards
its structured operands into `InstructionRecord.text`, forcing the emitter to
recover the same facts from strings.

This is the successor to
[`pcc1-exact-str-concat-chain-object-tax.md`](pcc1-exact-str-concat-chain-object-tax.md).
That candidate proved short strings are 68.55% of allocation requests / 77.21%
of requested bytes in a real frontend diagnostic window, but fusing 251
binary concat calls moved the worker only 1.01078x.  The unit of optimization
must therefore be the whole text construction/parse boundary, not a concat,
cache, renderer or root helper.

The pcc linker is not the owner of this gap.  A retained 636-assembly replay
completed in 76.832 seconds and its host flamegraph attributes the useful work
primarily to independent `arm64_encode`/`assemble_file` operations.  Stage1
already pays 63.350 seconds of the same driver, so linker-only work cannot
remove the pcc1 execution delta.

## Repro

Authoritative whole-stage inputs and phase receipts:

```text
build/no89-call-span-stage1-candidate-315-v1/stage1.profile.json
build/no89-current-gc0-stage2-profile-v1/profile/stage2.json
```

Representative exact-output controls:

```text
frontend AST/native-export worker:
  build/no89-frontend-worker0-replay-v1/profile/worker.manifest
  expected IR sha256 065100ba25f24b5ef5d423b4ed6246058e5d1f4fe7f1d152c1f4176f574a77a7

self-backend item:
  build/stage2-current-object-inputs-no62-v1/item_311.ll
  expected assembly sha256 ff943e10afe802c44faff43146a67b56735cd74bb6f1d79db1d8251cfe8f7251
```

The direct path must run beside the text oracle from one frozen builder state.
For each function/module, compare normalized seed facts, verifier verdict,
stack layout, packed stack maps, relocations and final assembly before allowing
the text parse to disappear from production.

## Test [CONFIRMED]

The performance failure is confirmed by the two source-frozen profile JSONs:
Stage2 is 598.629 seconds against Stage1 275.13 seconds, with codegen+emit
alone contributing 251.856 seconds of the gap.  The existing No.89 frontend
and item311 replays both return zero and retain the hashes above.

No implementation red exists yet.  The first checked-in tests must pin:

- a neutral direct-module schema whose supported instruction vocabulary is
  exactly the parser/kernel vocabulary, not an llvm_capi-only second IR;
- direct/text differentials for every call, alloca, load/store, cast, icmp,
  binop, select, GEP, PHI and terminator record plus diagnostic cold shapes;
- block/value/type IDs and module/function/global order independent of hash
  seed and worker completion order;
- one builder state producing both canonical LLVM text and a direct seed,
  with zero ordinary instruction reparse on the direct route;
- cancellation/error publication that cannot leave a partial object/cache
  result, and deterministic object ordering at the final link boundary;
- exact item311 assembly and the complete retained Stage2 frontend/emit corpus
  before any Stage2 run.

## Proposals

- No.1 Publish one neutral direct indexed-kernel plane from llvm_capi builder to self emitter [pending]

## No.1 Publish one neutral direct indexed-kernel plane from llvm_capi builder to self emitter

### Code Change

Use `IndexedFunctionSeed`/its neutral schema as the single downstream record
contract.  Extend `pcc.llvm_capi.ir` records so each builder operation publishes
the same final type IDs, value IDs, operands, flags and CFG facts while it still
has structured values; do not add a parallel tuple/dataclass IR.  Module/global
records get the same deterministic indexed ownership.  Canonical LLVM text
remains lazily materializable for `--emit-llvm`, diagnostics, cache audit and
the differential oracle.

Add a strict self-worker path that consumes the direct module in the same
short-lived process after frontend objects are released.  The ordinary text
worker remains the fallback for an explicitly unsupported/cold record and
increments a visible counter; the accepted pcc bootstrap corpus requires that
counter to be zero.  Function/module plans, symbol names, shard IDs, output
paths and final object ordering freeze before any parallel publication.

This proposal is not a new compiler language, a package special case, silent
LLVM fallback, global string interning, global managed-pointer bypass, or
permission to retain frontend and backend heaps simultaneously.  The worker
must release frontend-only state before direct verify/prepare/emit and stay
inside the measured process-tree RSS budget.

Pre-registered performance gates:

- direct/text output differentials must be exact on focused tests and the
  complete retained Stage2 corpus; any unsupported hot record or semantic
  difference denies the direct route;
- one source-frozen representative frontend+emit replay must improve combined
  wall and CPU at least 1.25x, retire at most 0.80x instructions, and use at
  most 1.02x peak footprint/tree RSS before a Stage1 rebuild;
- after a source-frozen pcc1 exists, the cache-off GC0 Stage2 must be no slower
  than the non-regressed Stage1, followed by sequential pcc2/pcc3 fixed point;
- GC1--4 execution remains deferred until the user-ordered post-performance
  transfer.  No threshold moves after observation.

### pending

No production source change accompanies this pre-registration.

## Update — topology tracer is exact but fails the performance prefilter

The first implementation established the neutral topology and same-process
adapter without yet changing builder instruction ownership.  It factors the
ordinary parser's final seed construction, builds the same
`IndexedFunctionSeed` from llvm_capi function/block order, and emits an already
indexed module through the ordinary verifier/prepare/AArch64 pipeline.  The
transitional adapter still feeds every instruction's canonical text back into
the parser.  It is therefore a semantic tracer, not the completed No.1 data
plane.

Focused direct/text seed and AArch64 assembly differentials pass.  Host module
7 and representative module 1 also emit byte-identical assembly:

```text
module 7  0f86730ea0dd86ed83a2678eccbd109ec8b7e5bc30d0f3a1a123aeb374431650
module 1  72e2f21af6fbb35e44097646a8d1aba3920234e7468ce24b4f0e022b96427ab0
```

The representative same-process control deliberately changes only the input
to the imported self emitter.  `PCC_TEXT_INDEXED_KERNEL_EMIT=1` runs canonical
text parse+emit; `PCC_DIRECT_INDEXED_KERNEL_CAPTURE=1` plus
`PCC_DIRECT_INDEXED_KERNEL_EMIT=1` runs transitional capture+direct emit.  Both
use the same worker manifest, source, frontend, process, backend imports,
performance lock and output publication:

```text
                         text control       topology direct       ratio
wall                       25.69s               25.29s            1.016x
user+sys                   25.18s               24.97s            1.008x
process-tree peak RSS     810.664MB            849.052MB          1.047x
self-emitter phase         21.450s       8.605+12.461=21.066s     1.018x
```

This fails the pre-registered 1.25x wall/CPU and 1.02x RSS gates.  The
`/usr/bin/time -lp` instruction counters from this pair are not admissible:
`time` wrapped `uv`, which then spawned the measured Python process, so the
hardware counters describe the wrong process boundary.  Wall and process-tree
RSS are independently measured by `run_process_tree_sample.py` and already
deny this transitional shape; no rerun or Stage1 rebuild is justified.

An eager call-record publication sub-experiment is separately denied.  It
reduced capture from 8615ms to 7226ms with zero call fallback, but increased
the full validation worker from 54904ms to 57466ms.  All eager-call source was
forward-removed before the representative pair above.

### Stacked correctness boundary and generic repair

The first frozen pcc1 tracer printed capture success and then observed
`codegen._direct_indexed_module is None`.  LLDB plus the sampled binary's own
disassembly proved the mismatch: `Layer1InitMixin._init_l1_state` initialized
the field at L1CodeGen slot 14, while
`GenerationLoweringMixin._generate_impl` wrote the captured object to its
standalone mixin slot 1.  The emitter entry and compiled-module import were
never reached.  The existing fixed-layout regression then failed directly:

```text
missing = ['_direct_indexed_module']
```

Adding the constructor field to `L1_CODEGEN_HOST_ATTRS` is the generic repair:
contextual codegen mixins now use the real L1CodeGen receiver schema.  The
existing constructor-contract test and direct differential pass.  A separate
217-module schema test exceeded its 130s watchdog without a final pytest
summary; it is explicitly not green evidence, and all children were gone after
the watchdog.

### Current verdict

The topology, direct prepare/emit adapter, exact-output oracle and same-process
control are retained because they are prerequisites of No.1.  The
text-reparsing capture implementation is denied as a performance candidate.
No.1 remains active: publish operands/types/flags/CFG facts from every
supported llvm_capi builder operation while those structured values are live,
drive the retained corpus's hot text-fallback counter to zero, and repeat the
same prefilter before building another pcc1.  No Stage2, Stage3 or GC1--4 gate
ran in this slice.

## Update — structured publication clears the host prefilter and transfers to Stage2

The next implementation publishes structured records directly from the
llvm_capi builder for the supported bootstrap vocabulary.  It preserves the
canonical text oracle, counts hot fallback, scans only reachable call records,
and resets direct state when strict function replacement installs an
unsupported stub.  PHI and switch scaffold helpers now publish through the
same canonical mutators; the single-file direct route uses the same worker
boundary as multi-file compilation.  Focused direct/text differentials and
the 65-test frontend packet passed before the source snapshot was frozen.

The representative host prefilter now clears the pre-registered gate with
byte-identical assembly:

```text
                         text control       structured direct      ratio
wall                       21.11s                16.35s             1.291x
CPU                        21.09s                16.31s             1.293x
instructions                 325.77B               241.18B          0.740x
RSS                           834MB                 762MB            0.914x
assembly sha256            72e2f21a...           72e2f21a...        exact
```

A source-frozen GC0/no-libpython/self Stage1 then completed in 199.40s and its
mandatory function-bearing compile/run canary printed `42`.  Its pcc1 built
Stage2 in 482.181s, or 497.213s including the publication barrier.  Linkage was
libSystem-only.  This is a real 19.4% Stage2 improvement over the prior
598.629s control, but it is not the target: Stage2 remains 2.418x Stage1.

The representative pcc1 direct/text pair is also exact:

```text
                         text control       structured direct      ratio
wall                       70.50s                68.98s             1.022x
instructions                 973.64B               942.66B          0.968x
peak footprint                 9.596GB               6.649GB        0.693x
assembly sha256            8a1dd249...           8a1dd249...        exact
```

The much smaller pcc1 speedup, despite a large memory reduction, triggered a
caller flamegraph.  It attributes 30.8% inclusive samples to the dynamic
`publish_call` bound-method adapter and 9.7% to the corresponding GEP adapter;
the receiver had lost its exact `DirectIndexedFunctionBuilder` projection in
compiled pcc1 code.

### Exact call/GEP wrapper sub-proposal [CONFIRMED locally; whole-stage pending]

Two module-level exact wrappers make the receiver type explicit at the call
boundary.  Contextual generated IR contains four static wrapper calls and zero
dynamic attribute calls at the callers, plus one static method call and zero
dynamic attribute calls inside each wrapper.  The host direct worker remains
exact and takes 16.56s.

The first source-frozen Stage1 observation is mixed: it is semantically green
but takes 223.17s versus 199.40s, while instructions change by only +0.12%
(92.380B versus 92.267B).  That single parallel whole-stage observation does
not prove an accepted Stage1 improvement.  On the same frozen module1 input,
however, the new pcc1 direct worker takes 65.37s versus 68.98s, CPU 65.29s
versus 68.71s, and 914.94B versus 942.66B instructions.  Footprint remains
6.649GB and assembly remains byte-identical at `8a1dd249...`.  This establishes
that exact receiver provenance removes real pcc1 work; it does not justify
claiming the partial two-wrapper batch as a whole-stage win.

No Stage3 or GC1--4 gate ran.  No.1 remains active: move every hot direct
publication operation across the same exact static ABI, keep the Dyn escape
path checked, rerun focused contextual-IR/output gates, and only then rebuild
and measure a complete source-frozen Stage1/Stage2 pair.

## Update — unbound static method ABI removes wrapper adapters and cuts Stage2 9.9%

Expanding the first exact-call experiment into twenty module-level wrapper
functions was semantically correct but the wrong physical ABI.  Its v11
Stage1 took 236.59s and compiled 93.269B instructions.  The representative
pcc1 module retired 898.81B instructions, down 4.65% from v9, but still took
67.17s and each new function also generated its own callable/native adapter.
That structure was forward-removed; it is not the accepted implementation.

The replacement calls each known class method as an unbound function and
passes the builder receiver explicitly:

```python
DirectIndexedFunctionBuilder.publish_call(direct_builder, ...)
```

This is a static cross-module symbol in contextual compiled IR even though the
receiver local itself remains Dyn.  It therefore avoids both dynamic bound
method lookup and the second family of wrapper functions/adapters.  All twenty
publication, mutation, flag and diagnostic operations use this ABI.  A new
real 219-module contextual regression proves both target modules have zero
fallback, every caller contains the static class-method symbol, and no
`dyn.attr.publish_*`/wrapper definition remains.  The focused direct packet is
8/8 and the adjacent Stage1/direct-route packet is 5/5.

Current host direct/text output remains exact and clears the prefilter:

```text
                         text control       unbound direct        ratio
wall                       21.89s                16.59s             1.319x
instructions                 325.53B               241.11B          0.741x
peak footprint                 946.4MB               862.5MB        0.911x
assembly sha256            72e2f21a...           72e2f21a...        exact
```

The source-frozen v12 pcc1 is correct, no-libpython/self/GC0, libSystem-only,
and its function canary prints `42`.  Stage1 is 211.95s and 92.945B
instructions.  That is still 6.3% slower in wall and 0.74% higher in
instructions than v9's 199.40s/92.267B, so Stage1 non-regression is not yet
proven.  The same pcc1 module1 input is 64.65s, 897.82B instructions and
217.96B cycles versus v9's 68.98s, 942.66B and 228.26B, with the same 6.649GB
footprint and byte-identical `8a1dd249...` assembly.

Stage2 completes correctly at 434.450s compile plus a 15.255s publication
barrier, 449.720s total.  Against v9's 482.181s/497.213s this is 9.90% faster
compile and 9.55% faster end-to-end.  Frontend codegen falls from 338.015s to
276.102s and the link driver from 76.714s to 66.238s.  pcc2 is libSystem-only.
Peak process-tree RSS is 23.037GB versus 22.644GB (+1.7%), so memory did not
improve.  Same-source Stage2/Stage1 remains 2.050x and the accepted
non-regressed-v9 denominator gives 2.179x; the terminal target is still open.

No Stage3 or GC1--4 gate ran.  The next measurement owner is the current v12
pcc1 frontend/direct worker: profile it again after the dynamic publication
adapters are gone, then remove the next evidenced object/data-plane owner while
also recovering the remaining Stage1 regression and RSS increase.

## Update — pinned final type IDs cut Stage2 to 364.616s

The v12 caller graph moved the next owner into the direct type plane.  Of 1,659
inclusive `IndexedCallPlane.intern_type` samples, 70.1% came from
`publish_gep`; the whole function was 7.29% of the worker.  Host cProfile then
measured 680,403 `intern_type` calls and 430,401 generated dataclass
`TypeDesc.__eq__` calls on the real module1 worker.

The direct builder now owns pinned final type IDs.  Source IR type identities,
canonical `TypeDesc` identities and GEP-derived pointee identities are stored
beside the referenced object, so allocator address reuse cannot create a stale
identity hit.  Opaque pointers use one integer slot.  Every direct publication
operation writes final type IDs; parser/Dyn paths retain the generic
`intern_type` implementation.

The first layered helper shape added 469,556 nested `_intern_type_desc` calls
and was simplified before bootstrap.  The accepted shape records:

```text
                                  v12 control       v13 candidate
intern_type calls                    680,403             8,372
TypeDesc.__eq__ calls                430,401           192,744
assembly sha256                    72e2f21a...        72e2f21a...
```

Focused direct/contextual tests pass 9/9; Stage1 harness and single/multi direct
routes pass 5/5.  The source-frozen v13 Stage1 is correct, libSystem-only and
prints `42` in the strong canary.  Its 212.18s/92.855B instructions are
effectively flat against v12's 211.95s/92.945B, but still slower than the
non-regressed v9 Stage1 199.40s.

The pcc1 module1 boundary validates the native cost model:

```text
                                  v12 control       v13 candidate       change
wall                                 64.65s             61.61s          1.049x
instructions                          897.82B            857.48B        -4.49%
cycles                                217.96B            207.59B        -4.76%
peak footprint                          6.649GB             6.491GB      -2.38%
assembly sha256                      8a1dd249...        8a1dd249...      exact
```

Stage2 completes at 364.616s compile plus a 16.290s publication barrier,
380.931s total.  This is 16.1%/15.3% faster than v12 and 24.4%/23.4% faster
than v9.  Frontend codegen falls 276.102s -> 238.585s and safe-worker wall
235.211s -> 198.382s.  Peak process-tree RSS falls 23.037GB -> 22.539GB and is
also slightly below v9's 22.644GB.  pcc2 is libSystem-only.

Same-source Stage2/Stage1 is now 1.718x compile or 1.795x including the barrier;
against the non-regressed 199.40s denominator it is 1.829x.  The remaining
gap is no longer the link driver (65.584s Stage2 versus 66.339s Stage1): it is
the pcc1 frontend safe workers (198.382s versus 53.718s).  No Stage3 or GC1--4
gate ran.  Profile the v13 worker before selecting the next data-plane owner.

## Update — five-module native worker reuse wins CPU but fails memory; stream before recycling

Stage1 groups the closure into forty source-worker chunks while the native
pcc1 policy forces all 219 modules into singleton processes.  The v13 direct
worker now releases frontend-only state after each module, so the retained
singleton rule was tested rather than assumed.

One process handling five real safe modules (8KB, 11KB, 17KB, 24KB and 57KB)
was compared with five sequential fresh pcc1 processes.  All five result rows,
LLVM files and assemblies were byte-identical and both arms had empty stderr:

```text
                         five fresh       one five-module worker      ratio
wall                       26.76s                 22.94s               1.166x
user + system              26.06s                 22.61s               1.153x
peak process-tree RSS       1.326GB                2.687GB              2.03x
```

The five-module shape is `[DENIED]` against its pre-registered <=1.5x RSS
line.  The mechanism is source-visible: `run_codegen_worker` decodes every AST
assigned to the chunk before entering the module loop, so `_release_direct_frontend_state`
cannot reclaim the other four AST graphs.

### Proposal — streamed AST decode plus explicit bounded native recycling [pending]

Read one AST wire at the top of each codegen-loop iteration and release that
reference before the next module.  Preserve the existing all-at-once fallback
only when no AST sidecar exists.  Add an explicit
`PCC_PY_FRONTEND_NATIVE_MODULES_PER_WORKER` policy knob; default one preserves
the current production boundary.  A positive value computes a bounded number
of deterministic LPT chunks without changing the concurrency ceiling, module
order inside a chunk, result publication order, failure ownership or atomic
cache publication.

Fail-first requirements: tests prove no more than one AST wire is live during
the codegen loop, default native chunking remains singleton, invalid/zero
values fail closed to one, and explicit five produces the exact expected
chunk count.  A source-frozen pcc1 must then rerun the same five-module probe:
exact outputs, wall/CPU >=1.10x versus five fresh v13 processes, peak RSS
<=1.5x the 1.326GB control and <=2GB.  A miss removes streaming/recycling
before Stage2.  A pass permits one same-source Stage2 with explicit value five;
require exact/runnable libSystem-only pcc2, compile wall >=1.05x versus
364.616s and peak RSS <=22.644GB.  Default policy does not change before that
complete-stage result.  Stage3 and GC1--4 remain deferred.

### Result — streamed native recycling `[DENIED]`

The ownership/policy packet passed 104/104 and both changed modules passed
strict self/no-libpython closure.  Source-frozen v14 differed only in the two
declared worker files, built a libSystem-only pcc1 and passed the function
canary.  Its one-shot Stage1 was 225.48s; instructions were only 0.07% above
v13, so the slower wall is not used as source attribution.

The same v14 pcc1 then ran both five-module arms.  All five result rows, LLVM
files and assemblies were exact with empty stderr:

```text
                         five fresh       streamed batch       ratio
wall                       24.64s              21.22s           1.161x
user + system              24.57s              21.21s           1.158x
peak process-tree RSS       1.318GB             2.664GB          2.02x
```

Streaming changed neither the batch peak nor its ratio materially (the v13
pre-stream batch was 2.687GB).  The retained high water is therefore the
allocator and other compiler state accumulated across modules, not simultaneous
AST ownership.  The candidate misses both <=1.5x and <=2GB lines and is denied;
no Stage2 ran.  The two production files and candidate-only tests were
forward-restored byte-for-byte to v13, and the restored packet passes 103/103.
Do not retry with a smaller batch or a moved memory threshold: process exit is
still the only proven reclaim boundary.

## Update — fixed-shape/lazy-metadata InstructionRecord proposal [pending]

The v13 module1 direct path still constructs 329,129 `InstructionRecord`
instances.  Each internal record currently has an ordinary instance dict and
eagerly allocates a second `_metadata = {}` even though direct workers never
attach debug metadata.  No existing investigation records a slots/lazy-metadata
attempt, and repository search finds no dynamic record attributes.

One representation proposal: give `InstructionRecord` exact slots for its five
declared fields and store `_metadata = None` until the first `set_metadata`.
Text/debug mode allocates the same dict on demand and preserves replacement/
suffix diagnostics; direct mode retains the same record/order/mutation API but
allocates zero per-record metadata dicts.  This is not the 61.7%-coverage bulk
finalize fast path, which sizing denied, and it does not change unsupported
fallback behavior.

Pre-registered gates: focused metadata replacement and C debug parity; direct
kernel/contextual packet; direct-only record inventory proves every metadata
field remains null; host module1 text/direct assembly exact.  Then one
source-frozen pcc1 differing only in `ir.py` must emit exact module1 assembly
and improve wall and CPU >=1.05x versus v13, improve instructions and keep
footprint <=0.95x.  A miss forward-removes slots/lazy metadata before any
Stage2.  A pass permits one Stage2; Stage3 and GC1--4 remain deferred.

### Result — fixed-shape/lazy metadata `[DENIED]`

Focused direct/contextual/debug gates passed 11/11 and the adjacent Stage1/
direct-route packet passed 5/5.  The frozen v13 host control versus candidate
showed the intended representation effect with exact assembly: instructions
247.38B -> 238.94B (-3.41%) and footprint 885.98MB -> 828.65MB (-6.47%).
Candidate wall was contaminated by 19,080 involuntary context switches and was
not used.

Source-frozen v15 differed only in `ir.py`, built a libSystem-only pcc1 and
passed the strong canary.  Its representative native result rejects the
transfer:

```text
                         v13 control        v15 candidate      candidate/control
wall                        61.61s              62.62s             1.0164
CPU                         61.54s              62.57s             1.0167
instructions               857.48B             860.36B            1.0034
cycles                     207.59B             207.78B            1.0009
peak footprint               6.491GB              6.312GB          0.9724
assembly                    8a1dd249...         8a1dd249...         exact
```

The candidate misses wall/CPU/instruction gates and does not reach the <=0.95x
footprint line.  Under pcc the ordinary internal class already has a fixed
physical layout; the Optional metadata branch costs more than the eager empty
dict it avoids.  No Stage2 ran.  The source and candidate-only test were
forward-removed, `ir.py` byte-matches v13, and the restored packet passes
10/10.  Do not infer pcc1 performance from the positive CPython record result.

## Update — direct/no-text tagged instruction-order plane [pending]

The complete-lifetime v13 worker profile moves the next qualifying owner to
the boundary shared by L1 generation and direct-kernel finalization.  The
worker constructs about 329,129 `InstructionRecord` objects even though its
canonical LLVM text is disabled.  `L1CodeGenEntrypointMixin.generate` is
31.79% inclusive; `build_direct_indexed_function` is 16.18%, and the latter
rescans the record objects only to recover final block order, dense record ID
and opcode.  This is one object projection spanning a >=25% current-profile
owner, not a local runtime-helper leaf.

The No.100 slots/lazy-metadata experiment is authoritative negative evidence
for making the object cheaper: pcc1 CPU regressed and footprint improved only
2.8%.  The new proposal does not retry that layout.  In direct/no-text mode,
each block instead owns a tagged-small-int order stream encoding the already
published `record_id` and stable opcode ID.  The ordinary `_instrs` and
`_text_lines` projections remain empty.  Finalization consumes those integers
directly; insertion/positioning and phi/switch/arithmetic-flag mutation use
the same IDs.  Canonical text mode, explicit diagnostic APIs and unsupported
fallback retain `InstructionRecord`, with lazy diagnostic projection from the
direct builder when requested.

Fail-first gates must cover append and middle insertion, position-before,
alloca-prefix placement, terminator placement, phi incoming, switch cases,
arithmetic flags, explicit diagnostic text and mixed fallback.  A direct
module regression must prove zero hot `InstructionRecord` and `_text_lines`
entries while preserving exact assembly and zero fallback.  Then run the
existing direct/contextual packets and strict no-libpython closure.  The
source-frozen pcc1 module1 transfer must keep exact assembly, improve wall and
CPU by at least 1.10x, reduce instructions, and keep footprint at or below
0.90x v13's 6.491GB.  A miss forward-removes the candidate before Stage2; a
pass permits one Stage1/Stage2 measurement.  Stage3 and GC1--4 remain
deferred.

### Result — recordless direct order `[DENIED]`

The implementation deleted the hot `InstructionRecord` and empty text entries
completely, using one tagged integer per final block-order row.  Focused host,
contextual closure and output gates passed.  Two pcc1-only correctness failures
were independently localized with LLDB: `isinstance(Dyn, int)` false-negative
branches in the binder and phi/switch mutation path tried to read or write
`_direct_record_id` on the tagged integer.  Both were replaced by the exact
direct/no-text representation flag; the third source-frozen Stage1 passed the
`42` canary and linked only libSystem at 206.55s/92.695B instructions.

The clean host module1 arm was positive (17.27s -> 15.77s, instructions
247.643B -> 239.672B, footprint 886.97MB -> 806.63MB) with exact assembly.
The required pcc1 arm reversed the CPU verdict: 61.61s/61.54 CPU/857.478B/
207.595B/6.491GB became 66.65s/64.41 CPU/860.415B/212.137B/6.431GB.  It misses
the 1.10x wall/CPU and 0.90x footprint lines and regresses instructions.  No
Stage2 ran.  Production and candidate-only test source was forward-restored
byte-for-byte to v13.  Another record shell or Python list-of-tagged-IDs order
plane is denied; a future design must publish final order directly into one
native arena without a per-record Python interface.  Full evidence:
[`030-recordless-direct-order-plane-denied.md`](../goal/evidence/PERF-P0-PROVEN-NATIVE-DATA-PLANE-PARALLEL-EMIT/030-recordless-direct-order-plane-denied.md).

## Update — end-to-end dense opcode-ID consumption [pending]

The complete v13 host worker was instrumented without production changes by
replacing each imported `PARSED_INSTRUCTION_KINDS` tuple with a label-specific
read proxy.  Exact module1 assembly remained `72e2f21a...`.  Its 329,129
records performed 3,653,049 ID-to-string projections, 11.1 per record:

```text
direct finalize       835,505
kernel                689,114
AArch64 regalloc      662,081
verify                544,265
precise stackmap      377,819
AArch64 emit          291,240
stackprep             253,025
```

The record distribution is call 169,891, cast 58,628, br/br_cond 42,944,
store 14,140, GEP 13,280, icmp 11,606, alloca 6,578, load 6,664 and smaller
families.  Each projected string then enters one or more string-equality
dispatch chains; calls in the AArch64 emitter traverse both memory and compute
chains.  This repeated projection spans direct construction, verifier,
stackprep, stackmap, register allocation and emit, so its current-profile
owner is the complete 55%+ indexed backend lifecycle rather than one local
leaf.

The proposal exports one stable integer constant per opcode and keeps the
existing metadata ID through every supported direct/AArch64 consumer.  Parser,
x86/legacy and explicit diagnostic paths retain string projection.  No new
cache, per-record object, token list or provenance bypass is permitted.  A
source-shape/record-inventory ratchet must prove the hot direct path performs
zero ordinary opcode string projection; malformed IDs continue to fail closed
with a projected diagnostic only on error.

Pre-registered transfer gates: focused direct/text kernel, verifier, slot,
stackmap, regalloc and exact AArch64 assembly differentials; all changed files
strict/no-libpython closure; then a source-frozen Stage1 no slower than v13's
212.18s.  The pcc1 module1 candidate must keep exact `8a1dd249...` assembly,
improve wall and CPU by at least 1.10x, retire at most 0.95x instructions and
not regress footprint.  A miss forward-removes the numeric consumer changes
before Stage2.  A pass permits one same-source Stage2, requiring exact/runnable
libSystem-only pcc2, at least 1.10x compile-wall improvement versus 364.616s
and footprint no worse than 22.644GB.  Stage3 and GC1--4 remain deferred.

### Interim result — dense opcode-ID retained for causal follow-up

The implementation reduced the measured hot projection count from 3,653,049
to four and passed the focused direct/kernel/inventory (29), verifier/regalloc/
stackmap/arena (128), complete AArch64 (301), direct/x86/bootstrap (15), and
strict closure gates with exact assembly.  The first frozen pcc1 module1 arm
retired 3.33% fewer instructions but was 1.7% CPU-negative.  A subsequent
complete Stage2 succeeded but measured 496.332s compile / 514.959s total,
versus v13's 364.616s / 380.931s; its codegen worker phase was 297.683s versus
198.382s.  This single non-adjacent Stage2 run is retained as negative evidence,
not discarded.

At the human's explicit instruction not to abandon the structural migration,
the exact frozen candidate was reapplied and an adjacent same-machine worker
pair was run.  It reversed the earlier local result with exact output:

```text
                         v13 control       dense-ID candidate
wall                        63.61s               61.78s
user + system               63.46s               61.66s
instructions               856.238B             828.776B
cycles                     213.281B             206.510B
peak footprint               6.491GB               6.379GB
```

Thus the representation change itself currently shows a 2.9% worker CPU/wall
win and 3.2% instruction/cycle reduction; the 496s Stage2 is not sufficient to
attribute a 36% source regression without an adjacent control.  The candidate
remains active while its next direct boundary is optimized, and must eventually
receive a controlled full-stage pair.  This supersedes the earlier plan to
forward-remove it; it does not claim Stage2 improvement yet.

## Update — fixed-arity exact calls write the final call plane directly [pending]

A nearly complete native worker flame graph puts
`_irbuilder_call_from_args_list` at 17.13% inclusive.  `IRBuilder_call1` and
`IRBuilder_call2` account for 8.40% and 6.48%.  A host-side counter on the exact
module1 AST recorded 169,890 direct/no-text exact-Function calls; arities zero,
one and two account for 158,356 (93.2%).  All observed calls at this boundary
were direct/no-text exact Functions.

The current fixed-arity wrappers nevertheless allocate an argument list,
`_irbuilder_call_from_args_list` allocates a second expected-type list, and
`DirectIndexedFunctionBuilder.publish_call` iterates both before writing the
already-final `IndexedCallPlane`.  This is not the denied signature-cache
shape: it adds no dict, attribute cache or memo entry.  The proposed exact
fast path for `IRBuilder_call0/1/2` accepts scalar operands and writes the final
call/argument records without either list.  Text mode, dynamic arity, function
pointers, subclasses/duck functions and diagnostics retain the generic path.

Fail-first gates must prove fixed and vararg type selection, void/value returns,
call flags, use order, exact packed records and byte-identical assembly; a
source-shape test must prove the specialized direct path does not enter the
generic helper.  Host module1 must remain exact and reduce list construction.
The same frozen-pcc1 module1 command then needs exact assembly, improving CPU,
instructions and footprint versus the retained dense-ID binary.  A regression
removes only the fixed-arity adapter, not the dense-ID plane.  Stage2/Stage3 and
GC1--4 remain deferred until the adjacent worker gate passes.

### Final result — retained structure, whole-Stage2 performance `[DENIED]`

The scalar adapter covers 158,356 of 169,890 module1 calls (93.2%).  Its
focused packet passes 96 nodes, strict closure passes, and output is exact.
Against the retained dense-ID pcc1 it reduces representative-worker
instructions 828.856B -> 818.857B and cycles 206.811B -> 205.961B, but wall
and CPU are both unchanged at 62.2s/61.9s.  It remains in the live source at
the human's explicit no-revert direction as a structural scalar path, not a
performance claim.

The final frozen v19 Stage2 succeeds and produces a runnable, libSystem-only
pcc2 whose function canary prints `42`.  It takes 395.960s compile / 410.872s
total with 22.885GB peak process-tree RSS, versus accepted v13's 364.616s /
380.931s and 22.539GB.  User+system CPU also rises 2254.044s -> 2312.621s.
Thus the complete dense-ID plus fixed-call line is `[DENIED]` as a whole-Stage2
speedup even though its adjacent module1 dense-ID arm is locally positive.

The current source is intentionally retained, but accepted performance
timings remain v13.  No Stage3 or GC1--4 ran.  Full receipt:
[`032-dense-opcode-final-validation.md`](../goal/evidence/PERF-P0-PROVEN-NATIVE-DATA-PLANE-PARALLEL-EMIT/032-dense-opcode-final-validation.md).

## Update — v15 full-cost emit window selects a native final-order plane `[DENIED]`

The memory-safe Stage2 work restored host Stage1 to 149.15 seconds but its
full-cost v8 worker receipts predict Stage2 at about 1022 seconds.  The next
slice is therefore measured on one exact v15 `class_gen` worker with both
direct capture and direct emit enabled, not on the earlier frontend-only
canary.  The first sample started too early and covered frontend emission; it
is retained but excluded.  A corrected 13-second-delayed, 16-second sample is
bound to v15 pcc1 `fab12aa0...`, returns zero, peaks at 4,318,380,032 bytes and
contains 13,215 on-CPU samples:

```text
build_direct_indexed_function       31.36% inclusive
  _finalize_structured_seed          14.09%
  get_indexed_function_kernel         2.26%
precise stack-map planning           17.89%
verify_parsed_function               10.91%
typed initializer emission            6.43%
```

Artifacts: `build/classgen-full-cost-profile-v17/{process.result.json,
emit.folded,emit.svg}`.

The old recordless-order candidate remains denied: its Python `list[int]`
projection made pcc1 CPU and instructions worse.  Its recorded reopen
condition was narrower and has not been tried: publish final instruction
order directly into one native arena with no per-record Python interface.

Proposal: in direct/capture mode, every `Block` owns a linked order over one
builder-owned `CompilerIntArena`.  Append and positioned insert update that
arena when the instruction receives its final record ID.  The supported
zero-fallback finalizer traverses native record IDs and never reads
`Block._instrs` or `InstructionRecord._direct_record_id`; text/diagnostic and
unsupported fallback retain the existing object list unchanged.  PHI/switch
payload mutation remains in the existing final scalar planes.  This removes
the finalizer's repeated object projection without changing builder order,
reachability, diagnostics or downstream kernel storage.

Fail-first gates: append, middle insertion, position-before, alloca-prefix,
PHI/switch mutation and terminator order; a poison-record-list test proving
supported finalization does not read the object projection; exact direct/text
kernel and AArch64 output; strict no-libpython closure.  Then one v15
full-cost `class_gen` control/candidate pair must preserve the assembly hash,
improve wall and CPU by at least 1.08x, reduce instructions, and keep peak RSS
at or below 1.02x.  A miss removes the native order plane before any Stage1
or Stage2 build.  This work executes inside the memory-safety task's explicit
emit-cost prerequisite; it does not claim the dependency-blocked parallel-
emit row complete.

The implementation met its structural claim: one native linked order plane
tracked append and positioned insertion, and supported finalization still
produced exact diagnostic instruction order and AArch64 assembly after
`Block._instrs` was replaced by a poison object.  Focused direct/value/
inventory gates passed 25 nodes and the 224-module contextual ABI/fallback
gate passed.  The host full-worker prefilter was positive and exact
(11.17 -> 10.50 seconds, instructions -4.0%, footprint -4.6%).

The required pcc1 transfer reversed that verdict.  Source-frozen v16 Stage1
was healthy at 146.71 seconds / 4.95 GB tree peak, self/no-libpython and
libSystem-only.  One adjacent full-cost class_gen pair against v15 used the
same runtime archive (`aa1f3102...`), manifest and flags and emitted identical
36,050,115-byte assembly (`2c66c5b7...`):

```text
                         v15 control       native-order candidate     C/B
wall                       40.17 s               40.37 s             1.0050
user + system              39.95 s               40.26 s             1.0078
instructions              507.122 B             516.023 B            1.0176
cycles                    134.692 B             135.849 B            1.0086
tree peak                   4.286 GB              4.313 GB            1.0061
footprint                   4.279 GB              4.280 GB            1.0002
```

The per-instruction linked-arena maintenance costs at least as much as the
final object scan it removes.  It misses the 1.08 line and regresses every
compute counter, so the production plane and its candidate-only test were
forward-removed; the v16 binary/receipts remain negative evidence.  Do not
retry a parallel order mirror, whether Python-list or native-linked.  A future
direct-construction proposal must write the one final plane rather than
maintaining a second plane beside `InstructionRecord`.

### Successor proposal — canonical native order, not a mirror `[DENIED]`

The complete denied recordless source remains at
`build/no101-recordless-stage1-candidate-v16-r3/source-snapshot`.  Differential
review against its v13 control shows it already solved the semantic migration:
direct/no-text positioning, alloca prefixing, terminators, PHI/switch mutation,
flags and diagnostic fallback all consume one canonical `_direct_instrs`
order.  Its physical mistake is isolated: `_direct_instrs` is a Python
`list[int]`, so every append/insert/index/set still pays generic list/object
protocol under pcc1.

Replace that one canonical list with `CompilerIntArena`.  Add an arena
`insert(index, value)` whose compiled implementation grows once and uses the
owned raw `memmove` intrinsic; the host oracle uses ordinary list insertion.
All direct order reads/writes use `get_unchecked`/`set_unchecked`; no
`InstructionRecord` is created for a supported direct instruction and no
parallel mirror exists.  The retained recordless source supplies the complete
API/call-site migration rather than re-deriving it.  Current dense opcode-ID
consumption remains authoritative and must be preserved while adapting the
older snapshot.

Fail-first: arena begin/middle/end insertion and growth parity; recordless
append/position-before/alloca/terminator/PHI/switch/flag/diagnostic gates;
zero supported direct `InstructionRecord`/text projection; exact host
class_gen assembly and strict closure.  The host full-worker must improve at
least 1.08x before one pcc1 build.  The pcc1 full-cost class_gen pair must
improve wall and CPU at least 1.10x, reduce instructions, keep RSS <=1.02x and
emit exact assembly.  Any miss removes the canonical arena plane before
Stage1/Stage2; thresholds do not move.

The retained recordless API was adapted to one canonical `CompilerIntArena`;
native insertion used owned `memmove`, and supported direct instructions no
longer entered `_instrs`/`_text_lines`.  Arena host/native lowering and the
initial direct packet passed.  Two alternating host full-worker pairs emitted
the same 37,684,184-byte assembly (`a319830d...`) and showed real, stable
deletion, but missed the pre-registered wall line:

```text
pair                    1          2       paired median
wall speedup          1.0782x    1.0789x      1.0786x   (< 1.08)
CPU speedup           1.0810x    1.0803x      1.0807x
candidate instructions/control     about 0.951x
candidate footprint/control        about 0.895x
```

The threshold is not rounded or moved after observation.  The candidate is
`[DENIED]` at its host prefilter, before a pcc1 build; this avoids repeating
the historical host-positive/pcc1-negative transfer.  The canonical arena
implementation, call-site migration, insert primitive and candidate tests
were forward-removed.  No Stage1, Stage2 or GC run occurred.  Both parallel-
order and canonical-order variants are now exhausted; a future direct-plane
proposal must remove a larger construction/analysis boundary, not change
final instruction-order storage again.
