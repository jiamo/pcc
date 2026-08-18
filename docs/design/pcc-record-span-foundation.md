# Record-span foundation and generic ABI prerequisites

This is the bounded prerequisite of the native emission-buffer plan, not the
full helper-list migration. The parent task remains open after these APIs pass.

## Requirements

R1. `CompilerRecordSpanArena` stores sequence roots and immutable concatenation
nodes in `CompilerIntArena`. A `CompilerInt2` handle is relative to its selected
arena (like kernel-local value IDs), not globally unique across independent
owners. Append, extend, self-extend and source mutation after extend preserve
sequence order and snapshot semantics.

R2. Reset invalidates old handles and cursors for that arena. Closed arenas
reject further operations. Tree replay uses an explicit native cursor stack,
not recursion or a Python generator. Independent cursor snapshots remain valid
until reset/close.

R3. Normal cursor replay does not materialize tuple/list/dict records. The
explicit `diagnostic_values` adapter increments its projection counter. The
CPython storage path is an oracle; an executable native test must assert the
real arena storage is native and projection count stays zero.

R4. Negative record IDs, invalid/stale indexes, cyclic child references and
virtual length overflow fail closed. The implementation's explicit per-span
limit is 2^31-1 records. Failed validation does not mutate a published span.
Owned scalar arenas/cursors are closed on success and failure by their owner.

R5. Method ABI matching accepts structurally equal anonymous aggregates and
arrays, preserving packing, field order/width, array count and pointer address
spaces. Identified structs are not equated merely by layout. Semantic argument
marshalling and managed/raw ownership classification remain authoritative.

R6. Export, inference and runtime layout share the method-field assignment walk
through if/else, loops/else, with and try/handler/else/finally. Nested function
and class scopes are excluded. Unpack targets and declared slot order remain
intact. Non-constructor writes add missing fields without replacing established
constructor/declaration types with cleanup sentinels.

R7. Contextual failures retain module and exception detail, including a durable
error file when an IR output directory was requested. A `-1` alone is not a
sufficient failure receipt.

R8. Focused regressions, existing ABI/field compatibility, native executable
output and full compiler contextual closure must pass. Current-source pcc1 and
the required fixed point remain necessary before the corresponding task rows
are complete; host-built native execution alone is a bounded prerequisite.

## Explicit open integration

The span arena is not yet the production helper output carrier or attached to
the emission kernel. Helper instruction lists/placeholders, residual text,
normal ASM publication and verifier/CFG/def-use remain in the parent task.
No end-to-end performance or completed native-data-plane claim follows from
this foundation. v76 passes native canaries and the source-frozen GC0 exact
fixed point; evidence066 retains the remaining baseline and integration work.
