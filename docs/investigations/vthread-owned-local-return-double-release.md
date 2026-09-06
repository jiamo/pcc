# Investigation: generator return consumes a local owner twice

## Status
active

## Problem Description
Native dashboard dispatch returns an invalid Response, and the benchmark
returns an empty dict and eventually aborts with BAD_INCREF. A generator's
owned-local return is transferred like an ordinary return, but generator
completion still releases every local.

## Repro
`gtimeout 90s env -u LC_ALL PCC_NO_AUTO_PCC1=1 uv run pytest -q -x -n0 --tb=short --show-capture=no tests/python/test_vthread_gateway_regressions.py -k generator_owned_local_return`

## Test [CONFIRMED]
A yielding child builds values=[1,2] and returns values. Before the fix three
runs print [], [], [] instead of [1,2] (3.21s). LLDB on the benchmark also
stops in pcc_debug_bad_incref during cleanup of the damaged result.

## Proposals
- No.1 Give the captured return value an independent owner [pending]

## No.1 Give the captured return value an independent owner
### Code Change
When a generator returns a borrowed load of an owned local, retain a separate
reference before transferring it into the return frame slot. The local keeps
its tracked owner, which completion or a finally reassignment may release.
Emitter-owned expression results keep their existing transfer behavior.
### pending
Regression covers both direct return and reassignment in a parking finally.
Full native dashboard, benchmark and fresh compiler qualification pending.
