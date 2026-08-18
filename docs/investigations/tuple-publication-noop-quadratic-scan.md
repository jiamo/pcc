# Investigation: tuple completion scans run for no-op publication backends

## Status

active

## Problem Description

The human asks why Stage2 still takes about three times Stage1 and requests
the large owner. Current same-source v80 is Stage1=185.70s and
Stage2=566.617s/1992.202 timed-tree CPU, with complete pcc2 and a passing ABI
executable. Stage3 and adjacent helper optimization are paused for this audit.

Phase accounting places about248s of the381s gap in frontend/backend execution:
host Stage1 completes frontend+emit inside7 workers in102.745s; Stage2 has
frontend103.920s, ASM119.876s and PCO126.674s as separate phases. Coordination
is about132.399s versus the host's approximately30.726s remainder. These
identify execution regions, not proof that merely overlapping phases fixes
them. The prior mixed ASM/PCO experiment was denied after full transfer; read
`pcc1-stage2-emit-throughput-and-memory.md` Updates99–101 and the2026-09-05
denials before proposing scheduling changes.

The native runtime exposes an algorithmic candidate in that region.
Both py_tuple_set_item mirrors fill one slot, then scan from slot0 until the
first NULL to decide whether to publish initialization. Filling N slots in
order therefore performs approximately N(N+1)/2 pointer reads. Yet the called
pcc_gc_publish_initialized returns immediately for GC0..3 and only clears
FRESH_ALLOC for GC4. Cycle tracking and pointer stores occur separately before
this completion scan and must remain intact.

## Repro

Build a function-bearing native program that constructs tuple([1] * N) using
the receipt-bound immutable runtime, execute at N and2N, and compare exact
contents with CPython. Count instructions rather than relying on rounded short
wall times. The large real-worker control is the retained py_ast PIDX; resolve
its actual module index through manifests rather than guessing numeric IDs.

## Test [CONFIRMED]

Native scaling is confirmed; deterministic scan-count regression is pending. Source proof
is the complete-prefix loops in py_runtime/src/py_tuple.c and
py_runtime/py/py_tuple.py, together with the GC4-only publication helpers in
py_obj.c/py_obj.py. The earlier v77 emit sample places968+934 of4203 samples
in tuple_set_item/load_ptr beneath module finalization; that is a partial
window, not a whole-stage Amdahl percentage.

The function-bearing tuple([1]*N) native probe uses the immutable runtime
SHA256 `50f7eb3312b77b838313d45b177ea66e03af07f162fbd30192441ba531c555c5`.
Its native executable and the identical CPython program return exact length,
first and last values at every size. The native build passes1 test in2.22s.

| Elements | Native instructions | Native CPU | CPython instructions |
| --- | ---: | ---: | ---: |
| 10,000 | 1,221,482,629 | 0.06s | 119,197,768 |
| 20,000 | 4,827,091,299 | 0.27s | 119,241,791 |
| 40,000 | 19,254,960,607 | 1.23s | 120,834,772 |

Native instruction growth is3.95x then3.99x when input doubles: the quadratic
source loop executes on the actual runtime. CPython's tiny timed runs are
startup-dominated, so no constant per-item speed ratio is inferred from them.
All six runs finish rc0 with exact stdout under the performance lock.
Artifacts:`build/tuple-growth-baseline-build/` and
`build/tuple-growth-baseline-{native,cpython}-n{10000,20000,40000}/`.

The checked reference/contract separates cycle tracking from publication:
the CPython free-threading collector keeps tp_traverse/refcount processing;
pcc GC4's fresh object publication clears FRESH_ALLOC under its graph lock.
The proposed early return occurs only after pcc's existing store/incref and
cycle-track operations. GC4's completion loop and empty-tuple publication
remain untouched. Resolved forwarding/substrate investigations were read;
their GC movement/identity semantics are not relaxed by this change.

## Proposals

- No.1 Gate completion scanning by the publication backend [CONFIRMED 2026-09-06: linear native scaling, exact output, 1.25x py_ast PCO worker; staged Stage2/Stage3 qualification still owed].

## No.1 Skip no-op publication work

### Code Change

Pending proof and GC reference audit: preserve slot writes, increfs, cycle
tracking and barriers, and avoid completion scans where publication is a
proven no-op. Keep GC4's partial-construction/full-publication behavior exact.
No tuple layout, identity, allocation cap or collector contract is weakened.
This row makes no claim that GC4's remaining construction is linear.

### Pending

Require focused native N/2N and exact-result evidence, all relevant GC0..4
partial/out-of-order/empty/NULL-slot cases, C/pcc-Python mirror equality and a
large same-input pcc1 worker improvement before another full stage. If the
observed whole owner does not move, reject the performance attribution and
resume complete-path profiling; do not relabel a small helper gain as parity.

## Update — mirror guard, deterministic tests and runtime control

The C and pcc-Python setters now return before the completion scan when the
current backend is not4. The existing write/incref/cycle-track code is unchanged,
and GC4's exact loop remains. No layout or public signature changes.

Both actual setter implementations first observed red completion counts
`[1,2,2,2,4]` onGC0. After the guard,20 deterministic tests pass in0.97s;
backend0..3 counts are zero and GC4 keeps the original sequence. The C test
compiles the whole actual tuple translation unit with prefixed symbols and a
TU-confined pointer-load counter, linking the immutable runtime. The Python
test invokes the real port with unsafe/extern seams modeled. Additional
per-call backend-selection and Python-None-versus-NULL tests pass2/0.10s.

One test initially assumed FRESH_ALLOC was always clear outsideGC4 and failed
onGC1 before any setter call. Frozen v80 source reproduced the failure. Source
audit showsGC1/2 deliberately retain that bit as allocation grace; the test
now requires it to remain unchanged, whileGC4's publication/relocation checks
remain strict. The first failure and frozen-control receipt are retained.
Logs:`build/tuple-publication-scan-counts-corrected-harness.log`,
`build/tuple-publication-selection-none.log`, and
`build/tuple-publication-frozen-v80-backend1-control.json`.

The source-keyed isolated pcc-Python runtime builds successfully in26.839s
under the shared lock/8GiB guard (peak671,547,392B), with fresh staging and
verified provenance. Candidate archive SHA:
`658c2c956af1c49bc176ebd9412557374d434f7cf614ebffd6b1135b03b3384f`.
Its native tuple program passes exact contents in2.13s. However128/186 object
hashes differ from the old frozen archive despite only tuple source changing,
because the compiler checksum also changed. The old archive is therefore not
a single-variable performance control for this rebuilt library.

The runtime cache tool is being extended to accept an explicit runtime source
tree with the same current compiler and logical source-key paths. Rebuild the
pre-fix tuple sources through that route, require a member/source comparison,
then measure. No speed attribution is made from the rebuilt library yet.

## Update — same-compiler control, linear scaling and emitter replay `[CONFIRMED]`

2026-09-06. HEAD `2203dc3d` equals the frozen v80 snapshot in every build
input except the two tuple files, so the pre-fix control is the v80
`py_runtime` compiled by the current compiler through
`cached_pcc_python_runtime(runtime_source=...)`
(`ea82adb9aa3a29624dcd4a08-pcc-py`, 28.2 s). Control and candidate share one
codegen checksum and differ only in `py_tuple` source; the 128 differing
objects are the 128 members that embed their own staging path (`strings`
count 128 for each archive). The earlier "compiler checksum changed" reading
of the old v80 bundle was path embedding plus a different build root, not a
compiler difference.

Native `tuple([1] * N)` instructions retired, exact CPython output at every
size: control 1,224,152,962 / 4,824,874,353 / 19,238,083,913 at 10k/20k/40k
(3.94x, 3.99x per doubling); candidate 23,619,777 / 22,750,078 / 29,981,553
there and 397,660,460 / 784,971,142 / 1,544,177,432 at 1M/2M/4M (1.974x,
1.967x). The completion-check work is linear on GC0.

Candidate Stage1 `v81` (frozen snapshot + candidate archive, v80 recipe):
187.21 s wall, 730.65 s CPU, 4,827,348,992 B tree peak, libSystem-only pcc1
`5fd934f0...`; v80 control was 185.70 s / 736.87 s / 5,048,434,688 B.

Receipt-bound replay of retained v80 Stage2 sidecars with each pcc1 under the
recorded Stage2 environment (`--pcc-self-backend-indexed-emit-worker`,
`os.execve` launcher, lock + 8 GiB guard). `py_ast` PCO lane, two alternating
pairs, PCO exact `cb81f6c2...`: user 15.05/15.13 s -> 12.00/12.04 s,
instructions 229.59B/229.53B -> 172.02B/171.98B (-25.1%), RSS flat at
1.0528 GB. Nine more PCO workers exact, total user 66.98 s -> 59.49 s
(1.126x; large modules 0.84-0.88 instruction ratio, small ones 0.96-0.97).
`cli_bootstrap` ASM lane, exact `04b55bb2...`: 26.62 s -> 26.28 s user,
instructions -0.7%. The tuple scan owns a real share of the PCO lane and
almost none of the worst ASM module, so it does not close the 3.05x stage gap.

Gates: scan-count harness 22 passed on the candidate archive; GC0..4
production contract 169 passed per backend with three pre-existing reds
deselected; strict mirror/ABI gates 178 passed with two pre-existing reds.
Every red was attributed as independent of this change (identical on
pre-fix/candidate/v80 archives, or on untouched files) and routed:
`GC-P2-BACKEND4-CONTRACT-REDS-BISECT` (whose waitset cluster is backend 2 /
mode auto, not backend 4), `RT-P1-PORT-RAW-TYPE-TAG-RATCHET-RED`,
`RT-P1-THREADS-OFF-ARCHIVE-REENTRANT-LOCK-PROBE-RED`. Evidence:
`docs/goal/evidence/RUNTIME-P0-TUPLE-PUBLICATION-NOOP-SCAN/002-same-compiler-control-and-emitter-replay.md`
and `build/tuple-noop-scan-v81-summary.json`.

Open: source-frozen Stage2 then Stage3 from `build/tuple-noop-scan-stage1-v81`
have not run; no parity, fixed-point, GC1..4 stage or GC4-linearity claim.
