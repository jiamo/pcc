# Investigation: Backend #4 relocate copy must be single-use

## Status
resolved

## Problem Description
`pcc_gc_relocate_copy()` should not copy the same source object more than once.
Once a relocation copy installs a forwarding entry, a second copy of the same
source can create another object with the same stable ID and update the
forwarding target away from the first copy.

## Repro
Run:

```bash
env -u LC_ALL /opt/homebrew/bin/timeout 120s uv run pytest 'tests/test_gc_backend_relocating.py::test_colored_relocating_copy_consumes_relocation_entry' -q -n0
env -u LC_ALL /opt/homebrew/bin/timeout 120s uv run pytest 'tests/test_runtime_substrate_spike.py::test_pcc_python_relocate_copy_consumes_relocation_entry' -q -n0
```

Expected current failure before the fix:

```text
False
1
False
```

The correct behavior is:

```text
False
0
True
```

## Test [CONFIRMED]
Both pytest nodes listed in `## Repro` failed before the fix.

Observed C runtime output:

```text
False
1
False
```

Observed pcc-Python archive output:

```text
first_null=0
still_selected=1
second_null=0
```

## Proposals
- No.1 Consume relocation entries after successful copy     [CONFIRMED]

## No.1 Consume relocation entries after successful copy
### Code Change
Reject `pcc_gc_relocate_copy()` when the source already has a forwarding entry,
and remove the source from the relocation set after a successful copy/forward.
Apply the same behavior to the pcc-Python runtime mirror.
### CONFIRMED
Implemented in `pcc/py_runtime/src/py_gc_backend.c` and
`pcc/py_runtime/py/py_gc_backend.py`.

Both runtimes now:

- refuse to add already-forwarded objects to the relocation set;
- refuse `pcc_gc_relocate_copy()` on already-forwarded source objects;
- remove the source from the relocation set after a successful copy/forward.

Confirmed with:

```bash
env -u LC_ALL /opt/homebrew/bin/timeout 180s make -B -C pcc/py_runtime libpy_runtime.a
PATH="$PWD/.venv/bin:$PATH" env -u LC_ALL /opt/homebrew/bin/timeout 300s make -B -C pcc/py_runtime libpy_runtime_pcc_py.a
env -u LC_ALL /opt/homebrew/bin/timeout 120s uv run pytest 'tests/test_gc_backend_relocating.py::test_colored_relocating_copy_consumes_relocation_entry' -q -n0
env -u LC_ALL /opt/homebrew/bin/timeout 120s uv run pytest 'tests/test_runtime_substrate_spike.py::test_pcc_python_relocate_copy_consumes_relocation_entry' -q -n0
env -u LC_ALL /opt/homebrew/bin/timeout 180s uv run pytest tests/test_gc_backend_relocating.py tests/test_gc_abstraction_surface.py -q -n0
env -u LC_ALL /opt/homebrew/bin/timeout 180s uv run pytest tests/test_runtime_substrate_spike.py -q -n0
env -u LC_ALL /opt/homebrew/bin/timeout 180s uv run pytest 'tests/test_py_multi_file_bootstrap_shim.py::MultiFileBootstrapShimTests::test_py_gc_backend_runtime_file_compiles_without_libpython_fallback' -q -n0
env -u LC_ALL /opt/homebrew/bin/timeout 300s uv run pytest tests/test_gc_*.py -q -n0
```

Observed results:

```text
libpy_runtime.a rebuilt
libpy_runtime_pcc_py.a rebuilt
1 passed in 1.01s
1 passed in 0.35s
20 passed in 9.10s
31 passed in 6.36s
1 passed in 1.42s
143 passed, 25 xfailed, 15 xpassed in 142.50s
```

Formatting note: `env -u LC_ALL uv run black pcc/py_runtime/py/py_gc_backend.py tests/test_runtime_substrate_spike.py tests/test_gc_backend_relocating.py`
could not run because this environment does not expose `black` in the active
pyenv/uv path.

## Report (only when the investigation is closing)
No.1 landed. The chosen fix keeps the relocation primitive conservative:
forwarding is installed once, the relocation-set entry is consumed, and future
selection skips already-forwarded objects. This prevents duplicate copies and
duplicate stable IDs without changing the public forwarding-table ABI.

Backend #4 remains a research backend. This closes a correctness gap in the
restricted copy primitive, not the larger page evacuation or container
reference-update work.
