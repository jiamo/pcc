# Native emission fragments

Status: implementation proposal; no production helper migration claimed.
Parent: native emission-buffer R3 in
[the buffer plan](pcc-native-aarch64-emission-buffer.md).
Prerequisite: [record spans](pcc-record-span-foundation.md), qualified by
v76 native canaries and Stage2; fixed-point validation remains pending.

## Architectural boundary

The static review from prologue and dense indexed-block roots finds 152
list-bearing helper definitions and 711 syntactic callsites. These counts
describe conservative source reachability, not measured execution frequency.
The native loop has eight fragment intake seams: prologue, entry labels,
ordinary instructions, safepoint suffixes, inline error edges, terminator
prefixes, terminators and deferred cold stubs.

The full producer-chain migration is the architectural work. A packed-stackmap
vertical establishes its API and lifetime contract; that small family is not
the solution to the roughly 3x Stage2/Stage1 gap. Do not close the parent or
claim end-to-end speed from the prerequisite alone.

## Representation contract

R1. One module-owned, inventory-classified arena stores emission records and
owns a CompilerRecordSpanArena. A CompilerInt2 span handle is relative to this
owner. Words, labels, barriers and relocations have explicit record IDs;
publication never matches anonymous placeholders with a FIFO. Pointer-bearing
symbol spellings live in a named owning side table.

R2. Helpers append into a supplied span where composition does not require a
separate fragment. New fragments are reserved for actual ordering/composition
boundaries. In particular, do not allocate a span per leaf instruction: the
current span append uses up to six i64 node scalars before record storage.
Reset only after every function fragment, including cold stubs, is published.
Close all owned arenas and cursors on success and failure.

R3. Publication replays native IDs into the existing canonical module builder.
Final labels, alignments, fixups, compact unwind and precise stackmap offsets
retain one layout authority. Reuse the current opcode encoders and validation;
do not add a separate assembler or change register/GC semantics.

R4. A producer with optional handling returns an explicit boolean while
appending to a supplied span. False means unhandled; a successful empty
fragment is still handled. False must leave the destination span unchanged,
including when it already contains records. Test fallback dispatch into a
populated span so a later producer cannot inherit abandoned partial output.
Never return list/span unions or recover the
aggregate through dynamic ValueBox calls. The address helper's existing
tuple[list[str], str] becomes append-to-span plus a register-name result.

R5. Oracle/optimized text APIs remain explicit compatibility paths until they
are migrated. Machine words alone lose aliases and explicit-shift spelling;
normal ASM closure requires structured spelling/operand facts and bounded
serialization. Keeping the old text path preserves exactness during the PCO
vertical, but leaves normal ASM and residual producer text visibly open.

R6. Inventory and counters distinguish record publication, transient producer
text, final assembler fallback, helper containers and diagnostic projection.
No empty or short-lived list may be described as a completed native migration.
Ordinary Python list/class/identity semantics remain unchanged.

## First complete vertical

Convert all three packed-stackmap seams together: entry labels, safepoint
record labels/reloads, and terminator labels/nop padding. Use explicit owner
and span arguments to new append methods on FunctionStackMapPlan; replace the
three native_helper_lines allocations in the dense native loop.

The seven list-return helpers are `_reload_asm_lines_packed`,
`load_slot_to_reg_parts`, `store_reg_to_slot_parts`,
`emit_slot_base_address_parts`, `emit_add_offset`, `emit_const_to_reg` and
`emit_const_to_reg_bits`. This excludes the three append methods and five
string-returning primitives. The native path bypasses their global placeholder
capture and calls the canonical encoders. Pointer reloads use scalar type/width
facts instead of constructing TypeDesc objects per reload.
Cover short and large
frame offsets, positive/negative/zero derived offsets, multiword immediates,
ordered labels, terminator nop padding and repeated/independent fragments.
The x86/oracle paths retain their exact behavior and remain outside this
AArch64 production slice.

Gate the complete chain with source-shape counts, host ASM/PCO differential,
exact final stackmap offsets, stale/failed-owner cleanup, contextual direct-ABI
inspection and a pcc1-executed reload canary through the real worker boundary.
Measure representative native instructions/CPU/RSS before expanding. Then
migrate the remaining helper owners under the same representation contract;
do not stop the parent task after this vertical.
