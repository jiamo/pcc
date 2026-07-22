# Investigation: module attribute lookup loses an extension binding

## Status

resolved locally (2026-07-21)

## Problem Description

After the current compiler completes NumPy's pure-Python closure, NumPy's
`numpy._core.multiarray` wrapper fails its import-time check because
`hasattr(multiarray, "_multiarray_umath")` is false. Opt-in runtime tracing
shows the relative extension import successfully publishes
`_multiarray_umath` into that compiled module's attribute dictionary. A later
lookup of the same spelling returns null.

This is separate from the resolved SetType compilation failures. It must be
fixed as a generic module-attribute, dictionary, or ownership mechanism; NumPy
specific dispatch is not allowed.

## Causality Audit

- The pinned NumPy 2.4.4 site passes the existing L5 import-and-array-add gate
  with the older `build/bootstrap/pcc1` compiler.
- The same site fails when compiled from the current source.
- A fresh compiler cache produces the same failure, excluding a stale-object
  explanation.
- A minimized relative extension wrapper with 200 later module globals keeps
  its early extension binding. Therefore ordinary dictionary growth alone is
  insufficient to reproduce the failure.
- Runtime backend 0 is enough to reproduce, so relocation is not required.

## Root Cause

`py_module_attrs_dict()` exposes the side table's dictionary as a borrowed
reference. `_emit_globals_builtin()` returned that pointer as an ordinary
Python call result without retaining it. A function-local assignment such as
`namespace = globals()` therefore released the side table's sole reference
when the local died. The stale pointer was subsequently reused for a string
allocation; this is why dictionary lookup returned null even though the
extension binding had previously been stored.

The package-neutral regression reproduces the failure with a function-local
`globals()` result followed by another module lookup. The fix retains the
borrowed side-table dictionary at the Python call-result boundary. Temporary
NumPy-specific tracing was removed after classification.

The first post-fix probe reported missing `copyreg` only because the ad-hoc
compile command set `PCC_HOST_PYTHON=/usr/bin/false`. Host-assisted acquisition
is allowed to use its labeled host interpreter to locate stdlib source during
compilation; the emitted no-libpython artifact is the boundary that must run
without it. Recompiling with normal host stdlib discovery passes NumPy import
and array addition with `PCC_HOST_PYTHON=/usr/bin/false` at runtime.

## Proposals

- No.1 Classify the missing binding at the first failed lookup [done]
- No.2 Add the smallest package-neutral regression [done]
- No.3 Repair and validate the shared mechanism [active]
