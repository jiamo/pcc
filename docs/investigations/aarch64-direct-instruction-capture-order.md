# Investigation: direct AArch64 instruction capture order and lifetime

## Status

Active. Correctness gate for the uncommitted producer-side instruction transport;
not a Stage2 speed claim. Predecessor:
[emit throughput and memory](pcc1-stage2-emit-throughput-and-memory.md).

## Problem Description

The producer-side PCO experiment appends packed words at helper invocation time
and returns one shared placeholder. Finalization consumes those words in final
line order. The two orders must agree, including cold exception stubs, aggregate
argument moves, and precise GC reloads. Review found that the original WIP did
not establish that invariant. An exception also left the global capture active.

## Repro

Run each focused node independently with `gtimeout 30s env -u LC_ALL uv run
pytest -x -n0 -q --tb=short`:

- `tests/python/test_llvm_capi_direct_indexed_kernel.py::test_direct_transport_preserves_deferred_instruction_order[hfa]`
- `tests/python/test_llvm_capi_direct_indexed_kernel.py::test_direct_transport_preserves_deferred_instruction_order[cold_landing]`
- `tests/python/test_arm64_structured_encoding.py::test_direct_capture_exception_does_not_poison_later_asm`
- `tests/c/test_self_backend.py::test_self_backend_aarch64_register_helpers_cover_large_offsets_and_immediates`

## Test [CONFIRMED]

All four nodes failed on the resumed WIP before production edits. HFA stores
preceded their address calculation; cold stub words occupied normal-path PCs;
an invalid-target rejection contaminated a later ordinary ASM emission with
seven placeholders; nonzero low movewide chunks lost the exact `, lsl #0` text.
Each run completed in under one second. No bootstrap was run on this source.

The same pattern also exists in the legacy emitter's eager stack-map line
index: it constructs reloads before emitting body instructions. This owner must
be covered by a managed-root differential before another expensive build.

## Proposals

- No.1 Final-order emission and scoped capture [pending]

## No.1 Final-order emission and scoped capture

### Code Change

Make address setup precede HFA memory emission. Defer cold-stub instruction
construction, not merely placement. Consume packed stack-map routes lazily on
the AArch64 path, including parsed inputs. Give capture a guaranteed cleanup
scope and reject nested capture before changing the outer owner. Preserve the
existing ASM spellings. Do not remove GC reloads or semantic verification.

### Pending

Require focused exact section/relocation/stack-map comparisons, existing backend
tests, a pcc1-compiled feature canary, and retained real PIDX equality before
claiming closure. The constant-placeholder transport still depends on final-order
producer discipline; general mutable emission-buffer migration is not proven by
this slice.

## Update: host correction gates

HFA helpers now emit address setup first. Packed AArch64 capture uses lazy
stack-map routes even for parsed inputs; ordinary uncaptured parsed ASM keeps
its prior route and optimized layout. Cold stubs are constructed at final
placement by walking the existing kernel edge IDs, removing the cold line
container on the dense path. Every emitter mode reserves an emission scope;
capture creation rejects a nested owner, and cleanup closes before dropping the
global arena reference. A negative movewide shift is now rejected by the parts
API instead of producing a negative instruction word.

Focused gates passed: 314 backend/inventory tests, 144 structured/stackmap/cold
tests, and final 123 structured/direct tests (overlapping sets). The contextual
227-module closure including terminators passed in47.16s. Three review passes
closed the confirmed ordering/lifetime findings; both overlap directions were
replayed with deterministic host thread checkpoints. No general concurrent
compiler support is claimed: overlapping emission fails closed.

Native qualification remains pending in frozen v69. The test
`test_direct_transport_preserves_deferred_instruction_order` accepts an exact
compiler through `PCC_INDEXED_EMIT_TEST_COMPILER` and compares both ASM and PCO
from actual compiled workers. Host-only green does not close this investigation.

## Update: native boundary qualification

Frozen v69 pcc1 (SHA256
`23750d836588f64c15f5feb5eaed61fb26e47da4918d46d0f64feedc0a1b810b`)
passed the HFA and cold-landing ASM/PCO worker differentials, and its function
compile/run smoke printed 42. The retained real py_ast PCO and cli module1 ASM
also match the v65 oracles byte for byte. Evidence and measured tradeoffs are
in [receipt 058](../goal/evidence/PERF-P0-PROVEN-NATIVE-DATA-PLANE-PARALLEL-EMIT/058-direct-instruction-producer-transport.md).
The reproduced order/lifetime failure class is corrected through the native
worker boundary; complete bootstrap/fixed-point qualification remains open.
