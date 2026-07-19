# pcc GPU-participating GC — measurement harness (CPU-only surrogate)

Status: **first slice — CPU-only surrogate measurement.** This document
specifies the benchmark/measurement harness for the `pcc.gpu_gc` CPU-only GC
oracle, its device-mode taxonomy, and — most importantly — the **hard claim
boundary** that keeps this a *measurement target*, not a claim of a finished
GPU/Metal collector.

* Harness package: `tests/benchmarks/gpu_gc/`
* Gate command: `env -u LC_ALL uv run pytest tests/benchmarks/gpu_gc -q -n0`
* Measures: the oracle in `pcc/gpu_gc/` (`substrate`, `cpu_collector`,
  `assist`, `metal_adapter`, `tiered`).
* Design of the oracle it measures: `docs/design/pcc-gpu-gc.md`.
* Research source: `tmp_research/deep-research-gpu-gc.md`.

This harness is deliberately separate from the existing long-running GC
benchmarks (`tests/python/test_boc_benchmarks.py`, the gc-longrun harness): it
measures a **CPU-only surrogate model**, not the five production GC backends,
and it reports **logical counters**, never wall-clock / RSS / throughput.

---

## 1. What this harness measures (and what a "measurement" means here)

The research survey concluded that a modern GPU-participating collector is a
**partitioned, mostly-non-moving, concurrent collector** with a **CPU-owned
control plane** and **GPU-assisted data-plane kernels** over stable, blockized
metadata. The `pcc.gpu_gc` package is the CPU control plane + its correctness
oracle. **This harness measures the *logical behavior* of that oracle model.**

"Measurement" here means **deterministic logical counters**, not resource
measurements:

| Counter family | Source module | What it counts | What it is NOT |
|---|---|---|---|
| mark / sweep step counts | `cpu_collector` | pages scanned to BLACK (mark), ALLOCATED pages inspected (sweep), pages reclaimed | not a pause duration |
| classification ratios | `assist.classify_page` | pages routed GPU_TRACEABLE / GPU_SUMMARY_ONLY / CPU_ONLY | not a GPU-speedup ratio |
| assist fallback telemetry | `assist.AssistOracle` | gpu_dispatched / cpu_fallbacks / kernel_unavailable / parity_mismatches | not a real kernel result |
| tiered hit / recompute | `tiered.BlockDirectory` | content-addressed reuse hits vs recomputes | not a cache-capacity claim |
| serving surrogate pause/RSS/frag | harness over `substrate`+`tiered` | mark-step sum, peak ALLOCATED page count, free-span count | not seconds, not bytes, not fragmentation-% |

Every serving-surrogate resource-shaped counter carries an explicit
`_surrogate` marker in its field name (`pause_surrogate_mark_steps`,
`rss_surrogate_peak_pages`, `fragmentation_surrogate_free_spans`) precisely so
no reader can mistake a step/page/span count for a measured resource. A
dedicated test (`test_no_wall_clock_or_capacity_claim`) statically pins that the
harness reads no clock (`time.time`/`perf_counter`/`monotonic`/`process_time`).

---

## 2. Device-mode taxonomy

The harness reasons about three device modes. Exactly one runs; the other two
are structurally skipped with a recorded, testable reason — "we did not measure
a device" is an explicit outcome, never a silent omission.

| Mode | Verdict | Taxonomy | Reason |
|---|---|---|---|
| `cpu-only` | **RUNS** | `RUNS` | CPU surrogate; the substrate / cpu_collector / assist / tiered oracles are measured as logical counters |
| `cuda-assisted` | **SKIPPED_WITH_REASON** | `NO_CUDA_DEVICE` | no CUDA device or kernel exists in this slice; nothing to measure |
| `metal-assisted` | **SKIPPED_WITH_REASON** | `NO_METAL_TOOLING` | `pcc.gpu_gc.MetalResidencyAdapter` reports Metal/MLX tooling absent |

The Metal skip is **proven, not asserted**: `device_mode_availability()` asks
the package's own `MetalResidencyAdapter().available` — the same detector the
oracle uses — so the harness cannot drift from the package's own honest answer
about device presence. If a future environment actually has Metal tooling, the
`metal-assisted` mode's `runs` flips to `True` automatically and the skip test's
guard (`pytest.fail` if it claims to run without measurement) forces the harness
to grow a real measurement rather than silently pass.

The `cuda-assisted` mode is unconditionally skipped in this slice: there is no
CUDA device, driver, or kernel anywhere in the repo, and inventing a "CUDA
present" branch would be a fake-device claim.

---

## 3. Harness module / class map (`tests/benchmarks/gpu_gc/`)

| Symbol | Kind | Role |
|---|---|---|
| `DeviceMode` | enum | `CPU_ONLY` / `CUDA_ASSISTED` / `METAL_ASSISTED` |
| `SkipTaxonomy` | enum | `RUNS` / `NO_CUDA_DEVICE` / `NO_METAL_TOOLING` |
| `ModeAvailability` | dataclass | per-mode `{runs, taxonomy, reason}` verdict |
| `device_mode_availability()` | fn | verdict map for all three modes (defers Metal to the package detector) |
| `SubstrateProfile` | dataclass | static shape: regions, pages, per-layout histogram |
| `CollectorStepCounts` | dataclass | mark_steps / sweep_steps / reclaimed / survivors |
| `ClassificationRatios` | dataclass | per-`AssistClass` counts + exact ratios |
| `AssistOracleReport` | dataclass | dispatch / fallback / kernel-unavailable / parity telemetry |
| `TieredReport` | dataclass | content-addressed hit / miss / recompute / registration counts |
| `ServingSurrogateCounters` | dataclass | LLM-serving surrogate pause/RSS/frag/reuse counters |
| `CpuOnlyBenchResult` | dataclass | bundle of the five reports above |
| `build_classified_substrate(groups)` | fn | deterministic substrate: one page per layout class per group |
| `run_cpu_only_bench(groups)` | fn | the cpu-only measurement (profile + steps + ratios + assist + tiered) |
| `run_serving_surrogate(...)` | fn | KV/attention-block churn stress over `tiered` + `substrate` |

Test module `test_gpu_gc_bench.py` encodes the real assertions and the skip
taxonomy. Expected outcome of the gate: **15 passed, 2 skipped** (the two device
modes).

### 3.1 The cpu-only measurement

`build_classified_substrate(groups=G)` allocates `6*G` pages in a non-moving
OLD region — one page of each `LayoutClass` per group — so the classification
ratio is exactly known and both GPU-traceable and CPU-only pages exist. For
`G=4` (default):

* 24 pages: 16 `GPU_TRACEABLE` (flat_array/object_vector/raw_payload/immutable),
  4 `GPU_SUMMARY_ONLY` (pointer_table), 4 `CPU_ONLY` (pointer_graph)
  → ratios `2/3`, `1/6`, `1/6` (sum to 1, asserted).
* Collector cycle with the 20 non-CPU_ONLY pages as roots → `mark_steps=20`,
  `sweep_steps=24`, `reclaimed=4` (the unreachable pointer graphs),
  `survivors=20`; conservation `survivors + reclaimed == sweep_steps` asserted.
* Assist with **no** GPU kernel → all 20 dispatch-eligible pages record
  `kernel_unavailable` + `cpu_fallbacks`, `gpu_dispatched=0`,
  `parity_mismatches=0` (the deterministic fallback path).
* Tiered over the 4 immutable pages → 4 registrations, 4 reuse hits (no
  recompute), 4 forced misses each recomputed once → `hits=4, misses=4,
  recomputes=4, registrations=8`.

### 3.2 The LLM-serving surrogate stress

`run_serving_surrogate(requests, blocks_per_request, shared_prefix_blocks,
collect_every)` models vLLM-style KV-cache pressure over `substrate` + `tiered`:

* each request allocates `blocks_per_request` short-lived KV attention pages in
  a NURSERY region and touches a shared, content-addressed prompt *prefix* of
  `shared_prefix_blocks` immutable blocks in the `BlockDirectory` (vLLM
  prefix-cache discipline: register once, hit thereafter);
* the oldest live request is finished (its KV pages freed) once more than two
  are live — a bounded-working-set churn model;
* every `collect_every` requests a collector cycle runs, accumulating its mark
  steps into the pause surrogate.

For the default shape (24 requests × 4 blocks, prefix 2, collect every 6):
`blocks_allocated=96`, `blocks_touched=48`, `blocks_freed=88`, `collections=4`,
`kv_recomputes=2` (first request only), `kv_reuse_hits=46` (23 later requests ×
2 prefix blocks). The `test_serving_surrogate_bounded_working_set` assertion
proves 4× the requests does **not** 4× the peak resident page count — i.e. the
churn model actually reclaims — **without** any capacity or throughput claim.

---

## 4. Hard claim boundary (the load-bearing section)

> **This harness is a measurement target only. It does NOT claim a completed,
> correct, or performant GPU/Metal garbage collector.**

Concretely, this slice **does** establish:

* the `pcc.gpu_gc` CPU oracle model produces **deterministic** logical counters;
* page classification ratios are **exact and auditable** under the deterministic
  `assist` policy, and match the package's `classify_page` page-by-page;
* the assist fallback path is exercised and its telemetry is correct;
* content-addressed tiered reuse hits vs recomputes are correct;
* the device-mode taxonomy skips CUDA/Metal **with a recorded, proven reason**.

This slice explicitly does **NOT** establish, and no test asserts:

* any **wall-clock**, latency, or pause-*time* result (only step counts);
* any **memory-capacity**, RSS-in-bytes, or fragmentation-percent result (only
  page/span counts, all `_surrogate`-suffixed);
* any **throughput** number or **collector ranking** (this measures one CPU
  oracle model; it does not compare collectors and does not rank the five
  production `PCC_GC_KIND_*` backends);
* any **GPU / CUDA / Metal / MLX** device measurement — no kernel is launched,
  no device memory is allocated, no driver is touched;
* any **vLLM / vLLM-Metal / Mooncake** interop — the serving workload is a
  *surrogate* built only from `pcc.gpu_gc` primitives, borrowing vocabulary
  (block identity, prefix reuse, touch/free/evict), not integrating a real
  serving engine.

One-line claim boundary (quotable):

> **CPU-only surrogate measurement of the `pcc.gpu_gc` GC-oracle model —
> deterministic logical counters (mark/sweep steps, classification ratios,
> fallback telemetry, tiered reuse) and device-mode skip taxonomy — NOT a
> throughput, capacity, pause-time, or collector-completion claim, and NOT a
> ranking of the five production GC backends.**

---

## 5. Risk notes

1. **Surrogate-vs-real confusion.** The single biggest risk is a future reader
   (or agent) treating `pause_surrogate_mark_steps` / `rss_surrogate_peak_pages`
   as a real pause time / RSS. Mitigations in place: the `_surrogate` suffix on
   every resource-shaped field, the explicit §4 boundary, and
   `test_no_wall_clock_or_capacity_claim` which statically fails if a clock read
   is added to the harness. **Do not remove the `_surrogate` suffix or that
   test.**
2. **Determinism drift.** All counters are hand-derived and asserted exactly.
   If a future change to `pcc.gpu_gc` alters routing, refcount, or reuse
   semantics, the exact-count assertions here will fail *loudly* — that is
   intended (the harness is a regression gate on the oracle's logical
   behavior), but whoever changes the oracle must update the derivations here
   and in §3, not weaken the assertions to ranges.
3. **Metal-present environment.** If run where Metal/MLX tooling is detectable,
   `metal-assisted` will report `runs=True` and the skip test's guard will
   `pytest.fail` (it refuses to silently pass a mode that claims to run but has
   no measurement). That is deliberate: it forces a real Metal measurement to be
   written rather than faked. Same design for a hypothetical CUDA environment.
4. **Coupling to the oracle's private state.** `TieredReport.entries` reads
   `BlockDirectory._entries` and the collector step counter reads `color_of`;
   these are introspection into `pcc.gpu_gc` internals. If the oracle's internal
   layout changes, these two touch-points must be updated. They are localized in
   `harness.py` (`_measure_tiered`, `_measure_collector_steps`).
5. **Scope creep into the production GC bench.** This harness must stay disjoint
   from `tests/python/test_boc_benchmarks.py` / the gc-longrun harness and from
   the `PCC_GC_KIND_*` backends. It measures a CPU model, not the shipped
   runtime; merging the two would blur the claim boundary in §4.
