# Investigation: resurrection test class attributes lower through libpython

## Status
resolved

## Problem Description
`tests/test_gc_resurrection.py::test_resurrection_only_happens_once_per_object`
does not reach resurrection runtime semantics because the Python frontend
generates `py_cpy_*` calls and the libpython-off gate rejects compilation.

The source uses class-level state in the finalizer path:

- `Lazarus.resurrected = Lazarus.resurrected + 1`
- `Lazarus.stash.append(self)`
- `Lazarus.stash.clear()`

This likely overlaps the python-types roadmap class-variable task, but it
is also a GC blocker because resurrection cannot be validated while this
program needs libpython fallback.

## Repro
Run the focused xfailed node with xfail disabled:

```bash
/opt/homebrew/bin/timeout 300s env -u LC_ALL uv run pytest \
  tests/test_gc_resurrection.py::test_resurrection_only_happens_once_per_object \
  -q -n0 --runxfail -ra
```

Expected current result: one compile-time failure. The pipeline raises
`PyPipelineError` because generated IR still calls `py_cpy_*`.

## Test [CONFIRMED]
The command above was run on 2026-05-08 and produced:

```text
1 failed in 0.46s
```

Failure marker:

```text
Python pipeline requires libpython fallback ... generated IR still calls py_cpy_* helpers
```

## Proposals
- No.1 Add native dyn-list `clear()` dispatch     [CONFIRMED]

## No.1 Add native dyn-list `clear()` dispatch
### Code Change
Add `clear` to the `DynType` list-method native dispatch whitelist.
The underlying `py_list_clear` helper already existed; the missing part
was routing a dynamically typed receiver such as `Lazarus.stash.clear()`
to the native list method path instead of CPython fallback.

### CONFIRMED
IR-only reductions showed the fallback source:

```text
read_int py_cpy_calls 0
write_int py_cpy_calls 0
list_append py_cpy_calls 0
list_clear py_cpy_calls 5
in_del py_cpy_calls 5
```

After adding dyn-list `clear`, the same reductions reported:

```text
read_int py_cpy_calls 0
write_int py_cpy_calls 0
list_append py_cpy_calls 0
list_clear py_cpy_calls 0
in_del py_cpy_calls 0
```

Focused command:

```bash
/opt/homebrew/bin/timeout 300s env -u LC_ALL uv run pytest \
  tests/test_gc_resurrection.py::test_resurrection_only_happens_once_per_object \
  -q -n0 --runxfail -ra
```

Observed result:

```text
1 passed in 0.71s
```

The xfail marker was removed from that test. The containing file now
reports:

```text
4 passed, 2 xfailed in 3.82s
```

## Report (only when the investigation is closing)
The confirmed root cause was not resurrection semantics. The test was
blocked earlier by `Lazarus.stash.clear()`: the receiver was a `DynType`
class attribute holding a native list, and the dyn-list method whitelist
omitted `clear`, causing `py_cpy_getattr`/`py_cpy_call_noargs` emission.

After routing `clear()` to `py_list_clear`, the test compiles and passes
without libpython fallback. The remaining resurrection xfails are runtime
GC semantics: transitive resurrection and isolation of resurrecting
objects from non-resurrecting cleanup.
