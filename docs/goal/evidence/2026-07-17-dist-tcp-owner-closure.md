# DIST-P0-TCP-TRANSPORT-OWNER closure evidence

## Outcome

`pcc.dist.transport.select_owner("tcp-ring", manifest, rank)` now selects a
first-class localhost TCP-ring owner.  Its selection record proves requested
backend == actual backend and `fallback_used == false`; default capability
probing remains fail-closed and opens no socket.

The owner validates loopback-only `host:port` manifest endpoints, binds the
incoming rank identity to the shared canonical-manifest digest, and gives every
connect/accept/read/write a finite timeout.  Wire frames carry source rank,
destination rank, monotonic sequence, payload length, and SHA-256.  Close is
idempotent.  A spawned two-process test runs actual localhost sockets in both
ring directions and verifies the no-fallback selection record in each process.

Claim boundary: localhost multi-process point-to-point transport only.  This is
not TLS, multi-Mac, QUIC/RDMA, transport-backed collectives, throughput,
training, or inference support; those remain separate task-board rows.

## Gates

- `env -u LC_ALL uv run pytest -q -n0 tests/dist/test_tcp_transport_owner.py`
  — **6 passed in 0.60s**.
- `env -u LC_ALL uv run pytest -q -n0 tests/dist`
  — **95 passed in 0.57s** after the final implementation and claim updates.

