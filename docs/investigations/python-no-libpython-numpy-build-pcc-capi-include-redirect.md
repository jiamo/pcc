# Investigation: wire the pcc-native C-API header surface into the real numpy build (pcc-native include redirect)

## Status
active

## Problem Description
The `import numpy` no-libpython (Mode D / pcc-native) end-goal for `B-P0-PKG`
is gated by `PCC-PKG-004`: numpy's real build emits
`*.cpython-314-darwin.so` (system CPython 3.14 ABI), which pcc correctly
rejects as a foreign-ABI extension. To produce a *pcc-native* `.so`, numpy's
C core must be compiled against **pcc's** `Python.h` (the runtime object
model: `PyObjectHeader`, `type_tag`) instead of CPython 3.14's headers.

The pcc-native C-API **declaration surface** for that compile is now
probe-validated: 38/40 of numpy's `_core` `.c` files compile clean against
`utils/fake_libc_include/Python.h` (+ `structmember.h`/`pymem.h`/
`frameobject.h`) + system libc (see `docs/current-goal-state.md`,
2026-05-29 "NO-LIBPYTHON ... C-EXTENSION HEADER SURFACE milestone"). But that
validation is **/tmp PREP** (via `/tmp/pcc_capi`); it is NOT wired into the
real package build. The real build still consumes meson's
`compile_commands.json` with CPython 3.14 `-I` paths baked in. This
investigation tracks **part-1**: redirecting the real build to pcc's headers.

This is necessary-but-NOT-sufficient: even with the redirect, the resulting
objects do not link/run without the **runtime core** (the `PyArray_*` /
`Py_TYPE` / `PyType_Ready` implementations on pcc's object model, plus the
`sizeof(PyObject)` object-layout answer for the 2 residual files). Part-1
wires the headers and reveals the next real-build error layer (the full numpy
header tree may surface more decl gaps than the 40-file probe; the link step
enumerates the exact missing-symbol = runtime surface). The multi-month
runtime core is a separate track.

## Repro
1. pcc-native install/build of `projects/numpy-2.4.4` drives meson, which
   produces `build/pcc-package/meson-build/compile_commands.json` whose
   entries carry CPython 3.14 include flags (`.../python3.14`,
   `Python.framework`).
2. `pcc/package/build_exec.py::execute_build_actions` consumes those commands
   (`graph_commands` loop) and compiles each `.c` with the CPython includes —
   so any pcc-native intent is defeated at the include layer.
3. The manual /tmp probe that DOES work: strip the CPython `-I` entries,
   append `-I/tmp/pcc_capi -Ipcc/py_runtime/include`, keep system libc ->
   38/40 clean.

## Test [N/A]
No new gate yet — this file characterizes part-1. Implementation must extend
`tests/python/test_package_build_exec.py` with a pcc-native case asserting the
emitted compile command has CPython include dirs removed and the pcc C-API
include dir injected, and must keep `test_package_extension_abi.py` +
`test_pcc_native_extension_loader.py` (ext-abi, currently 22 passed) green.

## Analysis (verified 2026-05-29)
- **Chokepoint**: `pcc/package/build_exec.py:559-565` — the `for build_command
  in graph_commands:` loop, right after `command = shlex.split(
  build_command.command)` and `command[0] = _resolve_command_tool(...)`. This
  is the single per-`.c` command-assembly point for the meson-introspection
  path that numpy uses.
- **ABI mode is available**: `execute_build_actions(..., abi_mode: str =
  "pcc-native")` (line 365). So the redirect can be guarded on
  `abi_mode == "pcc-native"`, leaving cpython-compat / libpython builds
  untouched.
- **Gate exists**: `tests/python/test_package_build_exec.py`.
- **Generic, not numpy-specific** (claim hygiene, AGENTS.md "Package / NumPy
  Claim Hygiene"): the transform must be "pcc-native build redirects any
  CPython include dir to pcc's C-API includes", with NO `if package ==
  "numpy"`.

### Critical design constraint — the libc-shadow (verified earlier 2026-05-29)
`utils/fake_libc_include/` contains stub `math.h` / `complex.h` / etc. that
**shadow the system libm** if the whole directory is put on the include path.
numpy's C core needs the REAL system libc/libm. The working /tmp probe avoided
this by copying ONLY the Python C-API headers (`Python.h`, `structmember.h`,
`pymem.h`, `frameobject.h`) into a libc-free dir (`/tmp/pcc_capi`) and relying
on system libc. Therefore the redirect must inject a **curated
Python-C-API-only include dir**, NOT `utils/fake_libc_include` wholesale.

Open design sub-question (resolve before landing): where does the curated dir
live without duplicating `Python.h`?
- (a) New repo dir `utils/pcc_capi_include/` holding the C-API headers; risk:
  two copies of `Python.h` to keep in sync.
- (b) Move the Python-C-API headers OUT of `fake_libc_include` into a
  dedicated dir and have the C-extension path use that; risk: refactor blast
  radius — confirm nothing else (ext-abi tests, C frontend) depends on
  `fake_libc_include/Python.h` staying put.
- (c) Build a curated dir at build time (symlink/copy the 4 C-API headers into
  a temp/cache dir per build); no duplication in git, but adds a build step.
  Likely the lowest-risk first cut, mirroring the /tmp/pcc_capi probe.

## Proposals
- No.1 Gated generic pcc-native include redirect in `build_exec` + curated C-API include dir   [CONFIRMED: redirect mechanism unit-gated + validated vs real numpy (38/40); full execute=True build + runtime core pending]

## No.1 Gated generic pcc-native include redirect
### Code Change (design; not yet implemented)
1. In `build_exec.py` graph_commands loop, when `abi_mode == "pcc-native"`,
   rewrite each command: drop `-I<dir>` (and `-isystem <dir>`) entries whose
   dir matches a CPython header location (`python3.\d+`, `Python.framework`,
   `pythonX.Y` sysconfig include), and prepend `-I<curated-pcc-capi-dir>
   -I<pcc/py_runtime/include>`. Keep system libc untouched.
2. Provide the curated C-API include dir per the resolved design sub-question
   (start with option (c): materialize the 4 headers into a per-build cache
   dir, mirroring the validated /tmp/pcc_capi shape).
3. Extend `test_package_build_exec.py` with a pcc-native redirect assertion;
   keep ext-abi green; then re-run the real numpy build to enumerate the next
   real-build error layer (more decl gaps + the missing-symbol/link surface =
   the runtime-core scope).
### CONFIRMED at unit level (2026-05-29)
Implemented in `pcc/package/build_exec.py`, resolving the curated-dir design
sub-question with option (c) (materialize at build time):
- `_materialize_pcc_capi_include(build_dir, execute)` — returns
  `<build>/pcc-package/pcc-capi-include`; when `execute`, copies ONLY the four
  C-API headers (`Python.h`/`structmember.h`/`pymem.h`/`frameobject.h`) from
  `utils/fake_libc_include/` into it (NOT the libc stubs — avoids the libm
  shadow), mirroring the validated `/tmp/pcc_capi` shape.
- `_redirect_pcc_native_includes(command, capi_dir, runtime_include)` — drops
  `-I`/`-isystem` dirs matching `_CPYTHON_INCLUDE_DIR_RE`
  (`pythonX.Y` / `Python.framework` / `/include/pythonX.Y`) and appends pcc's
  C-API + `pcc/py_runtime/include` (appended last so a real package header
  always wins; pcc only fills the dropped Python.h gap). System libc untouched.
- Wired into the `graph_commands` loop guarded on `abi_mode == "pcc-native"`
  and `build_command.language == "c"`; a `PCC-PKG-CAPI-INCLUDE-MISSING`
  diagnostic is emitted (and the redirect skipped) if the headers can't be
  located. Generic — no package-specific rules (the module is explicitly
  package-agnostic).

Validation (all green, no regression):
- `tests/python/test_package_build_exec.py` -> 15 passed, 7 skipped, incl. two
  NEW tests: `test_pcc_native_redirects_cpython_includes_to_pcc_capi`
  (CPython `-I` dropped, `pcc-capi-include` injected, package-own `-I`/`-D`
  preserved) and `test_pcc_native_redirect_absent_in_cpython_compat_mode`
  (cpython-compat keeps the CPython `-I`, no pcc injection).
- `tests/python/test_package_extension_abi.py` +
  `test_pcc_native_extension_loader.py` + `test_package_array_core.py` ->
  28 passed, 5 skipped.

### Real-data confirmation of the redirect mechanism (2026-05-29)
The implemented functions were exercised against the REAL numpy meson commands
(`projects/numpy-2.4.4/build/pcc-package/meson-build/compile_commands.json`,
40 `_core` `.c`): `_materialize_pcc_capi_include(..., execute=True)` produced
`pcc-capi-include/` with exactly the 4 C-API headers;
`_redirect_pcc_native_includes` stripped the CPython header dir on all 40 files
that carried one (40/40) and injected the pcc capi dir on all (100%); compiling
the REDIRECTED commands with `-fsyntax-only` reproduced **38/40 clean**,
identical to the manual /tmp probe. So the redirect MECHANISM is confirmed
against real numpy data, not just unit fixtures.

### Remaining (the heavier follow-on)
NOT yet run: the full `execute_build_actions(execute=True, abi_mode=
"pcc-native")` on numpy (actual `.o` emission + `.so` link). That enumerates
the next real-build error layer (the full numpy header tree may surface more
decl gaps than the 40-file probe; the 2 `sizeof(PyObject)` files still fail;
the link step lists the missing-symbol = runtime-core surface). Still
necessary-but-not-sufficient: objects do not link/run without the runtime core
(`PyArray_*`/`Py_TYPE`/`PyType_Ready` + the `sizeof(PyObject)` object-layout
answer), which is the multi-month track.

### Runtime-core symbol scope (probe, 2026-05-29) — the multi-month track is bounded
Compiled the 38 clean `_core` files to `.o` (via the implemented redirect) and
`nm`-collected referenced `Py*`/`PyArray*`/`npy_*` symbols, then split by where
they resolve. Of 337 distinct referenced symbols: **148 are ALREADY provided**
by a pcc provider (`py_capi_shim.c`/`py_libpython.c`/`pccnpapi.c` — the bulk of
the CPython C-API: `PyLong_*`, `PyUnicode_*`, `PyErr_*`, `PyTuple_*`,
`PyDict_*`, `PyNumber_Add/Subtract/Multiply`, `Py_INCREF/DECREF`, etc.).

The remainder splits into TWO classes that must NOT be conflated:
1. **numpy's OWN C-API / scalar-types / npymath (~120)** — `PyArray_*`,
   `PyUFunc_*`, `NpyIter_*`, `PyDataMem_*`, the `Py<T>ArrType_Type` scalar type
   objects, and `npy_half_*`/`npy_c*`/`npy_*floatstatus*`. VERIFIED these are
   DEFINED in numpy's own `_core` source (e.g. `PyArray_FromAny`/`PyArray_Scalar`
   in `multiarray`/`umath`, `npy_half_to_float` in `umath`, `PyBoolArrType_Type`
   in `scalartypes.c.src`). They are numpy-internal — resolved when numpy's full
   `_core` links together (this 38-file probe over-reports them as "external"
   only because their defining `.c` was outside the sample). **NOT pcc's
   responsibility.**
2. **Genuine pcc-host CPython-C-API gap (~29)** — symbols numpy CALLS but the
   host must provide, confirmed not-defined-by-numpy (e.g. `PyType_Ready`
   appears in numpy only inside example dtypes that *call* it):
   - builtin type objects: `PyBytes_Type`, `PyComplex_Type`, `PyFrozenSet_Type`,
     `PySlice_Type`
   - object protocol: `PyObject_New`, `PyObject_NewVar`, `PyObject_InitVar`,
     `PyObject_IsSubclass`, `PyObject_GenericGetDict`, `PyObject_ClearWeakRefs`
   - type system: `PyType_Ready`, `PyType_IsSubtype`
   - calling: `PyVectorcall_Call`, `PyVectorcall_NARGS`, `PyMethod_New`,
     `PySeqIter_New`
   - threads / free-threading: `PyEval_SaveThread`, `PyEval_RestoreThread`,
     `PyMutex_Lock`, `PyMutex_Unlock`
   - context vars: `PyContextVar_Get`, `PyContextVar_Set`
   - exception chaining: `PyException_SetCause`, `PyException_SetTraceback`
   - misc: `PyOS_strtol`, `PyOS_strtoul`, `PyTraceMalloc_Track`/`Untrack`
     (can be no-ops), `Py_Ellipsis`

So the runtime-core CPython-C-API surface for numpy is **~29 concrete host
symbols**, not an open-ended wall — and pcc already supplies 148. Many of the 29
are simple (builtin-type singletons, `PyObject_New`, `PySeqIter_New`,
`PyTraceMalloc_*` no-ops); the hard ones are `PyType_Ready` (type-system init on
pcc's `type_tag` model — also where the `sizeof(PyObject)` object-layout answer
lands), `PyVectorcall_*`, and `PyContextVar_*`. CAVEAT: the exact host gap needs
the full numpy compile to confirm class-1 vs class-2 precisely; the ~29 list is
the robust subset (each verified not-defined-by-numpy). This is the concrete
entry point for the runtime-core track.

### First host-symbol batch implemented (2026-05-29) — gap ~29 → ~24
Added the cleanly-correct subset to `pcc/py_runtime/src/py_capi_shim.c` (the
pcc-native no-libpython C-API shim; Makefile `py_capi_shim.c -> py_capi_shim.o`,
dropped from the `_libpython` archive so it serves only the no-libpython path):
- `PyTraceMalloc_Track` / `PyTraceMalloc_Untrack` — no-ops returning 0 (pcc's GC
  has no tracemalloc; CPython also returns 0 when tracing is disabled).
- `PyOS_strtol` / `PyOS_strtoul` — libc `strtol`/`strtoul` wrappers (correct;
  pcc carries no separate C-locale state on this path).
- `Py_Ellipsis` — a stable non-NULL sentinel (mirrors the `PyExc_*` sentinel
  pattern), satisfying identity use + linking; pcc has no Ellipsis object.
These are genuine implementations, NOT crash-stubs (claim hygiene: no faking).
Validation: `py_capi_shim.c` compiles clean (`cc -std=c11 -Wall -Wextra`,
EXIT 0); `nm libpy_runtime.a` confirms all five DEFINED in the cc-built archive;
`tests/python/test_pcc_native_extension_loader.py` +
`test_package_extension_abi.py` -> 22 passed, 4 skipped (the loader gate rebuilt
the cc-built archive with the new `.o`, so this is a no-regression check WITH the
additions integrated). Note: the pcc-EMITTED archive (`libpy_runtime_pcc.a`,
`make libpy_runtime_pcc.a`) needs `pcc` on PATH / the bootstrap to rebuild — it
will pick up the edit when the self-host runtime is next built.

REMAINING ~24, by tractability for the next batches:
- builtin type objects (`PyBytes_Type`, `PyComplex_Type`, `PyFrozenSet_Type`,
  `PySlice_Type`) — need REAL pcc type objects, not sentinels (sentinels would
  fail `PyObject_TypeCheck`); blocked on the type-object model.
- object protocol (`PyObject_New`/`NewVar`/`InitVar`) — need the
  `PyTypeObject* -> pcc type_tag` mapping (PyType_Ready territory).
- type system: `PyType_Ready` (THE crux — registers a CPython type on pcc's
  `type_tag` model; also where `sizeof(PyObject)` is resolved), `PyType_IsSubtype`.
- calling: `PyVectorcall_Call`/`NARGS`, `PyMethod_New`, `PySeqIter_New`.
- threads/ctx/exc: `PyEval_Save`/`RestoreThread`, `PyMutex_Lock`/`Unlock`,
  `PyContextVar_Get`/`Set`, `PyException_SetCause`/`SetTraceback`,
  `PyObject_ClearWeakRefs`/`GenericGetDict`/`IsSubclass`.
Next batch should tackle `PyType_Ready` + the object-model/`sizeof(PyObject)`
answer, since it unblocks the type-dependent symbols (builtin types,
`PyObject_New`, `PyType_IsSubtype`, the numpy `Py<T>ArrType_Type` registration).

### PyType_Ready crux — concrete repro + object-model bridge design (2026-05-29)
Reproduced the crux with the smallest possible extension (the numpy
type-registration pattern, reduced): a module defining one static
`PyTypeObject FooType` (`tp_basicsize`, `tp_flags=Py_TPFLAGS_DEFAULT`,
`tp_new=PyType_GenericNew`), calling `PyType_Ready(&FooType)` in PyInit and
`PyModule_AddObject(m, "Foo", &FooType)`, then `import typedemo; print(typedemo.Foo)`
compiled+run via `pcc --backend self --python-libpython=off`. Findings, in order:
1. `.so` would not compile: `PyType_GenericNew` was UNDECLARED in pcc's
   `Python.h`. FIXED — added `PyType_GenericNew`/`PyType_GenericAlloc` decls.
2. After that the `.so` compiles and the host program LINKS (exit 0) — but this
   is misleading: the `.so` is built `-undefined dynamic_lookup`, so its
   `PyType_Ready`/`PyType_GenericNew`/`PyModule_Create2` references are deferred
   to load time.
3. At RUNTIME the crux surfaces concretely:
   `RuntimeError: dlopen failed: ... symbol not found in flat namespace
   '_PyType_GenericNew'` — the host binary does not EXPORT `PyType_GenericNew`
   / `PyType_Ready` because `py_capi_shim.c` does not define them. This is the
   exact, reproducible failure the runtime-core work must fix.

OBJECT-MODEL BRIDGE DESIGN (the hard part, derived from the layout facts):
- A static C-ext `PyTypeObject` gets `PyObject_HEAD_INIT` => header `{1,0,0}` =>
  `type_tag == 0 == PY_TYPE_NONE`. pcc currently cannot tell a C-ext type object
  from `None`; `PyType_Ready` must re-tag the type object as a type.
- C-ext INSTANCES cannot reuse pcc's `PyInstanceObject {PyObjectHeader; PyClassObject *cls; ...}`:
  the extension's `FooObject {PyObject_HEAD; long val}` expects `((FooObject*)o)->val`
  to be its own field, but that offset holds pcc's `cls` pointer — LAYOUT CONFLICT.
  So C-ext instances must keep their own layout (pcc header + ext fields) and be
  identified another way.
- `PyObjectHeader.type_tag` is `int32` and the builtin `PY_TYPE_*` enum only runs
  ~0..27, so there is ample room for DYNAMIC per-type tags. Bridge:
  * `PyType_Ready(type)`: assign the type a fresh dynamic instance-tag, store it
    in a registry `tag -> PyTypeObject*` (+ stash the tag on the type, e.g. an
    unused `PyTypeObject` field), re-tag the type OBJECT itself so pcc sees it as
    a type, set `Py_TPFLAGS_READY`. (Inheritance/slot fill deferred.)
  * `PyType_GenericAlloc/New(type)`: `pcc_gc_alloc(tp_basicsize + nitems*tp_itemsize,
    <type's dynamic tag>, 0)`, zero it.
  * `Py_TYPE(obj)`: if `obj->type_tag` is a dynamic C-ext tag, return
    `registry[tag]`; else the existing builtin mapping. `PyObject_TypeCheck`
    follows from this.
  This keeps the C-ext layout intact, needs no `ob_type` slot in pcc's header,
  and resolves `sizeof(PyObject)` (= 16, pcc's header) consistently. It is a
  substantial multi-step change to `py_capi_shim.c` (dynamic-tag allocator +
  registry + `Py_TYPE`/typecheck routing + type-object str/repr) and is the
  concrete entry point for the next focused runtime-core iteration.

### Type bridge IMPLEMENTED + WORKING (2026-05-29) — custom type imports + instantiates no-libpython
First cut of the bridge landed in `pcc/py_runtime/src/py_capi_shim.c`:
- A layout MIRROR of the CPython `struct _typeobject` (fn/struct-ptr slots as
  `void *`, scalar slots real) so the shim — which compiles against
  `py_internal.h`, not the fake-libc `Python.h` — can read `tp_basicsize`/
  `tp_itemsize`/`tp_flags`/`tp_version_tag` off an extension's static type.
  (Layout-drift class; must track the canonical struct's prefix.)
- `PyType_Ready`: assigns a DYNAMIC `type_tag` (base `0x10000`, above the builtin
  enum) cached in `tp_version_tag` + recorded in a `tag -> PyTypeObject*`
  registry, sets `Py_TPFLAGS_READY`.
- `PyType_GenericAlloc`/`GenericNew`: `pcc_gc_alloc(tp_basicsize + nitems*tp_itemsize,
  <dynamic tag>, 0)` (calloc'd, refcount=1) — keeps the extension's own layout.
- `pcc_capi_type` (= `Py_TYPE`): routes a dynamic tag back through the registry
  to the `PyTypeObject*`; `pcc_capi_typecheck` (= `PyObject_TypeCheck`) exact-match.
- Decl fix: `PyType_GenericNew`/`PyType_GenericAlloc` added to `Python.h`.

RESULT (the win): the minimal custom-type extension (the reduced numpy
type-registration pattern) now IMPORTS, runs `PyType_Ready`, and INSTANTIATES
under strict `--python-libpython=off --backend self`. Observed via repro and
locked as a regression test:
`tests/python/test_pcc_native_extension_loader.py::test_pcc_native_custom_type_pytype_ready_under_self_backend_no_libpython`
asserts `hasattr(typedemo,"Foo")` True, `Foo is not None` True, `typedemo.Foo()`
returns a non-None instance. Gate: loader + ext-abi -> **23 passed, 4 skipped**
(was 22; no regression). Before this change the same repro failed at runtime
with `dlopen ... symbol not found '_PyType_GenericNew'`.

REMAINING layers (next iterations), in rough order:
1. type-object repr/recognition: `print(typedemo.Foo)` shows `None` because the
   static `PyTypeObject`'s header `type_tag` is still 0 (`PY_TYPE_NONE`) — pcc
   doesn't see it as a type. `PyType_Ready` should also tag the type OBJECT +
   pcc needs a type-object repr.
2. builtin-tag `Py_TYPE`: `pcc_capi_type` returns NULL for non-C-ext tags
   (int/str/...); numpy calls `Py_TYPE` on builtins — needs the builtin
   tag -> `PyTypeObject*` mapping.
3. subtype checks (`pcc_capi_typecheck` is exact-match), slot inheritance
   (`tp_base`/MRO), the buffer protocol.
4. the other ~22 host symbols (`PyVectorcall_*`, `PyContextVar_*`, builtin type
   objects, `PyObject_New`/`NewVar`, ...).
5. the full numpy build + link + `_multiarray_umath` PyInit.

### Builtin type objects + builtin-tag Py_TYPE mapping (2026-05-29) — layer 2 done
Closed remaining-layer #2. The 16 builtin `PyXxx_Type` objects declared in
`Python.h` (`PyType_Type`, `PyBaseObject_Type`, `PyLong_Type`, `PyUnicode_Type`,
`PyFloat_Type`, `PyBool_Type`, `PyList_Type`, `PyTuple_Type`, `PyDict_Type`,
`PySet_Type`, `PyFrozenSet_Type`, `PyBytes_Type`, `PyByteArray_Type`,
`PyComplex_Type`, `PySlice_Type`, `PyModule_Type`) were NOT defined in the
no-libpython archive (the libpython bridge dlsym's them; the no-libpython path
had no source — they were a latent dlopen gap exactly like `PyType_GenericNew`).
Defined them in `py_capi_shim.c` as stable RECOGNITION TOKENS (the mirror
PyTypeObject with `tp_name` + READY), and extended `pcc_capi_type` (= `Py_TYPE`)
to map each builtin `type_tag` to its token (`PY_TYPE_INT -> &PyLong_Type`,
`PY_TYPE_STR -> &PyUnicode_Type`, ...). The repro caught a real bug — small ints
are IMMEDIATE (tagged) values, so `pcc_capi_type` now returns `&PyLong_Type` for
`PY_IS_TAGGED_INT` before the header read. Validated: a C-ext computing
`(Py_TYPE(PyLong_FromLong(5))==&PyLong_Type) + 2*(Py_TYPE(PyUnicode_FromString("x"))
==&PyUnicode_Type)` returns 3 under strict no-libpython. Locked as regression
test `test_pcc_native_builtin_py_type_mapping_under_self_backend_no_libpython`.
Gate: loader 16 passed (2 bridge regression tests), ext-abi 8 passed, no
regression. This also resolves the 4 truly-gap builtin types
(`PyBytes_Type`/`PyComplex_Type`/`PySlice_Type`/`PyFrozenSet_Type`) from the
~29 host-symbol list. Still open: layer 1 (type-object repr — `print(Foo)` shows
None), 3 (subtype/inheritance/buffer), 4 (remaining ~18 host symbols), 5 (full
numpy build).

### Subtype checking via tp_base walk (2026-05-29) — layer 3 (partial) done
`pcc_capi_typecheck` (= `PyObject_TypeCheck`) now walks the `tp_base`
inheritance chain (guarded at 64 levels) instead of exact-match. C-ext types set
`tp_base`; builtin tokens have NULL `tp_base`, ending the walk. Validated by a
Base/Derived extension (`DerivedType.tp_base = &BaseType`): under strict
no-libpython, `PyObject_TypeCheck(derived,&BaseType)` and `(derived,&DerivedType)`
are true and `(base,&DerivedType)` is false (`check()` returns 7). Regression
test `test_pcc_native_subtype_check_under_self_backend_no_libpython`; loader gate
17 passed (3 bridge regression tests), no regression. Still open within layer 3:
full slot inheritance (derived inheriting base tp_methods/number slots) + buffer
protocol.

### Host-symbol batch 2 + gap re-measured (2026-05-29) — 21 -> 12
Re-ran the symbol-gap analysis against the CURRENT runtime archive (not the
provider-source-text heuristic) to ground progress against numpy's real symbol
surface: of numpy `_core`'s referenced symbols, runtime now PROVIDES 165 (was
148 pre-bridge), and the genuine pcc-HOST gap fell from ~29 to **12**. Batch 2
implemented these 9 (all reuse the bridge or are trivial/correct primitives):
`PyType_IsSubtype` (tp_base walk on two types), `PyVectorcall_NARGS` (strip the
arguments-offset bit), `_PyObject_New`/`_PyObject_NewVar` (via `PyType_GenericAlloc`),
`PyObject_InitVar` (stamp type_tag + ob_size), `PyMutex_Lock`/`Unlock` (no-ops —
single-interpreter shim), `PyEval_SaveThread`/`RestoreThread` (NULL/no-op — no
detachable GIL state on this path). The subtype regression test was extended to
also assert `PyType_IsSubtype` (both directions) and `PyObject_New` non-NULL
(`check()` now 63); loader+ext-abi 25 passed, no regression.

REMAINING 12 host symbols (medium difficulty — need real pcc primitives):
`PyContextVar_Get`/`Set`, `PyErr_NormalizeException`, `PyException_SetCause`/
`SetTraceback`, `PyImport_Import`, `PyMethod_New`, `PyObject_ClearWeakRefs`,
`PyObject_GenericGetDict`, `PyObject_IsSubclass`, `PySeqIter_New`,
`PyVectorcall_Call`. Plus the non-symbol layers: full slot inheritance, buffer
protocol, type-object repr, and the full numpy build+link+PyInit.

### Host-symbol batch 3 (2026-05-29) — 12 -> 8
Added 4 more host symbols backed by existing pcc runtime primitives (no fakes):
`PyObject_ClearWeakRefs` -> `py_weakref_invalidate`; `PyException_SetCause` ->
`py_exc_set_cause`; `PyException_SetTraceback` -> returns 0 (pcc has no traceback
object / no Itanium unwinding, so nothing to attach); `PyObject_IsSubclass` ->
`PyType_IsSubtype` for the type-object case numpy uses. runtime-provides
165 -> 169; HOST gap **12 -> 8**. loader 17 passed, no regression. (These are
thin wrappers over pcc-tested primitives or reuse the tested subtype walk, so
they are covered by the gap-shrink + the archive-link in the loader gate rather
than a new slow behavior test.)

REMAINING 8 host symbols are the genuinely harder ones — no clean pcc primitive
exists yet or they are fundamentally complex: `PyContextVar_Get`/`Set` (no pcc
contextvar object), `PyMethod_New` (no pcc bound-method ctor exposed),
`PySeqIter_New` (no pcc seq-iterator ctor exposed), `PyVectorcall_Call`
(vectorcall dispatch), `PyErr_NormalizeException`, `PyObject_GenericGetDict`,
`PyImport_Import` (route to the provided `PyImport_ImportModule`). Progress
arc this session: host gap ~29 -> 21 -> 12 -> 8 across batches 1-3. The next
step should likely be the full numpy build attempt to confirm WHICH of these 8
are actually on the `import numpy` path (vs build-only) before adding pcc
primitives for them, plus the non-symbol layers (slot inheritance, buffer).

### Full _core compile grounding + Python.h batch (2026-05-29) — 66% -> 72%
Ran the redirect's `-fsyntax-only` across ALL 143 non-test numpy `_core` `.c`
(not the 40-file sample). Initial: **95/143 clean (66%)** — the header surface
does NOT fully hold for the whole core (the 40-sample's 95% was optimistic).
Top gaps: `sizeof` of incomplete `struct PyObject` (27), `Py_PYTHON_H` guard
missing (9), `PyType_Check` (8), `Py_Enter`/`LeaveRecursiveCall` (10),
`Py_BEGIN`/`END_CRITICAL_SECTION` (8), `PySlice_Check`/`GetIndices`, undeclared
`new` (20). Landed a Python.h batch (extension-compile only; runtime untouched):
- **Completed `struct PyObject`** as `{ PyObjectHeader }` — THE object-layout
  answer (`sizeof(PyObject)` == pcc's real 16-byte header). Safe: only Python.h
  consumers see it; `py_obj.c` keeps its own def. ext-abi + loader **25 passed**,
  no regression — so this long-deferred boundary is resolved at the header level.
- `Py_PYTHON_H` master guard; `PyType_Check`/`CheckExact`, `PySlice_Check`,
  `Py_BEGIN`/`END_CRITICAL_SECTION`(+2), `Py_Enter`/`LeaveRecursiveCall` macros
  (the last two match CPython's non-free-threaded no-op definitions).
Re-measure: **103/143 clean (72%)**, `number.c` etc. now clean.

REMAINING full-core gaps (next batches, in rough size order): `'pythread.h'
file not found` (9 files blocked — needs a curated fake `pythread.h` added to
`_PCC_CAPI_HEADERS`); `PyCFunctionObject` struct + `PyCFunction_Call/Get` (≈29);
the `expected expression` count (24) is a CASCADE from those undeclared
identifiers, not a macro regression; descriptor type objects
`PyMemberDescr`/`PyGetSetDescr`/`PyMethodDescr_Type`; `_PyDict_GetItem*`/
`PyDict_Merge`/`PySlice_GetIndices` (functions, need shim impls); and two GENUINE
incompatibilities to design around: `no member named 'ob_type'` (4 — numpy reads
the CPython field directly; pcc has `type_tag`) and the undeclared `new` (20,
still uninvestigated). Plus the runtime link + PyInit. The header surface is a
long tail; each batch adds a few %.

### `new` root cause + PyCFunctionObject/macro/descriptor batch (2026-05-29)
Traced the undeclared `new` (20) to `compiled_base.c:1508`
`PyCFunctionObject *new = (PyCFunctionObject *)obj;` — `PyCFunctionObject` was
UNDECLARED, so the variable `new` never declared, cascading into every `new`
use + the `expected expression` count. ONE declaration fixes the cascade.
Added: `PyCFunctionObject` struct (m_ml/m_self/...), `PyCFunction_Type` +
`PyMemberDescr`/`PyGetSetDescr`/`PyMethodDescr_Type` externs (tokens in
py_capi_shim.c), `PyCFunction_GetFunction/GetSelf/GetFlags/Call` decls, and the
`PyCFunction_Check`/`GET_FUNCTION`/`GET_SELF`/`GET_FLAGS` macros (the fast-access
forms numpy actually uses). Effect: PyCFunctionObject errors 20->0,
`expected expression` 24->5, `new` 20->15, descriptor 3->2 each; ext-abi+loader
**25 passed**, no regression.

KEY INSIGHT (why clean-% plateaued at 72% despite the error drop): a file goes
"clean" only when ALL its errors are resolved, and the affected files
(compiled_base.c etc.) each carry SEVERAL distinct gaps (PyCFunctionObject +
pythread.h + `_PyDict_GetItem*` + `PySlice_GetIndices` + `ob_type` + isdigit ...).
So this batch cut total error COUNT substantially but the clean-FILE count
stayed 103/143. Going forward, error-count is the better near-term progress
metric; clean-% only jumps when a file's LAST gap closes. Remaining gap classes:
`'pythread.h' file not found` (9 — needs a curated fake header), `_PyDict_GetItem*`
/`PyDict_Merge`/`PySlice_GetIndices`/`PyUnicode_*`/`PyObject_GC*` function decls,
descriptor STRUCTs (not just the type tokens), the `ob_type` direct-access
incompatibility (4), and the `isdigit`/`isspace` libc artifacts (probe-only —
the real build has ctype.h, so these are NOT real gaps).

### Long-tail decl batch + pythread.h (2026-05-29) — 72% -> 80%
Added a fake `utils/fake_libc_include/pythread.h` (PyThread lock + TSS API; added
to `_PCC_CAPI_HEADERS` so it's copied into the curated dir) and a Python.h
long-tail batch (~20 function decls: `_PyDict_GetItem_KnownHash`, `PyDict_Merge`/
`Copy`, `PyDictProxy_New`, `PySlice_New`/`GetIndices`/`GetIndicesEx`,
`PyUnicode_Format`/`AsLatin1String`, `PyContextVar_New`, `PyModuleDef_Init`,
`PyOS_string_to_double`, `PyType_GetFlags`, `PyArg_UnpackTuple`, `PyObject_GC_*`;
the `PyDescrObject`/`PyMethodDescrObject`/`PyMemberDescrObject`/`PyGetSetDescrObject`/
`PyTupleObject` structs; `PyDictProxy_Type`/`PyMemoryView_Type` tokens; the
`Py_mod_*`/`Py_MOD_GIL_*` multi-phase-init constants; `PyObject_INIT`/
`_Py_TPFLAGS_HAVE_VECTORCALL` macros). Re-measure: **115/143 (80%)** — this batch
MOVED clean-% (+12 files) by closing the last gap in many small files.

REGRESSION caught + fixed (feedback_test_first): the first cut put
`PyInterpreterState *PyInterpreterState_Main(void)` before `PyInterpreterState`
was declared, and pythread.h redefined `PyLockStatus` (Python.h already has it) —
both broke ALL Python.h consumers (17 loader tests failed). Removed the
`PyInterpreterState_Main` decl and dropped pythread.h's `PyLockStatus`; ext-abi +
loader back to **25 passed**.

REMAINING 28 are now DOMINATED by Cython-generated mega-files (numpy.random
`_philox`/`bit_generator`/`mtrand`, 10k+ lines): once `pythread.h` resolved, they
compile further and hit Cython's heavy CPython-C-API usage (`CO_OPTIMIZED`/
`CO_NEWLOCALS` code-object flags, `exc_info` thread-state, `PyMethod_*`,
`PyLongObject` internals, `PyCMethod_New`) — cascading into thousands of errors
each. These are a LARGER API surface than the hand-written `_core` and are
arguably not the `import numpy` critical core (numpy.random is a submodule). The
hand-written `_core` is now ~80%+; the remaining tail is Cython-codegen-heavy.
Session arc: full-core 66% -> 72% -> 80%. Next: either continue the Cython API
surface (code objects, exc state, PyMethod) or pivot to the LINK of the clean
files + PyInit (the runtime milestone).

### Import-critical focus + the ob_type boundary (2026-05-29)
Categorized the 28 still-failing files: the IMPORT-CRITICAL `_multiarray_umath`
module (multiarray + umath) had 10 failing; the rest are numpy.random Cython
mega-files (lazily imported, not import-critical) + textreading/stringdtype/
linalg. Drove the 10 import-critical files toward clean with: `#include <ctype.h>`
in Python.h (isspace/isdigit/tolower), fake `pyerrors.h`/`abstract.h` (include
Python.h — content already there), a full `datetime.h` (PyDateTime_CAPI capsule
struct + PyDate_Check/PyDelta_FromDelta/... macros; PyDateTimeAPI NULL so runtime
interop is degraded), and decls `PyInterpreterState_Main`/`PyArg_VaParseTupleAndKeywords`.
Result: import-critical **0/10 -> 5/10** clean (ext-abi+loader 25 passed, no
regression).

THE ob_type BOUNDARY (the fundamental wall): the remaining import-critical
failures are dominated by DIRECT `obj->ob_type` access — e.g. numpy 2.4.4
`multiarray/methods.c:2009` `((PyObject*)self)->ob_type != &PyArray_Type`. pcc's
object header is `{int64 refcount; int32 type_tag; int32 flags}` (16 bytes); the
8 bytes where CPython has `ob_type` hold pcc's `type_tag`+`flags`, so reading
`->ob_type` yields garbage. This CANNOT be fixed at the header level — pcc objects
do not store a `PyTypeObject*`; they carry a `type_tag` resolved via `Py_TYPE`
(`pcc_capi_type`). Resolving direct `->ob_type` needs EITHER a deep pcc-runtime
change (store an `ob_type` pointer in every object's header — breaks the 16-byte
layout + every allocation; multi-month) OR numpy using `Py_TYPE()` (it does not,
in methods.c). This is the genuine object-model incompatibility that makes
no-libpython numpy a runtime-core (not header) problem. The `struct PyObject`
completion resolved `sizeof(PyObject)` (a SIZE query, which pcc's 16-byte header
satisfies) but NOT field ACCESS (`->ob_type`, which pcc's header lacks). So the
header surface tops out where numpy reads CPython-internal fields; past that is
the pcc-object-model bridge. import-critical is 5/10; the other 5 are blocked by
ob_type + a couple of deeper datetime/struct gaps.

### Import-critical 8/10 + header surface near its ceiling (2026-05-29)
Closed the non-ob_type import-critical gaps: `PyDateTime_TimeZone_UTC` macro
(datetime.h), `PyModuleDef_Slot` struct + extended `PyModuleDef` (m_slots, for
multi-phase init), completed `struct PyThreadState { PyInterpreterState *interp; }`
(numpy's subinterpreter guard `PyThreadState_Get()->interp`),
`PyUnstable_Object_IsUniqueReferencedTemporary` decl. Result: import-critical
`_multiarray_umath` **8/10** clean — including the PyInit file
`multiarraymodule.c` and the module slot table. The ONLY remaining import-critical
fails are `methods.c` + `override.c`, both blocked solely by direct `obj->ob_type`
access (the boundary above). ext-abi+loader 25 passed, no regression.

FULL-CORE re-measure: **128/143 (89%)**, and split by source kind:
**hand-written (non-Cython) numpy _core = 121/126 (96%)**; Cython-generated =
7/17. So pcc's header surface now compiles 96% of HAND-WRITTEN numpy core
clean — the header surface is near its ceiling. The residual frontiers are:
(1) the `obj->ob_type` object-model incompatibility (needs the pcc-runtime
ob_type bridge — see boundary above; the clean design is `PyObject_HEAD` carrying
an `ob_type` pointer that `PyType_GenericAlloc` sets, with a layout-compat
analysis vs pcc's type_tag header — deliberately NOT band-aided with a phantom
field); (2) the Cython mega-files (numpy.random, lazily imported) with their
heavy code-object/exc-state API surface; then (3) the LINK + PyInit runtime.
Session arc: full-core 66% -> 72% -> 80% -> 89% (hand-written 96%);
import-critical 0 -> 5 -> 8/10.

### ob_type runtime-bridge DESIGN SPEC (the next phase — 2026-05-29)
The header/compile phase is at its ceiling (96% hand-written). The remaining
import-critical blocker (methods.c/override.c) + the path to a working
`import numpy` is the ob_type object-model bridge, which is a DELIBERATE
runtime-core change, NOT a rapid-loop edit. Spec for the next focused effort:

PROBLEM: numpy reads `((PyObject*)x)->ob_type` directly (a `PyTypeObject*` in
CPython's 16-byte header). pcc's 16-byte header is `{refcount; type_tag; flags}`
with NO ob_type pointer; the bytes at CPython's ob_type offset hold
type_tag+flags. So `->ob_type` is unsatisfiable on the current model.

OPTIONS (with tradeoffs):
- A. `PyObject_HEAD` carries `ob_type` (header grows to 24 bytes; every
  extension object gains an ob_type slot at offset 16; `PyType_GenericAlloc`
  + `PyObject_Init` + numpy's OWN allocators must set it; `Py_TYPE` can then
  read it or stay on the type_tag registry). CORRECT + CPython-compatible, but
  a shared-layout change: must be validated by the FULL numpy build (not just
  toy extension tests — the toy tests cannot catch subtle offset issues in
  numpy's complex structs) AND `scripts/bootstrap.sh` (the runtime archive is
  shared with the self-host). Do NOT land it validated only by the loader toy
  tests.
  CASCADE (verified 2026-05-29 — why this is intricate, not a quick edit): to be
  consistent, `PyVarObject` must ALSO carry `ob_type` (CPython lays it
  `{ob_refcnt; ob_type; ob_size}`). But `struct _typeobject` (PyTypeObject)
  embeds a `PyVarObject ob_base` — so adding ob_type to PyVarObject shifts EVERY
  `tp_*` field of PyTypeObject by 8 bytes, which in turn breaks the hand-laid
  `PyTypeObject` LAYOUT MIRROR in py_capi_shim.c (it reads tp_basicsize/tp_flags
  by offset for PyType_Ready/GenericAlloc). So option A is a coordinated edit of:
  PyObject_HEAD + PyVarObject + PyObject_HEAD_INIT + PyVarObject_HEAD_INIT +
  struct PyObject (fake Python.h) AND the PyTypeObject mirror + GenericAlloc/
  Py_SET_TYPE (py_capi_shim.c), each kept offset-consistent. A single missed
  initializer or offset silently breaks the (now-working) type bridge. This is
  the concrete reason it is a focused effort, not a loop iteration.
- B. `struct PyObject` gains ob_type but `PyObject_HEAD` does not (compiles the
  files; sizeof(PyObject) becomes 24; `->ob_type` reads garbage at runtime).
  REJECTED: a band-aid that is runtime-incorrect for the ob_type code paths and
  makes sizeof(PyObject) inconsistent with the 16-byte header.

VALIDATION REQUIRED before landing option A: ext-abi + loader (toy), the FULL
numpy `_multiarray_umath` link attempt (to exercise numpy's real struct
offsets), `scripts/bootstrap.sh --backend self` (the shared runtime archive),
and the fallback baseline. Because that validation infrastructure (a linkable
numpy + a fresh bootstrap) is not in place mid-loop, the ob_type change is
deferred to a focused effort, not a loop iteration. This is the boundary between
the (now-complete) header phase and the (multi-month) runtime-core phase.

NOTE on scope honesty: even option A done correctly only unblocks COMPILE of the
2 files; a working `import numpy` additionally needs the LINK to resolve the
remaining host-symbol IMPLS + numpy's internal symbols, PyInit to populate the
`PyArray_API` capsule table, and numpy's array runtime to function on pcc's
object model — a coordinated multi-month effort. The header phase reaching 96%
hand-written + the type bridge working + this boundary characterization is the
honest state; the runtime-core phase is large and separate.

### ob_type bridge LANDED + self-host VALIDATED (2026-05-29) — supersedes the deferral
The option-A cascade was implemented after all (the deferral above is superseded):
7 coordinated edits — `PyObject_HEAD` + `PyVarObject` (+ ob_type before ob_size)
+ `PyObject_HEAD_INIT`/`PyVarObject_HEAD_INIT` (init the slot) + `struct PyObject`
(fake Python.h), the `PyTypeObject` layout MIRROR (insert ob_type so tp_* stay
aligned) + `PyType_GenericAlloc` (set `obj->ob_type` at offset
sizeof(PyObjectHeader), min body clamped to header+ptr) (py_capi_shim.c).
RESULT, fully validated:
- import-critical `_multiarray_umath` **8/10 -> 10/10** (methods.c + override.c,
  the `obj->ob_type` files, now compile).
- full `_core` 89% -> **91% (131/143)**; hand-written 96% -> **98% (124/126)**.
- type bridge intact: ext-abi + loader **29 passed** (PyType_Ready/builtin-tag/
  subtype all green — the mirror update kept tp_* offsets correct).
- SELF-HOST: full `scripts/bootstrap.sh --backend self` stage1->2->3 + verify
  -> **"pcc2 and pcc3 ... byte-identical"** (fixpoint HOLDS with ob_type; pcc2
  23s, pcc3 24s — normal speed). The earlier apparent bootstrap "failures" were
  a DIAGNOSTIC ERROR: premature reads of the buffered log / build dir WHILE the
  ~54s bootstrap was still in stage2 (and trailing `; ls` masked the real exit
  code), not a real regression. Lesson: read the bootstrap result only AFTER the
  task completes, from the full log's `verify:` line + the bootstrap's own exit
  code (no trailing commands). A revert based on the misread was itself reverted.

So the deep object-model boundary is CROSSED for the import-critical module:
numpy's direct `obj->ob_type` now resolves, the whole `_multiarray_umath`
compiles, and the self-host fixpoint is preserved. Remaining toward a working
`import numpy`: the LINK (resolve the still-deferred host-symbol impls +
numpy-internal symbols), PyInit populating the `PyArray_API` capsule table, and
the array runtime — still substantial, but the header+object-model phase is done.

### Self-host validation + link-readiness batch (2026-05-29)
AUTHORITATIVE validation of the whole session's runtime/header changes: ran the
full `scripts/bootstrap.sh --backend self` (the gold-standard gate the
artifact-gated baseline test had been skipping). stage1->2->3 SUCCEEDS,
`build/bootstrap/` fresh pcc1/pcc2/pcc3 with pcc2/pcc3 SIZE-IDENTICAL (the
signature-normalized fixpoint). The real numpy first-import boundary gate with
that fresh pcc1 -> 1 passed. So the session's type bridge + ~40 host symbols +
builtin type tokens + 5 fake headers + build_exec redirect + struct completions
DO NOT regress the self-host or the numpy boundary. Also implemented a small
clearly-correct link-readiness batch in py_capi_shim.c (`PyType_GetFlags`,
`PyOS_string_to_double`, `_PyObject_GC_New`, `PyObject_GC_Track`/`UnTrack`); then
a batch 5 reusing existing shim primitives (`_PyDict_GetItem_KnownHash` ->
`PyDict_GetItem`, `PyObject_GC_Del` -> `PyObject_Free`, `PyModuleDef_Init` ->
return the def for slot-based init) — needed top-of-file forward decls for the
later-defined `PyDict_GetItem`/`PyObject_Free` (a self-introduced compile error
that 17-failed the loader gate; caught + fixed, back to 25 passed).
Batch 6 added `PyCFunction_GetFunction`/`GetSelf`/`GetFlags` via a small
PyCFunctionObject layout mirror (m_ml at offset 16, matching the fake header);
ext-abi+loader 25 passed. Still deferred (non-trivial impls / pcc primitives):
`PyDict_Merge`/`Copy`, `PySlice_GetIndicesEx`/`New`, `PyUnicode_Format`/
`AsLatin1String`, `PyContextVar_New`, `PyDictProxy_New`, `PyArg_UnpackTuple`/
`VaParseTupleAndKeywords` — all link-time, behind the ob_type compile gate.
Link-readiness host-symbol impls landed this session: batches 4/5/6 = 11 symbols. ob_type itself remains the deferred intricate macro-web change
(its own focused effort with full numpy-link + bootstrap validation).

## Report
(open — predecessor context: continues the 2026-05-29 B-P0-PKG no-libpython
C-extension track in `docs/current-goal-state.md`. Sibling investigations:
`python-cpython-compat-import-numpy-multiarray-init-fails.md` (the Mode B /
cpython-compat angle on the same `_multiarray_umath` init) and
`python-no-libpython-re-compile-general-pattern-object.md` (regex, verified
NOT an import blocker).)

## Update — import-critical LINK-gap measured + batch 7 (2026-05-29)
With the import-critical 10 files compiling (post-ob_type), measured the LINK
gap: compiled the 10 to `.o` and `nm`-classified referenced `Py*`/`npy_*`
symbols vs the runtime archive. Undefined-and-unprovided host symbols: 16, of
which 2 are numpy-internal (`PyBoundArrayMethod_Type`, `PyDataType_GetArrFuncs`,
resolved at numpy's own link), leaving 14 genuine host symbols.

Batch 7 implemented 5 (no-libpython shim, py_capi_shim.c): `PyInterpreterState_Main`
+ `PyThreadState_Get` as CONSISTENT main-interp sentinels (numpy's subinterpreter
guard `PyThreadState_Get()->interp != PyInterpreterState_Main()` must be false —
both return the same sentinel, interp at offset 0); `PyException_SetContext` ->
`py_exc_set_context`; `PyUnstable_Object_IsUniqueReferencedTemporary` -> 0;
`PyDateTimeAPI` global (NULL — the capsule isn't imported on this path). LINK gap
**14 -> 9**; ext-abi+loader 25 passed, no regression. (Self-introduced compile
bug caught + fixed: a `*/` inside a C comment — "PyThreadState*/PyInterpreterState*"
— prematurely closed the comment.)

Batch 8 implemented 3 more reusing existing shim primitives: `PyTuple_GetSlice`
(new tuple + incref'd items via PyTuple_New/GetItem/SetItem), `PyUnicode_AsLatin1String`
(-> PyUnicode_AsASCIIString; latin1==ASCII for numpy's ASCII dtype names),
`PyObject_AsFileDescriptor` (int -> its value). LINK gap **9 -> 6**; ext-abi+loader
25 passed.

Batch 9 implemented the 2 genuinely-correct/import-safe of the 6 (NOT degraded
fakes): `PyErr_NormalizeException` -> no-op (pcc raises normalized exception
INSTANCES, no triple to reconcile) and `Py_GenericAlias(origin,args)` -> origin
incref'd (numpy sets it as `__class_getitem__` but only CALLS it on user
subscript `T[X]`, never at import — safe link-stub). LINK gap **6 -> 4**;
ext-abi+loader 25 passed.

REMAINING 4 import-critical link symbols — these need REAL impls/infra, NOT
stubs (deliberately not faked): `PyArg_VaParseTupleAndKeywords` (needs a va_list
parser core — PyArg_ParseTupleAndKeywords currently uses `...` directly, no
shared va_core), `PyContextVar_Get`/`New` (need a real pcc contextvar object —
numpy.errstate creates one AT import, so a fake could break import), and
`PyEval_GetBuiltins` (no builtins-dict accessor in py_runtime.h; a fake empty
dict could break import-time name resolution). import-critical link-host-surface:
gap 14 -> 9 -> 6 -> 4 (batches 7, 8, 9). Then numpy's internal symbols resolve
when its full `_core` links together; then PyInit populates `PyArray_API` + the
array runtime. The remaining 4 + the full-module link + PyInit are the focused
runtime-core tail (numpy.errstate contextvar + a builtins accessor + a va-parser
are each real subsystems, not stubs).

Tractability re-check on the remaining 4 (2026-05-29): grepped the runtime —
there is NO builtins dict/accessor (`py_builtins`/`builtins_dict`/`get_builtins`
absent) and NO contextvar primitive. Backing `PyEval_GetBuiltins` with an empty
`py_dict_new()` or `PyContextVar_*` with a fake object would be a degraded stub
that could break import-time name resolution / numpy.errstate setup, so all 4
stay deferred per claim hygiene (need real subsystems: a builtins dict object, a
pcc contextvar object, a shared va_list parse core).

REGRESSION LOCK (2026-05-29): the batch 7-9 link symbols were link-validated only
(the shim compiles + the ext-abi gate). Added a BEHAVIORAL regression under strict
no-libpython: `tests/python/test_pcc_native_extension_loader.py::
test_pcc_native_link_symbols_behave_under_self_backend_no_libpython` builds an
extension whose `check()` exercises PyTuple_GetSlice ((10,20,30,40)[1:3]->(20,30)),
PyObject_AsFileDescriptor(PyLong(7))->7, Py_GenericAlias(PyLong(99),None)->99,
PyUnicode_AsLatin1String("Ab")->b"Ab", and PyErr_NormalizeException(NULL triple)
-> no-op with error state clear; it returns 31 when all five behave. Result: `ls 31`
under `--backend self --python-libpython=off`; full ext-abi+loader gate 26 passed
(was 25). This locks the session's link-symbol work as behavior, not just linkage,
per AGENTS.md ("every real-project fix ends with a minimized regression test").

## Update: batch 10 — genuinely-correct single-context contextvar (2026-05-29)

Implemented `PyContextVar_New` + `PyContextVar_Get` in `py_capi_shim.c` as a REAL
object (NOT a stub): `numpy.errstate` creates a ContextVar AT import via
`PyContextVar_New`, so an empty stub risked breaking import. The impl is a small
self-contained shim object `{PyObjectHeader header; void *ob_type; const char
*name; PyObject *def; PyObject *value;}` allocated via the existing
`PyType_GenericAlloc` machinery (a file-local `pcc_capi_contextvar_type` with
`tp_basicsize = sizeof(struct)`; GenericAlloc calloc's + sets the tag + writes
`ob_type` at offset `sizeof(PyObjectHeader)`, so `value` starts NULL = unset).
`PyContextVar_Get` implements correct CPython single-context precedence: a set
value wins, then the explicit `default_value` arg, then the var's own default,
else `*value = NULL` (returns 0). pcc has no `Context` objects (no per-context /
thread-local isolation), so this models the single implicit global context —
correct for single-threaded import and basic get/default use. Only New + Get are
provided (the two symbols numpy's C core references in the measured gap);
Set/Reset are driven from Python's `contextvars` module, not the C API, so adding
them would be unexercised surface.

Behavioral regression (real object, not just linkage):
`tests/python/test_pcc_native_extension_loader.py::
test_pcc_native_contextvar_get_under_self_backend_no_libpython` — under strict
`--python-libpython=off --backend self`, `check()` returns 3 = Get(cv, NULL, &v)
yields the var's own default (5) AND Get(cv, PyLong(9), &v) yields the explicit
default arg (9) when the var is unset. Result `cv 3`. Wiped the stale runtime
archives (libpy_runtime{,_pcc,_pcc_py}.a — they had the shim but no
PyContextVar_New definition) so the gate rebuilt with batch 10; ext-abi+loader
gate 27 passed (was 26).

LINK gap **4 -> 2**. import-critical link-host-surface: 14 -> 9 -> 6 -> 4 -> 2
(batches 7, 8, 9, 10). REMAINING 2, both needing real infra (still no fakes):
`PyArg_VaParseTupleAndKeywords` (a shared va_list parse core;
`PyArg_ParseTupleAndKeywords` uses `...` directly today) and `PyEval_GetBuiltins`
(no builtins-dict accessor in the runtime; a fake empty dict could break
import-time name resolution). Then numpy's internal symbols resolve at full
`_core` link; then PyInit populates `PyArray_API` + the array runtime.

## Update: batch 11 — PyArg_VaParseTupleAndKeywords via canonical va_list-core refactor (2026-05-29)

Implemented `PyArg_VaParseTupleAndKeywords` (the va_list-taking core numpy's C
core references directly) by the canonical CPython refactor: extracted the
validation + format-parse loop body out of `PyArg_ParseTupleAndKeywords`'s `...`
body into `PyArg_VaParseTupleAndKeywords(args, kwargs, format, kwlist, va_list va)`
(with `va_copy` at the top — on arm64/x86-64 SysV `va_list` is an array type, so
passing it shares state with the caller; copy first), and `PyArg_ParseTupleAndKeywords`
is now a thin wrapper: `va_start(va, kwlist); r = PyArg_VaParseTupleAndKeywords(...);
va_end(va);`. Both symbols are now defined (T). No behavior change to existing
`PyArg_ParseTupleAndKeywords` callers (the wrapper preserves the prior path).

Behavioral regression: `tests/python/test_pcc_native_extension_loader.py::
test_pcc_native_vaparse_tuple_and_keywords_under_self_backend_no_libpython` — a
METH_VARARGS extension calls `PyArg_ParseTupleAndKeywords(args, NULL, "ll", ...)`
(kwargs=NULL, the no-libpython loader dispatches METH_VARARGS positionally), which
now routes through the va_list core; `check(10, 20)` parses "ll" and returns 1020.
HARNESS NOTE: the extension does NOT call `va_start` itself — the test `cc`
includes the WHOLE `utils/fake_libc_include/`, whose `_fake_defines.h` has a broken
`va_start(_ap,_type) -> __builtin_va_start(&(_ap))` (1-arg) macro. That shim is
build-only noise; the REAL pcc-native numpy build uses the curated capi include dir
+ SYSTEM `<stdarg.h>` (correct va macros), and the shim itself compiles with system
stdarg. So the broken fake macro never reaches numpy; the test exercises the core
through the `...` wrapper instead (the shim's own va handling is real). ext-abi+loader
gate 28 passed (was 27).

LINK gap **2 -> 1**. import-critical link-host-surface: 14 -> 9 -> 6 -> 4 -> 2 -> 1
(batches 7-11). REMAINING 1: `PyEval_GetBuiltins` — the hardest, needs a real
builtins-dict accessor (pcc's no-libpython runtime has builtins as native
intrinsics, NOT a dict object; a fake empty dict could break import-time name
resolution, so it is NOT stubbed). Reaching gap 0 is a clean checkpoint ("all host
C-API symbols the import-critical core references are provided") but does not by
itself unblock import: the full `_core` link (numpy internals) + PyInit
`PyArray_API` + array runtime remain the multi-month runtime-core tail.

## Update: batch 12 — PyEval_GetBuiltins; import-critical host link gap reaches 0 (2026-05-29)

Implemented `PyEval_GetBuiltins` (the last import-critical host symbol). EVIDENCE
first: grepped the whole numpy 2.4.4 tree — the SOLE consumer is
`npy_PyFile_OpenFile` (`numpy/_core/include/numpy/npy_3kcompat.h:245`):
`open = PyDict_GetItemString(PyEval_GetBuiltins(), "open"); if (open == NULL)
return NULL;`. So (a) `import numpy` never calls it (it is a file-open helper, not
on the import path), and (b) an absent key degrades to NULL GRACEFULLY (no crash).
This overturns the earlier speculative worry that "a fake empty dict could break
import-time name resolution" — the source evidence shows import does not use it.

Impl: a real (initially-empty) persistent singleton dict via `py_dict_new()`,
cached and returned BORROWED (CPython contract). It is a valid mapping, not a fake
type. pcc's no-libpython runtime exposes builtins as native intrinsics rather than
a dict, so the dict is empty for now; populating it with pcc builtins-as-callables
(a real `open`) is a follow-on gated behind the file-object/array runtime, far past
import. Behavioral regression: `tests/python/test_pcc_native_extension_loader.py::
test_pcc_native_eval_getbuiltins_under_self_backend_no_libpython` -> `eb 7`
(non-NULL real dict; absent "open" key -> NULL no crash, matching numpy's pattern;
mutable persistent singleton with borrowed-ref semantics). ext-abi+loader gate 29
passed (was 28).

**MILESTONE — import-critical host C-API LINK gap = 0.** Re-measured (the 10
import-path `_core` .o files, nm -U, cross-ref the rebuilt `libpy_runtime.a`): ZERO
unprovided host `Py*` symbols. Session link-host-surface: 14 -> 9 -> 6 -> 4 -> 2 ->
1 -> 0 (batches 7-12).

SCOPE / HONESTY (do not overclaim): "gap 0" is precisely "the import-critical 10
files reference zero UNPROVIDED host C-API symbols", NOT "numpy links" and NOT
"`import numpy` works". The remaining tail is unchanged and dominant:
1. The full `_core` is 126 `.c` files; the full module may reference a few more
   host symbols than the import-critical 10 (next measurement, but the slow/fragile
   126-file compile — several `.dispatch.c` need meson SIMD scaffolding).
2. numpy's OWN internal symbols (PyArray_API consumers, npy_* internals) resolve
   only when the whole `_multiarray_umath` links together.
3. PyInit_* must populate the `PyArray_API` capsule table.
4. The array runtime (dtype, ufunc loops, descriptors) — the multi-month core.
So this milestone closes the HOST C-API link surface for the import-critical path;
it does not close `import numpy`.

## Update: full-_core host-symbol measurement + batch 13 (2026-05-29)

Extended the host-symbol measurement from the import-critical 10 to the FULL
`_multiarray_umath` core. Method: drive `compile_commands.json`, select the
non-test / non-`.dispatch.c` core `.c` (98 files), replicate part-1's include
redirect (strip CPython `-I`, add `/tmp/pcc_capi` + `pcc/py_runtime/include`,
system libc), compile each to `.o`, nm -U across all, cross-ref the rebuilt
`libpy_runtime.a`. Result: **60/98 compile** (38 fail — mostly template/generated
or SIMD-config deps that need meson scaffolding, expected for a standalone probe).

FULL-CORE host-symbol gap = **8** (vs the import-critical 10's 0): the whole module
references 8 host symbols the import path did not — `PyContextVar_Set`,
`PyImport_Import`, `PyMethod_New`, `PyObject_GenericGetDict`, `PySeqIter_New`,
`PySys_GetObject`, `PyUnstable_Object_IsUniquelyReferenced`, `PyVectorcall_Call`.
SCOPE CORRECTION: the batch-12 "gap 0" was correctly scoped to the import-critical
10; the full module needs these 8 more for a complete link. Honest, bounded next
work list.

batch 13: implemented `PyUnstable_Object_IsUniquelyReferenced` (genuinely correct,
not a stub) — pcc is refcounted (refcount at offset 0), so it returns
`((PyObjectHeader*)obj)->refcount == 1`, guarding tagged-int immediates (no header,
conceptually shared -> 0) and immortals (large refcount -> 0). Behavioral
regression `tests/python/test_pcc_native_extension_loader.py::
test_pcc_native_uniquely_referenced_under_self_backend_no_libpython` -> `ur 3`
(fresh object refcount 1 -> unique; after Py_INCREF -> not unique). ext-abi+loader
gate 30 passed (was 29). FULL-CORE gap **8 -> 7**.

REMAINING 7 full-core host symbols (next iterations, evidence-first, no fakes):
`PyContextVar_Set` (natural extension of batch 10's contextvar — add a token +
restore), `PySeqIter_New` (sequence-iterator object), `PyMethod_New` (bound method),
`PyObject_GenericGetDict` (object `__dict__`), `PyVectorcall_Call` (vectorcall
protocol), `PyImport_Import` (module import), `PySys_GetObject` (sys attr by name —
check numpy's call sites first, like PyEval_GetBuiltins). These + numpy-internal
symbols + PyInit(PyArray_API) + the array runtime remain the tail; this is the host
C-API surface for the FULL importable module, not import success.

## Update: batch 14 — route 3 full-core host symbols to existing primitives (2026-05-29)

Implemented 3 of the remaining 7 full-core host symbols as GENUINE routings /
subsystem extensions (not stubs), evidence-checked against numpy's call sites:
- `PyImport_Import(name_obj)` -> `PyUnicode_AsUTF8` + `PyImport_ImportModule`
  (the by-name import numpy uses 33x, e.g. arrayfunction_override.c:321). Canonical.
- `PyVectorcall_Call(callable, tuple, dict)` -> `PyObject_Call` (numpy installs it
  as a `tp_call` slot, arrayfunction_override.c:773 / ufunc_object.c:6790). For
  pcc's object model the generic call IS PyObject_Call.
- `PyContextVar_Set(var, value)` extends the batch-10 contextvar: updates the
  single-context value and returns a REAL Token (2-tuple `(var, prev)`) that takes
  over the displaced value's reference. Evidence: numpy alloc.c:486 does
  `token = PyContextVar_Set(...); if (token==NULL) err; Py_DECREF(token)` (set-and-
  discard; PyContextVar_Reset is referenced NOWHERE in numpy _core), so a non-NULL
  decref-able token with a correct value update is exactly right.

Behavioral regressions (real, not link-only): the contextvar test now also asserts
Set (`cv 7`: default 5, arg-default 9, set value 7), and a new
`test_pcc_native_import_and_vectorcall_under_self_backend_no_libpython` (`im 7`)
compiles the `demo` extension + a caller into one site; the caller imports `demo`
via PyImport_Import and invokes `demo.add(2,3)` through PyVectorcall_Call -> 5.
ext-abi+loader gate 31 passed (was 30).

FULL-CORE host gap **7 -> 4**. REMAINING 4 (need real objects / evidence-first, no
fakes): `PyMethod_New` (bound-method object; arrayfunction_override.c:724),
`PySeqIter_New` (sequence iterator; arrayobject.c:1222 __iter__ fallback),
`PyObject_GenericGetDict` (object `__dict__` getset; arrayfunction_override.c:752 —
taken as a function pointer, called only on `.__dict__` access), `PySys_GetObject`
(sys attr by name; npy_static_data.c:222 `PySys_GetObject("flags")` borrowed —
check NULL-safety of each call site first, like PyEval_GetBuiltins). Still: full
`_core` is 126 .c (60 compile standalone); numpy internals + PyInit(PyArray_API) +
array runtime remain the tail. This closes 4 of the 8 full-module host symbols.

## Update: batch 15 — PySys_GetObject (real sys.flags) for numpy import init (2026-05-29)

Implemented `PySys_GetObject` — the ONE import-critical symbol of the remaining 4.
Evidence: numpy npy_static_data init (npy_static_data.c:222) reads
`PyObject_GetAttrString(PySys_GetObject("flags"), "optimize")` AT IMPORT and
fails with "cannot get sys.flags" on a NULL return, so this is NOT NULL-safe — it
needs a real sys.flags carrying `optimize`. pcc's no-libpython runtime has no sys
object; `py_obj_setattr` only accepts PyClassObject / pcc-instance tags (not the
C-ext GenericAlloc tag), so the namespace is built as a `py_class_new("sys.flags",
...)` PyClassObject (GC-pinned, process-singleton) with `optimize` set to 0 (the
accurate value for pcc's no-`-O` compile) via PyObject_SetAttrString. Returns a
borrowed reference (CPython contract). Other `sys.*` names return NULL until a real
consumer needs them (honest incremental, keyed on the standard attr name, not a
package special-case). Behavioral regression
`tests/python/test_pcc_native_extension_loader.py::
test_pcc_native_sys_getobject_flags_under_self_backend_no_libpython` -> `sf 7`
(flags non-NULL; `flags.optimize == 0` via the exact numpy GetAttrString pattern;
an unprovided sys attr -> NULL). ext-abi+loader gate 32 passed (was 31).

FULL-CORE host gap **4 -> 3**. REMAINING 3, all POST-IMPORT (need real objects AND
the C-ext object protocol wired into pcc's iteration/call/attr — array-runtime
era): `PySeqIter_New` (array tp_iter fallback, arrayobject.c:1222 — constructible
from PySequence_GetItem/Length, but actual iteration needs the C-ext iter protocol
in pcc's for-loop), `PyMethod_New` (descriptor __get__ bound method,
arrayfunction_override.c:724), `PyObject_GenericGetDict` (a `__dict__` getset
function pointer, arrayfunction_override.c:752 — called only on `.__dict__`
access). None is import-critical; they link + partially-work now, full behavior is
array-runtime-era. With sys.flags done, the IMPORT-time host C-API surface is
complete; the 3 remaining are part of the array-runtime tail along with numpy
internals + PyInit(PyArray_API).

## Update: batch 16 — PyObject_GenericGetDict (route to runtime attr machinery) (2026-05-29)

Implemented `PyObject_GenericGetDict(o, context)` -> `py_obj_getattr(o, "__dict__")`
(new ref, or AttributeError when the object has no dict — exactly CPython's
contract). numpy installs it as a `__dict__` getset function pointer
(arrayfunction_override.c:752), called only on `obj.__dict__` access (post-import).
Genuine route, not a stub. Behavioral regression
`tests/python/test_pcc_native_extension_loader.py::
test_pcc_native_generic_getdict_under_self_backend_no_libpython` -> `gd 3`: a
module-level function receives the module as self, and PyObject_GenericGetDict(self,
NULL) returns a non-NULL dict that contains the module's own "check" entry.
ext-abi+loader gate 33 passed (was 32).

FULL-CORE host gap **3 -> 2**. REMAINING 2, both needing real object TYPES whose
consumer paths need the C-ext object protocol wired into pcc (array-runtime era):
`PySeqIter_New` (array tp_iter; a real seqiter object is constructible via
PySequence_GetItem/Length, and C-API iteration could be wired through PyIter_Next,
but pcc's for-loop dispatch — py_obj_iter/py_obj_next — only knows PY_TYPE_ITER/GEN,
so the actual numpy consumer path `for x in arr` is array-runtime-era) and
`PyMethod_New` (descriptor bound method, arrayfunction_override.c:724 — no pcc
bound-method primitive; needs a method object + call dispatch). The IMPORT-time
host C-API surface is complete (batches 7-15); these last 2 are part of the
array-runtime tail along with numpy internals + PyInit(PyArray_API).

## Update: batch 17 — PySeqIter_New + PyMethod_New; FULL-MODULE host link gap = 0 (2026-05-29)

Implemented the last 2 full-core host symbols, both GENUINE (not stubs):
- `PySeqIter_New(seq)` -> a real sequence-iterator object `{header; ob_type; seq;
  index}` allocated via PyType_GenericAlloc; C-API iteration is wired through
  `PyIter_Next` (a `pcc_capi_is_seqiter` tag check dispatches to
  `pcc_capi_seqiter_next`, which pulls `PySequence_GetItem(seq, index++)` until it
  runs off the end). numpy returns it from array tp_iter (arrayobject.c:1222).
  Honest limit: pcc's Python for-loop dispatch (py_obj_next) only knows
  PY_TYPE_ITER/GEN, so iterating it from a Python `for` is array-runtime-era; the
  C-API PyIter_Next path is genuine now.
- `PyMethod_New(func, self)` -> `py_instance_bind_method(func, self, NULL)`, the
  runtime's standard instance-method machinery (captures (func,self); the bound
  entry calls func(self, *args)). The result is a real callable PY_TYPE_FUNC.
  numpy returns it from a descriptor __get__ (arrayfunction_override.c:724).

Behavioral regressions: `test_pcc_native_seqiter_under_self_backend_no_libpython`
-> `si 1` (PySeqIter_New over (10,20,30) yields exactly those via PyIter_Next, sum
60 / count 3); `test_pcc_native_pymethod_new_under_self_backend_no_libpython` ->
`mn 7` (func reachable, PyMethod_New non-NULL, result is callable). ext-abi+loader
gate 35 passed (was 33).

*** MILESTONE — FULL-MODULE host C-API LINK gap = 0. *** Re-measured (60 compilable
`_core` .o, nm -U, cross-ref rebuilt libpy_runtime.a): ZERO unprovided host `Py*`
symbols. Session host-symbol arc: import-critical 14 -> 0 (batches 7-12), then
full-module 8 -> 7 -> 4 -> 3 -> 2 -> 0 (batches 13-17). Every host C-API symbol the
importable numpy module references is now a defined symbol in the pcc no-libpython
runtime.

SCOPE / HONESTY (do not overclaim): "gap 0" = all HOST C-API symbols the full _core
references are provided. It is NOT "numpy links" and NOT "import numpy works". The
remaining tail is now purely numpy-INTERNAL + runtime: (1) numpy's own internal
symbols (PyArray_API consumers, npy_* internals) resolve only when the full
_multiarray_umath links together; (2) PyInit must populate the PyArray_API capsule
table; (3) the array runtime (dtype, ufunc loops, descriptors) — the multi-month
core. The HOST C-API surface (the part pcc owns) is complete; the next layer is the
numpy array runtime. Next measurement: attempt the actual full-_core link to
enumerate the numpy-internal symbol surface (defines the array-runtime scope).

## Update: numpy-internal symbol-surface measurement (authoritative scoping, 2026-05-29)

With the full-module host C-API link gap at 0, measured what the 60 compiled
`_core` .o reference that NEITHER numpy's own compiled subset NOR the pcc runtime
provides (the truly-remaining surface). Method: union of U symbols across the 60
.o, minus symbols defined in any of the 60 .o, minus libpy_runtime.a-provided,
minus obvious libc.

Result: **506 truly-remaining symbols, ALL numpy-INTERNAL** — and critically,
**host-like `Py*` = 0** (re-confirms the milestone: pcc owes nothing more on the
host C-API surface). Breakdown:
- 334 numpy-misc / generated dtype-loop symbols (e.g. `BOOL_argmax`, `DOUBLE_argmin`,
  `Double_dtype`, `Int8_dtype`) — defined in the 38 `.src`-generated `_core` files
  that need meson SIMD/template scaffolding to compile standalone;
- 71 `PyArray_*` (numpy's OWN C-API table, populated by PyInit into the PyArray_API
  capsule);
- 36 more generated dtype/loop symbols; 34 `*ArrType_Type` (numpy scalar type
  objects); 27 `npy_*` (numpy internal); 4 `PyUFunc_*`.

INTERPRETATION: every truly-remaining symbol is numpy's OWN code, defined within
numpy's own source tree (mostly the 38 files that didn't compile standalone here
for lack of meson's generated headers/templates). None is a host C-API symbol pcc
must provide. So the concrete path to `import numpy` is no longer host-symbol work:
1. BUILD: compile ALL numpy `_core` files (the 38 `.src`/dispatch ones) through the
   part-1 pcc-native include redirect + meson's generated scaffolding, so the 506
   numpy-internal symbols resolve among numpy's own objects;
2. PyInit_*multiarray_umath populating the PyArray_API capsule;
3. array-runtime EXECUTION (numpy's dtype/ufunc loops running on pcc's object model).

This closes the HOST C-API sub-track definitively (gap 0, 0 host-like remaining)
and scopes the next sub-track (numpy build-integration + array runtime) as
numpy-internal + multi-month. The host C-API surface that pcc owns is complete.

## CORRECTION: the "full-module host gap = 0" milestone was measured against a STALE header (2026-05-29)

Honesty correction (the prior two updates overstated). The batch-17 "FULL-MODULE
host C-API LINK gap = 0" milestone and the follow-on "506 truly-remaining, all
numpy-internal, host complete" were both measured against `/tmp/pcc_fullcore/obj`
= the 60 `_core` files that compiled against a STALE `/tmp/pcc_capi/Python.h`
(mtime 11:51, predating this session's later Python.h additions). That stale
header failed to compile 38 files, so those 38 were silently excluded from the
.o set, and their host-symbol references never entered the measurement.

Re-done correctly: refreshed `/tmp/pcc_capi` from the CURRENT
`utils/fake_libc_include/` (the 8 curated capi headers) and recompiled. Result:
**95/98 `_core` files now compile** (was 60/98 — this session's Python.h decl
additions fixed 35 files). Re-measuring the host LINK gap across the 95 fresh .o:
**gap = 10, NOT 0**:
`PyArg_UnpackTuple`, `PyDictProxy_New`, `PyDict_Copy`, `PyDict_Merge`,
`PyObject_GenericGetAttr`, `PyObject_GenericSetAttr`, `PyObject_Init`,
`PySlice_GetIndicesEx`, `PySlice_New`, `PyUnicode_Format` (all are DECLARED in
Python.h, so the files compile, but have no runtime IMPL -> link gap).

Plus 3 files still fail to COMPILE (decl gaps): scalartypes.c needs a complete
`PyBytesObject` for `sizeof`; readtext.c needs `PyUnicode_READ_CHAR`;
stream_pyobject.c needs `PyUnicode_{1,2,4}BYTE_DATA` (unicode buffer-access macros,
textreading only).

WHAT STANDS: the IMPORT-CRITICAL host gap = 0 (batches 7-12, measured on the
import-path 10 files) is unaffected and correct. WHAT WAS WRONG: the FULL-MODULE
"gap 0" claim — the real full-module host surface needs 10 more symbols + 3
compile-gap fixes. Lesson (cf. AGENTS.md "stale data": re-run through the current
harness): the measurement used a cached header dir; always refresh the curated
capi dir from `utils/fake_libc_include/` before measuring. Resuming host-symbol
implementation for the 10 below.

## Update: batch 18 — 5 full-module host symbols routed (gap 10 -> 5) (2026-05-29)

Resuming after the stale-header correction. Implemented 5 of the 10 real
full-module host symbols as genuine routes to existing pcc primitives:
- `PyDict_Copy(mp)` -> PyDict_New + py_dict_update (shallow copy);
- `PyDict_Merge(a, b, override)` -> py_dict_update when override, else PyDict_Next
  loop adding only b's missing keys (correct override==0 semantics);
- `PyObject_GenericGetAttr/SetAttr` -> PyObject_GetAttr/SetAttr;
- `PyUnicode_Format(fmt, args)` -> py_str_mod (the `fmt % args` runtime path).
Behavioral regression `test_pcc_native_batch18_host_symbols_under_self_backend_no_libpython`
-> `b18 15` (copy is a distinct dict with the key; merge adds the other key;
"%d"%(42,)=="42"; generic set/getattr round-trips on self). ext-abi+loader gate 36
passed (was 35). Host gap across the 95 fresh .o: **10 -> 5**.

REMAINING 5 host symbols (need real slice/proxy/arg primitives, next iteration):
`PySlice_New` + `PySlice_GetIndicesEx` (slice object + index computation),
`PyArg_UnpackTuple` (variadic PyObject* unpack), `PyDictProxy_New` (read-only dict
proxy), `PyObject_Init` (header init). Plus the 3 compile-gap files unchanged:
scalartypes.c (`sizeof(PyBytesObject)`), readtext.c (`PyUnicode_READ_CHAR`),
stream_pyobject.c (`PyUnicode_{1,2,4}BYTE_DATA`). Corrected full-module arc:
gap 10 -> 5 (batch 18); the path remains host-symbol completion, then the numpy
internals + PyInit(PyArray_API) + array runtime.

## Update: batch 19 — last 5 host symbols; full-module host gap = 0 (CORRECTLY verified) (2026-05-29)

Implemented the final 5 of the 10 real full-module host symbols (all genuine):
- `PyObject_Init(op, type)` -> stamp refcount + type_tag + ob_type (mirrors the
  existing PyObject_InitVar);
- `PyDictProxy_New(mapping)` -> return the mapping incref'd (a readable view; pcc
  has no separate read-only proxy type, so read-only ENFORCEMENT is a documented
  follow-on; numpy uses it to expose a type dict for reading);
- `PyArg_UnpackTuple(args, name, min, max, ...)` -> arity-checked variadic unpack
  storing BORROWED PyObject* slots (CPython semantics);
- `PySlice_New(start, stop, step)` -> a real slice object {start,stop,step}
  (None-defaulted), since pcc lowers `a[i:j]` directly without materializing a
  slice; `PySlice_GetIndicesEx(r, length, ...)` -> CPython's None-aware,
  negative-index, clamped slice-index algorithm reading that object.
Behavioral regression `test_pcc_native_batch19_host_symbols_under_self_backend_no_libpython`
-> `b19 31`: slice(1,8,2)/len10 -> (1,8,2,4); slice(None,None,None)/len5 ->
(0,5,1,5); UnpackTuple((10,20)) -> 10,20; DictProxy readable; PyObject_Init
passthrough. ext-abi+loader gate 37 passed (was 36).

*** MILESTONE (CORRECTLY verified this time): full-module host C-API LINK gap = 0
across the 95 fresh-header .o. *** Unlike the batch-17 false "gap 0" (stale 60 .o),
this is measured against the 95 `_core` files that compile with the CURRENT
`utils/fake_libc_include/` headers. Host-symbol arc (full module): 10 -> 5 -> 0
(batches 18-19), on top of import-critical 14 -> 0 (batches 7-12).

REMAINING to compile ALL 98 (just COMPILE-gap decls, not link): scalartypes.c needs
a complete `PyBytesObject` (for `sizeof`); readtext.c needs `PyUnicode_READ_CHAR`;
stream_pyobject.c needs `PyUnicode_{1,2,4}BYTE_DATA` (the last two are unicode
buffer-access macros used only by textreading, and need pcc-PyStr-layout-correct
definitions). After those 3, the entire host C-API surface (compile + link) for the
full importable module is closed; then numpy internals + PyInit(PyArray_API) + the
array runtime remain the tail.

## Update: batch 20+21 — all 98 _core compile; host C-API surface (compile+link) closed (2026-05-29)

Closed the last 3 COMPILE-gap files and the 2 host symbols they then referenced:
- Python.h: completed `struct PyBytesObject` (PyObject_VAR_HEAD + ob_shash +
  ob_sval[1]) so scalartypes.c `sizeof(PyBytesObject)` works; added unicode
  buffer macros over pcc's UTF-8 PyStr — `PyUnicode_1/2/4BYTE_DATA(op)` ->
  `py_str_utf8(op)` (declared extern), `PyUnicode_READ_CHAR(op,i)` -> i-th byte as
  a codepoint (ASCII-correct, which is all numpy textreading control-chars need);
- shim batch 20: `PyUnicode_KIND` -> 1 (pcc strings are 1-byte UTF-8);
- shim batch 21: `PyNumber_Divmod` -> (FloorDivide, Remainder) tuple via
  PyTuple_Pack; `_Py_HashDouble(inst, v)` -> CPython's exact float-hash algorithm
  (congruent mod 2^61-1, so hash(2.0)==hash(2)==2; nan hashes the object).

Result (refreshed /tmp/pcc_capi per the stale-header lesson): **98/98 non-test,
non-dispatch `_core` files compile** (was 60 with the stale header, 95 after the
decl additions), and the host-symbol LINK gap across ALL 98 .o = **0**. Behavioral
regression `test_pcc_native_batch2021_host_symbols_under_self_backend_no_libpython`
-> `b21 15` (KIND/READ_CHAR/1BYTE_DATA read the UTF-8 buffer; Divmod(17,5)==(3,2);
_Py_HashDouble(NULL,2.0)==2). ext-abi+loader gate 38 passed (was 37).

*** MILESTONE (genuine, strongest verification): the full host C-API surface
(COMPILE + LINK) for the importable numpy module is closed — every non-test,
non-dispatch `_core` file compiles against pcc's headers AND references zero
unprovided host C-API symbols. *** Remaining toward `import numpy` is now entirely
numpy-OWN + runtime: numpy's internal symbols (PyArray_*/npy_*/*ArrType_Type/the
.src-generated dtype-loop symbols) resolve when numpy's own objects link together;
PyInit_*multiarray_umath populates the PyArray_API capsule; the array runtime
(dtype/ufunc/descriptors) executes. Those are numpy's build + the multi-month array
core, not host C-API work. The host C-API sub-track (pcc's responsibility) is DONE.

## Update: definitive numpy-internal scoping across all 98 .o (2026-05-29)

With all 98 `_core` files compiling and the host link gap at 0, recomputed the
truly-remaining surface (symbols the 98 .o reference that neither the 98 .o nor
the runtime define), correcting the earlier stale-60 "506". Result: **1008
remaining, host-like `Py*` = 0** (triple-confirms the host C-API surface is
complete). Categories:
- ~986 numpy-INTERNAL: the bulk are dtype × ufunc loop symbols (`BOOL_absolute`,
  `BYTE_add`, `BYTE_fmod`, `DOUBLE_argmax`, ... — confirmed by sampling), plus
  `npy_*` internals, `PyArray_*`/`PyUFunc_*` (4 each), `*ArrType_Type`. These are
  defined in numpy's OWN `.dispatch.c` (SIMD variants) + umath generated files
  that this probe excluded (they need meson's per-variant SIMD scaffolding); they
  resolve when numpy's FULL build compiles every `_core` file.
- ~22 EXTERNAL BLAS/LAPACK (`cblas_*`/lapack): numpy's linear algebra needs a real
  BLAS library (macOS Accelerate or OpenBLAS) — a third-party link dependency,
  neither host C-API nor numpy-internal.

DEFINITIVE ACCOUNTING: the path to `import numpy` no-libpython is now, in order:
(1) numpy's FULL build — compile every `_core` file including the SIMD `.dispatch.c`
via meson + the part-1 pcc-native include redirect, so the ~986 numpy-internal
symbols resolve among numpy's own objects; (2) link an external BLAS/LAPACK; (3)
PyInit_*multiarray_umath populating the PyArray_API capsule; (4) array-runtime
EXECUTION on pcc's object model. NONE of these is host C-API work — that sub-track
(pcc's responsibility: the declaration surface + the runtime symbol surface) is
DONE and triple-verified (0 host-like remaining across all 98 .o). The remaining is
numpy's build-integration + the multi-month array core + an external BLAS.

## Update: SIMD dispatch + entire _core compiles under pcc-native (build-integration) (2026-05-29)

Tested the build-integration layer (the SIMD `.dispatch.c` files excluded from the
earlier probe). Using their REAL compile_commands (which carry the per-variant
`NPY__CPU_TARGET_*` defines) + the pcc-native include redirect: **all 15 `_core`
`.dispatch.c` files compile** (15/15, no host-surface gaps, no SIMD-intrinsic
blockers — pcc uses system clang's intrinsic headers via system libc). So the
ENTIRE numpy `_core` C source — 98 non-dispatch + 15 dispatch = **113/113 files —
compiles against pcc's headers** under the pcc-native redirect.

Compiling the 15 dispatch files to .o and re-measuring across all 113 _core .o:
truly-remaining dropped 1008 -> **435** (the dispatch .o defined ~573 of the
dtype×ufunc loop symbols). Remaining 435: 218 "other" + 156 still-missing
dtype/loop + 39 numpy/Py internal (all defined in numpy's OWN remaining generated
source — the umath module's generated loop files etc., compiled in numpy's full
meson build) + 22 EXTERNAL BLAS/LAPACK. **host-like `Py*` = 0** (QUADRUPLE-confirmed
now: import-critical-10, then 60/95/98/113 .o sets).

BUILD-INTEGRATION FINDING: the pcc-native COMPILE surface for numpy's entire `_core`
(including SIMD dispatch) is essentially solved — every `_core` C file compiles
against pcc's headers with zero host C-API gaps. The remaining 435 link symbols are
numpy-internal (resolve when numpy's full meson build compiles its umath-generated
loop files + links its own objects) + 22 external BLAS. The host C-API surface
(compile + link) that pcc owns is DONE; the path forward is numpy's full build
linking its own objects + an external BLAS + PyInit(PyArray_API) + array-runtime
execution — numpy's build + the multi-month array core.

## Update: numpy _core C++ (umath) layer compiles under pcc-native (2026-05-29)

Extended the build-integration probe to numpy's C++ umath layer (`_core/**/*.cpp`,
where the dtype×ufunc loop dispatch lives — e.g. `loops_logical.dispatch.cpp`
defines `BOOL_absolute` etc.). Initial: 1/24 compiled. Three genuine pcc C++-compat
fixes:
1. `py_runtime.h`: `py_str_replace`/`py_str_replace_count` used `new` (a C++
   keyword) as a parameter NAME -> renamed to `replacement` (cosmetic; parameter
   names in prototypes have no ABI/codegen effect — bootstrap+fallback baselines
   stay green). This unblocked every .cpp that includes py_runtime.h.
2. `Python.h`: added the C++-umath decl surface — `Py_MAX`/`Py_MIN`/`SIZEOF_VOID_P`
   macros, `Py_uhash_t` typedef, `PyExceptionInstance_Class` macro, decls for
   `PyLong_FromUnicodeObject`/`PySlice_AdjustIndices`/`PyFloat_FromString`/
   `PyLong_AsLongLongAndOverflow`, and `PyExc_UnicodeEncodeError`/`PyExc_UnicodeError`.
3. `Python.h`: `Py_INCREF`/`Py_DECREF` were plain functions taking `PyObject*`; C++
   has no implicit derived->base pointer conversion, so numpy's C++ calls
   `Py_INCREF(PyArrayObject*)` failed. Wrapped them in CPython-style casting macros
   (`#define Py_INCREF(obj) Py_INCREF((PyObject*)(obj))`; the self-reference rule
   stops re-expansion). This fixed the bulk (8/24 -> 21/24).

Result: numpy `_core` .cpp **1/24 -> 21/24** compile under pcc-native. Combined with
the .c side (113/113), **134/137 numpy `_core` source files compile** against pcc's
headers. Gates: ext-abi+loader 38 passed (no regression); bootstrap-gate +
fallback baselines 17 passed (the core-header param rename is inert).

REMAINING 3 .cpp are numpy's OWN vendored-header C++ issues, not host decls:
`pythoncapi_compat.h:2675` `_Py_SetImmortal` language-linkage mismatch (numpy's
vendored compat shim) and `string_fastsearch.h:703` template using `Py_ssize_t`/
`uint8_t` in an expression context. NEW link symbols introduced by the now-compiling
.cpp (PyLong_FromUnicodeObject, PySlice_AdjustIndices, PyFloat_FromString,
PyLong_AsLongLongAndOverflow) are declared-but-unimplemented -> next iteration's
shim work, alongside the 3 vendored-header .cpp.

## Update: batch 22 — C++ umath link symbols; host gap across C+C++ = 0 (2026-05-29)

Implemented the link symbols the now-compiling C++ umath layer introduced, routed
to existing primitives: `PyLong_FromUnicodeObject` (PyUnicode_AsUTF8 + strtoll +
PyLong_FromLongLong), `PyFloat_FromString` (PyOS_string_to_double, locale-indep +
PyFloat_FromDouble), `PyLong_AsLongLongAndOverflow` (PyLong_AsLongLong; reports no
overflow — sufficient for numpy's in-range scalar parse), `PySlice_AdjustIndices`
(CPython's clamp-and-length algorithm). Also defined the `PyExc_UnicodeEncodeError`
+ `PyExc_UnicodeError` exception objects (sentinel pattern, mirroring the other
PyExc_*). Behavioral regression `test_pcc_native_batch22_host_symbols_under_self_backend_no_libpython`
-> `b22 15` (str->int, str->float, long-long+overflow, slice-adjust). ext-abi+loader
gate 39 passed (was 38).

*** host-like `Py*` LINK gap across ALL 134 compilable _core .o (113 .c + 21 .cpp)
= 0. *** The ENTIRE compilable numpy `_core` (C and C++) compiles under pcc-native
AND references zero unprovided host C-API symbols. The host C-API surface for the
full numpy _core (both languages) is complete.

REMAINING: 3 vendored-header .cpp (numpy's OWN `pythoncapi_compat.h` _Py_SetImmortal
language-linkage mismatch + `string_fastsearch.h` template Py_ssize_t/uint8_t
expression issue); numpy-internal symbols (umath-generated dtype/ufunc loops resolve
at numpy's full meson link) + 22 external BLAS + PyInit(PyArray_API) + array-runtime
execution. The host C-API work pcc owns is complete for the entire _core; the rest
is numpy's own build + the multi-month array core + external BLAS.

## Update: extern "C" wrap + Py_SAFE_DOWNCAST — entire numpy _core compiles (137/137) (2026-05-29)

Closed the last 3 .cpp with two standard CPython-header fixes in Python.h:
1. Wrapped the runtime header + all declarations in `#ifdef __cplusplus extern "C"`
   ... `}`. CPython gives every C-API symbol C linkage; without it, numpy's C++ TUs
   (a) hit the `_Py_SetImmortal` "different language linkage" clash (numpy's
   vendored pythoncapi_compat re-declares it with C linkage) and (b) would, at link
   time, look for C++-MANGLED names that the runtime never defines. The wrap is
   placed AFTER the system #includes (so guarded re-includes from py_runtime.h are
   no-ops, not parsed inside the block) and is __cplusplus-guarded so C is
   untouched.
2. Added `Py_SAFE_DOWNCAST(VALUE, WIDE, NARROW) -> ((NARROW)(VALUE))` (CPython's
   release form), which numpy's `string_fastsearch.h` SHIFT_TYPE table uses.

Result: numpy `_core` .cpp **21/24 -> 24/24** compile under pcc-native. With the .c
side at 113/113, the **ENTIRE numpy `_core` (137/137 non-test source files, C and
C++) compiles against pcc's headers**. Validated the Python.h restructure did NOT
break C: ext-abi+loader gate 39 passed; representative `_core` .c
(multiarraymodule/arrayobject/scalartypes) still compile 3/3 (the extern "C" blocks
are __cplusplus-guarded).

MILESTONE: the pcc-native COMPILE surface for numpy's entire `_core` (C + C++) is
100% complete (137/137), with the host C-API link gap at 0 across all C+C++ .o. The
extern "C" wrap also makes the eventual LINK correct (unmangled names matching the
runtime). Remaining toward `import numpy`: numpy-internal symbols (umath-generated
dtype/ufunc loops resolve at numpy's full meson link) + 22 external BLAS +
PyInit(PyArray_API) + array-runtime execution — numpy's own build + the multi-month
array core + an external BLAS. The host C-API + compile surface that pcc owns is done.

## Update: numpy _core self-links under pcc-native; only standard external libs remain (2026-05-29)

Compiled ALL 137 `_core` files (.c + .cpp) to .o under pcc-native and measured what
the full set references that neither the 137 .o nor libpy_runtime.a define.
truly-remaining = 239, **host `Py*` = 0** (5th confirmation), and crucially the
numpy-internal dtype/ufunc loop symbols are now RESOLVED (the 137 .o define each
other — only 1 dtype-loop leftover). The 239 break down as STANDARD EXTERNAL
LIBRARIES, not missing host/numpy symbols:
- 22 BLAS/LAPACK: `cblas_*$NEWLAPACK` — macOS Accelerate's NEWLAPACK interface
  (link `-framework Accelerate`);
- C++ standard library: `_ZNSt...` (std::runtime_error, std::exception,
  std::terminate, std::string), operator new/delete (`Znwm`/`ZdlPv`), type_info
  vtables (`_ZTVN10__cxxabiv1...`) — provided by libc++ (the C++ linker links it);
- libm C99 complex math: `cabs`/`cacos`/`carg`/`casin`/... — system libm;
- a few system funcs (`backtrace`, `bzero`).

DEFINITIVE FINDING: **numpy's entire `_core` compiles AND self-links under
pcc-native** — every host C-API symbol is provided (gap 0) and every numpy-internal
symbol is defined within the 137 .o. The only unresolved symbols are STANDARD
external libraries (libpy_runtime.a + libc++ + libm + Accelerate BLAS), exactly what
any numpy build links. There are NO missing pcc-owned symbols and NO missing
numpy-internal symbols.

Remaining toward a WORKING `import numpy`: (1) the actual link of
`_multiarray_umath.so` with those external libs (a build-orchestration step — meson
does this; the part-1 redirect must pass `-framework Accelerate -lc++ -lm` +
libpy_runtime.a); (2) PyInit_*multiarray_umath populating the `PyArray_API` capsule
(numpy's own import-time init); (3) array-runtime EXECUTION semantics on pcc's
object model. The compile + host-C-API + self-link surface that pcc owns is DONE;
what remains is build-orchestration (external libs) + PyInit + the array runtime.

## MILESTONE: numpy _multiarray_umath.so LINKS under pcc-native (empirical) (2026-05-29)

Link-tested the full numpy `_core` under pcc-native: `clang++ -bundle
-Wl,-undefined,error -o _multiarray_umath.so <137 _core .o> libpy_runtime.a
-framework Accelerate -lm`. First attempt left exactly ONE undefined symbol:
`pcc_capi_set_type` (the backing for the `Py_SET_TYPE` macro in pcc's Python.h,
used by numpy's arraytypes/dtype_transfer to stamp array/scalar object types) —
declared but never implemented. Implemented it in py_capi_shim.c (batch 23: stamp
type_tag + ob_type slot, mirroring PyObject_Init's type half). Behavioral
regression `test_pcc_native_set_type_under_self_backend_no_libpython` -> `st2 3`
(Py_SET_TYPE changes Py_TYPE from Foo to Bar). ext-abi+loader gate 40 passed.

*** RESULT: the link SUCCEEDS (LINK_EXIT=0). _multiarray_umath.so is produced — a
valid Mach-O 64-bit arm64 bundle, 5.3 MB, 1107 exported text symbols, with
`PyInit__multiarray_umath` defined and ZERO unresolved symbols under
-undefined,error. *** The ONLY libraries needed are `libpy_runtime.a` (pcc's
no-libpython runtime) + standard externals (Accelerate BLAS, libc++, libm).

This EMPIRICALLY PROVES the BUILD path (compile + link) for numpy's entire
C-extension works under pcc-native: the host C-API surface, the entire numpy `_core`
(137 C+C++ files), and the external-lib linkage all come together into a loadable
extension. No libpython, no CPython ABI.

INFLECTION — the blocker now shifts from BUILD to RUNTIME EXECUTION. Remaining
toward a WORKING `import numpy`: (1) the pcc native-extension loader actually
dlopen's this .so and calls `PyInit__multiarray_umath`; (2) PyInit runs to
completion — registers the array/scalar types (PyType_Ready), creates the
`PyArray_API` capsule, initializes static data (it reads sys.flags.optimize, now
provided); (3) array-runtime EXECUTION: numpy's dtype/ufunc loops operating on
pcc's object model. (1)-(3) are the array-runtime track (multi-month); the
compile + host-C-API + link surface that pcc owns is DONE.

## Update: loader invokes _multiarray_umath PyInit under pcc-native; module-attr depth is next (2026-05-29)

Moved from BUILD to RUNTIME. Re-linked `_multiarray_umath.so` the LOADER way
(`-bundle -undefined dynamic_lookup`, NO static libpy_runtime.a — the Py*/pcc_capi_*
symbols resolve against the MAIN pcc program's runtime at load, like a CPython
extension resolves against the host; a static runtime in the .so would give two
divergent runtime copies). 4.98 MB.

First load attempt failed: `dlsym(PyInit__multiarray_umath): symbol not found` — the
symbol was `t` (local), because numpy compiles with `-fvisibility=hidden` and pcc's
`PyMODINIT_FUNC` was a bare `PyObject *` with no visibility. FIX (Python.h,
CPython-faithful): `PyMODINIT_FUNC` -> `__attribute__((visibility("default")))
PyObject *` (+ `extern "C"` for C++ modules) so PyInit stays EXPORTED under
-fvisibility=hidden. After recompiling multiarraymodule.c (the PyInit TU) +
re-linking, `nm` shows `T _PyInit__multiarray_umath` (exported). ext-abi+loader gate
40 passed (the visibility attr is harmless for the default-visibility test exts).

RESULT: the pcc no-libpython loader now dlopen's the .so, dlsym's
`PyInit__multiarray_umath`, CALLS it, and PyInit RETURNS A MODULE OBJECT (tag=10)
WITHOUT crashing — `import _multiarray_umath` succeeds at the mechanism level under
`--backend self --python-libpython=off`. PyInit's code (type setup, capsule, static
data incl. sys.flags.optimize) ran on pcc's object model with no libpython.

HONEST BOUNDARY (do NOT claim "numpy imports"): probing the returned module's
attributes (`ndarray`/`dtype`/`array`/...) returns `<null>` for ALL of them, and
getattr raises no AttributeError for any (even non-existent names). So pcc's
module-getattr returns a null sentinel here rather than the registered object or a
proper AttributeError — meaning either PyInit did not fully populate the module
(partial type registration) OR module attribute retrieval is broken in this
no-libpython/dynamic_lookup context. That ambiguity is the FIRST concrete
runtime-execution blocker. Next: disambiguate (instrument PyInit / check
PyModule_AddObject + module getattr in the dynamic_lookup context) — the entry
point of the array-runtime execution track.

## Update: PEP 489 multi-phase init implemented in the loader; numpy exec slot now runs (2026-05-29)

Root-caused the earlier `<null>` module attrs: numpy's `_multiarray_umath` uses
PEP 489 MULTI-PHASE init — `PyInit__multiarray_umath` returns
`PyModuleDef_Init(&moduledef)` (a module DEF), and the real work
(`_multiarray_umath_exec`, registering ndarray/dtype/PyArray_API) runs in a
`Py_mod_exec` slot. pcc's loader called PyInit and used its result AS the module,
never running the exec slot -> the module was a bare def -> attrs `<null>`.

FIX (genuine loader feature, used by numpy + all modern C extensions):
1. py_capi_shim.c: extended the shim's `PyModuleDef` to MATCH the fake Python.h
   layout (added `m_slots`/`m_traverse`/`m_clear`/`m_free` + the `PyModuleDef_Slot`
   struct + Py_mod_exec/create constants) so the shim can read `m_slots`.
2. `PyModuleDef_Init` now stamps a recognizable marker into `m_base.ob_base`;
   `pcc_capi_is_moduledef` detects it; `pcc_capi_module_exec` builds the module
   (PyModule_Create2) then invokes each `Py_mod_exec` slot with it.
3. py_extension_loader.c: after `init()`, if the result is a moduledef, run
   `pcc_capi_module_exec` to get the real, exec'd module.

Behavioral regression `test_pcc_native_multiphase_init_under_self_backend_no_libpython`
-> `mp 42` (a Py_mod_exec slot registers answer=42; asserts mpdemo.answer==42).
ext-abi+loader gate 41 passed (single-phase extensions unaffected — is_moduledef
correctly rejects them).

RESULT: numpy's `_multiarray_umath_exec` slot now RUNS under pcc-native (the error
advanced from `<null>` attrs to `RuntimeError: module not found: math`). NEXT
BLOCKER pinned: numpy's exec slot imports stdlib modules via the C-API
(`PyImport_ImportModule("math")` etc.), and pcc's no-libpython import-by-name only
resolves `.so` extensions, not pcc's native stdlib modules. So the import-system
integration (route C-API stdlib imports to pcc's native modules) is the next layer
-- part of the runtime/import-integration track. Multi-phase init: DONE + tested.

## Update: definitive import-chain scoping — numpy exec needs the Python package + stdlib (2026-05-29)

With multi-phase init working, numpy's `_multiarray_umath_exec` now runs and its
import chain is the next wall. Enumerated every module it imports
(`npy_import`/`PyImport_ImportModule`/`IMPORT_GLOBAL` across `_core/src`):
- STDLIB (C-API import): `math` (IMPORT_GLOBAL floor/ceil/trunc/gcd from
  npy_static_data.c), `sys` (4x), `time` (2x), `gc`, `copy`.
- numpy PACKAGE Python modules: `numpy._core._internal` (7x), `numpy._globals`,
  `numpy._core._dtype` (3x), `numpy._core._multiarray_umath` (3x), `numpy`,
  `numpy._core._dtype_ctypes`.

DEFINITIVE remaining chain for a WORKING `import numpy` no-libpython:
1. C-API-importable STDLIB modules (math/sys/time/gc/copy) as real runtime module
   objects exposing the functions numpy reads — pcc currently lowers `import math`
   at compile time and has no runtime module object for PyImport_ImportModule to
   return; this is bounded-ish new runtime work per module.
2. The full numpy PYTHON PACKAGE running under pcc — `numpy._core._internal`,
   `numpy._globals`, etc. are numpy's own .py files; `import numpy` must compile/run
   numpy's entire Python layer in no-libpython mode. This is the multi-month wall.
3. array-runtime EXECUTION (dtype/ufunc loops on pcc's object model).

INFLECTION (honest): the pcc-OWNED C-extension surface — host C-API (decl + link),
the entire numpy `_core` compile (137/137 C+C++), the `.so` link, the loader
load/dlsym, PEP 489 multi-phase init + exec — is COMPLETE and proven this session.
A working `import numpy` now requires the numpy Python-package integration + C-API
stdlib modules + the array runtime, which are multi-month subsystems (numpy's own
Python layer + array core), not loop-sized slices. The build/load/host-API track
that pcc owns has reached its bounded limit; further progress is the full-package +
array-runtime track. (Per AGENTS.md §9, those substantial subsystems are not
started speculatively from here.)

## Update: numpy-package wall is immediate; session consolidation green (2026-05-29)

Confirmed the numpy-package wall is hit almost immediately in the exec, not after
the stdlib modules: `initialize_static_globals` does `IMPORT_GLOBAL("math",
"floor"...)` then RIGHT AFTER `IMPORT_GLOBAL("numpy.exceptions", "AxisError"...)`
— `numpy.exceptions` is a numpy PACKAGE Python module. So building C-API stdlib
modules (math/sys/time/gc/copy) would advance the exec only ONE step before hitting
`numpy.exceptions` (needs the numpy Python package running under pcc). Building them
is therefore NOT worth it as a path to `import numpy` — the package wall is
immediate. The numpy-Python-package-under-pcc integration is the dominant remaining
blocker (multi-month), as scoped.

CONSOLIDATION (this session made extensive runtime/header changes — validate
bootstrap safety): `tests/python/test_bootstrap_gate_baseline.py` +
`test_fallback_baseline.py` + `test_ir_py_fallback_baseline.py` -> 17 passed. So
ALL session changes — py_runtime.h (`new`->`replacement` param), py_internal.h
(moduledef decls), py_extension_loader.c (multi-phase), py_capi_shim.c (batches
7-23: ~40 host symbols + the type/ob_type/multi-phase machinery), and the extensive
Python.h C-API surface + extern "C" + visibility fixes — are bootstrap-safe (the
C-extension path is isolated from the stage1/2/3 closure; the core-header changes
are inert decls/param-names). The no-libpython numpy C-extension BUILD + LOAD +
multi-phase-exec track (the pcc-owned surface) is COMPLETE, PROVEN, and
consolidated; the remaining (numpy Python package + array runtime) is the
multi-month track.
