# Investigation: virtual-thread resume unboxes args/return for a boxed worker (i64 vs ptr LLVM type error)

## Status
resolved (2026-06-18)

## Problem Description
`tests/python/test_virtual_thread_frontend.py::test_virtual_thread_spawn_runs_direct_user_function`
and `::test_virtual_thread_import_from_aliases_run` failed: compiling a program
that does `vt.spawn(worker, 20, 21)` for a `worker(left: int, right: int) -> int`
raised an LLVM error (surfaced as "clang link failed"):
`'%m.int_unbox.5' defined with type 'i64' but expected 'ptr'`.

## Repro
```python
import pcc.virtual_thread as vt
def worker(left: int, right: int) -> int:
    return left + right + 1
def main() -> None:
    thread = vt.spawn(worker, 20, 21)
    print(vt.run(2, 8)); print(vt.state(thread)); print(vt.result(thread))
main()
# compile_python(..., ir_scaffold_mode="on", libpython_mode="off")  (PCC_RUNTIME_CC=cc)
```

## Test [CONFIRMED]
`tests/python/test_virtual_thread_frontend.py` 6/6 pass; the repro runs and prints
`1 / 4 / 42` (rc=0).

## Proposals

## No.1 Resume must pass args/return in the worker's actual calling convention

### Root cause
`_emit_virtual_thread_resume_function` (`native_virtual_thread.py`) built the call
to the spawned worker by `marshal.marshal_from_object(slot, formal.annotation)`
per argument and `marshal.marshal_to_object(result, ret_ty)` for the result. For
`int`/`float`/`bool` annotations that marshals to the *native* scalar (i64 etc.).
But a function passed to `vt.spawn` is a first-class value, so pcc lowers it with
the **boxed (ptr) calling convention** — `define ptr @user_vt_worker(ptr, ptr)`.
The resume therefore passed an `i64` where a `ptr` is declared (and on the return
side would `ptrtoint` an already-boxed result). The worker borrows its boxed
params (uses `ptrtoint`/tagged-int arithmetic; no incref/release of params).

### Code Change
In the resume, key off the worker's actual LLVM types:
- per arg: if `fn.args[idx].type` is a pointer, pass the boxed `slot_obj`
  straight through (worker borrows; the resume already owns and releases the
  slots after the call); else marshal to the annotation's native scalar.
- return: if `result_val.type` is a pointer, the worker already returns a boxed
  PyObject — hand it straight to `py_virtual_thread_complete`; else
  `marshal_to_object`.

### CONFIRMED
6/6 vthread frontend tests pass; end-to-end output `1/4/42`. The else-branches
preserve the prior behavior for any unboxed worker. Bootstrap-inert: the resume
is only emitted when lowering `vt.spawn`, which pcc's own compiler source does
not use (the `pcc.virtual_thread` mentions in `pipeline.py` are module-registry
entries, not spawns), so the self-host closure is unaffected.

## Report
Frontend codegen fix in `native_virtual_thread.py`. `native_virtual_thread.py`
itself last changed in fe1de470; the mismatch is the resume assuming native-scalar
params while a spawned (first-class) worker is boxed. Regression-origin (whether
the worker-boxing decision shifted in a later commit) was not separately bisected
since the fix — respecting the worker's actual param/return types — is correct
regardless of when the boxing convention applied.
