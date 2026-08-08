from __future__ import annotations

from benchmarks.run_freestanding_allocator import (
    AllocatorBenchmarkError,
    parse_result_line,
    run_gate,
)


def test_parse_result_line_rejects_incomplete_or_wrong_mode():
    try:
        parse_result_line("host,1,2", expected_mode="host")
    except AllocatorBenchmarkError as exc:
        assert "fields" in str(exc)
    else:
        raise AssertionError("incomplete allocator benchmark row was accepted")

    valid = "pcc,4096,1000,4096000000,1048576,65536,0,0,123"
    try:
        parse_result_line(valid, expected_mode="host")
    except AllocatorBenchmarkError as exc:
        assert "mode" in str(exc)
    else:
        raise AssertionError("wrong allocator benchmark mode was accepted")


def test_small_source_bound_host_vs_pcc_manifest(tmp_path):
    manifest = run_gate(rounds=4096, repeats=1, cache_root=tmp_path / "cache")

    assert manifest["schema_version"] == 1
    assert manifest["mode"] == "host-vs-self-freestanding-allocator"
    assert len(manifest["source_sha256"]) == 64
    assert manifest["rounds"] == 4096
    assert manifest["repeats"] == 1
    assert [result["allocator"] for result in manifest["results"]] == [
        "host",
        "pcc",
    ]

    for result in manifest["results"]:
        summary = result["summary"]
        assert summary["throughput_ops_per_sec_median"] > 0
        assert summary["peak_rss_bytes_median"] > 0
        assert summary["retained_capacity_bytes_median"] >= 0
        assert len(result["samples"]) == 1

    pcc = manifest["results"][1]["summary"]
    assert pcc["live_requested_delta_median"] == 0
    assert pcc["live_usable_delta_median"] == 0
    assert pcc["retained_capacity_bytes_median"] > 0
    assert manifest["deltas"]["throughput_pcc_minus_host_ops_per_sec"] != 0
    assert "No speed or footprint ranking" in manifest["claim_boundary"]
