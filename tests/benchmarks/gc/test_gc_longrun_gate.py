from __future__ import annotations

from pathlib import Path

import pytest

import scripts.run_gc_longrun_gate as longrun_gate
from scripts.run_gc_longrun_gate import (
    DEFAULT_ROUNDS,
    GCLongrunGateError,
    SCHEMA_VERSION,
    evaluate_resource_budget,
    manual_gate_enabled,
    run_gate,
)


GC4_RETAINED_ZPAGE_GAP_LIMIT = 504_992


def test_production_rounds_require_explicit_manual_gate(monkeypatch):
    monkeypatch.delenv("PCC_GC_LONGRUN", raising=False)
    assert not manual_gate_enabled()
    try:
        run_gate(
            rounds=DEFAULT_ROUNDS,
            backends=(0,),
            build_timeout=1,
            backend_timeout=1,
        )
    except GCLongrunGateError as exc:
        assert "manual-only" in str(exc)
    else:
        raise AssertionError("production longrun gate ran without opt-in")


def test_small_source_bound_gc0_to_gc4_manifest(tmp_path, monkeypatch):
    monkeypatch.setenv("PCC_GC_LONGRUN_CACHE", str(tmp_path / "cache"))
    manifest = run_gate(
        rounds=600,
        backends=(0, 1, 2, 3, 4),
        build_timeout=180,
        backend_timeout=60,
    )
    assert manifest["schema_version"] == SCHEMA_VERSION == 2
    assert manifest["mode"] == "strict-no-libpython-self-backend"
    assert len(manifest["source_sha256"]) == 64
    assert len(manifest["binary_sha256"]) == 64
    assert manifest["backends"] == [0, 1, 2, 3, 4]
    assert manifest["repeats"] == 1
    assert [result["backend"] for result in manifest["results"]] == [0, 1, 2, 3, 4]
    assert [row["backend"] for row in manifest["aggregates"]] == [0, 1, 2, 3, 4]
    for result in manifest["results"]:
        summary = result["summary"]
        assert result["repeat_index"] == 1
        assert summary["completed_ops"] == 600 * 64
        assert summary["throughput_ops_per_sec"] > 0
        assert summary["rss_peak_bytes"] > 0
        assert summary["heap_capacity_bytes"] >= summary["heap_in_use_bytes"]
        assert summary["process_user_cpu_us"] >= 0
        assert summary["process_system_cpu_us"] >= 0
        assert summary["process_total_cpu_us"] == (
            summary["process_user_cpu_us"] + summary["process_system_cpu_us"]
        )
        assert summary["cpu_us_per_million_ops"] >= 0
        assert sum(summary["pause_histogram"].values()) == summary["pause_count"]
        assert result["samples_total"] == 3
        assert result["samples_persisted"] == 3
        assert len(result["samples"]) == 3

    # Keep the production row's byte-pinned GC4 bound in the source-bound
    # churn smoke as well as the manual 100k-round manifests.  This is the
    # unmodified public span-minus-live metric: the final live dict and its
    # registered entries payload remain counted.
    gc4 = next(result for result in manifest["results"] if result["backend"] == 4)
    assert (
        gc4["summary"]["zpage_retained_gap_bytes"]
        <= GC4_RETAINED_ZPAGE_GAP_LIMIT
    )


def test_resource_budget_reports_the_exact_backend_and_metric() -> None:
    healthy = {
        "backend": 0,
        "repeats": 3,
        "median_throughput_ops_per_sec": 300_000,
        "median_cpu_us_per_million_ops": 4_000_000,
        "max_peak_rss_bytes": 9_000_000,
        "max_positive_steady_rss_drift_bytes_per_million_ops": 0,
        "max_pause_us": 9_000,
        "max_allocator_fragmentation_bytes": 1_000_000,
        "max_zpage_retained_gap_bytes": 0,
    }
    assert evaluate_resource_budget([healthy])["status"] == "PASS"

    over_cpu = dict(healthy)
    over_cpu["backend"] = 3
    over_cpu["median_cpu_us_per_million_ops"] = 5_000_001
    verdict = evaluate_resource_budget([healthy, over_cpu])
    assert verdict["status"] == "FAIL"
    assert verdict["violations"] == [
        "gc3:median_cpu_us_per_million_ops=5000001"
    ]


def test_resource_budget_keeps_the_gc4_retained_gap_byte_pinned() -> None:
    gc4 = {
        "backend": 4,
        "repeats": 3,
        "median_throughput_ops_per_sec": 300_000,
        "median_cpu_us_per_million_ops": 4_000_000,
        "max_peak_rss_bytes": 9_000_000,
        "max_positive_steady_rss_drift_bytes_per_million_ops": 0,
        "max_pause_us": 9_000,
        "max_allocator_fragmentation_bytes": 1_000_000,
        "max_zpage_retained_gap_bytes": GC4_RETAINED_ZPAGE_GAP_LIMIT + 1,
    }
    verdict = evaluate_resource_budget([gc4])
    assert verdict["status"] == "FAIL"
    assert verdict["violations"] == [
        "gc4:max_zpage_retained_gap_bytes=504993"
    ]


def test_run_gate_executes_alternating_repeats_and_aggregates(monkeypatch) -> None:
    calls: list[tuple[int, int]] = []

    monkeypatch.setattr(
        longrun_gate,
        "build_workload",
        lambda **_kwargs: (Path("/tmp/fake-gc-longrun"), "a" * 64, "b" * 64),
    )

    def fake_run_backend(
        _executable,
        *,
        backend,
        rounds,
        timeout,
        repeat_index,
    ):
        del rounds, timeout
        calls.append((repeat_index, backend))
        return {
            "backend": backend,
            "repeat_index": repeat_index,
            "summary": {
                "throughput_ops_per_sec": 300_000 + repeat_index,
                "cpu_us_per_million_ops": 4_000_000 + repeat_index,
                "rss_peak_bytes": 9_000_000,
                "steady_rss_drift_bytes_per_million_ops": 0,
                "pause_max_us": 9_000,
                "allocator_fragmentation_bytes": 1_000_000,
                "zpage_retained_gap_bytes": 0,
            },
            "samples_total": 3,
            "samples_persisted": 3,
            "samples": [],
        }

    monkeypatch.setattr(longrun_gate, "run_backend", fake_run_backend)
    manifest = run_gate(
        rounds=600,
        backends=(0, 2),
        build_timeout=1,
        backend_timeout=1,
        repeats=3,
    )

    assert calls == [(1, 0), (1, 2), (2, 0), (2, 2), (3, 0), (3, 2)]
    assert manifest["repeats"] == 3
    assert [row["repeats"] for row in manifest["aggregates"]] == [3, 3]
    assert manifest["resource_budget"]["status"] == "PASS"


@pytest.mark.parametrize("repeats", [0, 11])
def test_run_gate_rejects_unbounded_repeat_counts(monkeypatch, repeats) -> None:
    monkeypatch.setattr(
        longrun_gate,
        "build_workload",
        lambda **_kwargs: pytest.fail("invalid repeats reached the build"),
    )
    with pytest.raises(GCLongrunGateError, match="repeats"):
        run_gate(
            rounds=600,
            backends=(0,),
            build_timeout=1,
            backend_timeout=1,
            repeats=repeats,
        )
