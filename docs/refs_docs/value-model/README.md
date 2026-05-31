# value-model reference snapshots

Reference implementations for identity-free/value-object work in pcc's Python
frontend and runtime.

| Subdir | Upstream | Purpose |
|---|---|---|
| `valhalla/` | OpenJDK Project Valhalla `lworld` | Value classes, identity-free objects, flattened fields/arrays, scalarized calling convention, and substitutability semantics. |

These snapshots are source references, not vendored dependencies. Use them to
compare design and implementation choices before changing pcc's value model.
