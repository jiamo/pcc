# LLVM statepoint/stack-map reference audit

Mode: attributed design/reference audit only. No runtime, frontend, backend,
object-writer, or linker implementation is claimed here.

The pinned LLVM 20.1.8 sources were mapped in
`docs/refs_docs/gc-research/llvm-statepoint/README.md`. The note records:

- statepoint relocation and stale-SSA invariants;
- backwards liveness including PHI edges and intermediate values;
- base/derived pointer provenance;
- normal and exceptional relocation placement;
- version-3 stack-map location kinds and Mach-O/ELF sections; and
- a concept-by-concept gap table against pcc frame roots, safepoints,
  `pcc_gc_load_ptr`, `pcc_gc_store_ptr`, and continuation maps.

The finite implementation follow-up is normalized into the task board as
`GC-P1-PC-INDEXED-PRECISE-STACKMAP-ABI`. It preserves the existing slot-based
five-GC contract until differential evidence proves an equivalent final-machine
location map, and it keeps LLVM as an oracle rather than a production owner.

No executable gate was run in the implementation-only phase. The metadata
validator is the only completion gate for this study-only row.
