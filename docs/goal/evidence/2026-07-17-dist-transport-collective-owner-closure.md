# DIST-P0-TRANSPORT-COLLECTIVE-OWNER closure evidence

## Outcome

`transport.select_collective_owner("tcp-ring", manifest, rank)` now runs
allreduce, reduce-scatter, all-gather, broadcast, and barrier across the
explicit localhost TCP-ring owner with no backend fallback.

Each ring packet carries protocol version, collective kind, operation sequence,
origin rank, and strict finite int/float POD data. Every rank gathers full rank
coverage, orders contributions by origin rank, and invokes the existing
single-process collective oracle. This preserves one semantic authority and
keeps floating-point reduction order deterministic across different ring
arrival orders. Cooperative cancellation is checked at each bounded ring
round; a missing participant is bounded by the TCP owner's I/O deadline.

The real three-process gate executes all five operations over localhost sockets
and checks requested backend == actual backend, `fallback_used=false`, rank
coverage, operation sequences, and oracle-equal results. A focused fault gate
proves a nonparticipating peer produces a bounded read timeout.

Claim boundary: localhost transport-backed deterministic collectives only. No
multi-Mac, secure admission, throughput/scaling, tensor/KV execution, training,
or inference claim is made.

## Gates

- `env -u LC_ALL uv run pytest -q -n0 tests/dist/test_transport_collective_owner.py`
  — **4 passed in 1.10s**.
- `env -u LC_ALL uv run pytest -q -n0 tests/dist`
  — **99 passed in 1.51s** after final code and claim updates.

