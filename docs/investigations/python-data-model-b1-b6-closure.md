# Python data-model B1-B6 closure

This bundle turns the earlier B1-B6 runtime/ABI work into compiled
no-libpython acceptance gates.

## Covered items

### B1 bytes literal / native bytes

Compiled gate checks `b"..."`, `len`, indexing, slicing, and bytes equality.

### B2 type(x) / type(x).__name__

Compiled gate checks builtins and user instances.

### B3 class-level variables

Compiled gate checks base class read, inherited read, subclass shadowing, and
base mutation after shadowing.

### B4 user dunders

Compiled gate checks `__str__`, `__hash__`, `__iter__`, and `__next__`.

### B5 exception chaining / traceback frames

Compiled gate checks `raise ... from ...`, `__cause__`, and exception type/name
surfacing.

### B6 module attr writes / call splat

Compiled gate checks `f(*args, **kwargs)` and cross-module `module.attr`
read/write.

## Gate

```bash
bash scripts/run_b1_b6_closure_gate.sh
```

This gate intentionally compiles source programs with `libpython_mode="off"`.
It is not a parser-only or runtime-only test.
