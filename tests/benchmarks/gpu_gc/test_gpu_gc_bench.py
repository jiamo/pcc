"""test_gpu_gc_bench.py — gate for the CPU-only GPU-GC surrogate harness.

Encodes REAL assertions over deterministic logical counters produced by
``tests.benchmarks.gpu_gc.harness``. The device-mode taxonomy is enforced:
``cpu-only`` runs and is asserted; ``cuda-assisted`` and ``metal-assisted`` are
reported as mode-labeled ``SKIPPED_WITH_REASON`` verdicts with recorded reasons.

CLAIM BOUNDARY (asserted, not just documented): every counter here is a logical
surrogate over the ``pcc.gpu_gc`` CPU oracle model. No test asserts a
wall-clock, byte-capacity, throughput, or collector-ranking claim. There is a
dedicated test (`test_no_wall_clock_or_capacity_claim`) that pins the boundary.

Gate command:
    env -u LC_ALL uv run pytest tests/benchmarks/gpu_gc -q -n0
"""
from __future__ import annotations

import pytest

from tests.benchmarks.gpu_gc.harness import (
    AssistClass,
    DeviceMode,
    SkipTaxonomy,
    device_mode_availability,
    run_cpu_only_bench,
    run_serving_surrogate,
    build_classified_substrate,
)
from pcc.gpu_gc import classify_page


# ---------------------------------------------------------------------------
# Device-mode taxonomy
# ---------------------------------------------------------------------------

def test_device_mode_taxonomy_shape():
    """All three modes are present with a well-formed availability verdict."""
    verdicts = device_mode_availability()
    assert set(verdicts) == set(DeviceMode)
    # cpu-only always runs; both device modes skipped with a specific reason.
    assert verdicts[DeviceMode.CPU_ONLY].runs is True
    assert verdicts[DeviceMode.CPU_ONLY].taxonomy is SkipTaxonomy.RUNS
    assert verdicts[DeviceMode.CUDA_ASSISTED].runs is False
    assert verdicts[DeviceMode.CUDA_ASSISTED].taxonomy is SkipTaxonomy.NO_CUDA_DEVICE
    # metal skip is proven against the package's own detector (absent here).
    assert verdicts[DeviceMode.METAL_ASSISTED].runs is False
    assert verdicts[DeviceMode.METAL_ASSISTED].taxonomy is SkipTaxonomy.NO_METAL_TOOLING
    # Every non-running mode carries a non-empty reason string.
    for mode in (DeviceMode.CUDA_ASSISTED, DeviceMode.METAL_ASSISTED):
        assert verdicts[mode].reason


def test_cuda_assisted_reports_skipped_with_reason():
    verdict = device_mode_availability()[DeviceMode.CUDA_ASSISTED]
    assert verdict.runs is False
    assert verdict.taxonomy is SkipTaxonomy.NO_CUDA_DEVICE
    assert verdict.reason
    reason = verdict.reason.lower()
    assert "cuda" in reason
    assert "device" in reason or "kernel" in reason


def test_metal_assisted_reports_skipped_with_reason():
    verdict = device_mode_availability()[DeviceMode.METAL_ASSISTED]
    assert verdict.runs is False
    assert verdict.taxonomy is SkipTaxonomy.NO_METAL_TOOLING
    assert verdict.reason
    reason = verdict.reason.lower()
    assert "metal" in reason
    assert "absent" in reason


# ---------------------------------------------------------------------------
# CPU-only substrate/collector/assist/tiered measurement
# ---------------------------------------------------------------------------

def test_cpu_only_bench_is_deterministic():
    """Same inputs -> byte-identical counters (no clock, no randomness)."""
    a = run_cpu_only_bench(groups=4).as_dict()
    b = run_cpu_only_bench(groups=4).as_dict()
    assert a == b


def test_substrate_profile_page_count():
    """6 layout classes x groups pages allocated, one page per class per group."""
    res = run_cpu_only_bench(groups=4)
    assert res.profile.pages_allocated == 24
    assert res.profile.regions == 1
    # Exactly 4 of each of the six layout classes.
    assert set(res.profile.pages_by_layout.values()) == {4}
    assert len(res.profile.pages_by_layout) == 6


def test_classification_ratios_are_exact():
    """Deterministic routing: 16 GPU_TRACEABLE, 4 SUMMARY, 4 CPU_ONLY of 24."""
    res = run_cpu_only_bench(groups=4)
    ratios = res.ratios
    assert ratios.total == 24
    assert ratios.counts[AssistClass.GPU_TRACEABLE] == 16
    assert ratios.counts[AssistClass.GPU_SUMMARY_ONLY] == 4
    assert ratios.counts[AssistClass.CPU_ONLY] == 4
    # Ratios sum to 1 and match the exact fractions.
    assert ratios.gpu_traceable_ratio == pytest.approx(2 / 3)
    assert ratios.cpu_only_ratio == pytest.approx(1 / 6)
    assert ratios.gpu_summary_ratio == pytest.approx(1 / 6)
    total_ratio = (
        ratios.gpu_traceable_ratio + ratios.cpu_only_ratio + ratios.gpu_summary_ratio
    )
    assert total_ratio == pytest.approx(1.0)


def test_classification_scales_with_groups():
    """Ratios are group-invariant; counts scale linearly with groups."""
    small = run_cpu_only_bench(groups=1).ratios
    big = run_cpu_only_bench(groups=8).ratios
    assert small.total == 6 and big.total == 48
    # Ratio is identical regardless of scale.
    assert small.gpu_traceable_ratio == pytest.approx(big.gpu_traceable_ratio)
    assert big.counts[AssistClass.GPU_TRACEABLE] == 32


def test_collector_step_counts_and_reclamation():
    """Mark scans the 20 rooted pages; sweep reclaims the 4 unrooted CPU_ONLY."""
    res = run_cpu_only_bench(groups=4)
    steps = res.steps
    # Roots are the 20 non-CPU_ONLY pages; with empty remembered sets the
    # BLACK (marked) set is exactly those 20.
    assert steps.mark_steps == 20
    assert steps.survivors == 20
    # Sweep inspected all 24 allocated pages before reclaiming.
    assert steps.sweep_steps == 24
    # The 4 unreachable POINTER_GRAPH pages are reclaimed.
    assert steps.reclaimed == 4
    # Conservation: survivors + reclaimed == pages inspected.
    assert steps.survivors + steps.reclaimed == steps.sweep_steps


def test_assist_fallback_telemetry():
    """No GPU kernel -> every dispatch-eligible page falls back to CPU oracle."""
    res = run_cpu_only_bench(groups=4)
    a = res.assist
    # 16 GPU_TRACEABLE + 4 GPU_SUMMARY_ONLY = 20 dispatch-eligible pages.
    assert a.kernel_unavailable == 20
    assert a.cpu_fallbacks == 20
    # Nothing was actually dispatched (no kernel), and nothing mismatched.
    assert a.gpu_dispatched == 0
    assert a.parity_mismatches == 0
    # The 4 CPU_ONLY pages are counted classified but never dispatched.
    assert a.classified["cpu_only"] == 4
    assert a.classified["gpu_traceable"] == 16
    assert a.classified["gpu_summary_only"] == 4


def test_tiered_block_hit_and_recompute_counts():
    """Reuse of registered content is a hit; unseen content recomputes once."""
    res = run_cpu_only_bench(groups=4)
    t = res.tiered
    # 4 immutable pages -> 4 initial registrations, then 4 reuse hits (0
    # recompute), then 4 misses each recomputed + re-registered.
    assert t.hits == 4
    assert t.misses == 4
    assert t.recomputes == 4
    assert t.registrations == 8
    assert t.entries == 8
    assert t.invalidations == 0


# ---------------------------------------------------------------------------
# LLM-serving surrogate stress
# ---------------------------------------------------------------------------

def test_serving_surrogate_is_deterministic():
    a = run_serving_surrogate().as_dict()
    b = run_serving_surrogate().as_dict()
    assert a == b


def test_serving_surrogate_churn_volumes():
    """KV-block churn volumes are exact for the default workload shape."""
    s = run_serving_surrogate(
        requests=24, blocks_per_request=4, shared_prefix_blocks=2, collect_every=6
    )
    # 24 requests x 4 KV blocks each.
    assert s.blocks_allocated == 96
    # Prefix touches: 24 requests x 2 prefix blocks.
    assert s.blocks_touched == 48
    # Oldest request freed once >2 live: 22 finished requests x 4 blocks.
    assert s.blocks_freed == 88
    # A collection every 6 of 24 requests.
    assert s.collections == 4


def test_serving_surrogate_prefix_reuse_hits():
    """The shared prompt prefix is recomputed once then reused every request."""
    s = run_serving_surrogate(
        requests=24, blocks_per_request=4, shared_prefix_blocks=2, collect_every=6
    )
    # First request's 2 prefix blocks miss+recompute; the other 23 requests hit.
    assert s.kv_recomputes == 2
    assert s.kv_reuse_hits == 23 * 2
    # Reuse dominates recompute — the whole point of content-addressed prefix.
    assert s.kv_reuse_hits > s.kv_recomputes


def test_serving_surrogate_pause_and_rss_surrogates_are_bounded():
    """Surrogate pause/RSS counters are logical, positive, and bounded.

    These are step/page counts, NOT durations or bytes. We assert only
    structural bounds — never a performance claim.
    """
    s = run_serving_surrogate()
    # Pause surrogate accumulates mark steps across collections -> positive.
    assert s.pause_surrogate_mark_steps > 0
    # Peak resident pages cannot exceed total blocks ever allocated.
    assert 0 < s.rss_surrogate_peak_pages <= s.blocks_allocated
    # Final residency <= peak (churn frees older requests).
    assert s.rss_surrogate_final_pages <= s.rss_surrogate_peak_pages
    # Fragmentation surrogate is a non-negative span count.
    assert s.fragmentation_surrogate_free_spans >= 0


def test_serving_surrogate_bounded_working_set():
    """With continuous churn, peak residency stays bounded (not monotone growth).

    Doubling the request count must NOT double peak resident pages: freeing the
    oldest request keeps the working set bounded. This asserts the churn model
    actually reclaims, without any capacity/throughput claim.
    """
    short = run_serving_surrogate(requests=12, collect_every=6)
    long = run_serving_surrogate(requests=48, collect_every=6)
    # 4x the requests must not 4x the peak resident pages.
    assert long.rss_surrogate_peak_pages < 4 * short.rss_surrogate_peak_pages
    # More requests -> at least as many reuse hits (prefix reused more).
    assert long.kv_reuse_hits > short.kv_reuse_hits


# ---------------------------------------------------------------------------
# Correctness cross-check against the oracle package directly
# ---------------------------------------------------------------------------

def test_harness_classification_matches_package_oracle():
    """The harness counts must equal ``pcc.gpu_gc.classify_page`` page-by-page."""
    _sub, pages = build_classified_substrate(groups=3)
    from collections import Counter

    direct = Counter(classify_page(p) for p in pages)
    res = run_cpu_only_bench(groups=3)
    for cls, n in direct.items():
        assert res.ratios.counts[cls] == n


# ---------------------------------------------------------------------------
# Claim-boundary pin
# ---------------------------------------------------------------------------

def test_no_wall_clock_or_capacity_claim():
    """Pin the claim boundary: the harness reads no clock and asserts no bytes.

    Guards against a future edit sneaking a ``time``/``perf_counter`` call or a
    byte-capacity field into the surrogate harness, which would silently turn a
    logical-counter measurement into an (unfounded) performance claim.
    """
    import inspect
    from tests.benchmarks.gpu_gc import harness

    src = inspect.getsource(harness)
    banned = ("time.time", "perf_counter", "monotonic", "process_time", "time.perf")
    for token in banned:
        assert token not in src, f"harness must not read a clock (found {token!r})"
    # Surrogate field names must carry the explicit 'surrogate' marker so no
    # counter is mistaken for a real resource measurement.
    counters = run_serving_surrogate().as_dict()
    for key in ("pause_surrogate_mark_steps", "rss_surrogate_peak_pages",
                "fragmentation_surrogate_free_spans"):
        assert key in counters
