# V104 native-plane growth guard and coordinator regression

## Tasks

- `PERF-P0-NATIVE-DATA-PLANE-OBJECT-PROJECTION-CLOSURE`
- `PERF-P1-STAGE2-COORDINATOR-IR-STREAMING`

## Frozen identities

- source snapshot: `build/native-data-plane-stage1-candidate-v104-batch-liveness/source-snapshot`
- bootstrap source SHA-256: `f7e86aff2b11b53a4ecc9e709f3de24319ab35ace7ac38d456779d0bb75c6909`
- pcc1 SHA-256: `8c6abbd28bd13d9789996169886f1f5547e1d203425a0caf18fc7c7f3df7a559`
- runtime archive SHA-256: `2cec034cd21f0d976e49803d8c65d32ef33a1af85f91c54febc330ac8e9ff2df`

## Growth guard

`scripts/pcc_record_inventory.py` now owns one fail-closed classification for
every top-level class under `pcc/backend/self_backend*.py`.  AST discovery
rejects missing, stale, and invalid classifications.  Every concrete class is
included in the stage graph regardless of category, so labeling a hot record
as target/control cannot hide a reachable object.  Direct construction of the
13 diagnostic record families is separately registered by enclosing function,
count and parse/diagnostic/legacy/oracle policy.

Focused proof:

```text
tests/python/test_pcc_record_inventory_tool.py     13 passed in 2.62s
changed backend/stackmap/inventory packet          77 passed in 3.31s
```

The negative regression creates a new `FutureHotRecord` plus a direct
`ParsedInstr` call and proves both appear as unclassified.  Current item311
inventory (`build/native-data-plane-v104-current-inventory-v2.json`, SHA-256
`733cd913c35f431be2e5059cbad457d43d4741e906f5fb30e190cefbf4a2f4ff`)
records:

```text
classes discovered/classified       44 / 44
diagnostic constructor sites        35 / 35
unclassified/stale/invalid          0 / 0 / 0
graph stages                        parsed, verified, stack_prepared,
                                    stackmap_planned, emitted
instructions / stack maps           59,984 / 20,004
normal diagnostic/type/call
projection counters                 all zero at all five stages
```

## Current GC0 Stage2 result

The exact V79 cache-off resource shape was replayed under
`scripts/run_process_tree_sample.py`: GC0, self/no-libpython, 10 frontend jobs,
8 self-backend jobs, 8 Mach-O link jobs, object and frontend IR caches off.
The 800-second watchdog was selected from V79's 683.796-second complete run.

```text
status                              TIMEOUT
elapsed                             800.467 s
return code                         -15 / wrapper 124
pcc2 / stage result                 absent
peak process-tree RSS               23,018,766,336 B
peak process count                  27
root coordinator max RSS            11,845,746,688 B
leftover children after cleanup     none
```

Artifacts:

- `build/native-data-plane-v104-gc0-stage2-resource-v1/process-tree-result.json`
- `build/native-data-plane-v104-gc0-stage2-resource-v1/process-tree-samples.tsv`
- `build/native-data-plane-v104-gc0-stage2-resource-v1/stage2.stdout`
- `build/native-data-plane-v104-gc0-stage2-resource-v1/stage2.stderr`

This is failed evidence, not a green Stage2 or a reason to widen the timeout.
V79's corresponding root/process-tree peaks were 6,089,441,280 and
10,856,660,992 bytes.

## Worker/control discrimination

The seven exact V104 oversized inputs preserved by the failed run all complete
serially; maximum isolated worker footprint is 2.751GB.  The largest exact
shard (`method_call_expression_lowering`, 8,715,309 bytes) produces identical
assembly under V79 and V104:

```text
                         V79                   V104
instructions             601.398B              483.246B
CPU                      44.61 s               41.54 s
peak footprint            4.395 GB              2.751 GB
assembly SHA-256          44f8e90c...           44f8e90c...
```

The normal worker native data plane therefore improved.  The failed Stage2
peak belongs to the coordinator retaining frontend/export/IR state while
workers execute.  Scheduler/pipeline sources are byte-identical between V79
and V104; final emit text grew about 12.6%, while coordinator RSS grew about
94.5%, so ordinary source growth exposed a non-linear ownership seam.

## Supported claim

New self-backend record families and direct diagnostic-constructor sites now
fail closed mechanically, and the current supported AArch64 worker has zero
normal record projection with materially improved representative and exact
oversized-worker resource counters.

## Not proven / open boundary

V104 has no complete current-source Stage2 and no GC0 fixed point.  The next
finite slice is canonical file-backed frontend IR handoff: retain exact `.ll`
text and existing semantic checks while passing ordered worker paths to the
self-backend before the frontend temp lifetime ends.  A structured IR sidecar
is explicitly denied by prior analysis and is not proposed.  Acceptance needs
a complete sampled pcc2, byte-identical output, coordinator peak at most 8GB,
and stage wall within 5% at equal cache state.

## V106 canonical file-backed handoff implementation

The selected seam is implemented without a structured sidecar.  Parallel
frontend workers may write canonical `.ll` directly into a caller-owned
handoff directory and return ordered paths plus the existing metadata.  The
strict self/no-libpython consumer stream-scans each file for fallback calls,
keeps the current ordering/fallback/native-extension gates, and passes paths
to the unchanged split/cache/admission/emit/link machinery.  The handoff temp
lifetime encloses the complete link and is removed afterwards.  The adapter is
narrowly disabled for frontend/action cache use, IR passes, target rewrites,
emit-llvm, LLVM, debug IR dumping, semantic layout, host/source workers and
unsupported modes.

Focused current-source gates:

```text
frontend path ownership, link, split, cache and sampler     38 passed in 3.54s
compiled current repo main -> toy binary -> output 123       1 passed in 173.59s
```

The sampler now also ignores repeated SIGINT during process-group cleanup,
writes `INTERRUPTED`, and has a double-SIGINT child-reaping regression.  This
repairs the cleanup defect exposed by the discarded multi-module emit-llvm
probe.

Source-frozen V106 Stage1:

```text
pcc1 SHA-256                 b746d84e44633f2a095e1c998c3de927c6347e411beadd96bb004bba80f8e117
wall                         279.24 s
instructions                 313.570B
CPU                          1081.15 s
peak footprint               1,659,963,168 B
linkage                       libSystem only
smoke / return code           green / 0
```

The exact item311 worker remains output- and performance-identical to V104:
23.83s, 292.688B instructions, 1.725GB footprint and assembly `ff943e10...`.

The two-module package canary proves real compiled-stage activation.  V106
pcc1's profile reports `multi_ir_file_handoff=1`, `multi_ir_modules=2`, and
`link_self_backend_ir_paths`; the resulting executable prints `42`.  The same
V106 pcc1 with `PCC_SELF_BACKEND_SKIP_LL_TEMP=0` reports the legacy
`emit_ll_many` + `link_self_read_ll` phases and no handoff counter.  Both
executables are byte-identical at SHA-256
`5ccd49232964830b37349e856e342f1b5a643c23d2c58431cfd101cfa49cee24`.

The sampled V106 Stage2 was deliberately not launched at the first preflight:
swap had 34,323/35,840MB used after consecutive source builds.  The repository
has a measured failure at lower swap pressure, so starting immediately would
confound the result.  `memory_pressure -Q` subsequently reported 51% available;
the launch still requires a fresh no-process/no-lock pressure read and retains
the existing 800-second watchdog rather than widening it.

## Final whole-boundary verdict: path handoff denied and removed

The candidate line subsequently consumed five complete Stage1 builds:
V105 318.73s, V106 279.24s, V107 243.37s, V108 245.78s and V109 386.47s
(1473.59s / 24.56 minutes total).  These are different implementation
revisions, not repeat samples suitable for an A/B average.

Across the current coordinator series there were five Stage2 launches: the
V104 baseline and V106 ran to the 800-second watchdog; V107, V108 and V109 were
stopped early after their registered denial condition.  No launch produced
pcc2.  The candidate-specific receipts are:

```text
V106  TIMEOUT 800.411s       tree 7,864,680,448B    largest 3,404,529,664B
V107  INTERRUPTED 179.870s   tree 22,703,980,544B   largest 14,861,287,424B
V108  INTERRUPTED 169.496s   tree 16,143,990,784B   largest 14,846,869,504B
V109  partial RUNNING JSON, no samples/final receipt; not evidence
```

V106's low peak was coupled to an accidental roughly three-minute coordinator
fallback scan.  Removing that duplicate scan in V107 exposed the retained
AST/export live set; adding explicit collection in V108 did not close it.
V109's repeated lazy AST decode regressed Stage1 from V108's 314.573B/245.78s
to 379.796B/386.47s.  It is denied independently of its unusable partial
Stage2 receipt.

All canonical-path, worker-fallback, collection and lazy-AST production edits
were forward-removed.  The retained text path plus eager semantic fixed point
passes 8 focused ownership/effect/sampler tests.  The sampler's early
double-SIGINT race is fixed by installing the flag handler before child launch.
No additional Stage1, Stage2, Stage3 or five-GC gate was run after removal.

The supported claim returns to V104: the normal AArch64 worker plane and its
growth guard are retained; current-source Stage2/fixed-point remains open.  A
future coordinator slice must replace the all-AST global semantic join with a
compact worker-published dense summary and prove equivalence cheaply before a
new Stage1 build.  Merely passing `.ll` file paths is now a measured denial.

## Dense semantic-summary follow-up: denied and removed

The follow-up implemented the missing dense summary rather than another text
adapter: one AST decode per module, dense callable IDs, arena edge pairs,
reverse CSR and one worklist fixed point.  On the retained 216-module Stage2
corpus it loaded all 216 wires once, built 4,765 nodes / 7,605 edges and matched
the complete eager export dict.  Host decode sizing was 513.3MB for eager-all
versus 161.7MB one-at-a-time.  Focused semantic/ownership/growth gates passed,
and a strict three-module binary proved raw-arena execution with zero
`py_cpy_*` calls/strict stubs.

The only source-frozen Stage1 succeeded:

```text
wall / instructions       298.13s / 281,911,455,005
CPU / footprint           1171.26s / 1,205,487,104B
pcc1 SHA-256               04c91c6d9d8196832c35ebd64fd70605d88dbe1731a02ce63cb549ea52994b68
linkage                    libSystem only
```

The produced pcc1 activated the summary coordinator on a two-module package,
printed `42`, and reported load/node/edge counters 2/2/1.  Its one sampled
Stage2 was stopped at the pre-registered memory boundary:

```text
status / elapsed           INTERRUPTED / 124.069s
root pcc1 max              14,666,301,440B at 81.757s
tree max                   25,454,723,072B
pcc2                       absent
```

Root RSS rose before worker overlap (1.87GB at 10s -> 14.57GB at 81s).  The
largest retained AST wire is the second module, `pcc.cli_bootstrap`, at
15,048,245 bytes.  Thus all-AST liveness was not the controlling peak: a single
large module's generic object projection and/or allocator high-water retention
can recreate ~14.7GB by itself.  The dense-summary production candidate was
forward-removed; retained sources pass 42 focused gates.  No second Stage1 or
Stage2 is authorized.  Next evidence must isolate decode, analysis and
post-release allocator footprint for that one module using the frozen failed
pcc1 artifact.

## Single-module control and allocator counters

The first inference from the monotonic root timeline was too strong.  An exact
single-module pcc1 codegen worker consumed the retained current
`pcc.cli_bootstrap` AST (14,488,049B, SHA-256 `7262ef5d...`) and full exports
wire (12,215,569B, `b82e99b4...`), completed rc0 in 15.790s, emitted
19,279,504B IR and peaked at 2,337,619,968B.  One module does not by itself
recreate 14.7GB.

Ten distinct modules in one worker peaked at 4,781,047,808B, but that worker
explicitly retains every assigned AST in `parsed_modules`; it is not an
allocator-only control.  Assigning the same module index four times is the
bounded control: every decode overwrites one list slot, yet the unattached run
completed four byte-count-identical outputs in 75.971s and peaked at
8,106,917,888B, rising monotonically across rounds.

Two ordered direct reads of the allocator's exported counters showed mapped
capacity 4.230GB -> 5.894GB while live usable was 2.870GB -> 3.959GB; retained
mapped-minus-live capacity grew from 1.360GB to 1.935GB and allocator metadata
from 77MB to 149MB.  Live requested also rose 2.278GB -> 3.100GB, so retained
slabs/metadata are confirmed contributors but not the entire live-set story.
The LLDB-attached sampler receipt is not performance evidence because debugger
attachment disturbs child wait/ownership; only the direct counter values are
used.  The unattached x4 receipt owns the 8.107GB peak claim.

Source confirms why no cheap phase `trim` exists: small raw/object frees only
return cells to global class freelists, every 64KiB slab stays mapped, and
granule tables/span/radix metadata are immutable or intentionally retained.
There is no per-slab live count or safe unlink/recommit protocol.  The next
compiler design is therefore short-lived, bounded per-module summary workers
plus a small parent dense fixed point; process exit reclaims worker allocator
high water.  Allocator decommit is a separate runtime design, not an inline
compiler patch.

## Short-lived per-module summary workers: focused implementation

The next candidate is implemented but not yet built by pcc1.  The parent
serializes a 1,402,392-byte vthread-only export surface, starts one-AST summary
workers with concurrency capped at two, and merges 216 compact wires totalling
1,358,138 bytes into a 4,600-node / 7,605-edge integer fixed point.  The parent
does not decode AST wires.

The retained 216-module corpus matches eager exports completely.  Targeted
function/method/two-level-reexport cases are green.  A new differential first
failed because duplicate `def` rebinding incorrectly unioned the earlier
caller's edges; summary construction now resets prior caller state so the last
definition wins exactly as eager/Python does.

Gates: focused ownership/effect/inventory/sampler packet 30 passed; existing
multi-file package reexport, reexported-class alias and typing-metadata tests 3
passed.  No new Stage1 or Stage2 has run for this source.  Acceptance remains
conditional on one frozen Stage1, a real pcc1 summary-worker RSS/activation
gate and only then one cache-off Stage2.

## Summary-worker compiled-stage verdict: denied and removed

The one frozen Stage1 succeeded at 303.07s / 190,738,794,177 instructions /
1,289,455,104B footprint.  Its pcc1 SHA is
`eb5b97e10656339a2aa5299a88c3355323b4f3b078bde69de9532652c33cef54`;
linkage is libSystem only and smoke passed.  This improves instructions 32.3%
from the dense-parent Stage1 without a material wall regression.

The required pcc1 four-module canary then failed rc1 in 0.594s.  Every summary
worker hit the explicit no-libpython unavailable stub for
`write_closed_world_vthread_effect_summary`; no executable was produced.  Host
equality and worker tests therefore did not prove pcc1 language closure.  No
Stage2 ran.

The complete short-lived-worker candidate was forward-removed without a second
build, as registered.  Retained source gates pass 42/42.  A future retry must
first isolate the wire codec in a standalone module and prove that the retained
pcc1 can compile and execute it in a strict small program before integration;
using another Stage1 to discover codec unavailability is not allowed.

## Standalone codec compiled-stage proof

`pcc/py_frontend/vthread_effect_summary_wire.py` is a dependency-light line
codec with source SHA-256 `c683ea08...`.  Malformed/deterministic host tests
pass 8/8.  A hyphenated temporary package first failed before codec execution
on the self-backend C-identifier rule and is not attributed to the codec.

The corrected underscore-package canary was compiled by accepted V104 pcc1 in
strict self/no-libpython mode: rc0, 16.378s, tree peak 225,312,768B.  It ran and
printed `codec-ok`; emitted wire bytes match the host expectation.  Dumped IR
has zero `py_cpy_*` call and zero strict stub, the binary links only libSystem,
and no codec/canary unavailable message is present.

This is the required pre-integration proof.  The codec is not yet connected to
summary workers and no new compiler Stage1 or Stage2 follows from it.

## Line-codec integration verdict: denied and removed

Thin-adapter integration retained all focused/corpus evidence and its sole
Stage1 completed: pcc1 `05ef3037...`, 404.52s, 191.575B instructions,
1399.03s CPU, 1.301GB footprint, libSystem only.  Instructions stayed close to
the prior 190.739B result, but wall/CPU regressed 33.5%/22.1%.

The first required pcc1 four-module canary failed in 1.182s: the first two
summary workers raised `AttributeError: __init__`; no program was produced.
The standalone codec itself remains exonerated by its separate pcc1 roundtrip.
No Stage2 ran and no second integrated build was attempted.

All compiler integration source/tests were forward-removed; standalone codec
plus retained paths pass 50/50.  Next work must isolate package-`__init__.py`
summary construction as a strict ordinary pcc1 canary before integration.
