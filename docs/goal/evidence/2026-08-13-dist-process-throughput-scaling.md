# Process-isolated collective throughput closure — 2026-08-13

Claim: real localhost TCP-ring traffic was measured with spawned rank
processes for world sizes 2 and 4 and payloads 4 KiB and 64 KiB. Each vector
uses at least three warmups and ten samples and records positive p50/p95 and
ring-allreduce effective bandwidth. The record is labeled
`localhost-multiprocess`; it does not claim cross-host scaling or an absolute
machine-independent speed floor.

The record builder rejects the process-isolated label unless every rank in
each declared world has a passing `spawn` summary and a distinct nonzero PID.

Command:

```text
gtimeout 120s env -u LC_ALL uv run pytest -q -x -n0 tests/dist/test_multi_mac_transport.py tests/dist/test_collective_performance_contract.py
4 passed in 1.45s
```

