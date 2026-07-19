# Investigation: `with` __exit__ not called on exception (py_context_exit leaves the exception pending)

## Status
resolved (2026-06-18)

## Problem Description
`with CM(): ...; raise` did not call `CM.__exit__` — neither single- nor
multi-manager. `test_context_manager_full.py::test_multi_manager_exception_unwind`
produced `[enter A, enter B, body, caught]` instead of
`[enter A, enter B, body, exit B, exit A, caught]` (no `__exit__` at all). This
is a resource-cleanup correctness bug: a context manager's teardown silently
does not run when the body raises.

## Repro
```python
class CM:
    def __init__(self, name): self.name = name
    def __enter__(self): print("enter " + self.name); return self
    def __exit__(self, et, ev, tb): print("exit " + self.name); return False
def main() -> None:
    try:
        with CM("A"):
            print("body"); raise RuntimeError("x")
    except RuntimeError:
        print("caught")
main()
```
```bash
env -u LC_ALL uv run pcc --backend self --python-libpython=off --ir-scaffold=on /tmp/cm_single.py -o /tmp/cm.out && /tmp/cm.out
# before: enter A / body / caught     after: enter A / body / exit A / caught
```

## Test [CONFIRMED]
`tests/python/test_context_manager_full.py` 6/6 pass (default mode); the self
no-libpython repro prints `exit A`; `tests/python/gc/test_pcc_bootstrap_full_gc0.py`
passes (runtime change doesn't break self-host).

## Proposals

## No.1 py_context_exit must run __exit__ with the exception cleared, then restore it

### Root cause
The frontend codegen for `with` is correct: `_emit_native_user_context_with`
(`async_with_lowering.py`) sets `_try_err_block` so `raise` branches to a
`with.err` block that calls `py_context_exit(ctx, exc_type, exc, None)`, and the
multi-manager desugaring (`with A, B:` → `with A: with B:`) nests these err
blocks correctly. The IR shows the branch and the `py_context_exit` call.

The bug is in the runtime `py_context_exit` (`pcc/py_runtime/src/py_context.c`,
a C-only `OBJ_PY_CC_HELPERS` helper — no pcc-Python port to mirror). It called
`py_obj_getattr(manager, "__exit__")` and the method-call path **while the
raised exception was still pending in TLS**. Those bail out on
`py_err_occurred()`, so `__exit__` was never invoked and the function returned
0 (no exit, no suppression). The no-libpython model has no stack unwinder — the
exception sits in TLS until explicitly handled — so the pending error poisons
the very call meant to clean up.

### Code Change
Stash the in-flight exception (`py_tls_exc_get` + `py_tls_exc_set(NULL)`) before
the `__exit__` lookup/call, then restore it (`py_tls_exc_set(...)`) afterwards.
`py_tls_exc_set` does not decref, so the stash/restore is refcount-neutral;
`pcc_gc_note_relocation_read` keeps the pointer valid if a relocating GC
(#3/#4) moved the object while `__exit__` ran. If `__exit__` raises its own
exception it supersedes the original (drop the stashed one). The caller's
codegen already expects the exception still pending on return — it clears it on
the suppress branch and propagates it otherwise — so restoring (rather than
leaving the slot empty) preserves that contract. CPython does the same: it runs
`__exit__` with the exception cleared and re-raises unless `__exit__` returns
truthy. Normal (non-exception) exit is unchanged: `stashed` is NULL so the
stash/restore are no-ops.

### CONFIRMED
Single- and multi-manager unwind now run `__exit__` R→L with the exception
info and re-raise out the top. Suppression (truthy `__exit__`) still works
(caller's suppress branch clears the restored exception). gc0 self-host green.
