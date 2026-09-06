# Investigation: native threading receiver temporary crosses a park in SSA

## Status
active

## Problem Description
Adding explicit native Lock/Event field types repairs gateway acquire dispatch,
but exposes an SSA error in BodyStream.read_chunk: threading acquire retains
the original attribute receiver until after a possible park.

## Repro
`gtimeout 90s env -u LC_ALL PCC_NO_AUTO_PCC1=1 uv run pytest -q -x -n0 --tb=short --show-capture=no tests/python/test_vthread_gateway_regressions.py::test_typed_lock_field_uses_native_threading_lowering`

## Test [CONFIRMED]
A Counter owns a typed Lock field and its increment method yields before
acquiring it. Before the fix, `self.lock.1.42` does not dominate
`threading.acquire.done.57` (1.92s). The same program without suspension passes.

## Proposals
- No.1 Persist the exact receiver in a managed frame slot [pending]

## No.1 Persist the exact receiver in a managed frame slot
### Code Change
Reserve receiver slots at acquire/wait/join call sites. After a successful
native park request, transfer the receiver into its slot before yielding.
Release it from that slot on completion or reacquire failure. Condition
reacquisition loads the original saved receiver rather than evaluating a
possibly mutated source field again. Generator cleanup owns exceptional exits.
### pending
Reduced native execution, threading regressions and gateway qualification pending.
