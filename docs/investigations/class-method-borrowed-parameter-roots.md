# Class method borrowed parameters were not registered as GC roots

## Problem Description

The self backend rejected the PCC-native Harness settings module while
compiling `SettingsProvider.register`:

```text
self precise stack-map analysis in
'user_settings_runtime_SettingsProvider_register': stale managed SSA value
'namespace.1053.11' outlives its active root
```

The method reassigns its borrowed `namespace: str` parameter from the result
of a validation call. The pre-call value was pinned as an argument, but its
local slot was not registered until assignment lowering later converted that
slot into a borrowed root. Precise stack-map liveness therefore found the
pre-call SSA value live across a safepoint without an active frame root.

Ordinary user-function lowering registers borrowed object parameters before
the first body statement. Class method lowering only did this for closure-boxed
parameters; normal receivers, object parameters, and value-class pointer
payloads omitted the corresponding registration.

## Repro

The permanent minimized regression is:

```bash
gtimeout 120s env -u LC_ALL uv run pytest -q -x -n0 \
  tests/python/test_gc_root_precision.py::test_borrowed_class_method_parameters_are_rooted_before_calls
```

Before the fix, neither the `self` nor `namespace` slot had a borrowed frame
enter before the call to `normalize`.

## Test [CONFIRMED]

The focused IR regression passed on 2026-08-14 in 0.32 seconds. It asserts
that both borrowed parameter slots are registered before the first call. The
original Harness settings module also completed native self-backend compilation
with strict no-libpython mode:

```bash
gtimeout 900s env -u LC_ALL uv run pcc --backend self \
  --python-libpython off --ir-scaffold on \
  projects/harness/settings_runtime.py -o /tmp/harness-settings-current
```

The command exited `0`; the precise stack-map rejection did not recur.

The next full Harness compile exposed the same entry-timing invariant for a
boxed `int` method parameter. `Session.fork` conditionally rebinds
`event_count: int` from native `len(...)`. Exact-int preanalysis had not forced
the pointer-ABI parameter into its owned rooted slot because both semantic
writes were still typed `int`; it registered the slot only after the first
comparison. Preanalysis now treats any reassigned boxed-int parameter as a
single exact-object local. The focused regression
`test_boxed_method_int_parameter_roots_before_raw_branch_rebind` passes in
0.37 seconds and proves retain/root setup precedes `py_int_cmp`.

## Proposals

- No.1 Mirror user-function borrowed-parameter roots in class methods [implemented]
- No.1b Pre-root reassigned boxed-int parameters [implemented]
- No.2 Teach precise stack maps to exempt pin/unpin regions [DENIED]

## No.1 Mirror user-function borrowed-parameter roots in class methods

### Code Change

After binding each normal static-method parameter, instance receiver, and
instance-method parameter, class lowering now applies the same borrowed object
root and value-class pointer-payload root registration used by ordinary user
functions. Runtime-library suppression remains unchanged.

### CONFIRMED

The focused emitted-IR gate and the original module's native self-backend
compile pass. The fix applies before all class method body statements and keeps
the existing runtime-library suppression rule.

## No.1b Pre-root reassigned boxed-int parameters

### Code Change

When Python integer ABI boxing is active, exact-int preanalysis now forces an
annotated integer parameter into the exact-object lane if the function body
assigns that name. This preserves one representation for the incoming boxed
value and native integer RHS values before the first statement executes.

### implemented

The focused method regression passes. The complete Harness module closure then
compiled with the current-source self backend and ran its runtime self-check,
GUI self-check, and CLI turn successfully. `otool -L` reported only
`/usr/lib/libSystem.B.dylib` for the core executable, so neither class-parameter
case requires libpython support.

## No.2 Teach precise stack maps to exempt pin/unpin regions

### Code Change

Track pin depth in backend liveness and allow managed SSA values to cross a
safepoint while pinned even when their source slot has no active frame root.

### DENIED

This would preserve the frontend inconsistency and require path-sensitive
pin provenance in every backend. Registering borrowed method parameters at
function entry matches the existing user-function ownership rule and keeps
relocation updates observable through one local slot.
