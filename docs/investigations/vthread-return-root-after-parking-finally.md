# Investigation: return root does not dominate a parking finally continuation

## Status
active

## Problem Description
Compiling the external local HTTP gateway with the self backend fails SSA
verification in `_proxy_exchange_attempt`: a return-root pointer created before
a parking finally block is reused after the state machine resumes.

## Repro
`gtimeout 120s env -u LC_ALL PCC_NO_AUTO_PCC1=1 uv run pytest -q -x -n0 tests/python/test_vthread_gateway_regressions.py::test_parking_finally_preserves_the_return_value`

## Test [CONFIRMED]
The reduced worker returns 42 inside try, then calls a yielding cleanup in
finally. Before the fix it fails `ssa-dominance`: `gen.return.root.28.21`
does not dominate `vthread.delegate.completed.37` (2026-09-06, 26.06s including
runtime-archive preparation). The expected executable output is 42.

## Proposals
- No.1 Re-derive the return-slot pointer after pending finally blocks [pending]

## No.1 Re-derive the return-slot pointer after pending finally blocks
### Code Change
Create the GC pointer cast from the frame's dominating value slot again at the
post-finally insertion point before loading the return value. Preserve the
existing managed root store, retention and generator completion semantics.
### pending
Reduced self-backend runtime test and gateway compile/run are pending.
