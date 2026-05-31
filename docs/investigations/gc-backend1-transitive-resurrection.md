# Investigation: Backend #1 drops transitive resurrection cycle

## Status
resolved

## Problem Description
Under `PCC_GC_BACKEND=1`, `test_resurrection_is_transitive` no longer preserves
CPython-style resurrection. The default backend passes, but the tracing backend
prints `0` resurrected objects and then aborts when the test indexes the empty
resurrection list.

This is separate from the resolved default-backend ordering investigation in
`docs/investigations/gc-transitive-resurrection-clear-order.md`.

## Repro
Run:

```bash
env -u LC_ALL PCC_GC_BACKEND=1 /opt/homebrew/bin/timeout 240s uv run pytest tests/test_gc_resurrection.py::test_resurrection_is_transitive -q -n0 -rxX
```

Expected current failure before the fix:

```text
assert -6 == 0
stdout='0\n'
```

The default backend should pass:

```bash
env -u LC_ALL /opt/homebrew/bin/timeout 180s uv run pytest tests/test_gc_resurrection.py::test_resurrection_is_transitive -q -n0
```

## Test [CONFIRMED]
The failing Backend #1 baseline was observed with:

```bash
env -u LC_ALL PCC_GC_BACKEND=1 /opt/homebrew/bin/timeout 240s uv run pytest tests/test_gc_resurrection.py::test_resurrection_is_transitive -q -n0 -rxX
```

Observed result: the compiled program returned `-6` after printing `0`.

The default-backend control was observed with:

```bash
env -u LC_ALL /opt/homebrew/bin/timeout 180s uv run pytest tests/test_gc_resurrection.py::test_resurrection_is_transitive -q -n0
```

Observed result: `1 passed`.

## Proposals
- No.1 Compare Backend #1 finalizer path against default cycle collector     [CONFIRMED]
- No.2 Root class-attribute globals during module execution     [CONFIRMED]

## No.1 Compare Backend #1 finalizer path against default cycle collector
### Code Change
No source change. Compare smaller programs with and without the post-collect
`Lazarus.resurrected_with_cargo[0]` access.

### CONFIRMED
The failure was not simply "Backend #1 clears referents before running
`__del__`". A reduced program that only printed
`len(Lazarus.resurrected_with_cargo)` after `gc.collect()` reported `2`,
while adding a later `instance = Lazarus.resurrected_with_cargo[0]` changed
the earlier printed length to `0`.

Generated IR showed that the class attribute lives in a separate
`.classattr.<module>.Lazarus.resurrected_with_cargo` global. Existing module
root setup entered class globals like `.class.<module>.Lazarus`, but did not
enter class-attribute globals. Backend #1 could therefore sweep the class
attribute list depending on root/frame layout.

## No.2 Root class-attribute globals during module execution
### Code Change
Update `pcc/py_frontend/codegen/layer1.py` so
`_emit_class_global_root_enters()` emits root frames for every declared
`info.class_attrs` global as well as the class object global.

### CONFIRMED
The focused Backend #1 test now passes:

```bash
env -u LC_ALL PCC_GC_BACKEND=1 /opt/homebrew/bin/timeout 240s uv run pytest tests/test_gc_resurrection.py::test_resurrection_is_transitive -q -n0 -rxX
```

Observed result: `1 passed`.

Controls also passed:

```bash
env -u LC_ALL PCC_GC_BACKEND=1 /opt/homebrew/bin/timeout 300s uv run pytest tests/test_gc_resurrection.py -q -n0 -rxX
env -u LC_ALL /opt/homebrew/bin/timeout 180s uv run pytest tests/test_gc_resurrection.py -q -n0
env -u LC_ALL PCC_GC_BACKEND=1 /opt/homebrew/bin/timeout 300s uv run pytest tests/test_gc_effectiveness.py tests/test_gc_api.py -q -n0 -rxX
```

Observed results: `6 passed`, `6 passed`, and `43 passed`.

## Report (only when the investigation is closing)
The landed fix is No.2. Backend #1 did not have a finalizer dispatch failure
for this case; it had an incomplete root set. Class-body assignment storage is
lowered into standalone `.classattr.*` globals, and those object globals must
be roots just like module globals and class object globals. Once class
attribute globals are entered as roots, the resurrection list remains alive
through tracing collection and `__del__` can resurrect `self` transitively.
