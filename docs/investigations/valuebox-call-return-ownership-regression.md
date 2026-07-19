# Nested-function default-cache ownership regression

## Status

Resolved in the working tree (2026-07-16).  The filename is retained because
the investigation began with a broader, incorrect call-return hypothesis.

## Problem

The strict no-libpython, self-backed five-GC production contract initially
failed `test_valuebox_pointer_payload_survives_gc` for GC0 through GC4:

```text
163 passed, 5 failed in 60.51s
```

The first read of the event count made this look like every later payload
crossing a user-function return remained live.  Instrumented event-by-event
output disproved that classification.  Direct returns, returned containers,
methods, decorated methods, properties, and chained attribute reads all
released their payloads.  One object remained pinned and shifted every later
expected count: the `Track` reachable from a nested function's default value.

A fresh compile with the relevant caches disabled reproduced the failure, so
this was not a stale compiler or runtime artifact.

## Minimized reproducer

`test_nested_function_defaults_are_fresh_per_def_execution` in
`tests/python/test_python_function_features_parity.py` executes the same nested
`def` twice with a default expression derived from its outer call.  Before the
fix, the program printed:

```text
first
first
```

CPython and the corrected pcc path print:

```text
first
second
```

That is both an ownership bug and a Python-semantics bug: defaults belong to
the function object created by each execution of the nested `def`.

## Root cause

The frontend hoists nested function bodies into synthetic module-level AST
definitions.  `user_function_lowering.py` decided whether to cache a native
function object by scanning `ast_module.body`.  Because hoisting appends the
synthetic definition to that list, an empty-capture nested function looked like
a true module-level definition.

Its emitted wrapper was therefore stored in
`__pcc_native_func_value_cache_*` and retained for process lifetime.  That
wrapper owned its signature/default tuple, which owned the `ValueBox`, which
owned the `Track` payload.  Dropping the returned reader could not run the
payload finalizer.

`_hoisted_capture_params` is the authoritative lexical-nesting marker and
contains hoisted nested definitions even when their capture list is empty.

## Fix

`_create_native_user_function_object` now returns a newly created function
object immediately when `resolved_name` is present in
`_hoisted_capture_params`.  Only genuine module-level definitions continue to
use the process-wide function-object cache.

This restores fresh defaults per nested-`def` execution and removes the
unbounded ownership edge without weakening caller/callee ownership or GC
semantics.

## Verification

- Focused old/new nested-default regressions: `2 passed in 1.02s`.
- ValueBox and direct-payload roots across GC0..4: `10 passed in 35.61s`.
- Entire five-GC production contract: `168 passed in 55.65s`.
- Strict fallback baselines: `25 passed in 263.48s`.
- Final shared five-GC self-backend bootstrap matrix:
  `5 passed in 1500.11s (0:25:00)`.

## Claim boundary

The evidence proves fresh nested-function defaults and release of the cached
default payload under GC0..4.  It does not remove the intentional cache for
genuine module-level native functions, and it does not claim a general change
to user-function return ownership.
