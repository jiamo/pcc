# PCC Execution Ownership

PCC owns Python execution by compiling semantic Python into auditable native
artifacts while preserving explicit object, GC, backend, fallback, and
self-host boundaries.  Compiler self-host performance is part of this context:
the compiler must eventually consume the same value model it offers users.

## Language

**Semantic type**:
The Python meaning that remains stable across every physical representation;
for example, `int` remains arbitrary precision and an ordinary class retains
identity semantics.
_Avoid_: Machine type, storage type

**Value projection**:
An identity-free physical representation of a semantic type, used only where
PCC proves that identity, weak references, mutation, subclassing, dynamic
attributes, and finalizers are not observable.
_Avoid_: Unboxed class, raw struct shortcut

**Object projection**:
The identity-bearing heap representation used when a semantic value escapes or
requires ordinary Python object behavior.  Boxing connects value projection to
object projection without changing the semantic type.
_Avoid_: Slow object, fallback object

**Compiler native data plane**:
The indexed, arena-owned representation used by PCC's own parser and backend
passes so compiler-internal immutable data does not repeatedly enter generic
Python object projection.  Growth is fail-closed: every self-backend class is
classified in the record-inventory contract, every concrete class stays
visible to stage graphs, and diagnostic constructor sites plus runtime
projections remain explicitly owned, lazy, and counted.
_Avoid_: Compiler special case, unsafe fast mode

**Indexed Function Kernel**:
The compiler native data-plane Module for one parsed function.  It owns stable
block, value, type, and opcode IDs plus shared indexed analysis consumed by
stack preparation, liveness, stack maps, register allocation, and emission.
_Avoid_: ParsedFunction wrapper, pass-local cache

**Diagnostic projection**:
A lazy object projection materialized from the Indexed Function Kernel only
for diagnostics, public inspection, or an explicitly unsupported slow path.
_Avoid_: Normal instruction view, eager compatibility view

**Execution root**:
The implementation that actually parses, lowers, links, and runs the program.
LLVM, CPython, C sources, and external linkers may be oracles but are not
silently relabeled as PCC execution roots.
_Avoid_: Backend helper, hidden fallback
