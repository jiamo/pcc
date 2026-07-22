# pcc-dist-bench — metadata-only distributed measurement harness (P-P0-DIST-BENCH, first slice)

This document specifies the first slice of the distributed-benchmark harness
that sits **over** the existing `pcc.dist` metadata oracles (`collective`, `kv`,
`transport`, `session`, `sharding`, `results`). It defines the **mode taxonomy**
and, most importantly, the **hard claim boundary**: what a result from this
harness may and may not assert.

The harness itself lives in `tests/benchmarks/dist/` (it is a test-tree module,
not part of the shipped `pcc.dist` package):

| File | Role |
|---|---|
| `tests/benchmarks/dist/bench_model.py` | `BenchMode` taxonomy constants, skip-reason vocabulary, `BenchResult`, `BenchManifest` (JSON round-trip) |
| `tests/benchmarks/dist/bench_runner.py` | Runner: measures `single-process`; resolves every other mode to `SKIPPED_WITH_REASON` |
| `tests/benchmarks/dist/test_bench_harness.py` | Real assertions (determinism, skip reasons, manifest round-trip, claim-boundary guards) |

## Gate command

```bash
env -u LC_ALL uv run pytest tests/benchmarks/dist -q -n0
```

## Mode taxonomy

Eight modes. Exactly **one** produces measurements in this slice; every other
mode is reported as `SKIPPED_WITH_REASON` with the exact missing capability.

| Mode | Status this slice | What it would need to become MEASURED |
|---|---|---|
| `single-process` | **MEASURED** (logical counts) | — (measured now: CPU, one process, no sockets) |
| `local-process` | SKIPPED_WITH_REASON | worker-process fan-out + IPC channel |
| `localhost-tcp-ring` | SKIPPED_WITH_REASON | real TCP sockets + a landed `pcc.dist.transport` tcp-ring |
| `multi-mac-tcp-ring` | SKIPPED_WITH_REASON | Bonjour/Network.framework discovery + cross-host sockets + an exact topology + security-mode label |
| `quic` | SKIPPED_WITH_REASON | QUIC/Network.framework transport |
| `jaccl-rdma` | SKIPPED_WITH_REASON | Thunderbolt-RDMA backend, fully-connected topology, macOS Recovery RDMA enablement |
| `minimind-train-smoke` | SKIPPED_WITH_REASON | PyTorch/MLX/pcc-native tensor training + multi-worker transport |
| `vllm-kv-surrogate` | SKIPPED_WITH_REASON | GPU + a vLLM engine + real KV cache memory |

### What `single-process` actually measures (logical, latency-free)

Two workloads, both derived from the oracle's own semantics — **op / logical-step
counts only, no wall clock**:

- **collective oracle** (`pcc.dist.collective`): allreduce + all-gather over
  fixed POD buffers (4 fake ranks × length-4). Reported metrics:
  `allreduce_reduce_ops = (world-1)*len`, `all_gather_copy_elems = world*len`,
  and a `result_digest` fingerprinting the actual reduced values so a
  determinism regression is visible.
- **KV surrogate** (`pcc.dist.kv.BlockManager`): two token sequences with a
  shared prefix. Reported metrics: blocks per sequence, `unique_blocks_created`,
  `prefix_cache_hits`, `releases`, `evictions`. These are bookkeeping counts,
  not cache latency or serving throughput.

The runner cross-checks each skipped networking mode against the underlying
`pcc.dist.transport` probe: if a transport that is currently unavailable ever
lands as `AVAILABLE`, the stale skip becomes a **hard error** rather than a
silently-wrong claim.

## The hard claim boundary

> A `MEASURED` result from this harness is a **mode-labeled logical count**
> (collective element-ops, KV block/logical-step counts) produced in **one
> process, on CPU, with no sockets**. It is **not** a latency, **not** a
> throughput, **not** a scaling result, and it proves **nothing** about
> multi-process, multi-Mac, localhost/localhost-ring, QUIC, JACCL-RDMA, secure
> cluster admission, MiniMind training, or vLLM serving. Any such claim requires
> a **real hardware run carrying an exact topology label and an exact
> security-mode label** — which this slice does not have and therefore reports
> as `SKIPPED_WITH_REASON`.

This boundary is enforced in code, not just prose:

- `BenchResult.__post_init__` **rejects** any timing/throughput key
  (`latency`, `seconds`, `ms`, `throughput`, `ops_per_sec`, `tokens_per_sec`,
  `wall_clock`, `speedup`, `gbps`, …) on a `MEASURED` result.
- A `SKIPPED_WITH_REASON` result must carry a **non-empty, mode-labeled** reason
  and **no** metrics.
- `single-process` is the only member of `MEASURABLE_MODES`; every other mode
  has a mandatory entry in `SKIP_REASONS` naming its missing capability.

### Explicitly out of scope for this slice (no claim may be emitted)

throughput · scaling curves · cluster size · security/admission (TLS/mTLS) ·
MiniMind training results · vLLM serving results · multi-Mac execution ·
any speedup-vs-baseline number.

## Provenance

The taxonomy is distilled from `docs/refs_docs/deep-research/deep-research-distribute.md`
(MLX's Apple-Silicon distributed reality: RING/TCP first, QUIC later,
JACCL/Thunderbolt-RDMA requiring fully-connected topology + Recovery
enablement; vLLM paged-KV block management as an upper-layer constraint). It
reuses the `SKIPPED_WITH_REASON` discipline already established in
`pcc/dist/results.py` and the transport/session skip surfaces, so the bench
layer never invents a capability the oracle would not itself report.
