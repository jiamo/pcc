"""P-P0-DIST-BENCH: assertions for the metadata-only distributed bench harness.

Gate command:

    env -u LC_ALL uv run pytest tests/benchmarks/dist -q -n0

These tests pin the claim boundary in code:

    * single-process is the ONLY measured mode; every networking / hardware /
      workload-placeholder mode is SKIPPED_WITH_REASON with a mode-labeled,
      non-empty reason naming the exact missing capability;
    * measured metrics are deterministic (identical across runs) and are
      logical COUNTS only — a wall-clock / throughput metric is rejected by the
      model;
    * the manifest round-trips through JSON byte-stably;
    * the bench skip taxonomy agrees with the underlying pcc.dist oracle skip
      surface (no stale "unavailable" claim if a transport ever lands).
"""
import pytest

from pcc.dist import session, transport

from . import bench_model as bm
from . import bench_runner as R


# --- taxonomy shape --------------------------------------------------------
def test_taxonomy_has_all_required_modes():
    required = {
        "single-process", "local-process", "localhost-tcp-ring",
        "multi-mac-tcp-ring", "quic", "jaccl-rdma",
        "minimind-train-smoke", "vllm-kv-surrogate",
    }
    assert set(bm.ALL_MODES) == required
    # single-process is the only measurable mode in this slice.
    assert bm.MEASURABLE_MODES == frozenset({"single-process"})


def test_every_non_measurable_mode_has_a_nonempty_skip_reason():
    for mode in bm.ALL_MODES:
        if bm.is_measurable(mode):
            continue
        reason = bm.skip_reason_for(mode)
        assert reason.strip(), f"mode {mode} has an empty skip reason"
        # The reason must be mode-labeled (names the mode) so a skip is auditable.
        assert mode.split("-")[0] in reason or mode in reason


# --- single-process measurement -------------------------------------------
def test_single_process_is_measured_with_logical_counts():
    res = R.run_single_process()
    assert res.measured
    assert res.mode == "single-process"
    assert res.reason == ""
    coll = res.metrics["collective"]
    kv = res.metrics["kv"]
    # Collective: 4 ranks x len-4 buffers -> (4-1)*4 reduce ops, 4*4 gather elems.
    assert coll["world_size"] == 4
    assert coll["allreduce_reduce_ops"] == 12
    assert coll["all_gather_copy_elems"] == 16
    assert coll["allreduce_status"] == "completed"
    # KV: seq_a=3 blocks, seq_b shares first 2 -> 4 unique, 2 prefix hits.
    assert kv["seq_a_blocks"] == 3
    assert kv["seq_b_blocks"] == 3
    assert kv["unique_blocks_created"] == 4
    assert kv["prefix_cache_hits"] == 2
    assert kv["releases"] == 6
    assert kv["evictions"] == 4


def test_single_process_measurement_is_deterministic():
    a = R.run_single_process()
    b = R.run_single_process()
    assert a.to_dict() == b.to_dict()
    # Content fingerprint, not just counts, is stable.
    assert a.metrics["collective"]["result_digest"] == b.metrics["collective"]["result_digest"]


# --- skip resolution -------------------------------------------------------
@pytest.mark.parametrize("mode", [
    "local-process", "localhost-tcp-ring", "multi-mac-tcp-ring",
    "quic", "jaccl-rdma", "minimind-train-smoke", "vllm-kv-surrogate",
])
def test_networking_and_placeholder_modes_are_skipped_with_reason(mode):
    res = R.run_mode(mode)
    assert res.skipped
    assert res.status == bm.STATUS_SKIPPED
    assert res.reason.strip()
    assert not res.metrics  # a skip carries no measurements


def test_skip_reasons_name_the_missing_capability():
    assert "TCP" in R.run_mode("localhost-tcp-ring").reason
    assert "multi-Mac" in R.run_mode("multi-mac-tcp-ring").reason
    assert "QUIC" in R.run_mode("quic").reason
    assert "RDMA" in R.run_mode("jaccl-rdma").reason
    assert "training" in R.run_mode("minimind-train-smoke").reason
    assert "serving" in R.run_mode("vllm-kv-surrogate").reason


def test_bench_skip_agrees_with_oracle_skip_surface():
    # Every networking mode the bench skips must be backed by a pcc.dist
    # transport that is itself unavailable — no stale "unavailable" claim.
    for oracle_mode in ("tcp-ring", "quic", "jaccl-rdma"):
        assert not transport.probe(oracle_mode).available
    # And the session's known network modes cover the bench networking modes.
    assert "tcp-ring" in session.network_modes()
    assert "quic" in session.network_modes()
    assert "jaccl-rdma" in session.network_modes()


def test_unknown_mode_raises():
    with pytest.raises(bm.BenchError):
        R.run_mode("infiniband-fabric")


# --- model invariants ------------------------------------------------------
def test_measured_result_rejects_a_timing_metric():
    # The claim boundary is enforced by the model: no wall-clock / throughput
    # metric may ride in a MEASURED result.
    with pytest.raises(bm.BenchError):
        bm.measured("single-process", latency=0.01)
    with pytest.raises(bm.BenchError):
        bm.measured("single-process", tokens_per_sec=1234)


def test_skipped_result_rejects_metrics_and_empty_reason():
    with pytest.raises(bm.BenchError):
        bm.BenchResult("quic", bm.STATUS_SKIPPED, reason="")
    with pytest.raises(bm.BenchError):
        bm.BenchResult("quic", bm.STATUS_SKIPPED, reason="x", metrics={"count": 1})


def test_measured_result_rejects_a_skip_reason():
    with pytest.raises(bm.BenchError):
        bm.BenchResult("single-process", bm.STATUS_MEASURED, reason="oops")


def test_invalid_status_is_rejected():
    with pytest.raises(bm.BenchError):
        bm.BenchResult("single-process", "MAYBE")


# --- manifest round-trip ---------------------------------------------------
def test_run_all_manifest_shape():
    man = R.run_all()
    assert man.harness == "pcc-dist-bench"
    assert man.measured_modes() == ("single-process",)
    assert set(man.skipped_modes()) == set(bm.ALL_MODES) - {"single-process"}
    # The claim boundary is stamped into the manifest detail.
    assert "no throughput" in man.detail["claim_boundary"].lower() or \
        "logical counts" in man.detail["claim_boundary"].lower()


def test_manifest_json_round_trip_is_byte_stable():
    man = R.run_all()
    blob = man.to_json()
    again = bm.BenchManifest.from_json(blob)
    assert again.to_json() == blob
    # And the reconstructed manifest exposes the same measured metrics.
    assert again.result_for("single-process").metrics == \
        man.result_for("single-process").metrics


def test_manifest_run_is_deterministic_across_runs():
    assert R.run_all().to_json() == R.run_all().to_json()


def test_manifest_rejects_duplicate_modes():
    dup = (bm.skipped("quic", "x"), bm.skipped("quic", "y"))
    with pytest.raises(bm.BenchError):
        bm.BenchManifest("h", dup)


def test_manifest_rejects_unknown_version():
    man = R.run_all()
    data = man.to_dict()
    data["version"] = 999
    with pytest.raises(bm.BenchError):
        bm.BenchManifest.from_dict(data)


def test_result_for_unknown_mode_raises():
    man = R.run_all()
    with pytest.raises(bm.BenchError):
        man.result_for("no-such-mode")
