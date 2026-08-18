# 046 — exact admission plus measured link width brings Stage2 below 600 s

## Exact-sidecar admission

The complete v49 population replaced the deliberately high first PCO floor
with generic exact-sidecar formulas that still cover every worker by measured
peak ×1.05 +100 MB:

```text
ASM  floor = 0.40 GiB + 0.07 GiB / sidecar MB
PCO  floor = 0.30 GiB + 0.18 GiB / sidecar MB
phase width cap = 12; charged/live RSS and the 8 GiB breaker still decide
```

The scheduler now deterministically admits the first later pending item that
fits when a large queue head does not, avoiding idle memory without changing
publication order.  The focused packet passed 60 tests.

Source-frozen v50:

```text
Stage1 wall / tree CPU / peak     161.35s / 662.19s / 4.857GB
Stage2 wall / tree CPU / peak     619.056s /2100.755s / 7.676GB
frontend / ASM / PCO phases       128.247 /120.880 /154.548s
pcc2                              ea837737... runnable, libSystem-only
```

Versus v49, Stage2 wall falls16.9% (745.327 ->619.056 s).  CPU rises0.86%,
so the claim is safe parallel utilization, not work deletion.

## Independent Mach-O link worker class

The frozen v50 final-link input has31 ASM and195 PCO objects.  With identical
inputs, runtime archive and output bytes, `PCC_MACHO_LINK_JOBS=8` versus the
old coupled value2 measured:

```text
                         jobs2       jobs8
assemble_pool            41.344s     13.640s
complete link            89.278s     61.350s
jobs8 process-tree peak               4.842GB
output SHA-256           ea837737... identical; --help passes
```

The default/harness therefore gives the independently measured host linker
class eight jobs while pcc1 codegen remains memory-admitted.  Resource
receipts now include `macho_link_jobs` as a parity key.

Source-frozen v51 under the unchanged common8 GiB envelope confirms transfer:

```text
Stage1 wall / tree CPU / peak     163.05s / 671.86s / 5.035GB
Stage2 wall / tree CPU / peak     595.457s /2143.782s / 7.679GB
frontend / ASM / PCO phases       128.111 /120.535 /160.355s
final link                         60.802s (assemble12.423, prepare41.525)
pcc2                              f13951d6... runnable, libSystem-only
```

Stage2 is now3.65x Stage1.  It is below the superseded600-second milestone but
does not satisfy the binding Stage2<=Stage1 goal or fixed point.

## Explicit common16 GiB envelope

Harness and bootstrap validation now keep8 GiB as the default but allow an
explicit16 GiB cap for both stages, bind the internal worker budget to that
outer cap, require the external process-group breaker and retain an8 GiB host
reserve. 62 focused harness tests pass.  The first v51 Stage1 launch was
correctly refused before compilation: swap was14.7/16 GiB used and reclaimable
RAM about46.9 GiB, below the48 GiB high-swap waiver line.  No safety threshold
was weakened and no pcc child started.  The16 GiB measurement remains unrun.

## Next architectural owner

Same v51 pcc1 and sidecar, isolated under the performance lock:

```text
py_ast       ASM 6.695s /0.939GB    PCO20.552s /2.101GB
class_gen    ASM16.766s /2.401GB    PCO54.120s /6.349GB
```

`class_gen` remains an ASM production lane and is only a scaling
discriminator.  `py_ast` is a real PCO lane: its13.86-second/1.16-GB delta is
the in-pcc1 assembly/native-object owner.  Its structured transport covers
158,304 instructions and leaves46,523 text instructions:

```text
adrp10827 add10757 sub7722 b4519 ldr2379 nop1668 cmp1550 cset1550
cbz1486 ldp732 autiasp732 ret732 str673 paciasp532 stp532 and117 ...
```

The next slice must migrate this finite emitter vocabulary and relocations
into the packed instruction plane, retain the text assembler as an exact
oracle/fail-closed diagnostic, and remove normal-path fallback records rather
than merely moving parsing between functions.  Common16 GiB scheduling can be
measured later when resource preflight permits; it is not a substitute for
reducing the 3.2x Stage2 tree-CPU gap.

## Verdict

`[CONFIRMED]` for exact-sidecar admission, deterministic fit scheduling and an
independent eight-worker host linker class.  `[OPEN]` for native instruction
closure, Stage2<=Stage1 and GC0 fixed point; GC1-4 remain downstream.
