# Investigation: exported class fields omit nested method writes

## Status

active

## Problem Description

The record-span canary compiles and traverses correctly after the separate
literal-aggregate method ABI fix, but its consumer prints `False` instead of
the integer `projection_count == 0`. Serial and parallel frontend controls both
reproduce the result. The provider initializes `spans` inside a constructor
`try`; the exported field list omits it and shifts later consumer indexes.

## Repro

`tests/python/test_compiler_record_spans.py::test_record_span_native_self_backend_executes_aggregate_handles`
prints `30`, `4`, `False`. The serial control reproduces the same output.
`build_closed_world_context` on the actual provider exports
`('nodes', 'generation', 'closed', 'projection_count')`, omitting `spans`.

## Test [CONFIRMED]

The real native test fails its unchanged stdout assertion in 7.62s; the serial
control fails in 7.81s. The minimized export test covers method order and
if/else, loop/else, with, try/handler/else/finally field order and fails before
the repair.

## Proposals

- No.1 Use one assignment traversal for export, type inference and runtime layout [pending].

## No.1 Shared method field traversal

### Code Change

Share the existing class-layout walk, including nested control flow
and excluding nested function/class scopes. Use it for every instance method,
not only top-level assignments in `__init__`. Preserve target unpacking,
annotations and declared slot order. Keep constructor exception cleanup intact.

### Evidence

`ClassLowering._collect_method_instance_fields` already descends control flow
across methods. `pipeline_context` and `_class_fields_from_def` only scan the
outer constructor body. The resolved inherited-field export investigation was
read in full; this case has no base class and exposes a separate missing walk.

### Focused result

`instance_field_assignment_statements` is shared by exports, inference and the
runtime layout collector. The minimized export order now matches all 14 fields;
the real provider exports `nodes, spans, generation, closed, projection_count`.
The unchanged native canary prints `30 / 4 / 0` and passes in 7.30s. Existing
inheritance/override/valueclass ABI tests and span tests pass together:
21 passed in 20.72s. Full compiler context, pcc1 and fixed-point verification
remain open, including regression checks for field type precision.

### Contextual denial and correction

The first full compiler context failed only `self_backend_kernel`. Its old
diagnostic helper suppressed the exception behind `-1`; a focused failing test
now requires the module/cause on stderr and in a per-module `.error.txt` file.
That revealed `tuples are immutable - subscript-assignment not allowed`.
A host-only store locator identified `_define_build_value`, line 1184,
`definition_position_values`: new non-constructor inference had overwritten
its declared list type with the `_EMPTY_SEQUENCE` cleanup sentinel.

The minimized declared-list/cleanup-tuple inference test was red before the
correction. Non-constructor writes now contribute missing field order without
overwriting established constructor/declaration types. The nested layout repair
remains in place; 10 context/order tests pass. A new full contextual gate is
required before native rebuild. The temporary locator was never production
instrumentation; its log is `build/kernel-field-store-locator.log`.

### Adversarial review corrections

Code-converge review confirmed two additional R6 regressions before native
qualification: an earlier method annotation could own an exported field type
over its constructor/declaration, and method-only dataclass fields entered the
synthetic constructor signature. Four focused regressions were observed red,
including unknown constructor RHS types and inherited dataclasses.

Export discovery now pre-collects constructor-owned names and declared
annotations independently of field order. Separate inherited `init_field_defs`
keeps method-only fields in runtime metadata without adding constructor args.
The second read-only review found these two fixes clean. Root verification:
42 ABI/context/span tests passed in 20.79s. Full contextual/pcc1/fixed-point
gates remain separate and are not inferred from review approval.

## Update: precise arena restoration and fixed-point qualification

The first expansion also replaced declared arenas with DynType when a
constructor RHS could not be inferred. The separate
[projection-loss investigation](pcc1-native-arena-projection-loss-after-field-discovery.md)
records the observed native regression, minimal type-preservation correction
and controlled restoration. Constructor/declaration precedence and dataclass
signatures remain correct; unknown RHS preserves an established precise type.

v76 passes the full contextual getter ratchet and source-checked pcc1/pcc2
execution canaries. Frozen GC0 Stage2 and Stage3 pass and pcc2/pcc3 are raw
byte-identical. [Evidence066](../goal/evidence/PERF-P0-PROVEN-NATIVE-DATA-PLANE-PARALLEL-EMIT/066-span-foundation-frozen-stages.md)
records source/binary identities and receipts. No.1 is CONFIRMED through the
fixed point; fallback baseline shards remain before task-level closure.
