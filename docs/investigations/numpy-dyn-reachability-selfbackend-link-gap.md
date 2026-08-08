# Investigation: numpy closure widened by dyn-flow references runtime symbols the pcc1 self-backend link cannot resolve

## Status

active

## Problem Description

The README NumPy example (`pcc1 np_demo.py`: import + np.array + scalar add +
element access) compiles, links, and runs green (verified 2026-08-07, NumPy
2.4.6, strict self/no-libpython). But a slightly different user program that
passes the array through an UNTYPED helper (`def touch(o): return o`) widens
the reachable numpy closure (dispatcher/npyio/arrayprint paths) and the
self-backend link then fails with undefined runtime symbols:

```text
"_py_type_builtin"          <- user_numpy_blas_fpe_check, __pcc_py_module_top_numpy, ...
"_py_user_matmul_dispatch"  <- user_numpy_blas_fpe_check
"_py_weakref_new"           <- user_numpy_lib__npyio_impl_BagObj___init__
"_py_zip_star"              <- user_numpy__core_numeric_roll, arrayprint FloatingFormat, ...
ld: symbol(s) not found for architecture arm64
error: PCC-PY-COMPILE-001: [python-frontend] self backend link failed (exit 1)
```

Threading is NOT the variable: the same failure hits a single-threaded
variant; `import threading` alone (no numpy) links and runs fine under pcc1,
and `import numpy` alone (README shape) links fine.

## Repro

```bash
scripts/bootstrap.sh --stage 1
# numpy already installed in the pcc env (2.4.6)
build/bootstrap/pcc1 /tmp/np_shared_serial.py -o /tmp/np_ser.out   # FAILS
```

where np_shared_serial.py passes `SHARED = np.array([7,8,9])` through an
untyped `touch(o)` function inside a loop (full source in the evidence file
of GC-P1-SHARED-REFCOUNT-CONTENTION-SCALING follow-up work). The README
np_demo.py compiles green from the same tree/env.

## Test [CONFIRMED]

Observed 2026-08-07 on the freshly rebuilt stage1 pcc1
(PCC_RUNTIME_CC=pcc PCC_RUNTIME_HIGH=py): three variants (serial /
4-thread / 4-thread+gc.immortalize) of the touch()-shaped program all fail
at link with the four symbols above; np_demo.py from the same pcc1 is green.

Facts established:

- All four symbols are declared in pcc/py_frontend/codegen/runtime_abi.py
  (codegen knows them) and implemented in BOTH the C runtime
  (py_call_splat.c, py_weakref.c, py_capi_shim.c, py_protocol.c) and the
  pcc-Python ports (py_call_splat_runtime.py, py_weakref.py,
  py_capi_object_runtime.py / py_obj_ops_dispatch.py, py_protocol_runtime.py).
- The runtime tier linked by pcc1-emitted apps is NOT missing everything
  recent: a brand-new symbol (pcc_gc_immortalize, added the same day to
  py_obj.c + py_obj.py) resolves and runs fine through pcc1. So this is a
  per-module selection/export gap in the app-link runtime closure, not a
  stale-archive problem.

## Proposals

- No.1 Identify why the app-link runtime module selection omits the four
  symbols' modules (self-backend app link registry / PY_MODULES tier list /
  export whitelist) and fix the generic selection mechanism   [pending]

## Open boundaries

- Which link path assembles the runtime for pcc1-emitted app binaries
  (per-app runtime closure vs prebuilt archive) not yet traced.
- Per AGENTS.md §ecosystem rules this must be fixed generically (module
  selection/registration), never by special-casing numpy.
- Workaround for demos: keep the user program's numpy usage inside the
  L4/L5 gate surface (module-global array, direct subscript, int()); do
  not route the array through untyped helpers.

## Update 2026-08-07 (hypothesis narrowed — untyped helper is NOT the trigger)

A narrowed variant (module-global `SHARED = np.array([7,8,9])`, worker with
typed signature reading `int(SHARED[0])` directly, no untyped helper) fails
with `-o` on the IDENTICAL four symbols. So "dyn-widening via the untyped
helper" is DENIED as the sole trigger. Remaining discriminating variables
between the green README np_demo run and the red demos: (a) `-o` persistent
link vs script-style run-cache path, (b) source shape (module-level-only
statements vs user functions + loops + main()). Two experiments in flight:
npb_serial.py script-style (no `-o`), and np_demo.py with `-o`. A run-cache
staleness illusion is also possible for the earlier green np_demo (content-
addressed cache could predate today's tree); the `-o` experiment settles it.

## Update 2026-08-07 (ROOT CAUSE CONFIRMED — not a reachability/selection gap)

Both discriminators failed identically (README np_demo.py itself fails with
`-o`; the earlier green script-style run was a content-addressed run-cache
hit on an older binary). The real chain, read from the FULL compile log
instead of its tail:

```text
warning: failed to build py_runtime (command [... make -B -C pcc/py_runtime
  libpy_runtime_pcc_py.a PCC=build/bootstrap/pcc1
  PYTHON=/Users/jiamo/.pyenv/shims/python3] returned non-zero exit 2);
  final link may fail on undefined py_* symbols
...
ModuleNotFoundError: No module named 'llvmlite'   (building build_py/py_tuple.o)
make: *** [build_py/py_tuple.o] Error 1
```

1. Every pcc1 app compile force-rebuilds the pcc-Python port archive
   (`make -B libpy_runtime_pcc_py.a`) before linking.
2. The rebuild's host python was discovered as the pyenv shim
   (`~/.pyenv/shims/python3`), which has NO llvmlite — exactly the
   AGENTS.md "do not rely on bare python; pyenv state may not match the
   repository" trap, now inside pcc1's own app-link path.
3. The failure is FAIL-OPEN: a warning, then the link proceeds against the
   stale/partial state and dies later with four undefined `py_*` symbols
   that have nothing to do with the cause. `nm` confirms the repo archive
   actually CONTAINS all four symbols (and same-day-added
   pcc_gc_immortalize).
4. Workaround verified in flight: `PCC_HOST_PYTHON=<repo .venv python3>`
   (has llvmlite 0.46.0) for pcc1 app compiles.

Two real defects to fix generically:
- Host-python discovery for the app-link runtime rebuild must prefer the
  repository-blessed interpreter (or the port build must not need llvmlite
  at all under a self-backend pcc1 — why does a PORT module build import
  the LLVM binding?).
- The runtime-rebuild failure must fail closed (propagate the make error)
  instead of warning and producing a far-away undefined-symbol link error.

## Update 2026-08-07 (layers 2 and 3 fixed; runtime dlopen layer reached)

With PCC_HOST_PYTHON pointing at the repo .venv python, the full rebuild
surfaced and we fixed two more layers:

- Layer 2 — bad extern bindings from today's capi migration commit
  (py_type_of static-inline, py_int_rem nonexistent): own file,
  capi-port-extern-static-inline-py-type-of.md, plus a static regression
  (tests/python/test_port_extern_symbols_resolve.py) that closes the class.
- Layer 3 — single keep-alive anchor: the app link kept the C-API surface
  alive with ONE undefined-symbol anchor (`-Wl,-u,_PyArg_ParseTuple`),
  correct when the whole shim was one object, wrong after the migration
  split it across port members. numpy's _multiarray_umath then failed at
  dlopen: `symbol not found in flat namespace '_Py_GenericAlias'` (that
  symbol lives in py_capi_misc_runtime.o, which nothing in the app
  references). Fix in pcc/py_frontend/pipeline.py:
  `_capi_export_anchor_symbols()` derives the anchor list from `nm` of the
  actual runtime archive (every exported Py*/_Py* symbol, 373 today) so the
  mechanism is migration-proof; fallback to the historical single anchor if
  nm fails. pipeline.py is bootstrap-critical: stage1 rebuilt (108s) so
  pcc1 carries the change; three numpy demo binaries then compile AND link
  green.

Still open here: the fail-open rebuild warning, host-python discovery, and
the runtime demo timings (a concurrently running full GC4 bootstrap gate
owns the CPU; timing deferred until quiet).

## Update 2026-08-08 (layers 4-7; official L4 gate RED at HEAD; the whole
## chain is one migration commit)

The repository's own acceptance gate reproduces the breakage independent of
any demo: `PCC_RUN_NUMPY_L4_INTEGRATION=1 pytest
tests/integration/test_numpy_l4_pcc1_gate.py` FAILS on current HEAD
(`AttributeError: _ArrayFunctionDispatcher`, 1 failed in 251s). The earlier
"green np_demo" was a run-cache artifact; commit 93cfbca5 shipped the numpy
claim red behind incremental stamps.

Fixed this session, in causal order after layers 1-3:

- Layer 4 — three UNBALANCED `#if`/`#endif` regions in py_capi_shim.c (a
  missing `#endif` after `PyObject_InitVar`'s else-branch, a missing one
  after `PyObject_CallMethodObjArgs`'s else-branch, and an `#else`-swallowed
  sequence region), silently compiling out symbols intended to survive:
  PyEval_GetBuiltins, PyObject_Vectorcall(+Method), PyIter_Check/Next,
  PyUnicode_Compare(+WithASCIIString), PyException_SetCause/SetTraceback.
  Guards realigned; not-port-owned entry points made unconditional with
  rationale comments. Two genuinely missing symbols added to their
  port-domain owners: PyIndex_Check (py_capi_number_runtime, nb_index@136)
  and PyLong_AsLongLongAndOverflow (py_capi_numeric_runtime, CPython
  overflow contract via py_int_to_i64 + bignum sign@16).
  RESULT: the numpy _multiarray_umath undefined-import list vs the archive
  export set went 55 -> 8 -> 0 (nm -u diff; the 47 were data symbols my
  first T-only filter missed — sidecar now emits T/D/S/B/C).
- Layer 5 — duplicate function definition in
  py_capi_method_bridge_runtime.py (pcc_capi_builtin_object_getattr twice,
  copy-paste in 93cfbca5): host pcc emits valid IR for redefinition, pcc1
  MERGES both bodies into one define (31 `entry:` labels vs 30 defines,
  verifier: "Terminator found in the middle of a basic block"). Source
  deduplicated; the pcc1 redefinition divergence itself is a separate
  compiler bug worth its own row.
- Layer 6 — py_capi_module_state_runtime.py read PyModuleDef with CPython
  offsets, not pcc's fake_libc Python.h shape (base is 32 bytes; m_name@32,
  m_size@48, m_methods@56, m_traverse@72). numpy's def was rejected as
  "invalid module definition" (offset 8 = m_init, NULL in static defs).
  Five offsets + the file docstring fixed.
- Layer 7 — exception-code mislabels from the same commit: py_exc_new(6)
  commented RUNTIMEERROR is actually PY_EXC_ATTRIBUTEERROR (enum: 6=Attr,
  7=Runtime; no PY_EXC_SYSTEMERROR exists). Three raisers corrected to 7.

Remaining failure (the L4 gate's current red): `AttributeError:
_ArrayFunctionDispatcher` at numpy import. Same symptom as the RESOLVED
numpy-loader-probe-cext-reimport-load-once.md layer 2 (parent-before-child
import), but that fix IS present in the port archive
(py_compiled_module_ensure_parent_packages exported, loader calls it). That
earlier resolution was verified on the C runtime tier
(PCC_RUNTIME_CC=cc); today's failure is on the pcc-Python PORT tier —
discriminator in flight: the same app compiled with PCC_RUNTIME_CC=cc. If
cc passes, the bug is a port-mirror divergence somewhere in the compiled
-module/extension import chain.

## Update 2026-08-08 (discriminator verdict: tier-split — two distinct
## residual bugs)

The same numpy program compiled by the same pcc1 against the two runtime
tiers behaves differently:

- PORT tier (pcc1 default, PCC_RUNTIME_HIGH=py): import fails,
  `AttributeError: _ArrayFunctionDispatcher`.
- C tier (PCC_RUNTIME_CC=cc PCC_RUNTIME_HIGH=c): numpy imports, the
  program runs to DONE — but every worker prints `acc=0` instead of
  3500000: `int(SHARED[0])` on `np.array([7,8,9])` yields 0. A silent
  value bug on the element-access path, on the tier whose import works.

So the residual work is TWO mirror-divergence bugs, one per tier:
1. Port tier: the compiled-module/extension import chain's port mirror
   diverges from the C originals somewhere past ensure_parent_packages
   (that function is present and called). Diff the port mirrors of the
   import chain against py_capi_shim.c / py_extension_loader.c per the
   cc-vs-port playbook.
2. C tier: numpy subscript element value reads 0 — value/marshal path in
   the C-tier capi surface (PyLong/index conversion or getitem), not a
   crash, which makes it the more dangerous of the two (silent wrong
   data). Needs its own minimized repro (np.array + [0] + int()).

Neither is closable by demo-side changes; both are runtime-mirror
correctness slices with the L4/L5 gates as the acceptance criterion.

## Update 2026-08-08 (BOTH official gates run; earlier framing CORRECTED)

Ran the repository's two acceptance gates on the current tree:

```text
L5 (PCC_RUN_NUMPY_L5_ARRAY_INTEGRATION=1): 1 passed in 193.15s
L4 (PCC_RUN_NUMPY_L4_INTEGRATION=1):       1 failed in 251.94s
                                            AttributeError: _ArrayFunctionDispatcher
```

This corrects two things I asserted earlier in this file:

1. **Not a site difference.** L4 and L5 use the SAME numpy site
   (`build/head-truth/numpy-core/site` + meson-build + numpy root via
   PCC_PACKAGE_SITE), the same `build/bootstrap/pcc1`, and the same
   `--backend self --python-libpython=off --ir-scaffold=on`. My earlier
   probes used a different (pip-installed 2.4.6) site, which confused the
   picture.
2. **Not a generic import-path break from this session's edits.** L5 imports
   numpy, constructs an array, iterates it and unboxes scalars — strictly
   more runtime surface than L4 — and it is GREEN across PCC_GC_BACKEND=0..4
   on the production (pcc-Python port) tier. So the port tier's numpy import
   chain works; something narrower is wrong.

The ONLY difference between the green and red gate is the user program:

```text
L5 GREEN: import numpy as np; print([int(x) for x in np.array([1, 2, 3]) + 1])
L4 RED:   import numpy as np; print(np.__version__)
```

The program that does LESS fails. Under pcc's whole-program compilation the
program determines the compiled numpy module closure, so the working
hypothesis is that the closure (and therefore the package-init order) differs
between the two, and only one order avoids running `numpy/_core/__init__`
mid-exec of `_multiarray_umath`. That is the same mechanism
[`numpy-loader-probe-cext-reimport-load-once.md`](numpy-loader-probe-cext-reimport-load-once.md)
layer 2 fixed for the order it saw.

A concrete candidate mechanism, present in BOTH the C original and the port:
`pcc_run_compiled_module_init()` / `_run_compiled_module_init()` return 0
(success) when NO init node matches the requested name. So if a parent
package is not in the compiled closure under that exact name,
`ensure_parent_packages` silently does nothing, and the parent body is left
to be triggered mid-exec by `npy_import` — exactly the failing order. This is
a fail-open where a fail-closed (or at least a diagnostic) belongs.

Mirror comparison performed (C vs pcc-Python port), all EQUIVALENT — none of
these is the bug:

- register-before-exec ordering: present both sides (C 220/242, port 271/288)
- `ensure_parent_packages`: line-by-line equivalent dot-scan + parent init
- cache re-check after parent init: present both sides
- node layouts: PccCompiledModuleInitNode {name@0, init@8, state@16, next@24}
  and PccCompiledModuleNode {name@0, module@8, next@16} both match the port's
  offsets; extension node {name@0, path@8, handle@16, module@24, next@32}

One real (if minor) mirror regression found: the port's `_debug_event` drops
the `cached=` field that the C loader prints, so a port-tier trace cannot
show whether a lookup hit or missed. That is a diagnostic-parity defect and
it materially slowed this diagnosis.

Differential in flight: compile the EXACT L4 and L5 programs against the
head-truth site and compare `PCC_DEBUG_EXT_IMPORT=1` traces plus the compiled
module closures.

## Update 2026-08-08 (L4 GREEN — the import chain is fixed)

```text
L4 (PCC_RUN_NUMPY_L4_INTEGRATION=1 PCC1_BINARY=build/bootstrap/pcc1):
    1 passed in 177.86s          (was: 1 failed in 251.94s earlier today)
L5 (PCC_RUN_NUMPY_L5_ARRAY_INTEGRATION=1):
    1 passed in 193.15s
```

Both official numpy acceptance gates are now green on the production
(pcc-Python port) tier. The hand-built L4 program prints `2.4.4` and the
`_ArrayFunctionDispatcher` failure no longer reproduces anywhere.

The closure/ordering hypothesis in the previous Update is therefore NOT
needed to explain the failure and is withdrawn: the cause was in this file's
layer 4-7 list, and the last piece to land before L4 flipped green was the
exception-code correction (`py_exc_new(6)` is `PY_EXC_ATTRIBUTEERROR`, not
RuntimeError, in py_capi_module_state_runtime / py_capi_module_runtime /
py_capi_type_descriptor_runtime). A module-machinery error path was raising
**AttributeError** where CPython raises RuntimeError/SystemError, and numpy's
import machinery reacts to AttributeError differently — which is exactly why
the symptom read as a missing `_ArrayFunctionDispatcher` attribute. I did not
bisect the individual contribution of each of layers 4-7, so the honest
attribution is "the layer 4-7 set", with the exception-code fix as the last
change before the flip.

Diagnostic parity restored while chasing this: the port loader now prints
`cached=0/1` like the C loader (`load cached=1 name=...`), which is what
finally made the traces comparable.

## Update 2026-08-08 (residual found and fixed: len() on a C-extension object)

With import green, the remaining probe divergence is a real runtime gap, not
an import problem. Minimal repro (`np.array([7, 8, 9])`, port tier):

```text
CPython:  len=3  int_e0=7  int_e1=8  int_direct=7  str_e0=7   int_b0=5
pcc1:     len=0  int_e0=7  int_e1=8  int_direct=7  <null>     int_b0=5
```

Element access and `int()` unboxing are CORRECT; `len()` returns 0 and
`str()` of a numpy scalar prints `<null>`.

Root cause for `len()`: `py_obj_len()` had **no C-extension branch at all**,
while its immediate neighbour `py_obj_getitem()` does
(`if (pcc_capi_is_cext_type_tag(tag)) return pcc_capi_cext_object_getitem(...)`).
A cext tag then fell into the `tag >= PY_TYPE_USER` arm, looked for a Python
`__len__` on a user class, found none, and returned 0 — silently, with no
error. Same omission in the pcc-Python mirror.

Fix (three mirrors, because the cext surface has three C/port copies):

- `pcc_capi_cext_object_length(o)` — reads `mp_length` (tp_as_mapping@120)
  then `sq_length` (tp_as_sequence@112); returns **-1** when the type exposes
  neither, so the caller falls through instead of reporting a bogus 0. The
  sentinel avoids an out-parameter since real lengths are non-negative.
  Added to `src/py_capi_shim.c` (guarded, port-owned), `src/py_capi_shim_oracle.c`
  (the copy the default C archive actually links — discovered via
  `ar t libpy_runtime.a`), and `py/py_capi_cext_runtime.py` (the port owner).
- `py_obj_len` gains the symmetric cext branch in `src/py_obj_ops_dispatch.c`
  and `py/py_obj_ops_dispatch.py`.
- Declaration in `src/py_internal.h`.

Note for future mirror work: the cext/capi surface exists in THREE places —
`py_capi_shim.c` (port-archive member `py_capi_compat.o`, built with the
`PCC_PY_CAPI_*_RUNTIME` defines so it externs the port), `py_capi_shim_oracle.c`
(the member the default C archive links), and the `py_capi_*_runtime.py`
ports. A helper added to only one of them links in one tier and is undefined
in another.

`str()` of a numpy scalar had the same asymmetry and is fixed the same way:
the print formatter (`py_print_fmt`) already fell back to
`pcc_capi_cext_object_repr` when `py_obj_str` returned NULL, so `print(x)`
rendered correctly while `str(x)` returned NULL and `"..." + str(x)` produced
`<null>`. The fallback moved INTO `py_obj_str` (`src/py_obj_stubs.c` +
`py/py_obj_stubs.py`, declaration in `py_internal.h`) so concatenation,
f-strings and print all agree.

## Update 2026-08-08 (the len fix WOKE A DORMANT PATH — next frontier)

Re-running the element/len probe on the FULL pip-installed site
(`~/.local/share/pcc/environments/.../numpy` 2.4.6) after the length fix:

```text
Traceback (most recent call last):
  .../numpy/lib/_polynomial_impl.py", line 675, in polyfit
  .../numpy/__init__.py", line 844, in _mac_os_check
  .../numpy/__init__.py", line 851, in <module>
TypeError: object is not callable
** On entry to DLASCL, parameter number  4 had an illegal value
```

This is progress, not a regression: numpy's module-level `_mac_os_check()`
runs a real `polyfit` at import. While `len()` silently returned 0 that check
degenerated and never exercised the linear-algebra path; with a correct
length it now runs for real and hits the next genuine gap
(`TypeError: object is not callable`, plus LAPACK rejecting an argument).

Claim boundary: this is the FULL pip site, which no gate covers today. The
gated surface is the head-truth numpy-core site (L4/L5). Because the len/str
changes touch shared dispatch (`py_obj_len`, `py_obj_str`), both gates are
being re-run to confirm no regression before this work is described as done.

Next frontier for full-numpy import (new, not yet owned by a row):
`_mac_os_check` -> `polyfit` -> "object is not callable". Likely a callable
resolved through a path that yields a non-callable (cext function object or
a module attribute), to be minimized on its own.

## Update 2026-08-08 (gates went RED again — by my own correct fix; owned as P0)

Re-running both gates after the len/str dispatch fixes:

```text
L5: 1 failed in 186.30s      L4: 1 failed in 218.48s
    ** On entry to DLASCL, parameter number 4 had an illegal value
    numpy/lib/_polynomial_impl.py:675 in polyfit
    numpy/__init__.py:844 in _mac_os_check
    numpy/__init__.py:851 in <module>
    TypeError: object is not callable
```

Causal chain (confirmed, not guessed): `numpy/__init__.py:851` runs
`_mac_os_check()` at import, which calls `polyfit`. **While `len()` on a cext
ndarray wrongly returned 0**, polyfit degenerated early and raised
`ValueError`, and numpy's own `except ValueError: pass` swallowed it — so
L4/L5 were passing OVER a runtime bug of ours. With a correct length the
check runs for real, reaches `lstsq`, and the failure escapes as TypeError.

This is recorded as `BUG-P0-NUMPY-LSTSQ-NOT-CALLABLE-BLOCKS-L4-L5`. The fix
must NOT be to revert the len()/str() dispatch fixes: that would restore a
silently-wrong `len()` and a gate that is green only because numpy swallows
the exception our bug happens to trigger.

Two false leads killed by evidence, worth recording:

1. "lstsq is not callable" is a **misleading message**, not a finding.
   `PyObject_Call` emits `"object is not callable"` only when `py_obj_call`
   returned NULL *and no exception was set*; `py_obj_call`'s final
   fall-through is a bare `return null()`. So the message means "a call
   failed silently", not "this object has no tp_call". Verified separately
   that the call path HAS a cext branch in both mirrors, that
   `_ArrayFunctionDispatcher` declares `tp_call = &PyVectorcall_Call`, and
   that `PyVectorcall_Call` IS defined in the port archive.
2. `lstsq` is in fact reached and executed: LAPACK itself prints
   `On entry to DLASCL, parameter number 4 had an illegal value` before the
   traceback, i.e. a *dimension* argument is wrong. The remaining bug is a
   bad size/shape reaching LAPACK, not a callability problem.

Diagnostic added (pure instrumentation, no semantic change): dispatch event
10 `call_unmatched`, logged at `py_obj_call`'s fall-through with the type tag
in value0, so `PCC_LOG=dispatch` names the object a call silently gave up on.
Mirrored in `src/pcc_runtime_log.c` + `py/py_runtime_log.py` and
`src/py_obj_ops_dispatch.c` + `py/py_obj_ops_dispatch.py`.

### Instrumentation verdict: the object is an ordinary compiled function

The fall-through probe fired **zero** times, so `py_obj_call` never gave up
on an unmatched tag. Moving the probe to the raise site itself (in
`PyObject_Call`, mirrored across the port and both C copies) gave the answer
in one run:

```text
[pcc.dispatch] event=call_unmatched value0=9 value1=99 ptr=0x108c98fd0
```

`value0=9` is `PY_TYPE_FUNC`. The callable is an ordinary compiled pcc
function; `py_obj_call` took its FUNC branch and **`py_func_call_kwargs`
returned NULL without setting an exception**. Every callability theory is
therefore dead — tp_call, vectorcall, cext dispatch were all red herrings
generated by the caller-invented message.

Two stacked defects remain, and they should not be conflated:

- **(A) root**: a wrong size/shape reaches LAPACK — it complains
  `On entry to DLASCL, parameter number 4 had an illegal value` *before* the
  traceback, so `lstsq` really runs and is handed a bad dimension. CPython
  runs the same `_mac_os_check` fine, so this is ours.
- **(B) mask**: the error numpy raises in response does not register
  (`py_err_occurred() == 0`), so the compiled call returns NULL silently and
  the true cause is replaced by `object is not callable`. This belongs to the
  fail-closed contract in `DX-P1-RUNTIME-DIAGNOSTIC-FIDELITY`.

Next step for (A): a minimized shape/dimension probe of the polyfit inputs
(`vander(x, 3)` over a 5-point `linspace`), which needs numpy import to
succeed first — so (B) or a probe-local bypass has to come first.

Process note worth keeping: the C runtime sources are transition
implementations **and differential oracles**. Today's whole diagnosis rested
on comparing the cc tier against the port tier; an oracle that has drifted
from the port silently stops being an oracle and turns every differential
into a false signal. I introduced exactly one such drift while adding the
instrumentation above (edited the C event table without its
`py_runtime_log.py` mirror) and corrected it in the same pass. Every other
change in this session was landed in all of its mirrors — for the cext
surface that means THREE (`py_capi_shim.c`, `py_capi_shim_oracle.c`, and the
`py_capi_*_runtime.py` port).

## Update 2026-08-12 (duplicate-definition source implementation)

The separate compiler bug now has a source implementation and focused gate
source. Each same-named module `FuncDef` is keyed by AST identity and receives
a definition-ordinal native symbol, its own adapter, and its own function-value
cache. The executable `def` statement publishes that exact callable in source
order, including definitions inside module control flow, so a saved earlier
callable retains its original body while the module name is rebound. The new
codegen maps and sets are part of the closed-world L1 host contract. A real
pcc1 gate compares host/pcc1 verified IR shape and executes the escaped-callable
program without host Python. This records source closure only: no focused or
bootstrap gate was executed in the implementation-only phase, so the task
remains unfinished until those commands produce final summaries.

## Update 2026-08-12 (runtime rebuild fail-closed source implementation)

The app-link runtime rebuild boundary now resolves the configured host Python
or the pcc source artifact's `.venv` before consulting cwd/PATH, passes that
absolute interpreter to Make, and streams the causal child diagnostics. A
failed Make raises at `_ensure_runtime` and cannot proceed to the linker.
Transitional C runtime bundles additionally require a real archive member and
an exact archive-derived Py*/_Py* inventory, so the previously observed empty
`ar` publication plus stale sidecar is rejected. Focused source includes a real
pcc1 app build with a deliberately unusable PATH `python3`; it requires the
repository interpreter marker and forbids a downstream undefined-symbol error.
No command was executed in the implementation-only phase, so this is source
closure rather than gate evidence.
