# Investigation: Backend #4 needs stable relocation identity

## Status
resolved

## Problem Description
Backend #4 is the colored-relocating backend.  `goal.md` lists `id
indirection` as a required missing piece: `id(obj)` cannot use the object
address once relocation can move an object.  The current backend has a
forwarding side table and a load barrier, but no stable object identity that
survives forwarding from an old address to a relocated address.

## Repro
Run the focused backend #4 gate:

```bash
/opt/homebrew/bin/timeout 300s env -u LC_ALL uv run pytest tests/test_gc_backend_relocating.py::test_colored_relocating_stable_id_survives_forwarding -q -n0
```

Expected before the fix: the probe fails to link because
`pcc_gc_object_id` is not provided by the runtime.

## Test [CONFIRMED]
`tests/test_gc_backend_relocating.py::test_colored_relocating_stable_id_survives_forwarding`

Observed before the fix on 2026-05-07:

```text
ld: Undefined symbols:
  _pcc_gc_object_id, referenced from:
      _user_probe_main in probe-277c35.o
pcc.py_frontend.pipeline.PyPipelineError: clang link failed (exit 1)
```

## Proposals
- No.1 Add backend #4 stable identity side table     [CONFIRMED]

## No.1 Add backend #4 stable identity side table
### Code Change
Add a small runtime identity side table keyed by `PyObject *`, export
`pcc_gc_object_id(PyObject *)`, and make `pcc_gc_install_forwarding(from, to)`
copy the stable ID from `from` to `to`.  Mirror the same ABI in
`pcc/py_runtime/py/py_gc_backend.py` so the pcc-Python runtime archive keeps
building.
### CONFIRMED
Implemented a `PyObject * -> int64_t` identity side table in
`pcc/py_runtime/src/py_gc_backend.c`, exported
`pcc_gc_object_id(PyObject *)` through `py_runtime.h`, and copied the stable
ID from `from` to `to` during `pcc_gc_install_forwarding()`.  The
pcc-Python mirror defines the same globals and ABI in
`pcc/py_runtime/py/py_substrate.py` and `pcc/py_runtime/py/py_gc_backend.py`.

Observed verification:

```bash
/opt/homebrew/bin/timeout 300s env -u LC_ALL uv run pytest tests/test_gc_backend_relocating.py::test_colored_relocating_stable_id_survives_forwarding -q -n0
# 1 passed in 29.10s

/opt/homebrew/bin/timeout 300s env -u LC_ALL uv run pytest tests/test_gc_backend_relocating.py -q -n0
# 3 passed in 2.18s

/opt/homebrew/bin/timeout 180s env -u LC_ALL make -B -C pcc/py_runtime libpy_runtime.a
# passed; existing unused-function warning in py_gc_backend.c remains

/opt/homebrew/bin/timeout 420s env -u LC_ALL PATH="$PWD/.venv/bin:$PATH" make -B -C pcc/py_runtime libpy_runtime_pcc_py.a
# passed

/opt/homebrew/bin/timeout 420s env -u LC_ALL uv run pytest tests/test_gc_threading_substrate.py tests/test_gc_backend_*.py tests/test_gc_effectiveness.py -q -n0
# 47 passed, 3 xfailed, 3 xpassed in 41.02s

/opt/homebrew/bin/timeout 420s env -u LC_ALL PCC_GC_BACKEND=4 uv run pytest tests/test_gc_g1_cycle_collector.py tests/test_gc_g2_finalizers.py -q -n0 -rxX
# 9 passed, 1 xfailed, 5 xpassed in 9.45s
```

Black was attempted but unavailable in the active `uv`/pyenv environment:
`pyenv: black: command not found`.

## Report (only when the investigation is closing)
No.1 landed.  Backend #4 now has the first explicit identity indirection
needed by a moving collector: old addresses, forwarding targets, and
load-barrier-resolved pointers can all report the same stable runtime ID.
This is deliberately narrower than full ZGC movement; relocation set
selection and object copying remain separate backend #4 follow-up work.
