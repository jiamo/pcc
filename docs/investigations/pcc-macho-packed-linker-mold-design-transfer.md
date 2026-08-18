# Investigation: transfer mold's packed and frozen-pass linker design into pcc's owned Mach-O path

## Status

active

## Problem Description

The cold No.75 Stage2 takes 977.866 seconds.  The final owned assembler/linker
driver records 118.087 seconds, about 12.1% of Stage2.  The separately named
`link_self_emit_objects_native` phase records 583.303 seconds, but source
inspection proves that under `internal_link` it is self-backend IR-to-assembly
generation: each worker writes a `.s` path and the later driver assembles it to
`.pco`.  It must not be counted as linker time.  Link work is therefore a real
secondary bottleneck, while the 583-second native emitter is the primary
bottleneck and the 118-second final link alone cannot close the full
Stage2-to-Stage1 gap.

The user requested four concrete design transfers after a complete reading of
`rui314/mold`, without using mold itself:

1. keep `.pco`, relocation and symbol-table state in a packed arena or
   read-only codec view instead of decoding millions of Python dataclasses;
2. freeze archive selection, duplicate resolution and section ordering before
   parallel relocation scanning, payload copying and fixup application;
3. intern symbol strings to integer IDs for downstream passes;
4. stream native objects so the coordinator does not retain decoded objects,
   relocations and duplicate output payloads simultaneously.

The design reference is mold commit
`e633272dc92c83c6c56c4d8449279da468701193`.  Relevant mechanisms are the
serial semantic pass pipeline in `src/main.cc`/`src/passes.cc`, input-backed
spans and compact indexed entities in `src/mold.h`/`src/input-files.cc`,
sharded symbol registration followed by deterministic gather, layout freeze
before disjoint output writes, and relocation application while copying input
payloads.  Mold is an ELF linker and is not introduced as a binary, library,
runtime owner, or Mach-O fallback.

Predecessor evidence:

- `pcc1-stage2-emit-throughput-and-memory.md` Update No.70 measured 464 input
  decodes at 47.313 profiled seconds and hundreds of millions of coordinator
  calls.  A private trusted same-worker transport was removed after missing
  its preregistered CPU threshold.  That proposal skipped a repeated
  validation through provenance; it is not the proposal here.
- `stage1-cold-build-speedup-2026-08-15.md` records earlier raw stack-map
  scanning wins and the retained rule that a structural byte scan may replace
  materialization only when it enforces the same fail-closed boundary.

## Repro

Frozen No.75 evidence:

```text
build/bootstrap-no75-v1/profile/stage2.json
stage wall                                      977.866 s
link_self_emit_objects_native                   583.303 s
link_self_pcc_driver                            118.087 s
assembly inputs                                 464 .s files
assembly bytes                                  1,024,085,177
assembly-cache hits/misses                      0 / 464
```

The deterministic direct-link replay uses the exact 464 assembly inputs under
`build/bootstrap-no75-v1/cache-cold`, the source-matched pcc-Python runtime
archive, a unique output/cache directory, and the shared
`build/.pcc-performance.lock`.  Every candidate/control pair must execute the
produced compiler with `--help` and compare the complete executable bytes.

## Test [CONFIRMED]

The performance failure is confirmed by the completed No.75 Stage2 profile and
the predecessor coordinator cProfile.  Correctness gates for Proposal No.1:

```bash
gtimeout 120s env -u LC_ALL uv run pytest -q -x -n0 \
  tests/python/test_native_object_fastpath.py \
  tests/python/test_macho_link_relocatable.py \
  tests/python/test_macho_exec_link.py \
  tests/python/test_macho_incremental_link.py
```

The new codec-view tests must prove malformed/truncated/trailing payload
rejection parity, indexed relocation parity, standard Mach-O external-boundary
parity, and that the link route does not construct one Python object per packed
relocation.  A direct frozen-input A/B is required before any Stage2 rebuild.

## Proposals

- No.1 Consume validated indexed `NativeObject` directly [CONFIRMED]
- No.2 Fully validated read-only `.pco` codec view [CONFIRMED]
- No.3 Global symbol-name interning and integer-only downstream identity [DENIED]
- No.4 Frozen semantic plan with parallel relocation/copy/fixup shards [pending]
- No.5 Read-only mmap input lifetime [DENIED — incomplete]
- No.6 Reuse packed validation summaries and avoid ordinary special-table materialization [CONFIRMED]

## No.1 Consume validated indexed `NativeObject` directly

### Code Change

Keep the existing public/disk `decode_native_object` and its full validation.
After that boundary, make `_coerce_link_object` retain `NativeObject` instead
of constructing `NativeObjectView`, and teach relocatable merge to consume its
indexed sections, symbols and relocations directly.  Copy a section payload
only when section-target normalization actually needs mutation; ordinary
immutable section bytes flow directly into the merged region.  External
Mach-O input behavior and the final prepared-object validator are unchanged.

This is a safe prerequisite to the raw codec view: it first removes the eager
second representation (payload copies plus Mach-O-shaped symbol/relocation
dictionaries) without changing the validation boundary.  No.2 can then replace
the remaining input dataclass decode behind this indexed linker protocol.

### CONFIRMED

Focused native-object, relocatable, executable, incremental, archive,
parallel, external-Mach-O and driver-argument gates pass 113 tests.  The new
regression monkeypatches `NativeObject.link_view` to fail and proves that the
relocatable core consumes indexed inputs without entering the expansion.

`scripts/run_pcc_link_ab.py` assembled the frozen 464 No.75 `.s` inputs once,
then ran one warmup per arm and three alternating pairs over identical `.pco`
and runtime-archive bytes.  All eight complete 174,301,592-byte executables
had SHA-256
`d4694dc75cf4495388ceffcc37341944079a02f1209c1a581853eedd3c86fe20`,
all eight `--help` outputs matched, and source/archive receipts were unchanged.

```text
pair   control/candidate wall   wall speedup   user speedup   candidate/control instructions   RSS   footprint
1      61.20 / 59.21 s          1.0336x        1.0379x        0.9615                           0.8856 0.8837
2      61.80 / 59.32 s          1.0418x        1.0405x        0.9590                           0.8807 0.8784
3      61.00 / 58.44 s          1.0438x        1.0429x        0.9619                           0.8824 0.8802
```

This is accepted despite being below 1.05: all three wall and CPU pairs move
in the same direction, instructions fall about 3.9%, memory falls about 11.8%,
and the change removes a known duplicate representation without adding a
policy branch.  The temporary A/B selector was removed after the verdict.

## No.2 Fully validated read-only `.pco` codec view

### Code Change

Add a version-pinned read-only view over encoded native-object bytes.  Parse
and validate framing, canonical symbol/section order, relocation bounds and
section semantics directly from the codec.  Retain compact offsets/counts and
iterate relocation records from their packed spans; do not create one
`NativeRelocation` per input relocation or copy every section payload.  Keep
`decode_native_object` as the public materializing API for callers that
explicitly need semantic objects.

Teach the driver/cache seam and the direct indexed linker protocol to accept
this explicit validated type while leaving raw `.pco` bytes rejected at the
Mach-O boundary.  External `.o`/`.a` parsing and final Mach-O validation remain
unchanged.  This is not the denied trusted-provenance shortcut: disk/cache
bytes remain fully validated.

### CONFIRMED

The raw decoder retains symbol/section descriptors and fixed relocation spans.
It validates canonical partitions, section layout, symbol bounds, data-in-code,
every proven relocation shape/index/bound, special unwind/mod-init/stack-map
contracts, framing and trailing bytes without creating input relocation
dataclasses.  The driver and assembly cache now return this packed type by
default; callers that need a semantic object retain `decode_native_object`.

Evidence before the expensive run:

- 122 focused codec/link/incremental/archive/parallel/external-boundary tests
  passed;
- 2,000 deterministic single-byte mutations had zero accept/reject mismatches
  between materialized and packed decoders;
- 590 existing cache objects (358,392,697 encoded bytes, 3,775,914
  relocations) all validated through the raw view in 5.438 seconds.  The
  earlier materializing pass over that same set took about 18.3 seconds; this
  is sizing evidence, not the acceptance measurement.

The frozen 464-input A/B used accepted No.1 as control.  All eight complete
executables and `--help` outputs matched the same No.1 SHA and source/archive
receipts remained stable:

```text
pair   control/candidate wall   wall speedup   user speedup   candidate/control instructions   RSS   footprint
1      56.62 / 47.65 s          1.1882x        1.1878x        0.8491                           0.8793 0.8765
2      57.39 / 48.98 s          1.1717x        1.1926x        0.8497                           0.8797 0.8784
3      57.24 / 47.89 s          1.1952x        1.2001x        0.8484                           0.8791 0.8775
```

No.2 is accepted: wall improves 17.2-19.5%, user CPU 18.8-20.0%, instructions
about 15.1%, and memory about 12.1%, with full validation and no output change.
The temporary driver selector was removed; packed `.pco` input is the one
production driver route.

## No.3 Global symbol-name interning and integer-only downstream identity

### Code Change

Prototype a deterministic interner after local-symbol rename.  Pre-map every
input symbol-table index to either its existing string (control) or a global
integer ID (candidate), then carry that key through relocation/reference/
definition/stack-map/rebase state and materialize strings only when building
the final `NativeObject`.  Both arms use the same new merged-record structure,
so the A/B isolates key type rather than comparing unrelated algorithms.

### DENIED

Both key modes passed 54 focused tests; integer IDs passed the complete 121
test adjacent boundary.  All frozen images, `--help` outputs and receipts
matched, but three pairs were noise/slower:

```text
pair   control/candidate wall   wall speedup   user speedup   candidate/control instructions   RSS   footprint
1      53.05 / 53.39 s          0.9936x        0.9959x        1.0023                           0.9980 0.9975
2      53.31 / 53.04 s          1.0051x        1.0081x        1.0001                           0.9968 0.9974
3      52.98 / 53.43 s          0.9916x        1.0004x        1.0008                           0.9977 0.9979
```

The shared merged-record projection also raised the approximately 48-second
accepted No.2 shape into the approximately 53-second range in this run.
Integer IDs did not recover that cost and did not reduce instructions.  The
entire prototype and selector were removed by forward patch; No.1+No.2 remain.
This does not prove integer IDs are universally bad in a native/C++ linker. It
proves that adding a second ID projection to this Python merge architecture is
not an optimization.  Reconsider only if a future frozen-plan representation
uses IDs natively rather than wrapping existing Python objects.

## No.5 Read-only mmap input lifetime

### Code Change

Prototype read-only mmap for explicit `.pco` paths so the coordinator does not
first copy all 343,526,026 encoded input bytes into Python `bytes`.  The packed
validator reads the mapping directly and the driver closes every mapping after
the image is complete.  The ordinary `read()` arm remains the control.

### DENIED — incomplete

The candidate passed 126 focused tests, including mapping cleanup and full
packed/incremental/link boundaries.  It never obtained an uncontaminated
three-pair measurement: four sequential external `/tmp/pcc-no71-profile`
`gc3probe{,2,3,4}_pcc2` jobs started without taking the repository performance
lock.  The improved A/B runner detected each one before or after a sample and
aborted instead of reporting contaminated numbers.  The only clean warmup in
the final attempt was control 47.54 seconds versus mmap 48.16 seconds, which
is not acceptance evidence.

With no proven speed or memory win, the mmap selector, buffer widening and
tests were removed.  This denies that implementation, not the broader
streaming goal: assembly-worker results and final regions can still be
streamed without changing the packed input's access locality.

## No.6 Reuse packed validation summaries and avoid ordinary special-table materialization

### Code Change

During packed semantic validation, retain the set of input symbol indices
targeted by relocations.  Reuse it in `_inspect_link_input` instead of walking
every relocation again.  In the special-section validator, return immediately
for ordinary sections instead of first converting every raw relocation record
to a giant tuple that is only needed by mod-init, unwind and stack-map tables.

### CONFIRMED

After explicit authorization, the remaining `/tmp/pcc-no71-profile` probe5
process groups were terminated without deleting their outputs.  The clean v2
A/B then completed.  Control and candidate focused gates passed 95 each, the
candidate adjacent boundary passed 124, every executable and `--help` output
matched, and source/archive receipts stayed equal.

```text
pair   control/candidate wall   wall speedup   user speedup   candidate/control instructions   cycles   RSS
1      50.85 / 48.58 s          1.0467x        1.0485x        0.9556                           0.9534   0.9975
2      58.98 / 49.53 s          1.1908x        1.0642x        0.9575                           0.9452   0.9971
3      50.75 / 79.67 s          0.6370x        0.9164x        0.9713                           1.1424   0.8318
```

Wall/cycles contain a severe third-pair system outlier, so no stable wall
speedup is claimed.  The load-independent result is directional in every
pair: instructions fall 4.44%, 4.25% and 2.87%.  The change removes an actual
ordinary-section relocation tuple materialization plus a redundant inspection
scan, adds no policy or semantic bypass, and keeps special-section validation.
It is accepted narrowly as an approximately 3-4% instruction reduction.  The
selector was removed and the summary route is the sole packed path.

Linker-local optimization stops here.  Frozen-plan process parallelism and
further streaming are not implemented in this round because No.75 attributes
583.303 seconds to IR-to-assembly native emit and only 118.087 seconds to the
owned assembler/linker.  The next optimization owner is the emit phase, not
another linker micro-candidate.
