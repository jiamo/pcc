# Process-isolated TCP transport closure — 2026-08-13

Claim: the owned TCP-ring and all five collective operations execute across
two independent spawned rank processes on one host. This is explicitly a
`localhost-multiprocess` claim, not a multi-host or encrypted-transport claim.

Evidence:

- each rank has a distinct OS PID and TCP endpoint;
- admission uses the 256-bit PSK challenge-response path;
- allreduce, reduce-scatter, all-gather, broadcast and barrier match their
  deterministic vectors with no owner fallback;
- both processes exit normally and owners report closed transports;
- adjacent authentication, replay/key mismatch, framing and timeout cases
  remain green.

Commands:

```text
gtimeout 120s env -u LC_ALL uv run pytest -q -x -n0 tests/dist/test_multi_mac_transport.py tests/dist/test_collective_performance_contract.py
4 passed in 1.45s

gtimeout 120s env -u LC_ALL uv run pytest -q -x -n0 tests/dist/test_tcp_transport_owner.py
9 passed in 0.82s
```

