# Compiler-owned i64x4 value projection foundation

Date: 2026-08-28

Task: `PERF-P0-COMPILER-VALUE-RECORD-PROJECTION`

## Claim boundary

This slice proves one generic compiler-owned four-i64 value projection across
host lowering, closed-world AST transport, strict pcc1 self-host compilation,
and self-backend execution. It does **not** accept the packed-instruction raw
arena, prove a whole Stage2 improvement, close the broader TypeDesc/slot/value
record migration, or prove GC0..4 fixed point.

## Implementation

- `pcc.unsafe.load_i64x4(ptr, offset)` and
  `load_i64x4_strided(ptr, offset, stride)` remain fail-loud CPython stubs.
- Type inference gives both calls one synthetic, identity-free
  `UnsafeI64x4(first, second, third, fourth)` value projection whose fields are
  explicit `pcc.i64`, not semantic Python `int` narrowing.
- Unsafe lowering emits four raw i64 loads and returns one literal aggregate at
  the call site; no helper call, tuple, dataclass, ValueBox, or libpython edge
  is introduced.
- Closed-world `pcc.unsafe` source exports intentionally carry `Any` stub
  annotations. `_bind_external_import_exports` now preserves the
  compiler-owned intrinsic return contract instead of overwriting it with
  `DynType`.

## Failure and regression

The first complete-closure build failed deterministically in
`pcc.backend.self_backend_ir.packed_instruction_record4`:

```text
marshal_to_object: DynType with IR { i64, i64, i64, i64 } not supported
```

A direct external-export substitution reproduced the same loss: the call and
all `lanes` names changed from `ValueClassType` to `DynType` only when the
closed-world `pcc.unsafe.load_i64x4 -> Any` export was supplied. The new
`test_i64x4_intrinsic_type_survives_closed_world_stub_exports` failed on that
state and passes after the priority fix.

Focused evidence:

```text
tests/python/test_unsafe_i64x4.py + test_py_frontend_ast_wire.py
  11 passed in 1.24s

tests/python/test_unsafe_i64x4.py, bound to the final pcc1
  3 passed in 0.78s

self-backend host packet
  355 passed; the sole environment-only current-pcc1 lookup node passed
  separately when explicitly bound to the candidate
```

The source-frozen Stage1 receipt is
`build/packed-instruction-intrinsic-stage1-v3/stage1-result.json`:

```text
source:   bfe79069278e54cbbcb4f6a269eb591226ed15ab00aeb40f8e5ee8feeed210f6
pcc1:     87ead8fa3874e3b4142f521845fe881bcce8545a209d3024905bdebb29ffaa13
wall:     312.32 s
linkage:  libSystem only; no libpython, no LLVM
smoke:    passed by the stage1 build tool
```

## Mechanism measurement

The rejected shared packed-record prototype was rebuilt with only its four
scalar helper reads replaced by the new call-site aggregate loads. On the same
frozen item311 input:

| arm | wall | instructions | peak footprint | assembly |
|---|---:|---:|---:|---|
| shared batch v12 | 54.84 s | 691.327 B | 4.680 GB | `ff943e10...` |
| call-site i64x4 | 48.53 s | 671.887 B | 4.679 GB | `ff943e10...` |

The intrinsic is 1.130x faster at this mechanism boundary and removes 2.81%
of instructions while preserving exact assembly. It also matches/slightly
beats the earlier duplicated consumer-local raw path (49.30 s), so a shared
aggregate API no longer carries the prior Python helper penalty.

The raw packed representation itself remains denied: the accepted tuple-based
item311 is 30.00 s / 407.414 B / 4.190 GB, so the intrinsic arm is still 1.62x
slower, executes 1.65x the instructions, and uses about 11.7% more footprint.
The next slice must migrate semantic TypeDesc/slot/value records and publish
kind-specific shared facts; it must not revive generic tagged re-decoding.

## Status

`[CONFIRMED]` as the generic fixed-i64 aggregate/value-projection foundation.
`[DENIED]` remains the verdict for the packed tagged instruction arena as an
accepted compiler data plane.
