# Investigation: handled exception context survives only in an SSA value

## Status
active

## Problem Description
After fixing return-root dominance, the external dashboard still fails the
self backend in `_proxy_exchange_attempt`. Full IR identifies the retained
handler exception as the nondominating operand of `exc.ctx.distinct` and
`py_exc_set_context` after cleanup parks.

## Repro
`gtimeout 120s env -u LC_ALL PCC_NO_AUTO_PCC1=1 uv run pytest -q -x -n0 --tb=short --show-capture=no tests/python/test_vthread_gateway_regressions.py -k parked_handler`

## Test [CONFIRMED]
A handler catches ValueError, yields, and raises RuntimeError. Before the fix
the self backend rejects `gc.retain.17.20` at `gen.resume.ok.29`/icmp (1.64s).
The oracle prints the original exception context and replacement exception.
Tests cover both named and unnamed handlers.

## Proposals
- No.1 Save active handler exceptions in managed generator frame slots [pending]

## No.1 Save active handler exceptions in managed generator frame slots
### Code Change
Plan a hidden slot per handler, root its original exception there, and reload
it for bare raise / implicit chaining. Named bindings reuse their already
planned frame slots. The ordinary non-generator handler path retains its
existing ownership behavior. Handler roots are cleared on normal completion;
generator cleanup handles early exits and cancellation through frame ownership.
### pending
Reduced runtime regressions and complete gateway qualification are pending.

## References
This is distinct from the prior [return-root fix](vthread-return-root-after-parking-finally.md).
