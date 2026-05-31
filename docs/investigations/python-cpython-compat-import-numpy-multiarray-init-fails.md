# Investigation: cpython-compat `import numpy` fails at `_multiarray_umath` C-extension init

## Status
active

## Problem Description
The real `import numpy` end-goal for `B-P0-PKG`. Under cpython-compat
(`--python-libpython=on`, pcc links the host CPython's libpython), `import numpy`
PROGRESSES through numpy's pure-Python module loading and then FAILS when the core C
extension `numpy._core._multiarray_umath` is imported, with:

```
SystemError: execution of module numpy._core._multiarray_umath failed without setting an exception
```

The same numpy on the same CPython (run directly) prints the expected results. So the
failure is specific to executing numpy's C-extension module-init under pcc's libpython
integration. This relocates the `import numpy` blocker: it is NOT the C-API symbol
surface (`pcc.capi_surface` is 384/406 implemented; generic CPython C-API incl.
PyCapsule/buffer/memoryview complete — see
`python-no-libpython-re-compile-general-pattern-object.md` sibling notes and
`docs/current-goal-state.md`), NOT the import lowering (`import numpy` correctly lowers
to `cpy.import.numpy` + `py_cpy_*` libpython calls), and NOT environment/ABI (resolved,
see Repro). It IS C-extension module-EXECUTION under pcc's libpython runtime.

## Repro
Deterministic, on this host (macOS arm64), pcc links **homebrew python@3.14**
libpython (confirmed via `otool -L` on the compiled binary; note `PCC_HOST_PYTHON`
sets only the host-query python, NOT the linked libpython):

```bash
# 1. numpy matching pcc's linked libpython ABI (3.14)
PY314=/opt/homebrew/opt/python@3.14/Frameworks/Python.framework/Versions/3.14/bin/python3.14
$PY314 -m venv /tmp/np314 && /tmp/np314/bin/pip install numpy   # -> numpy 2.4.6

# 2. probe
cat > /tmp/np.py <<'PY'
import numpy
def main() -> int:
    print(numpy.__version__)
    print(int(numpy.array([1,2,3,4]).sum()))
    return 0
main()
PY

# 3. cpython-compat compile (import lowers to libpython; compile succeeds)
env -u LC_ALL PCC_HOST_PYTHON=/tmp/np314/bin/python \
  uv run pcc --python-libpython=on /tmp/np.py -o /tmp/np_bin

# 4. run with the ABI-matched numpy on the path
env PYTHONPATH=/tmp/np314/lib/python3.14/site-packages /tmp/np_bin
# EXPECTED (CPython): 2.4.6 / 10
# ACTUAL (pcc):  SystemError: execution of module numpy._core._multiarray_umath
#                failed without setting an exception   (then prints 0, rc=0)
```

## Test [N/A]
No gate yet — characterization. A future gate is a cpython-compat `import numpy`
smoke that asserts `numpy.__version__` and a trivial array op match CPython. It must
be ABI-matched (numpy built/installed for pcc's linked libpython version) and is
host-environment-sensitive, so it likely needs an opt-in env guard like the existing
`PCC_RUN_REAL_NUMPY_IMPORT_BOUNDARY`.

## Analysis (2026-05-29)
The CPython diagnostic "execution of module X failed without setting an exception" is
raised when a module init — a `PyInit_*` returning NULL, or a multi-phase
`Py_mod_exec` slot returning -1 — reports failure while `PyErr_Occurred()` is false.
So numpy's `_multiarray_umath` init hit an error path under pcc's libpython but no
Python exception was set. Two leading hypotheses (unconfirmed):
1. A pcc-provided C-API symbol is present-but-STUBBED (returns NULL/-1 or a wrong
   value without setting an exception); numpy's init either propagates that as failure
   without an exception, or proceeds on a wrong value and a later required step fails
   silently.
2. pcc's libpython runtime initialization is non-standard (the run also emits
   `<frozen site>` `RuntimeWarning: Unexpected value in sys.prefix` — the embedded
   interpreter's prefix/site config differs from a normal CPython), so a runtime-state
   invariant numpy's init relies on (a type not fully initialized, a capsule/feature
   probe, the GIL/thread state, multi-phase init support) is off.

## Proposals
- No.1 Locate the exact failing call in `_multiarray_umath` init   [pending]

## No.1 Locate the exact failing call in `_multiarray_umath` init
### Code Change (investigation, not a fix yet)
Drive the Repro under lldb with a breakpoint at the numpy extension's init
(`PyInit__multiarray_umath` / its `Py_mod_exec` slots) and step to the first call
that returns an error indicator; cross-check whether that call is a pcc-provided
C-API symbol and whether pcc sets an exception on its error path. Alternatively
instrument pcc's libpython-fallback C-API shims to log the first `py_cpy_*` /
C-API call during the extension init that returns NULL/-1 with no exception set.
Then classify: (a) missing/stub C-API symbol → implement it functionally; (b)
runtime-init/state incompatibility → fix pcc's libpython embedding (prefix/site,
type readiness, multi-phase init). Validate by re-running the Repro to print
`2.4.6` / `10`.
### pending
Not yet executed. This is a deep C-extension-embedding bug; it needs lldb/extension
-level tracing, not a guess. Do NOT assume it is a missing C-API symbol — the surface
is 95% present and the import reaches the extension; the "without setting an
exception" signature points equally at a stubbed symbol's error path OR an embedding
runtime-state mismatch. Confirm the failing call before changing shared C-API or
embedding code.

## Update — the failure is GENERAL to all C extensions, not numpy-specific (isolation, 2026-05-29)
Isolation test: compiled `import unicodedata` (a trivial stdlib C-extension `.so`, in
the linked libpython's OWN stdlib — no PYTHONPATH needed) under the SAME cpython-compat
setup. It fails IDENTICALLY: `SystemError: execution of module unicodedata failed
without setting an exception`, plus `AttributeError: unidata_version` (a module-level
attribute that unicodedata's init body sets) and a `<null>` module object. CPython 3.14
on the same prints `Lu` / `LATIN CAPITAL LETTER A`.

So the blocker is NOT numpy-specific and NOT a missing numpy C-API symbol — it is a
GENERAL pcc-libpython-EMBEDDING bug (this file's title is therefore narrower than the
root cause; numpy is just the motivating case). The embedded interpreter does not
correctly execute a C extension's multi-phase module init: the module object is created
but its `Py_mod_exec` slot body — which sets attributes, registers types, etc. — does
not run/complete (hence `AttributeError: unidata_version`), so the slot reports failure
with no exception set. This reframes No.1: the target is pcc's C-extension LOADING/INIT
path in the embedding (`PyModule_FromDefAndSpec` / `Py_mod_exec` slot execution / the
`_imp.create_dynamic`+`exec_dynamic` machinery), which would unblock ALL `.so` C
extensions (numpy included) AT ONCE — a high-leverage fix, not per-symbol. Next: trace/
lldb pcc's extension-init path on the unicodedata repro (much smaller than numpy) to
find why the exec slot is not run/completed. Likely suspects: the embedding routes
extension import through a path that creates the module from the def but skips
`PyModule_ExecDef` / the multi-phase exec slots, or `_imp.exec_dynamic` is stubbed.

## Update — code-level candidate root cause: single-phase-only extension loader (2026-05-29)
pcc has its own C-extension loader, `pcc/py_runtime/src/py_extension_loader.c::
py_native_extension_import` (called by the C-API shim `py_capi_shim.c:2376/4628`).
At line ~123 it does:
```c
PyObject *module = init();          /* PyInit_<name>() */
if (module == NULL) { ...error... }
/* caches `module` and returns it directly */
```
i.e. it treats `PyInit_*`'s return as the FINISHED module — a SINGLE-PHASE assumption.
Modern CPython extensions (numpy `_multiarray_umath`, `unicodedata`, most recent `.so`)
use PEP 489 MULTI-PHASE init: `PyInit_*` returns a `PyModuleDef` (carrying `m_slots`),
and the loader must then `PyModule_FromDefAndSpec(def, spec)` to create the module and
`PyModule_ExecDef(module, def)` to RUN the `Py_mod_exec` slot bodies (which set module
attributes and register types). `py_native_extension_import` does NEITHER — no
def-vs-module discrimination, no `PyModule_ExecDef`. This exactly matches the symptom:
the module object exists but its attributes (`unidata_version`, `category`, numpy's
array-API) are never set, and the exec reports failure with no exception.

STRONG CANDIDATE — path still to confirm: the cpython-compat probe imports via
`cpy.import.numpy` -> `py_cpy_import` (libpython `PyImport`), so it must be confirmed
whether pcc ROUTES libpython's extension imports through `py_native_extension_import`
(an installed meta-path finder / `_imp` override) or whether libpython's native
`_imp.exec_dynamic` runs (in which case the bug is pcc's embedded `_imp`/exec-slot
support, not this loader). Either way the FIX SHAPE is the same: detect a multi-phase
`PyModuleDef` return and run `PyModule_FromDefAndSpec` + `PyModule_ExecDef` (PEP 489).
Next: confirm the live loader path on the small `unicodedata` repro (breakpoint
`py_native_extension_import` and `_PyImport_LoadDynamicModuleWithSpec`/`exec_dynamic`),
then add PEP 489 multi-phase handling to whichever path is active. This is one
high-leverage fix that would unblock all multi-phase `.so` extensions (numpy included).

## Update — evidence now favors libpython `_imp.exec_dynamic`, NOT pcc's loader (correction, 2026-05-29)
Weighing the error signature: "execution of module X failed without setting an
exception" is CPython's OWN message (raised by `_imp`/`import_run_extension` when an
exec slot returns -1 / `PyInit` returns NULL with `PyErr_Occurred()` false). Combined
with: pcc does NOT define/override `exec_dynamic`/`create_dynamic` (empty grep) and
does NOT install a `sys.meta_path` finder or `_imp` override — this is strong evidence
that **libpython's REAL `_imp.exec_dynamic` runs the extension's exec slot**, and the
slot itself returns failure without setting an exception. So the single-phase
`py_native_extension_import` loader (previous Update) is most likely a LATENT bug on a
DIFFERENT path (the C-API `PyImport_ImportModule` shim), NOT the cause of THIS failure.
Do not "fix" the loader expecting it to fix `import numpy`.

Most-probable cause (well-evidenced, exact call still to pinpoint): the extension's
`Py_mod_exec` slot, run by libpython, calls a pcc-PROVIDED C-API shim symbol
(`py_capi_shim.c` — PyType_Ready, PyModule_AddObject(Ref), PyModule_AddType,
PyDict_*, capsule, etc.) that returns an error/wrong value WITHOUT setting an
exception, so the slot bails with -1 and no exception. The "95% implemented" surface
can still contain a symbol that is present-but-stubbed on its error path or returns the
wrong success value. ALTERNATIVELY the slot's own logic reacts to pcc's non-standard
embedding state (the `sys.prefix` warning shows the embedded interpreter config is
off). Pinpointing REQUIRES lldb on the small `unicodedata` repro: break at
`_PyImport_LoadDynamicModuleWithSpec` / the exec-slot dispatch, step into the slot, and
find the first pcc C-API call returning an error indicator with `PyErr_Occurred()`
false. Only then change the specific shim symbol — do not patch shared C-API on a
guess.

## Update — clue: 286 pcc C-API shim symbols in the cpython-compat binary; two live hypotheses (2026-05-29)
Concrete finding: `nm -gU` on the cpython-compat binary shows it DEFINES (`T`) ~286
pcc Py* C-API symbols, including `PyModule_AddStringConstant`, `PyModule_AddObject`,
`PyModule_AddObjectRef`, `PyModule_Create2`, `PyModule_GetDict` — i.e. pcc's no-libpython
C-API shim (`py_capi_shim.c`) is compiled INTO the cpython-compat binary alongside the
linked libpython. Two live, not-yet-decided hypotheses for the exec-slot failure:

- H1 (shim conflict): the extension's `PyModule_AddStringConstant(real_cpython_module,
  "unidata_version", ...)` resolves to pcc's SHIM (binary-global) instead of libpython's
  real impl; the shim assumes pcc's object model and mishandles the real CPython module
  object → attribute not set, slot fails. CAVEAT: macOS uses TWO-LEVEL namespace, so the
  `.so` (built against the real Python framework) most likely binds these symbols to the
  framework, NOT the binary — which would make H1 false on macOS. Confirm with
  `dyld_info`/`otool` on the `.so` binding + an lldb breakpoint on the shim symbol's
  address during the exec.
- H2 (embedding state): the `.so` correctly calls libpython's C-API (two-level), but
  pcc's libpython INITIALIZATION is non-standard (the `<frozen site>` `sys.prefix`
  warning shows the embedded interpreter's prefix/config is off), so an exec-slot
  invariant (a type not ready, a sub-interpreter/GIL/thread-state assumption, a
  prefix/feature probe) is violated → slot returns -1 without an exception.

Decisive next test (lldb, on the small `unicodedata` repro): break at the exec-slot
failure (libpython `import_run_extension` / the exec-slot dispatch), inspect the bt and
which `PyModule_AddStringConstant` address is called (shim 0x…d404 vs framework). That
picks H1 vs H2 and names the exact failing call. Do NOT patch shared C-API/embedding on
a guess before this. (If H1: stop compiling the shim into / globalizing it for
cpython-compat binaries so libpython's C-API wins. If H2: fix pcc's libpython embedding
init — prefix/site/type-readiness.)

## Update — H1 strongly strengthened: pcc shim shadows libpython's C-API for extensions (2026-05-29)
Decisive evidence that resolves H1 vs H2 in favor of H1:
- `_multiarray_umath.cpython-314-darwin.so` has **NO linked libpython** (`otool -L`
  shows no Python dependency) and is TWO-LEVEL but built `-undefined dynamic_lookup`
  (standard for CPython extensions): its `Py*` C-API symbols are UNDEFINED in the `.so`
  and resolved at `dlopen` time from the LOADING PROCESS's global symbol pool.
- The pcc cpython-compat binary DEFINES 286 shim `Py*` symbols (`nm -gU`) and
  `dlopen`s extensions with `RTLD_GLOBAL` (`py_extension_loader.c:103`).
=> The extension's `PyModule_AddStringConstant`/`PyModule_AddObject`/etc. resolve to
pcc's SHIM (present in the main executable's global symbols), SHADOWING libpython's
real C-API. pcc's shim operates on its own object model, but the module object was
created by libpython (a real CPython object) → the shim mishandles it (attribute not
actually set / wrong layout write) → `unidata_version` missing, exec slot returns -1
with no exception. This cleanly explains WHY pure-Python loads fine (libpython's
internal C-API calls are two-level-bound WITHIN libpython, so they use the real impl)
but C EXTENSIONS fail (dynamic_lookup → resolve to the binary's shim).

Confidence: high (mechanism is fully consistent with every observed symptom); the one
remaining confirmation is an lldb breakpoint on the shim `PyModule_AddStringConstant`
(binary addr ~0x…d404) showing it is hit DURING the extension's exec slot.

Fix direction: in cpython-compat (`--python-libpython=on`) builds, the pcc no-libpython
C-API shim (`py_capi_shim.c`) must NOT shadow libpython's real C-API for dlopen'd
extensions. Options, smallest-blast-radius first: (a) do not link `py_capi_shim.c` into
cpython-compat binaries at all (libpython provides the full C-API — and the surface is
95% mirrored anyway, but here the REAL one must win); (b) if some shim symbols are still
needed by pcc's own emitted code, give the shim symbols hidden/weak visibility so
libpython's win at dynamic_lookup, or `dlopen` extensions WITHOUT `RTLD_GLOBAL` so they
bind to libpython's framework symbols rather than the executable's shim. Validate by
re-running the Repro to print `2.4.6`/`10` (numpy) and `Lu`/`LATIN CAPITAL LETTER A`
(unicodedata). This is one link/visibility-level fix that unblocks ALL `.so` extensions.

## Update — ROOT CAUSE CONFIRMED (code-level), 2026-05-29
Read pcc's shim impls in `py_capi_shim.c`:
```c
int PyModule_AddObject(PyObject *module, const char *name, PyObject *value) {
    ...
    int64_t rc = py_obj_setattr(module, name, value);   /* pcc object model */
    if (rc != 0) return -1;
    py_decref(value);
    return 0;
}
```
`PyModule_AddObject`/`AddObjectRef`/`AddStringConstant`/`GetDict` all operate via pcc's
own object ops (`py_obj_setattr`, pcc dict/module layout), and the shim has ZERO
libpython delegation (`grep py_cpy_|dlsym|real_Py|libpython py_capi_shim.c` = 0). So
when a libpython-run extension exec slot calls `PyModule_AddObject(real_cpython_module,
"unidata_version", v)`, it binds (dynamic_lookup → global executable symbols) to pcc's
shim, which runs `py_obj_setattr` reading pcc's object layout on a REAL CPython module
object → the attribute is written into the wrong place / mishandled → never set in the
CPython module → `AttributeError: unidata_version`, and the slot returns failure.

CONCLUSION (root cause, confirmed): in cpython-compat the pcc no-libpython C-API shim
(286 `Py*` symbols, pcc object model, no delegation) SHADOWS libpython's real C-API for
dlopen'd extensions (which use `-undefined dynamic_lookup` and so resolve `Py*` from the
main executable's globals). The shim then mishandles the real CPython objects libpython
created → every multi-phase `.so` extension's exec slot fails. This is the single,
general, high-leverage `import numpy` (and all-C-extensions) blocker. Confirmed by: (1)
extension `.so` has no bound libpython (`otool -L`); (2) 286 shim `Py*` defined in the
binary (`nm`); (3) shim uses pcc object model, 0 libpython delegation (code-read); (4)
the symptom (pure-Python loads, C extensions fail with attrs unset / "without setting
an exception") matches exactly. The only unran step is an lldb hit-confirmation of the
resolution order, but the mechanism is otherwise fully evidenced.

Status -> the fix (build/link level) is well-justified now: cpython-compat
(`--python-libpython=on`) builds must let libpython's real C-API win for extensions —
i.e. NOT compile/globalize the no-libpython `py_capi_shim.c` symbols into cpython-compat
binaries (libpython provides the C-API). Care: verify pcc's OWN emitted cpython-compat
code uses `py_cpy_*` (libpython wrappers), not the raw shim, so dropping the shim there
is safe; keep the shim for true no-libpython builds (which need pcc to provide the
C-API). Validate via the Repro (numpy `2.4.6`/`10`, unicodedata `Lu`/...) AND the
no-libpython gates (must stay green). This is a focused build-logic change for the next
iteration, not a same-session cram — it touches how the runtime C-API is linked across
both modes and needs the full no-libpython + cpython-compat validation set.

## Update — fix grounded + safety verified (2026-05-29)
- Location: the shim object `py_capi_shim.o` (with `py_extension_loader.o`) is in
  `OBJ_PY_CC_HELPERS` (`pcc/py_runtime/Makefile:152`), compiled from
  `py_capi_shim.c` and bundled into the runtime archive that cpython-compat binaries
  link — that is why the 286 `Py*` shim symbols are present in the binary.
- Safety verified: pcc's frontend codegen does NOT emit raw shim C-API calls — grep
  for `PyModule_AddObject`/`PyModule_AddStringConstant` in `pcc/py_frontend/codegen/*.py`
  is EMPTY; pcc's emitted/runtime code reaches libpython through `py_cpy_*` wrappers,
  not the public C-API shim. So removing/weakening the shim's PUBLIC C-API for
  cpython-compat does not break pcc's own code; it only stops the shim from shadowing
  libpython for dlopen'd extensions.
- Link site (for the link-flag fix option): the final libpython + runtime-archive link
  is constructed in `pcc/package/linkage.py` (+ `pcc/package/toolchain.py`); a
  mode-dependent `-Wl,-unexported_symbols_list <shim Py* symbols>` (or equivalent
  symbol-hiding) for `--python-libpython=on` links would belong there, hiding the shim's
  `Py*` from the cpython-compat binary's dynamic table so dlopen'd extensions bind to
  libpython's exported C-API instead.
- Candidate fix designs (next iteration, with full dual-mode validation): (1) mark the
  shim's exported `Py*` C-API symbols weak (e.g. a visibility/`__attribute__((weak))`
  pass or compile flag on `py_capi_shim.c`) so libpython's STRONG symbols win at
  dynamic_lookup in cpython-compat, while the weak shim remains the sole definition in
  no-libpython builds; or (2) exclude `py_capi_shim.o` from the link when
  `--python-libpython=on`. Verify macOS weak-symbol semantics at dynamic_lookup, and
  gate on BOTH the numpy/unicodedata repro AND the no-libpython bootstrap/fallback
  baselines (the no-libpython path depends on the shim, so this is bootstrap-critical —
  do not land without those gates green).

## Report
(open — predecessor/sibling context: this is the empirical continuation of the
2026-05-29 B-P0-PKG frontier mapping in `docs/current-goal-state.md`. The C-API
surface being 95% done and the import lowering being correct are what made this
test reach the extension-init failure rather than an earlier wall.)

### FIX LANDED + VALIDATED (2026-05-29)
Root cause CONFIRMED: the no-libpython C-API shim (`py_capi_shim.o`, ~286 `Py*` symbols,
pcc object model, no libpython delegation) was inherited into the cpython-compat archive
(`libpy_runtime_pcc_py_libpython.a`, built `= LIB_PCC_PY + py_libpython.o`,
`Makefile:199`) and SHADOWED libpython's real C-API for dlopen'd extensions (built
`-undefined dynamic_lookup`, so `Py*` resolve from the executable's globals). The shim
ran pcc-object-model ops on real CPython objects → every `.so` C-extension module init
failed ("execution of module … failed without setting an exception").

FIX (landed): the `$(LIB_PCC_PY_LIBPYTHON)` recipe in `pcc/py_runtime/Makefile` now does
`-$(AR) d $@ py_capi_shim.o` after copying from `LIB_PCC_PY`, so the cpython-compat
archive OMITS the shim and libpython's real C-API wins for extensions. `LIB_PCC_PY`
(no-libpython) keeps the shim and is UNCHANGED → bootstrap-safe by construction. The two
pcc-glue symbols `pcc_capi_refcnt`/`set_refcnt` are not referenced by pcc
frontend/runtime-py code (grep empty), so dropping the whole `.o` is safe.

Validation:
- Proof (archive surgery, CC-wrapper swap): shim-removed archive → `unicodedata`
  `Lu`/`LATIN CAPITAL LETTER A`; `import numpy` → `2.4.6`.
- Landed (Makefile, real pcc path, no wrapper): `_libpython` archive
  `PyModule_AddObject` T-count = 0 (was present); no-libpython archive unchanged
  (count 2); `unicodedata` via pcc → `Lu`/`LATIN CAPITAL LETTER A` rc=0; `import numpy`
  → prints `2.4.6` (vs the prior `SystemError: ... _multiarray_umath failed`).
- no-libpython self-host bootstrap + fallback baselines: RUNNING (the Makefile change
  only touches the `_libpython` recipe; the no-libpython archive is byte-unchanged, so
  no self-host regression is expected — confirming).

Remaining (SEPARATE follow-up, NOT this C-extension-import blocker): a deeper
pcc↔CPython OBJECT-INTEROP layer — `numpy.array([1,2,3,4]).sum()` raises
`AttributeError: sum` (passing pcc objects into numpy / attribute-method access on real
CPython array objects through pcc's native attribute path). `import numpy` itself and
module attribute access (`numpy.__version__`) now work; full array USAGE needs the
interop layer, which is a new investigation. The C-extension-import fix's bootstrap-gate
confirmation is DONE (18 passed).

Refinement (probed 2026-05-29): the `.sum` access LOWERS CORRECTLY — IR shows
`%cpy.fn.sum = py_cpy_getattr(arr, "sum")` + `py_cpy_call_noargs`, i.e. attribute access
already routes to libpython. And `numpy.arange(10).sum()` (an INT arg, NO pcc container)
fails the SAME way, so it is NOT pcc-container-arg marshaling. The numpy CALL result (via
`py_cpy_call`) lacks the expected attrs — pointing at the pcc↔CPython interop at the
`py_cpy_call` ARG/RESULT boundary (pcc-object args reaching `numpy.*`, and/or the returned
CPython array object not being usable through pcc's path). This is a distinct, broad
follow-up deserving its OWN investigation. Status stays `active` only for that new interop
follow-up; the C-extension-import blocker itself is FIXED and validated.

Sharper narrowing (probed 2026-05-29): numpy CALL results ARE valid CPython arrays —
`print(numpy.arange(5))` prints `[0 1 2 3 4]` correctly (str/repr through libpython
works). But ATTRIBUTE access on those call-result values fails (`a.sum`, `type(a)`),
while `numpy.__version__` (attribute on the IMPORT-result module) works. So the interop
bug is NOT arg-marshaling and NOT array validity — it narrows to `py_cpy_getattr` on
values returned by `py_cpy_call` (call-results) vs values from `cpy.import` (module):
pcc appears to hold/box the call-result cpy value such that str/print unwrap it
correctly but `py_cpy_getattr` receives the wrong object → `AttributeError`. The
dedicated follow-up investigation should compare how pcc holds a `py_cpy_call` return
vs a `cpy.import` result and how `py_cpy_getattr` unwraps each.

Ruled out (2026-05-29): `py_cpy_getattr` itself is a CORRECT thin wrapper —
`py_libpython.c:576/1461`: `PyObject_GetAttrString((CPyObject*)obj, name)`, no
boxing/unwrapping. So given the array is valid (`print(a)` works) yet
`py_cpy_getattr(a,"sum")` returns AttributeError, the `obj` or the `name` REACHING
the call at runtime must be wrong (a stale/over-decref'd `obj`, or a bad `name`
pointer). This requires lldb on the small repro (break `py_cpy_getattr`, inspect
`obj` type and `name` string at the failing call) — not more static probing. The
`print(a)` works but `type(a)`/`a.sum` fail pattern also hints at a refcount/lifetime
issue on cpy call-results (pcc may decref the value before the attribute use). The
dedicated follow-up investigation starts there.

MUCH-improved characterization (probed 2026-05-29): numpy is SUBSTANTIALLY USABLE via
pcc cpython-compat, not broken. A STORED real array works fully:
`a = numpy.arange(10); a.ndim` -> `1`, `a.sum()` -> `45`, `a.max()` -> `9` (all match
CPython), and data attrs on call-results work (`numpy.arange(5).ndim`/`.shape` ->
`1`/`(5,)`). So `py_cpy_getattr` + method calls on cpy values WORK for the common case.
The remaining gaps are narrow and specific, NOT "numpy usage broken":
1. CHAINING a temporary: `numpy.arange(10).sum()` (no intermediate variable) fails,
   while `a = numpy.arange(10); a.sum()` works. ROOT CAUSE (IR + code, 2026-05-29, NOT
   a lifetime/decref bug as first guessed): the chained `.sum` lowers to NATIVE
   `py_obj_getattr` instead of `py_cpy_getattr`. In
   `pcc/py_frontend/codegen/method_call_expression_lowering.py:459`, the
   `chain_val in self._cpy_values -> _emit_cpy_method_call_src` routing is guarded by
   `if isinstance(attr.obj, Attr):` (and a `Name` branch above). For
   `numpy.arange(10).sum()`, `attr.obj` is a `Call`, so BOTH branches are skipped and
   the method call falls through to the native path (`py_obj_getattr`), which mishandles
   the real CPython array. The cpy-call result IS added to `_cpy_values`
   (`cpy_call_lowering.py:390`), but the method-call lowering never checks it for a
   `Call` receiver. FIX: handle a `Call` receiver too — emit the receiver ONCE, and if
   the value is in `_cpy_values`, dispatch via `_emit_cpy_method_call_src`; else use the
   native path WITH that already-emitted value (must avoid double-emitting a `Call`
   receiver, unlike the side-effect-free `Attr` case). Highest-value, most-common gap;
   high-blast-radius (method-call codegen) so the fix needs full method-call + bootstrap
   validation, not a tail-of-session change.
   **FIXED + VALIDATED + TESTED 2026-05-29**: added a `Call`-receiver branch in
   `method_call_expression_lowering.py` (after the `Attr` branch) that detects a cpy
   call STRUCTURALLY (callable is a CPython-module attribute: `cfunc` is `Attr` with a
   `Name` root in `_cpy_module_env`/`_cpy_env_flags`) and routes through
   `_emit_cpy_method_call_src` — emitting the receiver exactly once (no double-call) and
   inert in no-libpython (no CPython modules => never matches => bootstrap unaffected).
   Functional: `numpy.arange(10).sum()`/`.max()`/`numpy.zeros(5).sum()` -> `45`/`9`/`0`
   (match CPython); was `AttributeError`. Regression: **88 passed, 2 skipped** (full
   3-stage no-libpython self-host bootstrap [broad method-call coverage] + fallback
   baselines + `test_package_import_path` + `test_native_os_misc` + the cext test). New
   IR regression test `test_chained_cpython_call_method_dispatches_via_libpython` (the
   chained `.sum` lowers to `@.cpy.attr.sum`/`py_cpy_getattr`, not native
   `@.pyattr.sum`/`py_obj_getattr`) -> passed. So chained-method numpy usage now works
   too. Remaining gaps 2 (type()) and 3 (container args) are still open.
2. `type()` builtin on a cpy value fails. **FIXED + VALIDATED + TESTED 2026-05-29**:
   root cause was `builtin_type_attr_lowering.py::_emit_type_builtin` lowering
   `type(obj)` to NATIVE `py_obj_getattr(obj, "__class__")`, which mishandles a real
   CPython object and returned a bogus value (`type(a)` printed `False`). Now, when the
   arg is a cpy value (`obj_val in _cpy_values`), `__class__` is fetched via
   `py_cpy_getattr` and the resulting CPython type is tagged cpy. `type(numpy.arange(5))`
   -> real `<class 'numpy.ndarray'>` (enables `==`/`isinstance`). Inert in no-libpython.
   Regression: **21 passed** (bootstrap + fallback + the 3 cpython-compat tests);
   bootstrap-safe. New IR test `test_type_builtin_on_cpython_value_dispatches_via_libpython`
   (uses `@.cpy.attr.__class__`/`py_cpy_getattr`). Remaining minor edge: the CHAINED
   `type(a).__name__` (attribute on the `type()` call-result) — `type(a)` and the stored
   form `t = type(a); t.__name__` work; only the inline chain `type(a).__name__` still
   has the attr-on-call-result edge (rare introspection). Chained DATA attrs on cpy-call
   results (`numpy.arange(5).ndim`/`.size`) DO work.
3. pcc-CONTAINER args: `numpy.array([1,2,3,4])` passes a pcc-native list to numpy.
   **RESOLVED 2026-05-29**: the container-arg marshaling already existed
   (`_marshal_to_cpython` in `cpy_bridge_lowering.py` routes pcc list/tuple/dict via
   `py_cpy_from_pcc_obj` -> CPython, recursing nested containers); the only failure was
   the chained method on the result (gap 1), so the gap-1 fix resolved this too.
   `numpy.array([1,2,3,4]).sum()` -> `10`, `numpy.array([10,20,30]).max()` -> `30`,
   `a=numpy.array([5,6,7]); a.sum()` -> `18` (all match CPython).
So gaps 1, 2, and 3 are all FIXED/RESOLVED. NET: cpython-compat numpy is substantially
usable — `import numpy`, array creation from int args AND pcc lists, data attrs (stored +
chained), methods (stored + chained), and `type()` all WORK and match CPython.

4. NEW GAP 4 — numpy ARITHMETIC (binary operators). **FIXED + VALIDATED + TESTED
   2026-05-29**: runtime `py_cpy_binop(op,a,b)` dispatcher over libpython
   `PyNumber_Add/Subtract/Multiply/TrueDivide/FloorDivide/Remainder` (`py_libpython.c`
   real + stub blocks, + decls/macros/RESOLVEs + ABI), and a `_cpy_values`-operand branch
   at the top of `binary_op_lowering.py::_emit_binop_value` (marshals operands to CPython,
   calls `py_cpy_binop`, tags result cpy; inert in no-libpython). `c=a+b; c.sum()`->`66`,
   `a*3`->`18`, `b-a`->`54` (match CPython). Regression: **22 passed** (bootstrap +
   fallback + the 4 cpython-compat tests); bootstrap-safe. IR test
   `test_cpython_value_binary_op_dispatches_via_libpython`.

5. NEW GAP 5 — BinOp-receiver method (`(a+b).sum()`). **FIXED + VALIDATED + TESTED
   2026-05-29** (5th fix this track): a method call on a binary-op result lowered `.sum`
   to NATIVE `py_obj_getattr` because the method-call lowering only recognised
   `Name`/`Attr`/`Call` receivers as cpy, not `BinOp` (the stored form `c=a+b; c.sum()`
   worked via the assignment's cpy tagging). FIX: a `BinOp` case in
   `cpy_return_analysis.py::_expr_looks_cpython` (a binop with a cpy operand is itself cpy,
   since `py_cpy_binop` returns a CPython object) + a `BinOp`-receiver branch in
   `method_call_expression_lowering.py` routing such a receiver through the libpython
   method path. Both inert in no-libpython. `(a+b).sum()`->`6`, `(c-a).sum()`->`3` (match
   CPython). Regression: **23 passed** (bootstrap + fallback + 6 cpython-compat tests). IR
   test `test_cpython_binop_receiver_method_dispatches_via_libpython`.

6. NEW GAP 6 — power operator `**`. **FIXED + VALIDATED + TESTED 2026-05-29** (6th fix this
   track): `a ** b` on cpy values routes through `py_cpy_binop` op 6, calling libpython
   `PyNumber_Power(base, exp, &_Py_NoneStruct)` (Py_None modulus for plain power) —
   decl/macro/RESOLVE + `case 6` in `py_libpython.c`, `"**": 6` in the
   `binary_op_lowering.py` cpy_op map. Native `py_int_pow` unaffected (non-cpy only, after
   the cpy branch). Completes the cpy binary operator set `+ - * / // % **`.
   `(a**2).sum()`->`5`, `a**3` sum->`9` (match CPython; also exercises GAP-5). Regression:
   **24 passed** (bootstrap + fallback + 7 cpython-compat tests); bootstrap-safe (op 6 dead
   in no-libpython). IR test `test_cpython_value_power_op_dispatches_via_libpython`.
   (see GAP 7 below for the remaining open edge.)

7. NEW GAP 7 — Subscript-receiver method + coverage unblock. **FIXED + VALIDATED + TESTED
   2026-05-29** (7th fix this track): a coverage probe found `a[1:4].sum()` (method on a
   SUBSCRIPT/slice result) raised `AttributeError: sum` — the method-call lowering handled
   `Call`/`BinOp` receivers but not `Subscript`, so `.sum` lowered to NATIVE
   `py_obj_getattr` on the real CPython slice. FIX: generalised the GAP-5 BinOp-receiver
   branch to `isinstance(attr.obj, (BinOp, Subscript))` in
   `method_call_expression_lowering.py` (`_expr_looks_cpython` already recurses Subscript
   objects). Frontend-only, inert in no-libpython. ALSO unblocked
   `np.sum(a)`/`np.dot(b,b)`/`a.shape[0]` (they followed the failing probe line). Probe (all
   match CPython): `a[2]`->`2`, `a[1:4].sum()`->`6`, `np.sum(a)`->`10`, `np.dot(b,b)`->`14`,
   `a.shape[0]`->`5`. Regression: **25 passed** (bootstrap + fallback + 7 cpython-compat
   tests). IR test `test_cpython_subscript_receiver_method_dispatches_via_libpython`.

8. NEW GAP 8 — matmul operator `@`. **FIXED + VALIDATED + TESTED 2026-05-29** (8th fix this
   track): a round-2 coverage probe found `a @ b` raised `TypeError: unsupported operand
   type(s) for @` — pcc lowered `@` to the native `__matmul__` protocol
   (`py_user_matmul_dispatch`) on the real CPython object. FIX (same clean pattern as power):
   `py_cpy_binop` op 7 over libpython `PyNumber_MatrixMultiply` (decl/macro/RESOLVE + `case 7`
   in `py_libpython.c`) + `"@": 7` in the `binary_op_lowering.py` cpy_op map. Native matmul
   dispatch unaffected (non-cpy only). Completes the cpy binary operator set
   `+ - * / // % ** @`. Probe (all match CPython): `(a@b).sum()`->`10`, `a.mean()*4`->`10`,
   `a.max()`->`4`, `a.T.sum()`->`10` (2D arrays, mean/max methods, transpose confirmed).
   Regression: **26 passed** (bootstrap + fallback + 8 cpython-compat tests). IR test
   `test_cpython_value_matmul_op_dispatches_via_libpython`.

9. NEW GAP 9 — augassign on a cpy name. **FIXED + VALIDATED + TESTED 2026-05-29** (9th fix):
   `a += 2` after `a = np.ones(3)` raised `TypeError: unsupported operand type(s) for +` —
   augassign loads `a` into a fresh SSA value not in `_cpy_values` (cpy-ness for NAMES lives
   in `_cpy_env_flags`), so `_emit_binop_value`'s cpy branch missed it -> native `+`. FIX:
   `_emit_augassign` tags the loaded value cpy when `_expr_looks_cpython(stmt.target)`.
   Frontend-only, inert in no-libpython. `a+=2; a.sum()`->`9`, `np.zeros(4).sum()`->`0`,
   `np.arange(5).astype(float).sum()`->`10`. Regression: **27 passed**. IR test
   `test_cpython_augassign_on_cpython_name_dispatches_via_libpython`.

10. NEW GAP 10 — deep cpy method chains. **FIXED + VALIDATED + TESTED 2026-05-29** (10th fix):
    `np.arange(4).reshape(2,2).sum()` raised `AttributeError: sum` — the `.sum()` receiver is
    a `Call` whose callable is `(Call).reshape` (`cfunc.obj` is a Call, not a Name), so the
    narrow Call fast path (fix #2) missed it. FIX: added `Call` to the generalised
    `(BinOp, Subscript)` receiver branch, detecting cpy via `_expr_looks_cpython` (recurses
    Call funcs). Frontend-only, inert in no-libpython. `np.arange(4).reshape(2,2).sum()`->`6`.
    Regression: **28 passed** (bootstrap + fallback + 10 cpython-compat tests). IR test
    `test_cpython_deep_chain_method_dispatches_via_libpython`.

11. NEW GAP 11 — attribute load on a BinOp cpy receiver. **FIXED + VALIDATED + TESTED
    2026-05-29** (11th fix): inline `np.arange(5).shape[0]`->`5` worked (Call-receiver attr
    routed) but `(a + b).dtype` raised `AttributeError: dtype` — the attr-load lowering routed
    `(Attr, Subscript, Call)` cpy receivers but not `BinOp`. FIX: structural
    `_expr_looks_cpython`-gated `BinOp` branch in `attr_load_lowering.py` (mirrors the
    method-call BinOp-receiver fix; inert in no-libpython). attr-load + method-call receivers
    now both comprehensive (Name/Attr/Subscript/Call/BinOp). `(a+b).dtype`->`int`. Regression:
    **30 passed** (bootstrap + fallback + 12 cpython-compat tests). IR test
    `test_cpython_binop_receiver_attribute_dispatches_via_libpython`.

12. NEW GAP 12 — inline `type(x).__name__` on a cpy value. **FIXED + VALIDATED + TESTED
    2026-05-29** (12th fix): inline `type(a).__name__` SILENTLY failed (no output) while
    stored `t = type(a); t.__name__` worked — the native `type(x).__name__` fast path
    (`attr_load_lowering.py` ~line 733) called `py_obj_type_name` (pcc native type model),
    mishandling the real CPython object. FIX: gate that fast path on
    `not _expr_looks_cpython(arg)` so cpy args fall through to the cpy Call-receiver branch
    (`py_cpy_getattr`). Inert in no-libpython. `type(a).__name__`->`ndarray`; same probe also
    confirmed `a[0:2].dtype`, `a.reshape(3,1).size`, `len(a.tolist())`. Regression:
    **31 passed** (bootstrap + fallback + 13 cpython-compat tests). IR test
    `test_cpython_type_name_inline_dispatches_via_libpython`.

   NET this track: TWELVE landed+validated fixes; numpy usable for import + creation (incl. 2D,
   ones/zeros/arange/array) + attrs (incl. inline `(a+b).dtype`) + methods (sum/mean/max) +
   type() (incl. inline `type(a).__name__`) + COMPLETE binary operator set + augmented
   assignment + chained-op-results + subscript + slicing + function-form calls + shape +
   transpose + astype + reshape + deep method chains + `tolist()` — the entire common numpy
   idiom surface BAR element-wise comparisons. Remaining open
   edge: comparison ops (`<`/`==` return numpy ARRAYS, a deeper mismatch with the
   i1-returning `_emit_compare` — numpy element-wise comparison yields an array not a bool,
   and pcc cannot statically distinguish a numpy-array operand from a scalar cpy object;
   needs always-return-cpy-object compare lowering + boolean-combination handling, deserves a
   fresh focused session, untested). HISTORICAL (original GAP 4 note):
   was OPEN (found 2026-05-29; corrects an
   was OPEN (found 2026-05-29; corrects an
   was OPEN (found 2026-05-29; corrects an
   over-broad "fully usable" phrasing). `a + a` -> `TypeError: unsupported operand
   type(s) for +`; pcc lowers binary operators NATIVELY and finds no handler for a real
   CPython object. This is the CORE of numpy (element-wise `a+b`, `a*c`), so numpy is NOT
   "fully" usable — operator arithmetic does not work. Root area:
   `binary_op_lowering.py::_emit_binop_value` has no cpy-value branch, and `py_libpython.c`
   has `PyNumber_Index/Long/Float` but NO `PyNumber_Add/Subtract/Multiply/...` wrappers.
   FIX (substantial, next focused iteration; analogous cpy-routing pattern but in
   HIGH-blast-radius binop codegen + needs new runtime wrappers): add `py_cpy_add/sub/mul/
   truediv/...` (or one `py_cpy_binop(op,a,b)` dispatcher) over libpython `PyNumber_*`
   (+ resolves + ABI), and route `_emit_binop_value` to them when an operand is in
   `_cpy_values`; inert in no-libpython; validate with arithmetic repro + full bootstrap.
   NOTE: indexing `a[i]` and `np.dot(a,a)` were NOT reached in the repro (it crashed at
   `a + a` first) — UNTESTED, do not assume they work; the next iteration should probe
   subscript and cpy-arg calls separately when fixing arithmetic.
