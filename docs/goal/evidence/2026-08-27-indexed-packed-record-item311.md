# Indexed kernel + packed value-record item311 gate — 2026-08-27

Claim level: source-frozen pcc1 worker acceptance. This does not yet prove
Stage2/Stage3, fixed point, or five-GC transfer.

Candidate source closure: `8eb0d3a10e4f948a46b73d478eb14e1c0bc2c85248e1ed74348509aec0fc2a0c`.
Candidate compiler: `2606324a0e7f836a2f238c804821464472ea177daaaf817fb2044e6c9c94515d`.
Control compiler: `dd8084474d374b2ffb47cf0abbfcc99fddc6bf2cd75e81d94076c7c5e2581885`.

The accepted worker batch consists of:

- one end-to-end `IndexedFunctionKernel` across verifier, stackprep,
  stackmap/liveness, AArch64 regalloc/target planning, and emit;
- zero normal-path `CompactParsedInstrView` construction;
- packed safepoint/location/reload scalar records backed by compiler-owned raw
  storage, read/written in batches through unboxed two/three/four-i64
  valueclass aggregates;
- traced object/string side tables only for labels, diagnostics, and existing
  root-group planning;
- generic cross-module valueclass schema/return propagation so caller and
  callee use the same aggregate ABI;
- raw allocator provenance stored as an integer address and recovered only at
  exact `pcc.unsafe` load/store/free seams; no global managed-pointer bypass.

Fresh bracketing item311 runs on the same frozen 5,108,635-byte input:

| arm | wall | CPU | instructions | footprint | assembly |
|---|---:|---:|---:|---:|---|
| control before | 61.12 s | 47.16 s | 638.18 B | 6.874 GB | `ff943e10...` |
| candidate | 33.33 s | 31.26 s | 400.24 B | 4.989 GB | `ff943e10...` |
| control after | 52.76 s | 47.49 s | 637.82 B | 6.874 GB | `ff943e10...` |

Using the faster bracketing control gives 1.583x wall, 1.509x CPU, 1.594x
instructions, and 27.4% lower footprint. Even the earlier fastest clean
control (44.60 s) gives 1.338x wall. The registered 1.25x wall/instruction
worker threshold is therefore satisfied without selecting a noisy control.

Focused evidence before the build:

- 520 self-backend/stackmap/export tests passed;
- 11 adjacent multi-file/valueclass/export tests passed;
- generic two-module valueclass caller/callee IR and self verifier passed;
- the real value-arena + precise-stackmap merged closure completed self
  backend emission;
- host item311 reported zero instruction and safepoint diagnostic projections
  and exact assembly.

Open boundary: run the full dedicated multi-file/fallback/bootstrap focused
gates, then build pcc2 and pcc3 sequentially from this exact pcc1/source. Only
after a green fixed point may the final GC0..4 matrix run once.
