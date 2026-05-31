# OpenJDK Valhalla upstream snapshot

This directory is a pinned reference pack for pcc's Valhalla-inspired Python
value model work.

## Upstream

| Field | Value |
|---|---|
| Repository | `https://github.com/openjdk/valhalla` |
| Branch | `lworld` |
| Commit | `b13d505a7be19e4c5348ef91b9517bcaafd56649` |
| Commit date | 2026-05-15T22:11:35Z |
| Commit subject | `8384759: [lworld] fix recently introduced typo in ObjectFree event spec` |
| Fetched | 2026-05-18 |

`MANIFEST.json` is the authoritative file list. It records the upstream path,
raw URL, SHA-256, and byte count for every copied file.

## Why `lworld`

OpenJDK Valhalla has several historical and early-access branches. The `lworld`
branch is the active implementation branch for the flattened/value-object model
that pcc wants to study. The repository `master` branch is less useful for this
task because it does not carry the same Valhalla implementation surface.

## Scope

This is not a full OpenJDK mirror. It intentionally copies the files pcc authors
need to reread while designing and implementing value-like Python objects:

| Area | Representative files |
|---|---|
| VM/classfile boundary | `src/hotspot/share/include/jvm.h`, `classFileParser.*`, `fieldLayoutBuilder.*` |
| Object model | `inlineKlass.*`, `inlineOop.hpp`, `valuePayload.*`, `layoutKind.*`, `markWord.*` |
| Flattened arrays | `flatArrayKlass.*`, `flatArrayOop.*`, `arrayKlass.*` |
| Runtime semantics | `interpreterRuntime.cpp`, `jvm.cpp`, `unsafe.cpp`, `synchronizer.*`, `reflection.*` |
| Compiler model | `inlinetypenode.*`, `parse2.cpp`, `parse3.cpp`, `graphKit.*`, `library_call.*`, `type.*` |
| Calling convention | `sharedRuntime.*`, `deoptimization.*`, `cpu/aarch64/sharedRuntime_aarch64.cpp`, `cpu/x86/sharedRuntime_x86_64.cpp` |
| Java surface | `Class.java`, `Object.java`, `Objects.java`, `IdentityException.java`, `ValueObjectMethods.java` |
| Internal Java helpers | `jdk/internal/value/ValueClass.java`, `NullRestricted.java`, `LooselyConsistentValue.java` |
| Javac path | `Flags.java`, `JavacParser.java`, `Attr.java`, `Check.java`, `ClassReader.java`, `ClassWriter.java` |
| Executable specs | selected Valhalla runtime/compiler tests under `test/hotspot/jtreg/.../valhalla/inlinetypes/` |

## pcc status boundary

These files describe the upstream Java/JVM design. They do not imply pcc should
copy Java syntax, classfile flags, or HotSpot internals directly.

For pcc, the useful architectural split is:

| Valhalla concept | pcc analogue to design |
|---|---|
| value class | opt-in `ValueClassType` for immutable Python classes |
| flattened field / flat array | pcc runtime layout optimization after semantics are proven |
| object projection | boxing a `ValuePayload` into a pcc runtime object |
| scalarized inline type node | LLVM/native aggregate passed without heap identity |
| substitutability / `acmp` rewrite | identity-free equality boundary and diagnostics |
| identity exceptions | rejecting `is`, `id`, weakref, synchronization-like identity use on unboxed values |

Keep `DynType` separate. `DynType` remains pcc's boxed/dynamic fallback; it is
not the Valhalla-like value representation.
