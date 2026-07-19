# Investigation: native self-backend emission from pcc1 without host Python

## Status

resolved 2026-07-13

## Problem Description

`S-P0-SELF` required the real M1 simplejson application to compile and execute
through pcc1 with `backend=self`, `python-libpython=off`, and no host Python,
host pcc, LLVM fallback, or libpython edge. The original strict compile
invocation failed before linking when `PCC_HOST_PYTHON=/usr/bin/false` because
`pipeline.py` ran the Python-authored `pcc.backend.*` emitter in a host Python
subprocess.

The intended fix is to make the existing generic self backend available as a
pcc-native component. It must not reintroduce compiled-stage imports of
`pcc.backend.*` through `py_cpy_*`, special-case simplejson, or silently select
LLVM.

## Repro

```text
PCC_PACKAGE_SITE=build/m1-site/simplejson-4.1.1 \
PCC_GC_BACKEND=0 \
PCC_HOST_PYTHON=/usr/bin/false PCC_HOST_PCC=/usr/bin/false \
build/bootstrap-compat-runner-pcc1/pcc1 \
  --backend self --python-libpython=off --ir-scaffold=on \
  <m1-app.py> -o <m1-app>
```

Observed stable boundary:

```text
error: PCC-PY-COMPILE-001: [python-frontend] self backend native emission failed: subprocess.run failed
```

No output executable is published and no LLVM/libpython fallback occurs.

## Proposals

- No.1 Compile the existing AArch64 Darwin self emitter into a pcc-native helper [confirmed]
- No.2 Route compiled pcc1 emission through the native helper without host Python [confirmed]
- No.3 Prove the real M1 app plus GC0 fixed point [confirmed]

## No.1 Compile the existing AArch64 Darwin self emitter into a pcc-native helper

### Test [CONFIRMED]

A minimal pcc-native helper entry importing the full backend closure first
failed for two self-host metadata boundaries. The x86_64 module is irrelevant
to the current AArch64 Darwin artifact and can be excluded by using the direct
AArch64 emitter. The remaining AArch64 closure failed because
`ParsedFunction` dataclass default-factory fields were treated as required
across the compiled module boundary.

### Code Change

Make backend construction explicit for all `ParsedFunction` mutable/default
fields. Continue compiling the actual generic AArch64 backend modules; do not
replace the emitter with a package-specific or IR-pattern-specific path.

### confirmed

The compiled helper emitted assemblable AArch64 Darwin output for the entry,
simplejson encoder, and collections modules. The in-process pipeline path uses
the same generic parser, stack preparation, and AArch64 emitter modules with
peephole optimization disabled for the compiled-stage path; public host calls
retain the optimized default.

## No.2 Route compiled pcc1 emission through the native helper without host Python

### Test [CONFIRMED]

With both host process escape hatches made executable failures,

```text
PCC_HOST_PYTHON=/usr/bin/false PCC_HOST_PCC=/usr/bin/false
```

pcc1 compiled and linked an import-only simplejson program and the full M1
behavior application through `--backend self --python-libpython=off`. The
import-only artifact printed `1`. The full application initially reached the
native extension's `list.sort` path and stopped in `pcc_debug_bad_incref`.
That boundary was therefore after strict self emission and linking, not a host
emitter fallback.

### Runtime ownership failure [CONFIRMED]

The same crash reproduced in a newly linked LLVM-backed oracle, ruling out the
self emitter. An LLDB hardware watchpoint on the signature magic string's
refcount identified the decrementing stack as:

```text
pcc_refcount_decref
py_decref
user_py_func__signature_valid
py_func_call_kwargs
py_obj_call
PyObject_Call
encoder_sort_items_inplace
```

`pcc/py_runtime/py/py_func.py::_signature_valid` loaded tuple slot zero through
`pcc_gc_load_ptr`, a borrowed load, but decremented it on every exit. The C
mirror already used owned `py_tuple_get`. Changing the pcc-Python mirror to
`py_tuple_get(sig, 0)` restored the documented ownership contract. The existing
`METH_VARARGS | METH_KEYWORDS` native-extension regression passes after the
change.

### Strict application result [CONFIRMED]

After rebuilding the normal precompiled pcc-Python runtime archive, the exact
hosts-disabled strict command completed and the artifact printed:

```text
native True
encoded {"items":[1,"two",null],"ok":true}
roundtrip True
```

`otool -L` reported only `/usr/lib/libSystem.B.dylib`; there was no libpython
or LLVM dynamic dependency. The M1 regression now sets both host variables to
`/usr/bin/false` and checks the final dependency list.

## No.3 Prove the real M1 app plus GC0 fixed point

### Bootstrap failure split [CONFIRMED]

The first final-state GC0 run reached `pcc1 -> pcc2` and exposed two distinct
self-emitter worker failures. The ownership slice stopped at numeric SSA value
`%.9`; the hoist slice stopped at `%.6`. LLDB at `assign_stack_slots` showed
that the entry block definitions never reached stack preparation even though
the parser had parsed them.

`_filter_reachable_blocks` keyed its reachable set with native strings. Under
compiled pcc, string-hash behavior could drop the entry block while retaining
later branch targets; the old recovery only noticed missing retained targets.
The fix uses a stable integer block-name key with collision buckets and integer
block indices. Numeric SSA values use their numeric id directly through parse,
used-value collection, stack preparation, materialization, and calls. This
keeps the normal path linear without relying on compiled string hashing.

The exact ownership and hoist workers pass after the change. Focused
reachability/SSA regressions cover a changed-hash entry-block drop and numeric
SSA slot lookup.

### Bootstrap harness boundary [CONFIRMED]

The full gate also proved two harness assumptions that needed to be explicit:

- a clean healthy stage takes longer than the old 600-second per-stage
  watchdog, so the bounded stage timeout is now 900 seconds;
- the standalone pcc1 publish smoke cannot rebuild its own pcc-Python runtime
  archive. `shared_stage1_pcc1` now explicitly depends on the host-built
  `pcc_py_runtime_archive`, and that fixture requires both a zero host-pcc exit
  code and a fresh archive instead of accepting a stale file left on disk.

The generated L1 static-method table was regenerated as valid Python after a
malformed leading path token was caught by the focused host runtime build. The
fallback ratchets were then rerun from the repaired final source.

### Final gates [CONFIRMED]

Strict real M1 canary behavior, with the test setting both host escape hatches
to `/usr/bin/false` and checking the produced dependency list:

```text
gtimeout 900s env -u LC_ALL \
  PCC_CURRENT_PCC1=build/bootstrap-compat-runner-pcc1/pcc1 \
  PCC_REQUIRE_CURRENT_PCC1=1 \
  PCC_M1_SIMPLEJSON_SITE=build/m1-site/simplejson-4.1.1 \
  uv run pytest -q -n0 tests/python/test_m1_simplejson_import_behavior.py
```

Result: `2 passed in 79.87s`.

Final-source fallback, IR, and bootstrap baselines:

```text
gtimeout 700s env -u LC_ALL uv run pytest -q -n0 \
  tests/python/test_fallback_baseline.py \
  tests/python/test_ir_py_fallback_baseline.py \
  tests/python/test_bootstrap_gate_baseline.py
```

Result: `23 passed, 4 skipped in 343.58s`.

Final-source GC0 self-host chain:

```text
gtimeout 3000s env -u LC_ALL uv run pytest -q -n0 \
  tests/python/gc/test_pcc_bootstrap_full_gc0.py
```

Result: `1 passed in 1952.44s (0:32:32)`. The gate rebuilt pcc1, ran
`pcc1 -> pcc2 -> pcc3`, rejected libpython linkage, and proved normalized
pcc2/pcc3 identity.

### Claim boundary

This resolves `S-P0-SELF` for the M1 application and GC0 fixed point. It does
not claim that the identical installed extension has run under GC1..4; that is
the separate `G-P0-GC` task.

Run focused self-backend and bootstrap-adjacent regressions, then the required
GC0 pcc1 -> pcc2 -> pcc3 fixed-point gate. Do not promote S-P0 from the strict
application result alone.
