# 2026-07-16 production virtual-thread runtime-effect evidence

Task: `T-P0-VTHREAD-SCALE`

## Result

The production scheduler now emits a checked event stream from the operations
that actually change virtual-thread state, scheduler visibility, and GC-root
ownership. `pcc_threads.c` owns a fixed 4096-entry allocation-free buffer; an
atomic reservation gives each producer a distinct slot, and overflow increments
a fail-visible dropped-event counter instead of allocating or overwriting prior
evidence.

The public runtime ABI exposes reset/count/dropped plus indexed kind, detail,
root-delta, and state reads. Recorded transitions cover:

- ready enqueue, start, explicit park/unpark, and carrier resume;
- timer and IO park/wake;
- timer and IO cancellation;
- completion;
- root-handle enter/leave at the actual ready, waiter, timer, and IO handle
  registration/unregistration sites.

`pcc/runtime_effects.py` maps each production event into the existing shared
`RuntimeEffect` vocabulary. Its checker validates known event schema, expected
state and root delta, rejects a root leave without a matching enter, rejects
scheduler visibility without a live root, and requires final root balance zero.
The gate therefore sees real transfers such as `timer root enter -> ready root
enter -> timer root leave -> ready root leave`, rather than a synthetic net
count around a public call.

## Focused gates

The new no-libpython C probe built one isolated production runtime and exercised
the complete transition path in a separate process for each GC backend 0..4.
It forced the already-tested poll IO backend for deterministic event ordering,
ran GC while timer/IO roots were the only owners, required every transition and
effect category, checked the full event stream, and required `dropped == 0`.

```text
gtimeout 240s env -u LC_ALL uv run pytest -q -n0 \
  tests/python/gc_production_contract/test_vthread_runtime_effect_events.py

6 passed in 11.22s
```

The directly affected runtime-effect, waiter/root, timer-cancel, and IO-waitset
regressions remained green:

```text
gtimeout 120s env -u LC_ALL uv run pytest -q -n0 \
  tests/python/test_runtime_effect_category.py \
  tests/python/gc_production_contract/test_vthread_waiter_node_pool.py \
  tests/python/gc_production_contract/test_vthread_timer_cancel.py \
  tests/python/gc_production_contract/test_vthread_io_waitset_runtime.py

35 passed in 27.83s
```

The pcc-Python archive rebuilt and exercised ready/timer/IO continuation-root
ownership across GC0..4, including the cross-translation-unit waiter event
hooks:

```text
gtimeout 120s env -u LC_ALL uv run pytest -q -n0 \
  tests/python/test_gc_coroutine_roots.py::test_pcc_python_runtime_virtual_thread_scheduler_queues_keep_continuation_roots_across_backends

1 passed in 35.36s
```

`tests/python/test_py_runtime_abi_attrs.py` passed (`2 passed in 0.30s`), and
`py_compile` passed for the changed Python checker, runtime ABI, and production
event test. No bootstrap, full-GC bootstrap matrix, full GCC, or broad test
suite was run.

## Claim boundary

This proves checked production park/resume/timer/IO/cancel/completion events and
balanced scheduler-root lifecycle under GC0..4 for the scoped stackless
virtual-thread route, including the pcc-Python runtime archive link boundary.
It does not prove an unbounded telemetry stream, Linux epoll, arbitrary
native-stack suspension, or the separate real one-million-thread RSS, latency,
and GC-pause gate.
