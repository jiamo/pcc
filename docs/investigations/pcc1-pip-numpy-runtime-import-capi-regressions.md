# Investigation: pcc1 pip-numpy chain regressed at HEAD — default acquire mode fails closed, and the compiled numpy app breaks at import on C-API shim holes

## Status
active — two distinct defects verified 2026-08-08 by running the real
network flow with tonight's pcc1 (build/bootstrap-pytest-self-gc4/pcc1,
all 2026-08-07 fixes included). The opt-in network gate
(`tests/integration/test_pcc1_pip_numpy_network.py`,
PCC_RUN_PCC1_PIP_NUMPY_NETWORK=1) has not been re-run since the July/August
package and C-API commits and no longer matches current behavior.

## Problem Description
User question: does `pcc1 -m pip install numpy` still succeed after the
de-C migration? Answer today: the INSTALL works only in explicit
host-assisted mode; the default mode fails closed before any network I/O;
and the installed package breaks at `import numpy` in the compiled app on
every GC backend.

## Defect 1 — default acquire mode cannot install numpy
`selected_acquire_mode("auto") -> "owned"` since 1255819a/cb80f06e
(2026-07-22, "Add owned package acquisition"). The owned resolver requires
a sha256-pinned compatible artifact and reports
`PCC-PKG-ACQUIRE-HASH-REQUIRED: no compatible artifact with a sha256
fragment was published` for numpy — so the plain
`pcc1 -m pip install numpy` (auto) fails closed instantly. The gate test
still asserts `acquire_mode == "host"` / `host_assisted is True` (the
pre-July auto behavior), so it fails at its first assertion in 0.1s.
Explicit `--acquire=host` works end-to-end for the install:
ok=True, numpy 2.4.6, pcc-native extensions only
(`*.pcc3-pcc_native-macosx_14_0_arm64.so`, zero cpython .so). Fix needs an
owner decision: either owned-auto learns a compatible+hashed source path
for real sdists, or auto falls back to host, or the gate/docs pin
`--acquire=host` as the supported command shape.

## Defect 2 — compiled numpy app fails at import (C-API shim surface)
With the host-mode install and tonight's pcc1:
`pcc1 --backend self --python-libpython=off --ir-scaffold=on main.py`
(main = version print + array add) compiles, but the app fails identically
under PCC_GC_BACKEND=0..4 with PCC_HOST_PYTHON=/usr/bin/false:
`AttributeError: _ArrayFunctionDispatcher` (raised during `import numpy`,
numpy._core.overrides fetching the dispatcher type from
_multiarray_umath). Backend-independent => not GC; the surface is the
C-API shim / extension-module attribute path.
Era fingerprint 2: an Aug-6 pcc1 (bootstrap-pytest-self-gc3) driving the
same flow against TODAY's runtime archive dies earlier —
`dlopen ... symbol not found in flat namespace '_Py_GenericAlias'` — i.e.
different C-API holes at different tree states, consistent with the
93cfbca5 "C-API shim closure: migrate all remaining Py* symbols to
pcc-Python" commit family (which already shipped four other red gates
fixed on 2026-08-07; see fallback-baseline-head-regressions doc).

## Repro
```bash
# install (works): --acquire=host
build/bootstrap-pytest-self-gc4/pcc1 -m pip install numpy --acquire=host \
  --target /tmp/site --cache-dir /tmp/cache
# default mode (fails closed): omit --acquire -> PCC-PKG-ACQUIRE-HASH-REQUIRED
# import break:
printf 'import numpy as np\nprint(np.__version__)\n' > /tmp/main.py
PCC_PACKAGE_SITE=/tmp/site pcc1 --backend self --python-libpython=off \
  --ir-scaffold=on /tmp/main.py -o /tmp/np_app
PCC_GC_BACKEND=0 PCC_HOST_PYTHON=/usr/bin/false /tmp/np_app
#   -> AttributeError: _ArrayFunctionDispatcher
```

## Test [CONFIRMED]
Both defects observed 2026-08-08 as above; the gate test fails at its
first assertion (install returncode) under the same env.

## Defect 2 narrowed to a behavioral hole in a named API set (2026-08-08)

Decisive reasoning: `dlopen` SUCCEEDS for the pcc-native extension, so every
symbol numpy references exists — this is NOT a missing-symbol hole (that
shape is the Aug-6 `_Py_GenericAlias` failure). The module imports and other
attributes resolve; only `_ArrayFunctionDispatcher` is absent. So some
registration call returns success WITHOUT storing the attribute.

`nm -u` on the built `_multiarray_umath.pcc3-pcc_native-*.so` shows the exact
registration surface numpy 2.4.6 uses — the candidate set is now finite:

```text
PyType_Ready        PyDict_SetItem      PyModule_AddObject
PyType_GenericNew   PyDict_SetItemString  PyModule_AddIntConstant
PyType_GetFlags     PyType_IsSubtype      PyModule_AddStringConstant
PyType_Type
```

`PyModule_AddType` is NOT referenced at all — the earlier "zero hits in the
tree" lead is a dead end and must not be "fixed". numpy registers this type
the classic way: `PyType_Ready(&PyArrayFunctionDispatcher_Type)` then a
module-dict insert (`PyDict_SetItemString` / `PyModule_AddObject`). Next
step is therefore mechanical, not exploratory: instrument those four calls
in the pcc-Python shim for this one type and find which returns success
without a store (prime suspects: a static-type `PyType_Ready` path that
leaves `tp_dict`/the type object unusable as a value, or a module-dict
insert writing into a dict that is not the imported module's namespace).

## Proposals
- No.1 (defect 2) Instrument PyType_Ready / PyDict_SetItemString /
  PyModule_AddObject in the shim for the `_ArrayFunctionDispatcher`
  registration and fix the call that reports success without storing.
  Symbol-surface diffing is DONE (see the narrowed set above); no C
  implementation returns to the production link.               [pending]
- No.2 (defect 1) Owner decision on the auto-acquire contract, then
  realign the gate test to the decided shape and re-run it in CI-visible
  form (it is currently opt-in and silently stale).            [pending]

## Notes
- Claim hygiene: PKG-P0 rows are DONE_STRONG for the state at their
  evidence date; this file records that the CURRENT tree no longer
  reproduces the end-to-end claim, without relabeling those rows'
  historical evidence.
- The de-C context the question was asked in: the production runtime
  archive is 176/177 pcc-Python members (only py_capi_compat.o remains
  C), so the C-API shim IS now pcc-Python — these holes are the first
  numpy-shaped bill for that migration.

## Update 2026-08-08 — Defect 2 CLOSED: 12 C-API shim holes found and fixed in pcc-Python [CONFIRMED]

`import numpy` now runs end to end under strict no-libpython on **all five GC
backends**, with no C implementation restored to the production link and no
numpy special-casing:

```text
GC0: 2.4.6 [2, 3, 4]   GC1: 2.4.6 [2, 3, 4]   GC2: 2.4.6 [2, 3, 4]
GC3: 2.4.6 [2, 3, 4]   GC4: 2.4.6 [2, 3, 4]     (all exit=0)
```

Command (host pcc, DEFAULT runtime mode so the pcc-Python ports are linked —
not `PCC_RUNTIME_CC=cc`, which would have linked the C sources and hidden every
hole below):

```bash
SITE=build/test-package-cache/default-env
printf 'import numpy as np\nprint(np.__version__)\nprint((np.array([1,2,3])+1).tolist())\n' > /tmp/np_main.py
PCC_PACKAGE_SITE=$SITE uv run pcc --backend self --python-libpython=off \
  --ir-scaffold=on /tmp/np_main.py -o /tmp/np_app
PCC_PACKAGE_SITE=$SITE PCC_GC_BACKEND=0 PCC_HOST_PYTHON=/usr/bin/false /tmp/np_app
```

### Method

The `_ArrayFunctionDispatcher` "registration succeeded without storing" theory
in the section above was **wrong**, and the way it was disproved is the
reusable part: an lldb breakpoint on `PyDict_SetItemString` never fired, so the
registration call was never reached at all. numpy 2.4.6 uses multi-phase init
(PEP 489) — `PyInit__multiarray_umath` only returns a moduledef, and everything
including that dict insert happens in the `Py_mod_exec` slot. The slot never
ran, so *every* attribute set after `PyModule_Create` was missing;
`_ArrayFunctionDispatcher` was simply the first one Python code asked for.

After that the work was a mechanical loop, one hole per iteration: run, read
the top frame / error, diff the pcc-Python port against its C oracle in
`py_capi_shim.c`, fix, rebuild the port archive, rerun.

```bash
env -u LC_ALL PCC="uv run --project $PWD pcc" make -C pcc/py_runtime libpy_runtime_pcc_py.a
```

### The 12 holes (all in `pcc/py_runtime/py/`, all migration drift)

1. **`m_slots` read at offset 40 instead of 64** (`py_capi_module_state_runtime.py`).
   Offset 40 is `m_doc`. The exec-slot walk therefore iterated a doc string as a
   `PyModuleDef_Slot` array. Root cause of the reported symptom.
2. **`Py_mod_exec` compared against 1, not 2** (same file). 1 is `Py_mod_create`.
   Both bugs had to be fixed before a single slot ran.
3. **Unresolved names → runtime `NameError`** in three ports: `PyLong_FromLong`
   / `PyUnicode_FromString` (`py_capi_misc_runtime.py`), `PyTuple_Size/New/GetItem`
   (same), `py_tuple_len` / `py_tuple_get` (`py_capi_exc_runtime.py`), and ten in
   `py_capi_cext_runtime.py` (`py_incref`, `py_str_utf8`, `pcc_gc_load_ptr`,
   `pcc_gc_store_ptr`, `PyBool_FromLong`, `PyFloat_FromDouble`, `PyLong_FromLong`,
   `PyUnicode_FromString`, `PyUnicode_FromStringAndSize`, `PyLong_FromUnsignedLong`).
   These compile clean and only fail when the line executes — see
   [`reference_port_unresolved_name_becomes_runtime_nameerror`]; `rg "is not defined"
   pcc/py_runtime/build_py/*.ll` lists them all in one shot and is the cheapest
   possible audit. Also `_TP_BASICSIZE`, a module-level int const, which is zeroed
   in stripped library builds — inlined at the use site.
4. **An extern function passed as a first-class value.** `_binary_int_result(left,
   right, op, name)` took `py_int_add` etc. as `op`; the library-mode port compiler
   lowers that to a NameError stub. Rewritten to an integer opcode + if-chain
   across all 12 call sites (`py_capi_number_runtime.py`).
5. **`Py_BuildValue` dict pair check inverted.** The port had
   `!= '}' and != '\0'` where the C oracle has `== '}' || == '\0'`, so every
   well-formed `{...}` raised "requires key/value pairs" and every malformed one
   passed. numpy hits this via `Py_BuildValue("{ON}", ...)` for `PyUFunc_Type.tp_dict`.
6. **`PyObject_CallFunction` supported only `""`, `"O"`, `"N"`.** The port had a
   hand-rolled mini-parser with the comment "the format string forms are rare";
   numpy calls `PyObject_CallFunction(helper, "Os", dtype_class, alias)`, whose
   trailing `s` was silently dropped. Fixed by exporting the real engine as
   `pcc_capi_build_call_args` from `py_capi_buildvalue_runtime.py` (force_tuple=1,
   same shape as the C helper of that name) and routing both
   `PyObject_CallFunction` and `PyObject_CallMethod` through it.
7. **`stack_alloc` wrapped in a helper that returns it.** `_stack_i32()` /
   `_stack_i64()` (`py_capi_arg_runtime.py`) and `_stack_ptr()`
   (`py_capi_contextvar_runtime.py`) allocated in the *helper's* frame and
   returned a dangling pointer; `PyArg_ParseTupleAndKeywords` then wrote parsed
   arguments through it into freed stack and jumped through a clobbered slot
   (`EXC_BAD_ACCESS` inside `libobjc`, one frame below `stringdtype_new`). All
   call sites now `stack_alloc` inline. **This pattern is a runtime-wide hazard:
   any `def f(): return stack_alloc(n)` in a port is a bug.**
8. **Raw pointer `==` inside the comparison runtime → infinite recursion.**
   `pcc_capi_cext_richcompare_bool` compared `left_type == right_type` and
   `left == right` with `==`, which lowers to `py_obj_eq`, which dispatches
   straight back into `pcc_capi_cext_richcompare_bool`. Stack overflow with a
   perfectly alternating backtrace. Now `ptr_eq`. The same function had also
   **dropped the left operand's slot entirely** (it tried right, then fell through
   to identity) — restored to the C oracle's left/right/subclass-priority order.
9. **Type-table walks missed the NULL-name sentinel.** `_find_getset` /
   `_find_member` / `_find_method` looped `while not ptr_is_null(array)`, but the
   array pointer never becomes NULL — CPython terminates these tables with a
   `{NULL, ...}` *entry*. They walked off the end into unmapped memory.
10. **`PyMemberDef` type codes were a made-up contiguous 0..14 table.** Real
    `structmember.h` values are `T_CHAR=7`, `T_OBJECT_EX=16`, `T_LONGLONG=17`,
    `T_ULONGLONG=18`, `T_PYSSIZET=19`, `T_NONE=20`; everything from `T_CHAR` up
    was decoded as the wrong type, and `T_BOOL` read 4 bytes instead of 1.
11. **`tp_call` kwargs not normalized.** The runtime passes `py_None` for "no
    kwargs"; the C ABI contract is NULL-or-dict. numpy's vectorcall path rejected
    it with "vectorcall kwargs must be a dict".
12. **Two argument-convention bugs at the call boundary.**
    `METH_FASTCALL|METH_KEYWORDS` was handed `total_count` as `nargs` when the
    contract is *positional* count only (keyword values follow in the vector,
    named by `kwnames`) — numpy reported "empty() takes from 1 to 3 positional
    arguments but 4 were given". And `py_obj_truediv` passed cext op code `3`,
    which is `nb_remainder` in the port's op table (the older C table used 3 for
    true divide); correct code is `12`.

Plus two protocol gaps found by the same loop:
`pcc_capi_cext_binary_number`'s `_binary_slot` only tried the *left* operand's
slot and returned NULL on a miss instead of implementing the CPython binary
protocol (right-operand and subclass priority, `NotImplemented` fall-through,
TypeError at the end) — this broke `int * numpy_scalar`; and the overflow
out-param of `py_int_to_i64` is a C `int *` (4 bytes) but three `PyArg_*` sites
allocated 8 and read `load_i64`, so uninitialized stack garbage in the high half
reported a bogus overflow for every `l`/`i`/`n` argument.

### Regression

`tests/python/test_pcc_native_extension_loader.py::test_pcc_native_multiphase_capi_surface_default_runtime`
— one multi-phase (PEP 489) numpy-shaped extension that exercises all of the
above generically (no numpy needed, ~1s): `m_slots`-vs-`m_doc` (the moduledef
carries a canary `m_doc`), `Py_mod_exec`, dict registration into the live module
namespace, `Py_BuildValue("{ss}")`, `sys.flags.optimize`,
`PyObject_CallFunction("Os")`, `METH_FASTCALL|METH_KEYWORDS` nargs,
`PyArg_ParseTupleAndKeywords("l|l:...")`, `T_PYSSIZET`/`T_CHAR` members,
`tp_richcompare` on both operand orders including a type *without* the slot, and
`nb_multiply` with the extension type on the right. It runs in **default runtime
mode** on purpose (`PCC_RUNTIME_CC` is popped from the env): under
`PCC_RUNTIME_CC=cc` it links the C sources and passes even with every port bug
present.

## Status update
Defect 2: **resolved**. Defect 1 (default `auto` acquire mode fails closed with
`PCC-PKG-ACQUIRE-HASH-REQUIRED`) is unchanged and still needs the owner decision
described above; the evidence in this Update used a site installed via the
host-assisted path.
