# DIST-P0-LOCAL-COLLECTIVE-ORACLE-CODE closure evidence

## Outcome

The local-only card is complete at its stated claim boundary: `pcc.dist`
provides single-process CPU reference semantics for session identity, transport
capability reporting and manifest validation, deterministic collectives,
DDP/FSDP sharding schedules, and KV block bookkeeping.  Network transports
remain fail-closed as `SKIPPED_WITH_REASON`; this card makes no multi-process,
multi-Mac, throughput, training, or inference claim.

Those higher claim levels are retained as separate finite task-board rows:
localhost TCP ownership, transport-backed collectives, strict multi-Mac proof,
throughput/scaling, and tensor/KV execution integration.  Moving them out of
this local-oracle card narrows the claim; it does not declare them complete.

## Gate

- `env -u LC_ALL uv run pytest -q -n0 tests/dist` — **89 passed in 0.25s**.

