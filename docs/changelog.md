# pcc Changelog

## Phase 1 → Phase 4 (through 2026-04-20)

### Phase 1 — Typed Python MVP

- Parser lifts stdlib `ast` to pcc internal AST.
- Annotation-driven type inference (int / float / bool / str /
  list[int] / None).
- L1 LLVM IR codegen: scalar ops, control flow, `for i in range`,
  function calls, recursion.
- `print()` runtime and minimal `py_list.c` (int-only path).
- `pcc foo.py -o foo` CLI.

### Phase 2 — Python True Semantics

- Tagged int + bignum fallback (`py_int.c`): Python-correct division,
  floor division, modulo; overflow-safe promotion to heap bignum.
- Full `str` runtime (`py_str.c`): UTF-8, indexing, slicing, `+`, `*`,
  `in`, `.split`, `.join`, `.strip`, `.replace`, `.startswith`,
  `.endswith`, `.find`, `len`.
- `list[T]`, `dict[K,V]`, `set[T]`, `tuple` runtimes.
- `None` singleton + safety: attribute access on `None` raises
  `AttributeError` rather than segfault.

### Phase 3 — OOP + Exceptions

- Class codegen with struct + vtable layout, single + multiple
  inheritance via declared-base order.
- `super().method()` resolution, `isinstance`, parent-field seeding
  into children so inherited `self.field` writes use the correct slot.
- `@property` (getter + setter), `@staticmethod`, partial
  `@classmethod`.
- Dunder dispatch for `__eq__/__ne__/__lt__/__le__/__gt__/__ge__`,
  `__add__/__sub__/__mul__/__truediv__/__floordiv__/__mod__`,
  `__len__`, `__getitem__`, `__call__`.
- Exception runtime (`py_exc.c`) with Itanium C++ ABI personality +
  `__cxa_throw` / `__cxa_begin_catch` / `__cxa_end_catch`; built-in
  classes (ValueError, KeyError, RuntimeError, ...).
- LLVM `invoke` + `landingpad` codegen for `try/except/else/finally`;
  bare `raise` re-raises the current exception; multi-handler chain
  with class match via `py_exc_matches`.
- `str(exc)` dispatches to the exception's message via
  `py_exc_get_message`.

### Phase 4 — CPython C-API Fallback

- `pcc/py_runtime/src/py_libpython.c`: Py_Initialize, PyImport_ImportModule,
  PyObject_GetAttrString, PyObject_CallNoArgs/OneArg/FunctionObjArgs,
  PyObject_Call (tuple-based for arbitrary arity), PyObject_GetItem,
  PyObject_Length, PyObject_IsTrue, PyObject_GetIter/PyIter_Next,
  PyLong_FromLongLong / PyLong_AsLongLong, PyFloat_FromDouble /
  PyFloat_AsDouble, PyUnicode_FromStringAndSize / PyUnicode_AsUTF8,
  Py_DecRef.
- `Makefile PCC_WITH_LIBPYTHON=1` toggle; pipeline builds with it and
  auto-appends `python3-config --ldflags --embed` when the source
  contains any `import`.
- Layer1 lowering: top-level and function-body `import X`, dotted
  imports (`import urllib.parse` binds `urllib`), `from X import Y`,
  attribute + method calls on imported modules, CPython-tagged
  locals, and chained `Attr/Subscript/Call` CPython values.
- `env_cpy_flags` propagates the CPython tag across variable
  assignments; `_emit_name` re-tags on load.
- Scalar marshalling from pcc int/float/str to CPython objects with
  owned-ref tracking; symmetric unbox paths in `_to_int64`,
  `_to_double`, and `_truthy` for CPython-tagged values.
- `module.method(args)` via `py_cpy_call1/2/3` for ≤3 args,
  `py_cpy_call_argv` + `PyTuple_New/SetItem` for arbitrary arity.
- `cpy_val[k]` via `PyObject_GetItem` with marshalled keys; `len(cpy)`
  via `PyObject_Length`.
- `for x in cpy_iter:` via `PyObject_GetIter` + `PyIter_Next`
  null-check loop.
- `if cpy_val:` truthiness via `PyObject_IsTrue`.
- `with EXPR as VAR: BODY` happy path via `__enter__` / `__exit__`
  method dispatch (exception-exit path pending).
- Module-level `Assign / AugAssign / If / While / For` lowered into
  synthesized `i32 @main()`; `Name`-targeted module-level assignments
  get a global var so user functions can read them.
- Multi-arg `print()` auto-converts CPython values to pcc strs.

### Phase 5 — Optimization + Release (in progress)

- `tests/py_corpus/run_pcc.py`: end-to-end acceptance + benchmark
  harness. Reports per-test compile time, best-of-3 run time, exe
  size. `106/137` tests pass as of 2026-04-20.
- Docs: `docs/python-tutorial.md`, `docs/python-limitations.md`,
  `docs/python-howto.md`, this changelog.

### IR-pass tracks (parallel to Python frontend)

Covered separately under
`docs/plans/all-pass-llvm-ir-1to1-master-plan.md`. This session also:

- Moved every visible pass out of `deprecated-source-approximation`
  into `subset` or `equivalent` — `0/82` remain at the deprecated
  tier (down from 13 at session start).
- Real narrow transforms landed for `infer-alignment`,
  `alignment-from-assumptions`, `speculative-execution`, `float2int`,
  `memcpyopt` (no-op drop subset).
- ADCE gained step-3 dead-conditional-branch rewrite + a
  cross-function SSA-name scoping bug fix.
- `tests/test_ir_passes_*.py`: 884+ passing.
