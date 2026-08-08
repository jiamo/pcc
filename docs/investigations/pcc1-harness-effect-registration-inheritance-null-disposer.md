# Investigation: pcc1 dispatches a service registration through Effect.dispose

## Status

active

## Problem Description

The native Harness self-check failed while withdrawing a Cordis Provider after `ServiceRegistration` and `EventRegistration` were changed to inherit `Effect`. Both subclasses override `dispose()` and initialize the base with no setup callback. CPython correctly calls `ServiceRegistration.dispose()`. The pcc1-built executable entered `Effect.dispose()` instead and attempted to call a null item as a disposer.

The first attempted correction removed inheritance but kept ordinary Effects, service registrations, and event registrations in one list. The rebuilt binary failed in the same method, proving self-backend statically selected `Effect.dispose()` for the heterogeneous list rather than dispatching by the unrelated element type. The production ownership list therefore needs one actual record type, not nominally or structurally different records.

## Repro

The failure was observed from the pre-fix native Harness binary:

```bash
gtimeout 60s env -u LC_ALL projects/harness/build/harness-core --self-check
```

Expected: `HARNESS_RUNTIME_SELF_CHECK_OK`. Observed: exit `1`, with a traceback through `PluginKernel._unload_unchecked`, `PluginContext.dispose`, `Effect.dispose`, and `RuntimeError: py_obj_call received NULL callable`.

## Test [CONFIRMED]

The realistic native self-check failure was observed on 2026-08-14. `projects/harness/tests/test_plugin_runtime.py` and `tests/python/test_harness_plugin_kernel.py` pass under CPython, so the failing dispatch is native-only. The final acceptance is the rebuilt Harness passing its reactive Provider withdrawal self-check and focused current-pcc1 lifecycle integration.

## Proposals

- No.1 Model every owned registration as one tagged Effect record [pending]
- No.2 Generalize PCC inheritance dispatch from this Harness trace [pending]

## No.1 Model every owned registration as one tagged Effect record

### Code Change

Use one `Effect` record for setup callbacks, service publications, and event listeners. A `kind` field selects service withdrawal, event removal, or reverse disposer execution inside one `dispose()` method; the service/event fields live on the same record. `PluginContext.effects` is then genuinely homogeneous while preserving acquisition order across all registration kinds.

### pending

Removing inheritance alone passed sixteen host tests and standalone compilation but failed the rebuilt native self-check in the same `Effect.dispose()` frame. The tagged homogeneous representation now requires the same host and native gates.

The first tagged build removed the null-call crash but left the Consumer active after Provider withdrawal. `kind` had been initialized as integer `0` and reassigned after `Effect()` construction. The revised representation passes the literal `effect`, `service`, or `event` tag into the constructor so the self backend fixes the field representation and value at object creation.

## No.2 Generalize PCC inheritance dispatch from this Harness trace

### Code Change

Minimize the subclass/list/override behavior into an isolated PCC regression, determine whether the error is method-slot dispatch or base-constructor state, and fix the compiler/runtime rather than encoding a Harness-specific exception.

### pending

This remains a separate PCC completeness follow-up after the production Harness path is restored. The current evidence is insufficient to choose a compiler layer safely.

## Update 2026-08-14: the failing receiver is PluginContext

LLDB on the rebuilt Harness stopped in `Effect.dispose()` directly from `PluginKernel._deactivate`. The receiver's first field contained `"probe-provider"`, and the backtrace showed the call came from `scope.dispose()`, where `scope` is the provider's `PluginContext`. The first failing call was therefore not an `Effect` list element: the compiler selected `Effect.dispose` for a `PluginContext` receiver.

The lowering cause is the last closed-world method fallback. When no class hint reaches a name receiver, it selected the first class declaring the method even when multiple unrelated classes declared that name. The proposed compiler fix keeps the direct call only for one unique candidate; multiple candidates use runtime attribute binding and the receiver's actual class/MRO. A minimized `Effect`/`Context`/`Fiber.context` regression accompanies the change.
