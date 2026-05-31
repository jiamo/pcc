# pcc for Python — Limitations

Current (2026-04-20). Tracked against
`docs/plans/python-frontend-plan.md`. Everything here is a deliberate
scope choice or a known future-phase item, not an accidental bug.

## Never planned (out of scope)

- `eval` / `exec` / `compile` on user-provided strings at runtime.
- `__import__` hooks, import-time metaclasses, import finders.
- Full CPython stdlib replacement — we either compile it natively
  (typed subset) or fall through to libpython.
- C extension ABI replacement — pcc does NOT reimplement the CPython
  C-ext ABI. Third-party `.so` modules load through the same
  libpython we link against.
- PEP 703 no-GIL. We inherit CPython's GIL when libpython is linked.

## Phase 3 (OOP + exceptions) — known gaps

| Feature | Status |
|---|---|
| Class + single inheritance | ✅ |
| Multiple inheritance (base-declared order) | ✅ |
| `super().method()` single-step lookup | ✅ |
| Full C3 linearization for diamond super() | ❌ `mro_super_chain` pending |
| `isinstance(obj, Cls)` | ✅ |
| `@property` getter + setter | ✅ |
| `@staticmethod` | ✅ |
| `@classmethod` full support | ⚠️ framework in place; blocked on class-variable reads like `Cls.count` |
| Class-level variable read/write (`Widget.kind`) | ❌ pending |
| Dunder `__eq__/__ne__/__lt__/__le__/__gt__/__ge__/__add__/__sub__/__mul__/__truediv__/__floordiv__/__mod__/__len__/__getitem__/__call__` | ✅ |
| Dunder `__iter__` / `__next__` (user-class iteration) | ✅ |
| Dunder `__hash__` / `__str__` | ❌ pending |
| `__dunder` name mangling | ❌ pending (`inherit_private_like`) |
| `type(x)` builtin / `type(x).__name__` | ❌ pending |
| `try` / `except E:` / `except E as e:` / `else` / `finally` | ✅ |
| Raising builtin exception with message | ✅ |
| Bare `raise` (re-raise) | ✅ |
| `raise X from Y` — `__cause__` chain | ❌ `raise_from` pending |
| `raise Y` inside `except X:` — `__context__` chain | ❌ `raise_new_in_except` pending |
| Traceback with frames | ⚠️ runtime has `py_exc_append_frame` but codegen doesn't call it yet |
| Uncaught exception exit code | ⚠️ inherits whatever C++ runtime decides; needs explicit top-level terminate |

## Phase 4 (CPython fallback) — known gaps

| Feature | Status |
|---|---|
| `import X` at module scope | ✅ |
| `import X` inside function body | ✅ |
| `import X.Y` dotted import (binds top-level `X`) | ✅ |
| `from X import Y` | ✅ |
| `import X as Y` / `from X import Y as Z` | ✅ |
| `module.attr` read | ✅ |
| `module.attr = val` write | ✅ native builtin modules |
| Chained attribute read `a.b.c` | ✅ |
| `module.fn(args)` with 0..3 args (typed scalars) | ✅ |
| `module.fn(args)` with 4+ args (tuple build) | ✅ |
| `module.fn(*args)` splat | ❌ pending |
| `module.fn(**kwargs)` splat | ❌ pending |
| `cpy_val[k]` subscript read | ✅ |
| `cpy_val[k] = v` subscript write | ❌ pending |
| `len(cpy_val)` | ✅ |
| `for x in cpy_val` (iter protocol) | ✅ |
| `if cpy_val` truthy check | ✅ |
| `with cpy_val as var: ...` happy path | ✅ |
| `with cpy_val as var: ...` exception-exit through `__exit__` | ❌ pending |
| Decorator on user function (`@lru_cache`) | ❌ pending |
| Generator function (`yield`) | ❌ pending |
| Async / `await` | ❌ out of scope for Phase 4 |

## Python runtime types — known gaps

| Feature | Status |
|---|---|
| `int` (arbitrary precision via bignum fallback) | ✅ |
| `float` (IEEE 754 double) | ✅ |
| `bool`, `None` singletons | ✅ |
| `str` (UTF-8, indexing, slicing, `.join/.split/.strip/.replace/.startswith/.endswith/.find`, `len`, `in`) | ✅ |
| `bytes` literals (`b"..."`) | ❌ no BytesLit AST node; lowered as latin-1 `str` (breaks non-ASCII) |
| `list[T]` (typed list with append, indexing, slicing, sort) | ⚠️ sort not user-visible via `.sort()` method yet |
| `dict[K,V]` (insertion-ordered) | ✅ |
| `set[T]` | ✅ |
| `tuple` | ✅ |

## CLI / build-system

| Area | Status |
|---|---|
| `pcc foo.py -o foo` basic entry | ✅ |
| `pcc --emit-llvm foo.py -o foo.ll` | ✅ |
| Multi-file modules (`import mymodule`) | ❌ pending — only stdlib imports tested |
| Cross-compilation flags | ⚠️ pcc has the framework for C cross-compilation; not validated for Python frontend |
| Debug info (DWARF) | ❌ pending |
| `python3-config` auto-link for Phase 4 | ✅ |

## Known shape-gaps vs CPython semantics

These are cases where pcc's behavior *could* diverge from CPython's —
call them out if you hit one:

- `str(exc)` returns an empty `""` when the exception has no message,
  but pcc's runtime has no shared empty-PyStrObject singleton yet, so
  `str(ValueError())` prints `<null>` in pcc output while CPython
  prints the empty line. Tracked in `py_obj_stubs.c::py_obj_str`.
- Numerical precision for bignum overflow during arithmetic in untyped
  code: pcc's phase-2 tagged-int fallback hits overflow and promotes
  to heap bignum correctly, but `python -c 'print(10**40)'` through
  pcc hasn't been formally verified yet.
- `print(f)` on a CPython float may format differently than
  CPython's `print()` — pcc goes through `py_cpy_to_pcc_str` which
  uses `PyObject_Str`, so output matches CPython for supported types.

## Testing surface

Acceptance harness:

```
tests/py_corpus/
  phase1/  # 25 typed MVP tests
  phase2/  # 30 true-semantics tests
  phase3/  # 45 OOP + exceptions tests
  phase4/  # 37 CPython fallback tests
  run_pcc.py  # full end-to-end runner (compile + run + diff)
```

Current pass rate (2026-04-20):

- phase1: 23/25
- phase2: 15/30
- phase3: 31/45
- phase4: 37/37
- **total: 106 / 137**

IR-pass suite (`tests/test_ir_passes_*.py`): 884+ passing.
