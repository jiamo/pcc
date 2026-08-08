from __future__ import annotations

from scripts.run_gc_longrun_gate import (
    DEFAULT_ROUNDS,
    GCLongrunGateError,
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
    assert manifest["mode"] == "strict-no-libpython-self-backend"
    assert len(manifest["source_sha256"]) == 64
    assert len(manifest["binary_sha256"]) == 64
    assert manifest["backends"] == [0, 1, 2, 3, 4]
    assert [result["backend"] for result in manifest["results"]] == [0, 1, 2, 3, 4]
    for result in manifest["results"]:
        summary = result["summary"]
        assert summary["completed_ops"] == 600 * 64
        assert summary["throughput_ops_per_sec"] > 0
        assert summary["rss_peak_bytes"] > 0
        assert summary["heap_capacity_bytes"] >= summary["heap_in_use_bytes"]
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
