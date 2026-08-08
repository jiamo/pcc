# LLVM statepoint and stack-map reference for pcc

This note records the precise-safepoint design mined for
`LLVMREF-P1-STATEPOINT-STACKMAPS`. It is a reference and gap analysis, not a
claim that pcc emits LLVM statepoints today.

## Pinned upstream

The local reference tree is
`~/pcc_refs/llvm-project-20.1.8-full-depth1` at detached revision
`87f0227cb60147a26a1eeb4fb06e3b505e9c7261`. Line references below are to that
tree and revision.

Primary sources:

- `llvm/docs/Statepoints.rst`
- `llvm/docs/GarbageCollection.rst`
- `llvm/docs/StackMaps.rst`
- `llvm/lib/Transforms/Scalar/RewriteStatepointsForGC.cpp`
- `llvm/lib/CodeGen/StackMaps.cpp`

## Design extracted from LLVM

### Safepoints and the relocation invariant

LLVM distinguishes the coordination protocol that stops threads from the
machine location whose state is parseable. A *statepoint* is the latter. A
collector needs every compiler-visible pointer copy, the allocation associated
with it, and the ability to update it
(`Statepoints.rst:29-96`).

The explicit IR contract is stronger than merely listing roots. Every
potentially relocated pointer gets a new SSA value, no reachable post-safepoint
use may refer to the old value, and the relocation operation must remain opaque
to optimization (`Statepoints.rst:128-164`). A `gc.statepoint` token ties the
original call, `gc.result`, and all `gc.relocate` results into one relocation
sequence (`Statepoints.rst:176-210`). LLVM states the key rule as: no static
path may contain an observably-after use of a pointer that could have moved
(`Statepoints.rst:490-519`).

This is the central lesson for pcc backend 3/4: a root being discoverable is
not sufficient if generated code can keep using an unhealed SSA/register copy
after a relocating safepoint.

### Liveness and intermediate values

Accurate GC requires compiler knowledge of live pointers in stack slots and
registers (`GarbageCollection.rst:93-116`). LLVM explicitly calls out the
`h(f(), g())` case: the result of `f()` must remain rooted while evaluating
`g()` (`GarbageCollection.rst:55-67`, `:259-269`).

`RewriteStatepointsForGC` computes block kill/live/live-in/live-out sets and a
per-statepoint live set (`RewriteStatepointsForGC.cpp:167-217`, `:310-329`).
Its backwards dataflow includes successor live-ins and PHI incoming operands,
then iterates to a fixed point (`RewriteStatepointsForGC.cpp:3200-3347`). The
instruction-local query walks backwards to immediately before the call and
excludes the call result itself (`RewriteStatepointsForGC.cpp:3350-3366`).

### Base and derived pointers

A derived pointer may be interior or even exterior to its allocation, so its
numeric address cannot reliably identify the owning object. LLVM therefore
records a base/derived relation; both operands must be live over the safepoint
(`Statepoints.rst:311-337`). The rewrite pass produces one `(derived, base)`
entry for every live pointer and asserts dominance of the inferred base
(`RewriteStatepointsForGC.cpp:1300-1321`). `gc.relocate` indexes the base and
derived entries separately (`RewriteStatepointsForGC.cpp:1496-1560`).

The reference also warns that unmanaged `ptrtoint`/`inttoptr` relationships
break abstract base inference (`Statepoints.rst:736-748`). pcc must not infer a
managed base from arbitrary `c_ptr` bits.

### Rewrite placement and rematerialization

The rewrite pass is intended after SSA construction and late in the optimizer
pipeline (`Statepoints.rst:575-609`). It can rematerialize cheap derived-pointer
chains rather than keeping them in the relocation set; invokes pay the cost on
both normal and unwind paths (`RewriteStatepointsForGC.cpp:2536-2604`). Its
fallback implementation materializes live values in entry allocas, stores each
relocated definition after a statepoint, reloads before uses, and can poison
unrelocated values for debugging (`RewriteStatepointsForGC.cpp:2017-2145`).

LLVM documents exceptional-edge relocation as a known limitation in this
pinned design (`Statepoints.rst:776-783`). pcc must retain explicit
normal/error/finally exit tests even if it later adopts PC-indexed maps.

### Stack-map encoding

Statepoint live locations are emitted in a dedicated object-file section
(`Statepoints.rst:428-488`). The generic version-3 format records:

- function address, stack size, and record count;
- statepoint ID and instruction offset;
- each value as Register, Direct frame address, Indirect spill, Constant, or
  ConstantIndex;
- DWARF register number, size, offset, and live-out registers.

The exact layout is specified at `StackMaps.rst:313-415`. Mach-O uses
`__LLVM_STACKMAPS,__llvm_stackmaps`; ELF uses `.llvm_stackmaps`
(`StackMaps.rst:419-435`). `Direct` means the frame address itself, whereas
`Indirect` means load the value from the recorded address
(`StackMaps.rst:480-509`).

The implementation converts machine operands to register/direct/indirect/
constant locations (`StackMaps.cpp:206-291`), serializes GC base/derived pairs
in pair order (`StackMaps.cpp:416-483`), binds each record to a function-relative
instruction offset and final frame size (`StackMaps.cpp:485-529`), and emits
header, function, constant, and callsite tables with 8-byte record alignment
(`StackMaps.cpp:581-747`). The format deliberately leaves source-level meaning
to the runtime; order and IDs are the stable association
(`StackMaps.rst:437-456`).

LLVM notes that statepoint generation is target-specific and supported there
for AArch64 and x86-64 (`Statepoints.rst:725-730`). This matches pcc's two
self-backend target directions, but it does not make LLVM the production owner.

## Gap table: LLVM concepts versus pcc today

| Concept | LLVM reference contract | Current pcc counterpart | Backend 3/4 gap |
|---|---|---|---|
| Safepoint identity | Function-relative instruction offset plus statepoint ID | `pcc_thread_stop_requested` poll and `pcc_thread_safepoint()` calls at selected function/loop boundaries (`pcc/py_frontend/codegen/core_helpers.py:116-145`) | No PC-indexed record identifies the exact machine state at a poll/call. |
| Live pointer discovery | Backwards SSA liveness, including PHI edges and intermediate expression results | Lowering explicitly allocates/root-registers object locals and temporary containers (`pcc/py_frontend/codegen/ownership_lowering.py:636-723`) | Coverage depends on every lowering path manually materializing each live temporary; registers/SSA values are not discovered from final machine code. |
| Frame map | Per-callsite register/direct/indirect locations plus function frame size | v0 map is one signed `int32` slot count; positive means owning and negative borrowed, followed by contiguous pointer slots (`pcc/py_runtime/include/py_runtime.h:212-220`) | No sparse slots, register locations, derived pairs, instruction offset, or final frame-size contract. |
| Root lifetime | Only values live across a particular statepoint appear | `pcc_gc_frame_enter/leave` registers entry allocas for a whole lexical activation (`pcc/py_frontend/codegen/ownership_lowering.py:520-588`, `:676-731`) | Safe when complete, but less precise and vulnerable to a missing manual root; cannot independently audit liveness at each safepoint. |
| Relocated SSA value | Every post-statepoint use consumes `gc.relocate` or a rematerialized value | backend 3/4 loads use `pcc_gc_load_ptr`; root/object stores use `pcc_gc_store_ptr`/`pcc_gc_store_root`, which resolve forwarding and apply barriers (`pcc/py_runtime/py/py_obj.py:393-540`) | A managed pointer retained only in an SSA/register copy can remain stale; correctness currently relies on pinning or reloading from an updateable slot. |
| Base/derived relation | Stack map emits paired base and derived locations | Heap object slots have an explicit owner for barriers; unsafe raw pointers are a separate compiler ABI (`pcc/py_runtime/include/py_runtime.h:116-126`) | No machine-level base/derived map for interior pointers. Raw `c_ptr` must remain excluded or be tied to an explicit pinned-owner protocol. |
| Exceptional edge | Relocations must dominate normal and unwind uses | Lowering patches owned cleanup and `frame_leave` into normal/error exits | No relocation identity proves the exceptional successor reloads every moved live value; LLVM's own limitation reinforces the need for pcc-specific gates. |
| Suspended continuation | Runtime can interpret a PC/location record independently of current execution | continuation ABI stores a frame map, slot array, and resume PC (`pcc/py_runtime/include/py_runtime.h:1039-1044`) | The saved map is still count-based, not PC-indexed final-machine liveness; register-only state cannot be reconstructed or rewritten. |
| Object format | Mach-O/ELF dedicated stack-map section | No production pcc PC-indexed stack-map section | Both self object writers/linkers need deterministic section emission, relocation, merge, and final-image validation. |

## Mode-labeled conclusion

Today pcc has an explicit, runtime-maintained root-slot protocol shared by all
five GC backends. In backend 0/1/2 it supplies root reachability; in moving
backend 3/4 it additionally supplies updateable locations and load/store
healing. This is **not** LLVM statepoint parity and does not prove that every
register/SSA temporary live at every native safepoint is discoverable or
relocated.

The useful adoption boundary is not “switch to LLVM statepoints.” It is to own
the same invariant in pcc's frontend and self backend: deterministic safepoint
IDs, final-machine liveness locations, base/derived provenance, and a verifier
that rejects stale post-safepoint uses. LLVM remains the differential oracle.

## Concrete follow-up row proposal

Proposed id: `GC-P1-PC-INDEXED-PRECISE-STACKMAP-ABI`.

Proposed boundary: design and implement a versioned, deterministic pcc-owned
PC-indexed stack-map ABI for AArch64 Darwin and x86-64 Linux. Start with already
materialized object/root slots and final machine locations; do not remove the
existing frame-root protocol until differential gates prove equivalent root
sets. Add explicit base/derived provenance for any managed interior pointer,
reject unclassified raw pointers, and verify that no stale managed SSA value is
used after a relocating safepoint. Teach both object writers/linkers to retain
and validate the section. This row must not add an LLVM runtime dependency.

Proposed focused gates:

1. same source compiled by LLVM oracle and self backend yields the same live
   managed-root identities at function-entry, loop-backedge, call, exception,
   and suspended-continuation safepoints;
2. backend 3/4 forced relocation rewrites stack-slot and register-spilled live
   roots, while backend 0/1/2 preserve behavior;
3. derived-pointer tests require an explicit base and reject raw-pointer
   ambiguity before object emission;
4. Mach-O and ELF parsers validate deterministic IDs, instruction offsets,
   location kinds, frame bounds, and corrupted/truncated sections;
5. pcc1 -> pcc2 -> pcc3 remains byte-stable with LLVM used only as oracle.

