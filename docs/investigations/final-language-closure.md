# Final language closure

This bundle closes the last six goal.md items after the GC/backend and B/D
runtime closure bundles.

## No.17 layer1.py split / ownership

`scripts/check_layer1_ownership.py` enforces that `layer1.py` remains a small
façade and that ownership documentation exists.

## No.31 D8 dynamic import + introspection

`pcc.py_stdlib.importlib` provides `import_module`, `reload`, and
`invalidate_caches`. The compiled acceptance test imports a module by string
name and exercises `getattr`, `hasattr`, `type(...).__name__`, and
`inspect.getdoc/isfunction`.

## No.33 T1 metaclasses

The compiled acceptance test covers `class C(metaclass=M)`,
`type(name, bases, dict)`, `Enum/auto`, and `ABCMeta` abstract method tracking.

## No.34 T2 typing runtime

`typing.Generic` and `typing.Protocol` now have runtime class behavior, and
`runtime_checkable` protocols support `isinstance` checks.

## No.35 T3 mutable dataclass setattr

The compiled acceptance test verifies default-None dataclass fields do not
share slots and can be assigned independently.

## No.36 T4 weakref.ref

The native acceptance test verifies runtime weakrefs are callable and clear to
`None` after target deallocation/collection under the relocating backend.

## Gate

```bash
bash scripts/run_final_language_closure_gate.sh
```
