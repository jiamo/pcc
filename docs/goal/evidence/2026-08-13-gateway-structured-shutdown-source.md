# Gateway structured shutdown source — unverified

Date: 2026-08-13

This is an implementation-only checkpoint. No pytest, compiler, pcc,
bootstrap, live-network or task-board validation command was run.

The gateway shutdown source now enforces one ownership rule: a control thread
may signal cancellation and shut down a descriptor, but only the connection
virtual thread may join its local-handler child and release request, body,
admission, TLS and continuation ownership. Carrier stop, listener close,
application shutdown and signal-owner release occur only after the accept
owner is terminal, the connection resource ledger is empty, and an independent
task ledger has observed every surrounding connection vthread's terminal
outcome. Resource release from the vthread's `finally` therefore cannot create
a remove-before-task-terminal restart window.

If either owner class remains nonterminal at the configured deadline, teardown
returns a named error and retains the carrier pool, listener descriptor,
connection ledger, admission counters and scheduler roots. The same
`GatewayServer.shutdown()` instance can be called again after cooperative
termination. Focused test source covers accept timeout/retry, connection
timeout/owner completion/retry, carrier-stop failure, and the prohibition on
control-thread connection settlement.

This does not claim forced preemption. An application callback or iterator
which never reaches a pcc safepoint/cancellation boundary may require the
embedding process owner to terminate its isolated worker.
