# Investigation: Backend #2 worker should trace allocation work

## Status
resolved

## Problem Description
Backend #2 has a threaded CMS worker and a queue, but positive allocation work
items are currently drained without doing mark work. That means the background
worker can report drains while the actual mark step remains dependent on
mutator assist or write-barrier gray-object tickets.

## Repro
Run:

```bash
env -u LC_ALL /opt/homebrew/bin/timeout 240s uv run pytest 'tests/test_gc_backend_concurrent.py::test_concurrent_backend_worker_traces_positive_allocation_work' -q -n0
```

Expected current failure before the fix:

```text
worker_traces remains 0 even after queued allocation work is drained
```

The correct behavior is for the worker to convert positive allocation work into
a bounded `pcc_gc_step_trace_cycle()` call and increment worker trace telemetry
when it marks at least one object.

## Test [CONFIRMED]
`tests/test_gc_backend_concurrent.py::test_concurrent_backend_worker_traces_positive_allocation_work`
failed before the fix with:

```bash
env -u LC_ALL /opt/homebrew/bin/timeout 240s uv run pytest 'tests/test_gc_backend_concurrent.py::test_concurrent_backend_worker_traces_positive_allocation_work' -q -n0
```

Observed result:

```text
wait_for_worker_trace() returned 0
```

## Proposals
- No.1 Let CMS worker run bounded trace steps for positive work     [CONFIRMED]

## No.1 Let CMS worker run bounded trace steps for positive work
### Code Change
Add a forward declaration for `pcc_gc_step_trace_cycle()` and have
`pcc_gc_cms_worker_main()` convert positive work queue entries into a small
bounded trace-cycle budget. Keep the existing gray-object ticket behavior.
### CONFIRMED
Implemented in `pcc/py_runtime/src/py_gc_backend.c`.

Positive queue entries now derive a bounded mark budget from allocation work,
run `pcc_gc_step_trace_cycle()`, and increment `PCC_GC_COUNTER_CMS_WORKER_TRACES`
when the worker marks objects. Negative gray-object tickets still use
`pcc_gc_cms_trace_gray_object()`.

Verification:

```bash
env -u LC_ALL /opt/homebrew/bin/timeout 240s uv run pytest 'tests/test_gc_backend_concurrent.py::test_concurrent_backend_worker_traces_positive_allocation_work' -q -n0
env -u LC_ALL /opt/homebrew/bin/timeout 240s uv run pytest tests/test_gc_backend_concurrent.py -q -n0
env -u LC_ALL /opt/homebrew/bin/timeout 180s make -B -C pcc/py_runtime libpy_runtime.a
PCC_GC_BACKEND=2 env -u LC_ALL /opt/homebrew/bin/timeout 180s uv run pytest tests/test_gc_g1_cycle_collector.py tests/test_gc_g2_finalizers.py -q -n0
env -u LC_ALL /opt/homebrew/bin/timeout 300s uv run pytest tests/test_gc_*.py -q -n0
```

Observed results:

```text
1 passed
3 passed
libpy_runtime.a built successfully
9 passed, 1 xfailed, 5 xpassed
145 passed, 25 xfailed, 15 xpassed
```

## Report
No.1 landed. This closes the specific gap where Backend #2 could drain
positive allocation work without performing background mark steps.

This does not make Backend #2 production CMS. Remaining work includes stronger
mark-termination behavior, lifecycle shutdown proof under stress, and thread
sanitizer-style validation of queue and marker interactions.
