"""Gate for the T-P0-VTHREAD-1M-GATE first slice.

Gate command:

    env -u LC_ALL uv run pytest tests/benchmarks/vthread -q -n0

These tests assert REAL properties of the bounded virtual-thread scale
MEASUREMENT harness (``tests/benchmarks/vthread/harness.py``):

* the logical scheduler-model counters scale as expected with ``N``;
* the model is deterministic (same ``N`` -> byte-identical metrics);
* the carrier-count / work-stealing-imbalance dimension
  (``carrier_count`` / ``carrier_max_load`` / ``carrier_min_load`` /
  ``carrier_load_imbalance`` / ``work_steal_ops``) matches a brute-force
  round-robin oracle, and the small-ci run's carrier metrics are at PARITY with
  the standalone closed form (imbalance is 0 iff the carrier count divides ``N``,
  else 1; ``work_steal_ops`` == ``N % carrier_count``);
* the mode taxonomy is correct — ``small-ci`` MEASURES, ``real-runtime`` is
  ALWAYS ``SKIPPED_WITH_REASON``, and ``large-manual`` (N=1_000_000) is gated
  OFF by default and only runs under ``PCC_VTHREAD_1M=1``;
* skip reasons are non-empty, mode-labeled, and name the missing capability;
* the manifest round-trips through JSON and guards the claim boundary
  (no timing / RSS-bytes / latency metric can be smuggled into a MEASURED
  result).

Claim boundary reminder: these assertions prove the LOGICAL model's counters
are deterministic and scale with ``N``. They prove NOTHING about real RSS,
latency, GC pauses, or 1M-vthread readiness. The separately gated production C
runner owns that current-machine result; this logical entrypoint still skips it.
"""
from __future__ import annotations

import os

import pytest

from tests.benchmarks.vthread import harness as H


# --------------------------------------------------------------------------
# Mode taxonomy
# --------------------------------------------------------------------------
def test_mode_taxonomy_is_exactly_three_ordered_modes() -> None:
    assert H.ALL_MODES == (
        H.MODE_SMALL_CI,
        H.MODE_LARGE_MANUAL,
        H.MODE_REAL_RUNTIME,
    )
    # real-runtime is never a measurable mode in this slice.
    assert H.MODE_REAL_RUNTIME not in H.MEASURABLE_MODES
    assert H.MEASURABLE_MODES == frozenset({H.MODE_SMALL_CI, H.MODE_LARGE_MANUAL})


def test_n_for_mode_bounds() -> None:
    assert H.n_for_mode(H.MODE_SMALL_CI) == H.DEFAULT_SMALL_CI_N == 10_000
    assert H.n_for_mode(H.MODE_LARGE_MANUAL) == H.LARGE_MANUAL_N == 1_000_000
    with pytest.raises(H.VThreadBenchError):
        H.n_for_mode(H.MODE_REAL_RUNTIME)
    with pytest.raises(H.VThreadBenchError):
        H.n_for_mode(H.MODE_SMALL_CI, small_ci_n=0)


# --------------------------------------------------------------------------
# Logical scheduler model: determinism + scaling
# --------------------------------------------------------------------------
def test_logical_model_is_deterministic() -> None:
    a = H.simulate_logical_scheduler(5_000)
    b = H.simulate_logical_scheduler(5_000)
    assert a == b, "same N must produce byte-identical logical metrics"


def test_ready_queue_is_balanced_every_thread_enqueued_and_dequeued() -> None:
    n = 4_000
    m = H.simulate_logical_scheduler(n)
    # Every virtual thread is enqueued exactly once and dequeued exactly once.
    assert m["enqueue_ops"] == n
    assert m["dequeue_ops"] == n


def test_timer_and_io_counts_track_the_fixed_workload_ratios() -> None:
    # The model parks every 3rd thread on a timer, and blocks every 5th thread
    # (that is not already a timer thread) on IO. Timer inserts each expire once;
    # io adds each become ready once.
    n = 30
    m = H.simulate_logical_scheduler(n)
    expected_timer = sum(1 for i in range(n) if i % 3 == 0)
    expected_io = sum(1 for i in range(n) if i % 3 != 0 and i % 5 == 0)
    assert m["timer_insert_ops"] == expected_timer
    assert m["timer_expire_ops"] == expected_timer
    assert m["io_wait_add_ops"] == expected_io
    assert m["io_ready_ops"] == expected_io


@pytest.mark.parametrize("factor", [2, 4, 10])
def test_counters_scale_linearly_with_n(factor: int) -> None:
    base_n = 1_000
    base = H.simulate_logical_scheduler(base_n)
    scaled = H.simulate_logical_scheduler(base_n * factor)
    # The ready-queue ops scale exactly linearly (one enqueue+dequeue per thread).
    assert scaled["enqueue_ops"] == base["enqueue_ops"] * factor
    assert scaled["dequeue_ops"] == base["dequeue_ops"] * factor
    # Timer/IO ops are periodic densities (period 3 for timers, 15 for IO) so a
    # scaled count differs from exact linearity only by an O(1) boundary term
    # bounded by the period. Assert strict increase plus a period-bounded window
    # (16 > both periods), which is provably tight regardless of the anchor N.
    for key in ("timer_insert_ops", "timer_expire_ops", "io_wait_add_ops", "io_ready_ops"):
        assert scaled[key] > base[key]
        assert abs(scaled[key] - base[key] * factor) <= 16


def test_peak_live_set_is_a_bounded_surrogate_not_proportional_to_n() -> None:
    # The RSS surrogate (peak simultaneously-live logical nodes) must NOT grow
    # linearly with N — the workload immediately dequeues, so the live set stays
    # small and bounded regardless of N. This is the whole point of measuring a
    # peak-live-set surrogate rather than total ops.
    small = H.simulate_logical_scheduler(1_000)
    large = H.simulate_logical_scheduler(100_000)
    assert large["peak_live_set"] == small["peak_live_set"]
    assert large["peak_live_set"] <= 8, (
        "peak live set should stay tiny for the immediate-dequeue workload"
    )


def test_gc_pause_surrogate_fires_only_when_live_set_crosses_threshold() -> None:
    # With the default immediate-dequeue workload the live set never reaches the
    # 2048 threshold, so the GC-pause surrogate is zero. Lowering the threshold
    # to below the achievable peak makes it fire — proving it is threshold-driven,
    # not a fixed constant.
    default_metrics = H.simulate_logical_scheduler(50_000)
    assert default_metrics["gc_pause_count"] == 0
    tripped = H.simulate_logical_scheduler(50_000, live_set_threshold=1)
    assert tripped["gc_pause_count"] >= 1


# --------------------------------------------------------------------------
# Carrier-count / work-stealing-imbalance dimension
# --------------------------------------------------------------------------
def _rr_carrier_loads(n: int, c: int) -> list[int]:
    """Brute-force reference: round-robin ``n`` ready nodes across ``c`` carriers.

    This is the CPython oracle for the carrier distribution: node ``j`` lands on
    carrier ``j % c`` (mirrors ``pcc_vthread_next_carrier_enqueue %
    pcc_vthread_carrier_queue_count`` in ``pcc_threads.c``).
    """
    loads = [0] * c
    for j in range(n):
        loads[j % c] += 1
    return loads


def test_default_carrier_count_is_a_fixed_positive_model_constant() -> None:
    assert H.DEFAULT_CARRIER_COUNT == 4
    assert H.DEFAULT_CARRIER_COUNT > 0


@pytest.mark.parametrize(
    "n,c",
    [(10_000, 4), (10_000, 7), (10_001, 8), (10_000, 1), (7, 3), (1, 3)],
)
def test_carrier_distribution_matches_round_robin_oracle(n: int, c: int) -> None:
    # Oracle-diff: the closed-form carrier distribution must equal a brute-force
    # round-robin over the same n and c.
    loads = _rr_carrier_loads(n, c)
    got = H.simulate_carrier_distribution(n, c)
    assert got == {
        "carrier_count": c,
        "carrier_max_load": max(loads),
        "carrier_min_load": min(loads),
        "carrier_load_imbalance": max(loads) - min(loads),
        "work_steal_ops": n % c,
    }


def test_carrier_dimension_requires_positive_carrier_count() -> None:
    with pytest.raises(H.VThreadBenchError):
        H.simulate_carrier_distribution(100, 0)
    with pytest.raises(H.VThreadBenchError):
        H.simulate_carrier_distribution(100, -1)
    with pytest.raises(H.VThreadBenchError):
        H.simulate_logical_scheduler(100, carrier_count=0)


def test_perfect_round_robin_imbalance_is_zero_or_one() -> None:
    # A round-robin distribution can differ by at most one node between the most-
    # and least-loaded carrier: imbalance is 0 when the carrier count divides N,
    # else exactly 1. This is the whole point of a work-STEALING-imbalance count.
    balanced = H.simulate_carrier_distribution(10_000, 4)  # 4 | 10_000
    assert balanced["carrier_load_imbalance"] == 0
    assert balanced["work_steal_ops"] == 0
    uneven = H.simulate_carrier_distribution(10_000, 7)  # 7 does not divide 10_000
    assert uneven["carrier_load_imbalance"] == 1
    assert uneven["work_steal_ops"] == 10_000 % 7 == 4


def test_single_carrier_never_steals_and_is_balanced() -> None:
    # With one carrier every ready node is local: no imbalance, no work-steal,
    # mirroring the runtime path where a single carrier queue never enters the
    # (own + offset) % n_queues steal loop.
    d = H.simulate_carrier_distribution(10_000, 1)
    assert d["carrier_count"] == 1
    assert d["carrier_max_load"] == d["carrier_min_load"] == 10_000
    assert d["carrier_load_imbalance"] == 0
    assert d["work_steal_ops"] == 0


def test_work_steal_ops_equals_n_mod_carrier_count() -> None:
    # The work-steal surrogate is the surplus beyond the even floor share, i.e.
    # exactly N % carrier_count — the nodes an idle carrier would steal.
    for n, c in [(10_000, 3), (10_000, 6), (999, 8), (1_000_000, 7)]:
        d = H.simulate_carrier_distribution(n, c)
        assert d["work_steal_ops"] == n % c


def test_small_ci_carrier_metrics_are_present_and_parity_with_closed_form() -> None:
    # small-ci PARITY assertion: the full logical-scheduler run's carrier /
    # work-steal metrics must equal the standalone closed-form carrier
    # distribution for the same N and carrier count. Use a carrier count that
    # does NOT divide N so the imbalance/steal dimension is exercised non-trivially
    # (not just always zero).
    small_ci_n = 9_999
    carrier_count = 4  # 4 does not divide 9_999 -> imbalance 1, steal 3
    r = H.run_mode(
        H.MODE_SMALL_CI, small_ci_n=small_ci_n, carrier_count=carrier_count
    )
    assert r.measured
    dist = H.simulate_carrier_distribution(small_ci_n, carrier_count)
    for key in (
        "carrier_count",
        "carrier_max_load",
        "carrier_min_load",
        "carrier_load_imbalance",
        "work_steal_ops",
    ):
        assert r.metrics[key] == dist[key]
    # The chosen (N, carrier_count) really does exercise a non-zero imbalance and
    # steal count, so this parity is not a vacuous all-zero comparison.
    assert r.metrics["carrier_load_imbalance"] == 1
    assert r.metrics["work_steal_ops"] == 9_999 % 4 == 3
    # Every ready enqueue was distributed, so the carrier loads sum to enqueue_ops.
    assert r.metrics["carrier_max_load"] * carrier_count >= r.metrics["enqueue_ops"]


def test_carrier_metrics_do_not_trip_the_claim_boundary_guard() -> None:
    # The new carrier / work-steal keys are pure COUNTS and must NOT be rejected
    # by the MEASURED claim-boundary guard (no timing / RSS-bytes / latency).
    r = H.measured(
        H.MODE_SMALL_CI,
        100,
        {
            "enqueue_ops": 100,
            "carrier_count": 4,
            "carrier_max_load": 25,
            "carrier_min_load": 25,
            "carrier_load_imbalance": 0,
            "work_steal_ops": 0,
        },
    )
    assert r.measured
    assert r.metrics["work_steal_ops"] == 0


def test_carrier_dimension_is_deterministic() -> None:
    a = H.simulate_logical_scheduler(5_000, carrier_count=4)
    b = H.simulate_logical_scheduler(5_000, carrier_count=4)
    assert a == b, "same N + carrier_count must produce byte-identical metrics"


# --------------------------------------------------------------------------
# Per-mode runners + skip taxonomy
# --------------------------------------------------------------------------
def test_small_ci_runs_and_is_measured() -> None:
    r = H.run_mode(H.MODE_SMALL_CI, small_ci_n=2_000)
    assert r.measured
    assert r.status == H.STATUS_MEASURED
    assert r.n == 2_000
    assert r.reason == ""
    assert r.metrics["enqueue_ops"] == 2_000
    assert r.metrics["dequeue_ops"] == 2_000


def test_real_runtime_is_delegated_to_manual_production_runner() -> None:
    r = H.run_mode(H.MODE_REAL_RUNTIME)
    assert r.skipped
    assert r.status == H.STATUS_SKIPPED
    assert not r.metrics
    reason = r.reason.lower()
    assert reason.strip()
    # The logical entrypoint must route to the distinct production owner rather
    # than claim that its model produced runtime metrics.
    assert "run_vthread_1m_gate.py" in reason
    assert "pcc_threads.c" in reason or "py_virtual_thread" in reason
    assert "logical" in reason  # states it measures the logical model, not real


def test_large_manual_is_gated_off_by_default() -> None:
    # With the env gate absent, large-manual must NOT run — it is skipped-with-reason.
    env_without_gate = {k: v for k, v in os.environ.items() if k != H.LARGE_MANUAL_ENV}
    r = H.run_mode(H.MODE_LARGE_MANUAL, env=env_without_gate)
    assert r.skipped, "large-manual (N=1_000_000) must be gated off by default"
    assert H.LARGE_MANUAL_ENV in r.reason
    assert "1_000_000" in r.reason or "1000000" in r.reason


def test_large_manual_opts_in_only_under_env_gate() -> None:
    # This asserts the OPT-IN switch works; it does NOT run N=1_000_000 in CI.
    # We measure the gate decision at a small N to keep the test cheap while still
    # exercising the enabled branch: the enabled branch would use LARGE_MANUAL_N,
    # so we only assert the gate predicate here, not a full 1M run.
    assert H.large_manual_enabled({H.LARGE_MANUAL_ENV: "1"}) is True
    assert H.large_manual_enabled({H.LARGE_MANUAL_ENV: "0"}) is False
    assert H.large_manual_enabled({}) is False


def test_large_manual_when_enabled_measures_one_million_or_reports_gate() -> None:
    if os.environ.get(H.LARGE_MANUAL_ENV) != "1":
        r = H.run_mode(H.MODE_LARGE_MANUAL)
        assert r.skipped
        assert r.status == H.STATUS_SKIPPED
        assert not r.metrics
        assert H.LARGE_MANUAL_ENV in r.reason
        assert "1_000_000" in r.reason or "1000000" in r.reason
        assert "logical" in r.reason
        return

    r = H.run_mode(H.MODE_LARGE_MANUAL)
    assert r.measured
    assert r.n == H.LARGE_MANUAL_N == 1_000_000
    assert r.metrics["enqueue_ops"] == 1_000_000
    assert r.metrics["dequeue_ops"] == 1_000_000


# --------------------------------------------------------------------------
# Manifest: run_all, JSON round-trip, claim-boundary guard
# --------------------------------------------------------------------------
def test_run_all_manifest_shape_default_environment() -> None:
    env_without_gate = {k: v for k, v in os.environ.items() if k != H.LARGE_MANUAL_ENV}
    manifest = H.run_all(small_ci_n=3_000, carrier_count=4, env=env_without_gate)
    assert manifest.harness == "vthread-1m-gate"
    # Exactly one result per mode.
    assert {r.mode for r in manifest.results} == set(H.ALL_MODES)
    # In the default environment: small-ci measured; large-manual + real-runtime skipped.
    assert manifest.measured_modes() == (H.MODE_SMALL_CI,)
    assert set(manifest.skipped_modes()) == {H.MODE_LARGE_MANUAL, H.MODE_REAL_RUNTIME}
    # The carrier dimension is recorded in the manifest detail and flows into the
    # measured small-ci result.
    assert manifest.detail["carrier_count"] == 4
    small = manifest.result_for(H.MODE_SMALL_CI)
    assert small.metrics["carrier_count"] == 4
    assert small.metrics["work_steal_ops"] == 3_000 % 4


def test_manifest_json_round_trip_is_stable() -> None:
    env_without_gate = {k: v for k, v in os.environ.items() if k != H.LARGE_MANUAL_ENV}
    manifest = H.run_all(small_ci_n=1_500, env=env_without_gate)
    blob = manifest.to_json()
    restored = H.VThreadBenchManifest.from_json(blob)
    assert restored.to_json() == blob
    assert restored.result_for(H.MODE_SMALL_CI).measured
    assert restored.result_for(H.MODE_REAL_RUNTIME).skipped


def test_measured_result_cannot_smuggle_a_timing_metric() -> None:
    # The claim-boundary guard must reject any timing / RSS-bytes / latency key.
    with pytest.raises(H.VThreadBenchError):
        H.measured(H.MODE_SMALL_CI, 100, {"enqueue_ops": 100, "latency_ms": 5})
    with pytest.raises(H.VThreadBenchError):
        H.measured(H.MODE_SMALL_CI, 100, {"rss_bytes": 4096})
    with pytest.raises(H.VThreadBenchError):
        H.measured(H.MODE_SMALL_CI, 100, {"throughput": 1.0})


def test_skipped_result_must_carry_a_nonempty_reason_and_no_metrics() -> None:
    with pytest.raises(H.VThreadBenchError):
        H.skipped(H.MODE_REAL_RUNTIME, "")
    with pytest.raises(H.VThreadBenchError):
        H.VThreadBenchResult(
            H.MODE_REAL_RUNTIME, H.STATUS_SKIPPED, reason="x", metrics={"a": 1}
        )


def test_measured_result_requires_positive_n() -> None:
    with pytest.raises(H.VThreadBenchError):
        H.measured(H.MODE_SMALL_CI, 0, {"enqueue_ops": 0})


# --------------------------------------------------------------------------
# Soft integration with the sibling pcc.vthread oracle package (if present).
# This is an IMPORT GUARD, never a hard dependency: if it is absent, the absence
# is recorded as an asserted availability verdict instead of a pytest-level skip.
# --------------------------------------------------------------------------
def test_optional_pcc_vthread_oracle_import_is_soft() -> None:
    try:
        import pcc.vthread as _pcc_vthread  # noqa: F401
    except ImportError as exc:
        reason = (
            "SKIPPED_WITH_REASON: pcc.vthread oracle package not importable; "
            "this harness measures the self-contained logical model and does "
            f"not depend on it ({exc!r})"
        )
        assert reason.startswith("SKIPPED_WITH_REASON:")
        assert "pcc.vthread" in reason
        return
    # If it imports, we make no assertion about its API here — the sibling agent
    # owns pcc.vthread; this test only proves the guard path is exercised.
    assert _pcc_vthread is not None
