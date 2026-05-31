# Investigation: cycle GC clears referents before resurrection finalizers

## Status
resolved

## Problem Description
Two remaining resurrection xfails show that pcc's cycle collector does not
preserve CPython-style resurrection semantics for unreachable cycles:

- when `__del__` resurrects `self`, objects reachable from `self` must remain
  reachable too;
- a resurrecting object must not count as collected in the collection that
  resurrected it, and dropping the resurrected external reference later must
  allow the object to be reclaimed without running `__del__` a second time.

Current backend #0 clears referents for every unreachable object before
dispatching instance deallocators/finalizers. That makes `self.cargo` disappear
before user `__del__` has a chance to resurrect `self`, and it also counts a
resurrected object as collected in the same pass.

## Repro
Focused resurrection gate:

```bash
env -u LC_ALL /opt/homebrew/bin/timeout 300s uv run pytest \
  tests/test_gc_resurrection.py::test_resurrection_is_transitive \
  tests/test_gc_resurrection.py::test_resurrection_does_not_block_other_cleanup \
  -q -n0 --runxfail -ra
```

Expected current result before the fix: both nodes fail.

## Test [CONFIRMED]
Observed locally on 2026-05-08:

- `test_resurrection_is_transitive` fails at runtime with
  `AttributeError: cargo`.
- `test_resurrection_does_not_block_other_cleanup` prints `1` for the
  collection that should report `0` because `Z.__del__` resurrected the object.

## Proposals
- No.1 Confirm resurrection failure shape     [CONFIRMED]
- No.2 Finalize unreachable user objects before clearing referents     [CONFIRMED]

## No.1 Confirm resurrection failure shape
### Code Change
No code change. Run the two focused resurrection xfail nodes with `--runxfail`
to keep the failures visible.
### CONFIRMED
The focused gate reproduces the same two failures recorded in
`docs/investigations/gc-xfail-closure-audit.md`: referents are already cleared
before a resurrected object is inspected, and a resurrected object is counted
as collected during the pass that made it reachable again.

## No.2 Finalize unreachable user objects before clearing referents
### Code Change
`pcc/py_runtime/src/py_obj_gc.c` now factors the update-refs/subtract-refs/
mark phase into a reusable reachability recomputation step. After building the
original unreachable candidate list, backend #0 dispatches user finalizers for
unreachable instances before clearing referents. If any finalizer actually ran,
the collector recomputes reachability over the current tracked-object graph.

Objects made reachable again by a finalizer, plus anything reachable from
them, skip the referent-clear and dealloc loops and are not counted as
collected in that pass. The pcc-Python runtime port in
`pcc/py_runtime/py/py_obj_gc.py` mirrors the same control flow for bootstrap
self-runtime parity.
### CONFIRMED
The two focused xfail nodes now pass with `--runxfail`:

```bash
env -u LC_ALL /opt/homebrew/bin/timeout 300s uv run pytest \
  tests/test_gc_resurrection.py::test_resurrection_is_transitive \
  tests/test_gc_resurrection.py::test_resurrection_does_not_block_other_cleanup \
  -q -n0 --runxfail -ra
```

Observed result:

```text
2 passed in 24.55s
```

The xfail markers were removed from both tests, and the full resurrection file
now reports:

```text
6 passed in 3.59s
```

## Report (only when the investigation is closing)
The confirmed fix landed in backend #0 and the pcc-Python runtime port. It
keeps referents intact while user `__del__` runs, then recomputes reachability
before any clear/dealloc work. This is more robust than checking only whether
the finalized object's refcount increased, because a finalizer can change
multiple references and can resurrect an object through an already tracked
root container.

Validation after the fix:

```text
tests/test_gc_resurrection.py: 6 passed in 3.59s
original 14-node GC xfail closure list: 2 failed, 12 passed in 13.40s
tests/test_llvm_capi_ir_parity.py tests/test_llvm_capi_end_to_end.py: 23 passed in 0.14s
tests/test_fallback_baseline.py tests/test_ir_py_fallback_baseline.py: 11 passed in 50.02s
tests/test_py_multi_file_compile.py tests/test_py_multi_file_bootstrap_shim.py: 70 passed in 139.22s
tests/test_gc_*.py: 193 passed, 2 xfailed in 158.95s
```

The remaining GC xfail debt is not resurrection: it is module-global shutdown
finalization and trashcan processing for deep `__del__` chains.
