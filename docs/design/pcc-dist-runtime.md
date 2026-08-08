# pcc.dist — Local Oracle, Process-isolated Owners, and Optional Remote Admission

Status: local oracle, explicit process-isolated localhost TCP-ring/collective
owners, and an optional authenticated-but-unencrypted remote admission path.
Source: converted from `docs/refs_docs/deep-research/deep-research-distribute.md` (TVM Disco
`Session`/`DRef`/device-mesh boundaries; Apple-Silicon transport reality;
vLLM paged-KV block management).
Goal rows: `D-P0-DIST-SESSION`, `D-P0-DIST-TRANSPORT`, `D-P0-DIST-COLLECTIVE`,
`D-P0-DIST-SHARDING`, `D-P0-DIST-KV-BRIDGE` (`docs/goal/task-board.yaml`).

## Claim boundary (read this first)

> **The default package surface is metadata only.** It models distributed identity, ownership,
> device placement, deterministic collective *semantics*, sharding *schedules*,
> and KV-block *bookkeeping* in a **single process, on CPU, with no sockets**.
> It does **NOT** implement — and its existence does **NOT** justify any claim
> of — multi-host execution, QUIC/RDMA transport, secure cluster
> admission (TLS / mTLS), PyTorch or MLX training, pcc-native tensors, or vLLM
> serving. Every networking capability is reported **UNAVAILABLE** through an
> explicit `SKIPPED_WITH_REASON` result, never silently skipped. The separate
> explicit `transport.select_owner("tcp-ring", ...)` path defaults to bounded
> localhost multi-process point-to-point transport. Its non-loopback path is
> separately opt-in and PSK-authenticated but unencrypted. The strict owned
> execution gate uses independent spawned rank processes and proves only
> same-host process isolation; neither path is labeled multi-host execution.

This is the honest-boundary discipline required by the project north star:
the mode label ("local-only metadata oracle") is part of every claim.

## The identity model

The identity model is the port of TVM Disco's `Session`/`DRef`/device-mesh, but
carrying **only identity and placement metadata** — no workers, processes, or
channels.

| Concept | Type (`pcc.dist.session`) | Meaning |
|---|---|---|
| Participant | `Rank(index, world_size)` | Stable identity in `[0, size)`. **Equality/hash are index-only**, so a rank keeps meaning across equal-index worlds and can key dicts / sort deterministically. |
| Participant set | `World(size)` | Immutable set of ranks; `leader == rank(0)`. |
| Placement | `DeviceMesh(shape, axis_names=…)` | Row-major bijection between ranks and N-D coords. `coord_of`/`rank_of` are exact inverses (the pinned invariant). `ranks_along_axis` yields a collective group (e.g. tensor-parallel peers). |
| Remote object ref | `DRef(owner, obj_id, label)` | `(owner rank, per-owner-unique id, serialization label)`. **Equality is `(owner, obj_id)`**; `label` is descriptive payload metadata. `serialize()`/`deserialize()` round-trip through a stable `dref:<owner>/<size>:<id>:<label>` wire string. |
| Session | `PCCDistSession(world, mesh?)` | Binds a world + optional mesh; mints monotonically-unique `DRef`s per owner; **refuses all networking**. |

`DRef` ownership + serialization is the seam a real transport uses to move
objects between processes; the local oracle exercises identity + wire
round-trip, while the explicit TCP owner binds rank identity to its manifest.

## The local-only boundary (how "distributed" is refused)

Networking is refused explicitly, not by omission:

- `PCCDistSession.connect(mode)` returns a `SKIPPED_WITH_REASON`
  `CapabilityResult` for every known mode (`insecure-dev`, `bonjour`,
  `tcp-ring`, `quic`, `jaccl-rdma`) and **raises** `SessionError` for an unknown
  mode (a typo is loud, never a silent skip).
- `PCCDistSession.require_connected(mode)` always raises
  `DistUnavailableError`.
- `pcc.dist.transport.probe(mode)` reports `AVAILABLE` **only** for
  `insecure-dev` (a local, in-process loopback marked `secure=False`); every
  network mode is `SKIPPED_WITH_REASON` with a mode-labeled reason.
- `pcc.dist.transport.open_channel(mode)` / `require_transport(mode)` raise
  `DistUnavailableError` for any network mode, so a caller cannot proceed as if
  a socket were connected.

## Explicit localhost TCP-ring owner

`transport.select_owner("tcp-ring", manifest, rank)` is the only socket-opening
route. It records requested backend == actual backend with no fallback, binds
rank identity to a shared manifest digest during handshake, bounds connect,
read, write, and close behavior, and carries rank, sequence, length, and
SHA-256 in every frame. Rank `r` sends to `(r + 1) % world_size` and receives
from `(r - 1) % world_size`; endpoints must be loopback addresses. This is not
TLS, multi-Mac, throughput, training, or inference support.

`transport.select_collective_owner("tcp-ring", manifest, rank)` runs
allreduce, reduce-scatter, all-gather, broadcast, and barrier through that
owner. Contributions circulate with strict operation sequence/kind/origin
envelopes; each rank orders them by origin rank and invokes the existing local
oracle, so the network path cannot introduce a second reduction semantics.
Cancellation is checked at bounded ring rounds, and a nonparticipating peer is
terminated by the underlying I/O deadline.

## Optional multi-host admission (outside the completion claim)

`TCPRingOwner(..., allow_remote=True, admission_key=<32+ bytes>)` is the only
route that accepts non-loopback endpoints. Before rank traffic, the accepting
peer sends a fresh random nonce and the connecting peer returns HMAC-SHA256
bound to that nonce, the canonical manifest digest, and source/destination
rank. A wrong key, replayed response, different manifest, or different rank is
rejected. The same bounded connect/I/O deadlines and idempotent close path
apply on failures.

This is authenticated cluster admission, not encrypted transport:
`selection.authenticated=True`, `selection.secure=False`. It is not TLS/mTLS,
and no confidentiality claim is made. `pcc.dist.multi_host` remains an optional
loader for an explicit two-node JSON config, per-process rank, shared
256-bit-or-longer key and bounded timeouts; it is not a completion gate or a
multi-host proof. The historically named
`tests/dist/test_multi_mac_transport.py` now runs two independent spawned rank
processes over real loopback TCP, checks authenticated admission and all five
collectives, and records the honest `localhost-multiprocess` scope.

### The `SKIPPED_WITH_REASON` result object

`pcc.dist.results.CapabilityResult` carries `status ∈ {AVAILABLE,
SKIPPED_WITH_REASON, ERROR}`, a mode-labeled `reason` (validated non-empty when
skipped), and a `detail` mapping. `raise_if_unavailable()` converts a non-
available result into `DistUnavailableError`. This is the one shared skip
taxonomy used across session/transport — there is no silent `pass` path.

### Signed cluster manifest oracle

`pcc.dist.transport` parses/validates a **signed cluster manifest**
(`build_manifest` → `sign_manifest` → `parse_signed_manifest`). It validates
structure (rank coverage `0..N-1` exactly once, known transports, non-empty
hosts), produces a canonical order-independent body, and verifies an
HMAC-SHA256 signature.

> The HMAC here is a **structural signature oracle** for round-trip and
> tamper-detection tests. It is **not** a PKI / mTLS cluster-admission control;
> real TLS/mTLS admission is a later `D-P0-DIST-TRANSPORT` gate.

## Deterministic collective oracle

`pcc.dist.collective` is the **reference semantics** a real transport-backed
backend must reproduce, computed in one process over fake ranks and POD (list)
buffers:

- `allreduce`, `reduce_scatter`, `all_gather`, `broadcast`, `barrier`.
- Reductions (`sum/max/min/prod`) are deterministic, left-to-right over
  ascending rank order (order-independent for commutative ops).
- Shape / dtype / rank-count mismatches raise `CollectiveError`;
  `reduce_scatter` requires the buffer length divisible by the world size.
- `CollectiveOp` carries `timeout_s` / `cancellable` **metadata** so the API
  shape matches a real backend; the local oracle is synchronous and never
  fires them (`status == "completed"`).
- Invariant: `reduce_scatter_then_all_gather(bufs) == allreduce(bufs)[rank]`
  (the ring-FSDP identity), pinned by tests.

## CPU-only sharding oracle

`pcc.dist.sharding` maps parameters (name + element count — **no tensors**) to
per-rank shards and validates the implied collective schedule against the
collective oracle:

- `plan_ddp`: full replica on every rank; schedule `("allreduce",)`.
- `plan_fsdp`: flatten + pad each parameter to a multiple of the world size,
  split into equal shards (pad on the last rank's tail); schedule
  `("reduce_scatter", "all_gather")`.
- `validate_plan`: full rank coverage, equal FSDP shard sizes, contiguous
  offsets.
- `validate_schedule_against_collective`: DDP → an allreduce runs cleanly;
  FSDP → reduce_scatter+all_gather reconstructs the allreduce result.
- **Parameter-server** is an explicit non-default marker
  (`STRATEGY_PARAMETER_SERVER`) that `build_plan` **rejects** with a clear
  error — it is documented, not implemented.

## Local KV block manager oracle

`pcc.dist.kv.BlockManager` models vLLM-style paged-KV bookkeeping as pure
metadata (token ids + counters; **no GPU, no cache memory, no serving**):

- Token sequences are chunked into fixed-size blocks; each block's identity is
  a **deterministic prefix hash** `sha256(parent_hash + tokens)`, so shared
  prefixes deduplicate to the same block (prefix caching) and hashes are stable
  across manager instances.
- `refcount` + `pin`/`unpin`: a block is evictable only when `refcount == 0`
  **and** unpinned.
- Eviction policy: among evictable blocks with **no live children**, evict the
  **shortest prefix depth first** (longest-prefix retention), breaking ties by
  least-recently-used. A full cache with no evictable victim raises `KVError`
  rather than silently over-committing.
- `KVBlockHandle.serialize`/`deserialize` and `BlockManager.serialize`/
  `deserialize` round-trip stably (re-serialization is byte-identical).
- `invalidate(handle)` drops the block and all descendants (children depend on
  the parent prefix) and refuses to drop a pinned block.

## Bounded tensor/KV execution bridge

`pcc.dist.tensor_kv_execution` adds one deliberately small execution layer over
the owned TCP collectives:

- `PccOwnedCpuTensor` owns f64 host values plus a `PccBufferHandle`; gradient
  synchronization performs a real transport allreduce and checks the result
  against `pcc.dist.collective`.
- `KVOwnership` moves one canonical `BlockManager` serialization from a source
  rank to a destination rank through transport broadcast. The source is marked
  released and the destination reconstructs byte-identical KV state.
- Both paths allocate a POD transfer buffer, schedule it behind a
  `PccFenceToken`, and prove it becomes `FREED` only after completion.

This is CPU/POD execution through the pcc-owned buffer and transport contracts.
It is not PyTorch/MLX training, a GPU tensor, a full model, elasticity, serving,
or an inference runtime.

## Module / test map

```
pcc/dist/
  __init__.py     re-exports submodules; pcc/__init__ does NOT import this
  results.py      CapabilityResult / SKIPPED_WITH_REASON / DistError taxonomy
  session.py      Rank, World, DeviceMesh, DRef, PCCDistSession
  transport.py    TransportSpec registry, probe/skip taxonomy, signed manifest
  tcp_transport.py explicit local/remote TCP-ring owner and frame codec
  multi_host.py    explicit strict two-host/rank/PSK configuration
  transport_collective.py five collective operations over the owned ring
  tensor_kv_execution.py bounded owned-CPU-tensor and KV transfer execution
  collective.py   allreduce/reduce_scatter/all_gather/broadcast/barrier oracle
  sharding.py     ParamSpec, ShardPlan, plan_ddp/plan_fsdp, schedule validation
  kv.py           KVBlockHandle, BlockManager (prefix hash / refcount / LRU)

tests/dist/
  test_session.py test_transport.py test_collective.py
  test_sharding.py test_kv.py
  test_tcp_transport_owner.py
  test_transport_collective_owner.py
  test_multi_host_config.py test_multi_mac_transport.py
  test_tensor_kv_execution.py
```

Each `pcc/dist/*.py` module is importable standalone (e.g.
`import pcc.dist.session`). Nothing in the wider `pcc` package imports
`pcc.dist`; it is opt-in.

Gates: `env -u LC_ALL uv run pytest tests/dist -q -n0` and the focused
`tests/dist/test_tcp_transport_owner.py` owner gate.

## What comes next (explicitly out of scope here)

- Execute the authenticated TCP owner on two physical Macs and retain the
  strict hardware evidence; then add `quic` and `jaccl-rdma`, each with its own
  strict hardware gate before any default capability flips from
  `SKIPPED_WITH_REASON`.
- TLS/mTLS cluster admission + certificate rotation replacing the toy HMAC
  signature oracle.
- Multi-Mac proof for the owned TCP collectives, then QUIC/RDMA providers.
- FSDP sharding over real (pcc-native or bridged) tensors, and a KV bridge to
  actual GPU cache blocks — each behind its own package/runtime gate.
```
