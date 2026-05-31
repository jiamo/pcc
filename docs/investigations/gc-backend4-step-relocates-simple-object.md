# Investigation: Backend #4 step relocates simple objects

## Status
resolved

## Problem Description
Backend #4 `pcc_gc_step()` currently selects relocation candidates but does not
copy them. The restricted copy primitive exists, so a bounded step should be
able to select and relocate simple unpinned objects, leaving the load barrier to
repair slots lazily through the forwarding table.

## Repro
Run:

```bash
env -u LC_ALL /opt/homebrew/bin/timeout 120s uv run pytest 'tests/test_gc_backend_relocating.py::test_colored_relocating_step_copies_selected_simple_object' -q -n0
env -u LC_ALL /opt/homebrew/bin/timeout 120s uv run pytest 'tests/test_runtime_substrate_spike.py::test_pcc_python_relocating_step_copies_simple_object' -q -n0
```

Expected current failure before the fix:

```text
step only selects a relocation candidate; no forwarding copy is installed
```

The correct behavior is that `pcc_gc_step(2)` performs one selection unit and
one copy unit for a simple object, increments relocation-forward telemetry, and
lets `pcc_gc_load_ptr()` repair the slot to the moved object.

## Test [CONFIRMED]
Both pytest nodes listed in `## Repro` failed before the fix.

Observed behavior:

```text
step=1
forwards=0
same_old=1
same_id=1
barrier_forwards=0
```

## Proposals
- No.1 Relocate selected simple objects during backend #4 step     [CONFIRMED]

## No.1 Relocate selected simple objects during backend #4 step
### Code Change
Add a bounded relocation-step helper that copies objects already in the
relocation set, releases the step-owned destination reference, and update
backend #4 `pcc_gc_step()` to run existing relocation work, select more
candidates, then relocate newly selected simple objects within the same budget.
Apply the same behavior to the pcc-Python runtime mirror.
### CONFIRMED
Implemented in `pcc/py_runtime/src/py_gc_backend.c` and
`pcc/py_runtime/py/py_gc_backend.py`.

The final version keeps Backend #4 relocation conservative:

- relocation selection skips unsupported container/reference-bearing object
  tags and only selects the simple tags supported by `pcc_gc_relocate_copy()`;
- relocation selection skips forwarding sources and forwarding targets, so a
  `gc.collect()` loop does not repeatedly relocate each new copy;
- `pcc_gc_step()` first drains existing relocation work, then selects more
  candidates, then relocates newly selected simple objects within the same
  budget.

Confirmed with:

```bash
env -u LC_ALL /opt/homebrew/bin/timeout 180s make -B -C pcc/py_runtime libpy_runtime.a
PATH="$PWD/.venv/bin:$PATH" env -u LC_ALL /opt/homebrew/bin/timeout 300s make -B -C pcc/py_runtime libpy_runtime_pcc_py.a
env -u LC_ALL /opt/homebrew/bin/timeout 120s uv run pytest 'tests/test_gc_backend_relocating.py::test_colored_relocating_step_copies_selected_simple_object' -q -n0
env -u LC_ALL /opt/homebrew/bin/timeout 120s uv run pytest 'tests/test_runtime_substrate_spike.py::test_pcc_python_relocating_step_copies_simple_object' -q -n0
env -u LC_ALL /opt/homebrew/bin/timeout 180s uv run pytest tests/test_gc_backend_relocating.py tests/test_gc_abstraction_surface.py -q -n0
env -u LC_ALL /opt/homebrew/bin/timeout 180s uv run pytest tests/test_runtime_substrate_spike.py -q -n0
env -u LC_ALL /opt/homebrew/bin/timeout 180s uv run pytest 'tests/test_py_multi_file_bootstrap_shim.py::MultiFileBootstrapShimTests::test_py_gc_backend_runtime_file_compiles_without_libpython_fallback' -q -n0
PCC_GC_BACKEND=4 env -u LC_ALL /opt/homebrew/bin/timeout 180s uv run pytest tests/test_gc_g1_cycle_collector.py tests/test_gc_g2_finalizers.py -q -n0
env -u LC_ALL /opt/homebrew/bin/timeout 300s uv run pytest tests/test_gc_*.py -q -n0
```

Observed results:

```text
libpy_runtime.a rebuilt
libpy_runtime_pcc_py.a rebuilt
1 passed in 0.84s
1 passed in 0.37s
21 passed in 9.88s
32 passed in 7.27s
1 passed in 1.49s
9 passed, 1 xfailed, 5 xpassed in 9.11s
144 passed, 25 xfailed, 15 xpassed in 149.22s
```

Formatting note: `env -u LC_ALL uv run black pcc/py_runtime/py/py_gc_backend.py tests/test_runtime_substrate_spike.py tests/test_gc_backend_relocating.py`
could not run because this environment does not expose `black` in the active
pyenv/uv path.

## Report (only when the investigation is closing)
No.1 landed. Backend #4 steps can now perform bounded simple-object movement
instead of only marking candidates, while still avoiding the unsafe parts of a
real page-relocating collector: containers and reference-bearing objects remain
out of the relocation set until reference-update semantics exist.

This is still not production ZGC-style relocation. The remaining gaps are page
evacuation selection, container/reference rewriting, and fragmentation control.
