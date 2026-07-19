# Authenticated multi-Mac owner — local implementation complete, hardware blocked

Task: `DIST-P1-MULTI-MAC-TRANSPORT-E2E`.

## Implemented claim

The owned TCP ring now has an explicit non-loopback path with no backend
fallback, explicit two-host/rank configuration, bounded connect/I/O cleanup,
and fresh-nonce PSK admission.  This is authenticated but unencrypted TCP
(`authenticated=True`, `secure=False`), not TLS/mTLS.  No multi-Mac execution
claim is made by the local implementation or tests.

## Changes

- `TCPRingOwner` retains localhost-only behavior by default.  Non-loopback
  endpoints require both `allow_remote=True` and a key of at least 256 bits.
- The accepting rank sends a fresh random challenge.  The peer response is
  HMAC-SHA256-bound to the nonce, canonical manifest digest, source rank, and
  destination rank.  Wrong keys, stale challenges, manifest drift, and rank
  drift fail closed before data frames.
- `pcc.dist.multi_host` loads an explicit two-node JSON config, local rank,
  shared key, and bounded timeouts.  It rejects loopback, one-host, invalid
  rank, short key, and unbounded timeout configurations.
- The strict test runs the same allreduce, reduce-scatter, all-gather,
  broadcast, and barrier vectors on each physical rank and verifies no
  fallback plus close cleanup.

## Local gates

- Import/compile smoke passed.
- Multi-host configuration, authenticated handshake, authenticated
  two-process ring, existing localhost owner, and existing collective owner:
  `19 passed in 1.60s`.
- Strict fail-closed probe:
  `PCC_DIST_HARDWARE_STRICT=1 ... tests/dist/test_multi_mac_transport.py`
  produced `1 failed in 0.21s` with
  `PCC_DIST_CLUSTER_CONFIG is required for the strict two-Mac gate`.

## Real blocker / unlock contract

This environment exposes no `PCC_DIST_*` cluster configuration and no second
Mac/rank.  Completion requires two concurrently reachable Darwin hosts and:

1. One shared JSON file with a unique `cluster_id` and exactly two entries,
   rank 0/1, each advertising its non-loopback `host:port`.
2. The same uncommitted `PCC_DIST_ADMISSION_KEY_HEX` (at least 32 bytes) on
   both hosts.
3. `PCC_DIST_RANK=0` on the first host and `PCC_DIST_RANK=1` on the second.
4. Concurrent execution on both hosts of the board's strict pytest command,
   with final passing summaries from both processes.

Until that external hardware execution exists, localhost success and a
default skip are explicitly not evidence.  The task remains blocked and is
not `DONE_STRONG`.
