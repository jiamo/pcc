# Investigation: user __eq__ in dict/set key lookup (no-libpython)

## Status
active — gap CONFIRMED + a fix ATTEMPTED and REVERTED 2026-05-30 (it works for
standalone programs but breaks the stage1->2->3 self-host bootstrap). Needs a
narrower fix or a diagnosis of which compiler-internal __eq__ interaction
breaks. The building-block helper py_user_eq_dispatch is left in place (inert).

## Problem Description
A custom object with `__hash__`/`__eq__` used as a dict/set key: construction
works (keys stored by `__hash__`, which `py_user_hash_dispatch` already
dispatches), but LOOKUP with a fresh-but-equal key raises `KeyError` / `in`
returns `False`.

```python
class K:
    def __init__(self, v): self.v = v
    def __hash__(self): return self.v
    def __eq__(self, o): return self.v == o.v
d = {K(1): "a"}
d[K(1)]        # KeyError under =off; "a" under CPython
K(1) in {K(1)} # False under =off; True under CPython
```

Found 2026-05-30 by the real31 batch probe.

## Root cause (CONFIRMED)
`py_obj_eq` (py_obj_ops_compare.c + the pcc-Python port) handles
bool/int/str/bytes/tuple/list/dict/set/valuebox, then `return 0` — it has NO
instance case, so two distinct user instances compare not-equal unless the
identity `ptr_eq` shortcut matches. Dict/set lookup calls `py_obj_eq` after the
identity check, so a fresh-but-equal key never matches.

## Repro
```bash
printf 'class K:\n    def __init__(s,v): s.v=v\n    def __hash__(s): return s.v\n    def __eq__(s,o): return s.v==o.v\ndef main():\n    d={K(1):"a"}\n    print(d[K(1)])\nmain()\n' > /tmp/k.py
env -u LC_ALL uv run pcc --backend self --python-libpython=off --ir-scaffold=on /tmp/k.py -o /tmp/k_bin
/tmp/k_bin   # KeyError ; python3 -> a
```

## Proposals
- No.1 py_user_eq_dispatch + py_obj_eq instance case  [ATTEMPTED, REVERTED — breaks self-host bootstrap]

## No.1 py_user_eq_dispatch + py_obj_eq instance case [REVERTED]
### Code Change (reverted from py_obj_eq; helper kept)
- NEW `py_user_eq_dispatch(a, b)` in py_protocol.c (C-only OBJ_PY_CC_HELPER):
  `lookup_dunder(a, "__eq__")` (UNBOUND func) + `call_binary(method, a, b)` —
  the mechanism `py_user_hash_dispatch` uses, which AVOIDS the
  `py_obj_call_method1` bound-method double-self bug (the gap-a root). Tri-state
  return: -1 = no __eq__ (caller keeps identity), 0/1 = __eq__ result;
  NotImplemented -> -1. Declared in py_runtime.h. **This helper is LEFT IN
  PLACE** (inert without a caller) as the building block for a future fix.
- py_obj_eq (C py_obj_ops_compare.c + port) gained an INSTANCE case
  (`tag == 11 || tag >= 100`) calling it. **THIS instance case was REVERTED.**
### Standalone result: WORKS
`/tmp/gap_probe/objkey.py` was IDENTICAL to python3 — dict lookup with a fresh
equal key, `in`, set dedup, `[K(1)] == [K(1)]`, and plain int/str-key + no-__eq__
identity regressions all passed. A focused unit test (2 funcs) passed.
### Self-host result: BREAKS — REVERTED
`tests/python/test_pcc_bootstrap_full.py::test_full_three_stage_bootstrap_self`
FAILED with `PCC-PY-COMPILE-001: subprocess.run failed` (a stage binary errored;
fast ~2s, suggesting a crash). Reverting BOTH py_obj_eq instance cases (C + port)
restored 18 passed / 4 skipped. So the py_obj_eq instance dispatch is the
culprit. Per feedback_test_first (revert immediately on bootstrap failure),
reverted.

## Update 2026-05-31 — recursion-guard hypothesis DISPROVEN
Re-enabled the py_obj_eq instance dispatch (C + port) WITH a recursion guard
added to `py_user_eq_dispatch` (`static __thread int _eq_depth`, bail to -1 past
depth 64). The custom-key dict/set feature worked (objkey.py IDENTICAL), but the
FULL self-host bootstrap FAILED with the SAME `subprocess.run failed` ~2s as #65.
So Hypothesis 2 (unbounded recursion / stack overflow) is WRONG — the guard at
depth 64 did not change the failure. The break is a DETERMINISTIC NON-RECURSION
issue → Hypothesis 1 (a compiler-internal `set()`/`dict` that relies on instance
IDENTITY; my global value-equality `__eq__` dispatch collapses value-equal-but-
distinct instances → wrong dedup → miscompile → bad link/subprocess command).
Reverted the instance cases again (bootstrap restored to 18 passed/4 skipped).
The recursion guard is LEFT in `py_user_eq_dispatch` (inert, harmless) as a
building block. ★ IMPLICATION: a GLOBAL runtime py_obj_eq instance dispatch may
be a DEAD END — at runtime you cannot distinguish "user wants value-equality"
from "pcc's compiler relies on identity" (both are instance `==`/set/dict). A
viable fix likely must be NARROWER (frontend static `__eq__` for `==` on
known-ClassType operands, like gap-a #54 — but that may not reach the runtime
dict/set lookup that motivated this) OR requires a stage2 instrument to find the
exact breaking class + a per-class exclusion. DEFERRED: not a /loop slice.

## Hypotheses for the self-host breakage (to test next)
pcc's own compiler uses `@dataclass(frozen=True)` types (the Type hierarchy, AST
nodes) that auto-generate `__eq__`/`__hash__`, and uses instances as dict keys /
in sets (type interning, visited-sets, caches). Two candidates:
1. **Identity-reliant set/dict usage**: a `seen = set()` of nodes where the
   compiler wants IDENTITY dedup; value-equality (my fix) wrongly collapses
   distinct-but-equal nodes -> miscompile. (But pcc runs fine under CPython =
   value-equality, so the compiler logic should be value-equality-compatible —
   weakens this hypothesis unless a pcc-only path differs.)
2. **A crash, most likely unbounded recursion**: a class `__eq__` that compares
   an attribute which routes back through py_obj_eq -> py_user_eq_dispatch ->
   __eq__ on a self-referential / deeply-nested structure -> stack overflow ->
   "subprocess.run failed". The ~2s fast failure fits a crash more than a
   wrong-output miscompile.
### Next step
Instrument `py_user_eq_dispatch` (log the class name / a depth counter) on a
stage2 self-compile, or gate the instance dispatch narrowly (e.g. a recursion
guard, or only classes with a *non-dataclass* explicit __eq__) and re-run the
bootstrap to bisect. The helper is already in place; only the py_obj_eq instance
case must be re-added (carefully) once the breakage is understood. Related: the
gap-a runtime defect (cmp_threeway can't dispatch user dunders) was sidestepped
in the FRONTEND (#54/#55); dict/set lookup is runtime-only and cannot be
frontend-rerouted, so this one needs the runtime dispatch to work.
