# Shared definition facts and stack-map native-plane denials

Date: 2026-08-29

Claim level: one accepted compiler-internal definition-fact slice, one generic
class-constructor correctness repair, and two denied stack-map representation
experiments. This is not native-data-plane closure and not a Stage2/fixed-point
claim.

## Accepted: kernel-owned definition position

The verifier no longer constructs `_Definition` dataclasses, stable-hash
buckets, bucket lists, or a temporary per-verification arena. The parser/kernel
publishes definition block, type, position and first-duplicate ID once; the
verifier reads those shared value-ID facts and resolves spellings only on an
error branch.

Source-frozen GC0/self/no-libpython V84:

- receipt: `build/native-data-plane-stage1-candidate-v84-shared-definition/build-receipt.json`
- pcc1 SHA256: `619386a1c32d2bd8779d1c4c9e24ebe6211db8b9cdf91a951ec61e2c66596431`
- Stage1: 281.61s, 309.248B retired instructions, 1.653GB peak footprint
- linkage: libSystem only
- 114-byte full self-emitter canary: exit 0
- item311 assembly: `ff943e10afe802c44faff43146a67b56735cd74bb6f1d79db1d8251cfe8f7251`

Adjacent A-B-A item311:

```text
V84 shared definition  376.880B  27.48 CPU  27.60 wall  3.085GB
V79 control            384.031B  28.03 CPU  28.17 wall  3.105GB
V84 repeat             376.963B  27.56 CPU  27.66 wall  3.085GB
```

This is a stable approximately 1.9% instruction/CPU improvement and a 0.6%
footprint improvement. It is retained as shared analysis plus a small measured
win, not described as a 1.05x result.

## Denied: verifier-local dense table

V80 and V81 removed the object graph but rebuilt a separate dense table during
verification. V81 delayed value spelling until error branches, yet its adjacent
comparison was still only about 1% fewer instructions with worse wall/CPU:

```text
V81 candidate  380.837B  33.04 CPU  36.52 wall  3.085GB
V79 control    385.292B  31.58 CPU  32.39 wall  3.105GB
```

Both candidates were removed. Their result motivated the accepted kernel-owned
fact instead of another pass-local table.

## Confirmed compiler bug: aggregate attr as raw-int constructor arg

The first packed stack-map build exposed a generic frontend defect. In a
`pcc.*` raw-int module, `Reload(origin.second, ..., origin.third)` loaded the
`CompilerInt4` aggregate correctly but class-constructor argument recovery
called `py_obj_getattr` with that aggregate as a `ptr`. The self verifier failed
closed with:

```text
call expects void* for 'classgen.arg.origin', got <anon-struct>
```

The checked-in cross-module reduction reproduces that exact failure. The fix in
`class_gen.py` uses normal expression lowering when a named receiver's recorded
storage IR type is non-pointer; ordinary object receivers retain dynamic
attribute behavior. The reduction, the complete cross-module valueclass file,
and the focused classgen/dataclass packet are green. Source-frozen V85 crossed
the former failing boundary, completed Stage1 and passed the 114-byte canary.

## Denied: per-word packed stack-map provenance/liveness

The candidate replaced `_ManagedValueOrigin`, provenance transfer dict/tuples,
and per-block liveness sets with raw scalar arenas and a dense 32-bit word
matrix. It passed 66 focused tests, exact host inventory/assembly, strict
closure, source-frozen Stage1 and the canary after the generic frontend fix.
It is decisively slower under pcc1:

```text
V85 packed plane  429.775B  30.93 CPU  31.05 wall  3.110GB
V84 control       377.190B  27.50 CPU  27.54 wall  3.085GB
regression         +13.94%   +12.47%   +12.75%     +0.81%
```

The candidate was removed; `self_backend_precise_stackmaps.py` is byte-equal
to frozen V79 (`e70d74f...`). Do not retry a per-word/per-scalar arena loop.

## Current owner and open boundary

An immediate V84 caller flamegraph (`11,592` on-CPU samples) attributes:

- `build_function_stack_map_plan`: 4,184 samples (36.1%)
- verifier: 2,178 samples (18.8%)
- `_block_entry_states`: 955 samples (8.2%)
- nested `add_record`: 580 samples (5.0%)
- `_managed_value_origins` + `_managed_live_after`: only 260 samples (2.2%)

Therefore the next stack-map design must fuse/cache the root-state transition,
location and record plane as a whole, with batch span operations or shared
state IDs. It must not optimize the denied 2.2% sub-owner again. Parser
terminator/PHI construction and the remaining root-state/location side tables
keep the task `IN_PROGRESS`; current-source Stage2 and GC0 fixed point remain
required before closure.
