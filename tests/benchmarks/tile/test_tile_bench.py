"""P-P0-TILE-TVM-BENCH — metadata-only Tile/TIRx/Metal measurement gate.

These tests assert REAL invariants over the ``pcc.kernel_ir`` package, not a
shape that merely looks similar:

  * cpu-only RUNS and produces DETERMINISTIC IR node counts;
  * the host/device split has the correct shared/host/device node counts and
    actually schedules the device finalize for device=metal;
  * the TVM-oracle golden match holds;
  * plain-TIR freeze succeeds (carries the freeze marker);
  * metal-source-only RUNS-with-descriptor or SKIPS-with-reason, and NEVER
    claims a .metallib or a host launch;
  * metal-runtime is ALWAYS SKIPPED_WITH_REASON;
  * every resource metric (launch latency / TFLOPS / throughput) is the literal
    ``not-measured`` placeholder — no speed claim without hardware.

Gate command (main runs this; this agent does not run pytest):

    env -u LC_ALL uv run pytest tests/benchmarks/tile -q -n0
"""

import pytest

from tests.benchmarks.tile.harness import (
    NOT_MEASURED,
    KERNEL_SHAPES,
    RunStatus,
    TileBenchMode,
    build_kernel,
    metal_toolchain_available,
    run_all_modes,
    run_cpu_only_bench,
    run_metal_runtime,
    run_metal_source_only,
)
from pcc.kernel_ir.ir import KernelIRError
from pcc.kernel_ir.target_split import SHARED_FRONT_HALF

# Golden, deterministic per-shape node counts. These are pinned on purpose:
# changing a kernel builder must change the expected count here, never silently.
#   (funcs, params, local_buffers, body_ops, scalar_params, buffer_params)
_EXPECTED_IR_COUNTS = {
    "vector-add": (1, 3, 0, 2, 0, 3),
    "copy": (1, 2, 0, 1, 0, 2),
    "fill": (1, 1, 0, 1, 0, 1),
    "reduction": (1, 2, 1, 4, 0, 2),
    "gemm": (1, 3, 3, 5, 0, 3),
}

# Expected frozen device-side op count per shape (== body_ops, 1:1 freeze).
_EXPECTED_DEVICE_NODES = {
    "vector-add": 2,
    "copy": 1,
    "fill": 1,
    "reduction": 4,
    "gemm": 5,
}


# --------------------------------------------------------------------------
# Shape coverage sanity.
# --------------------------------------------------------------------------


def test_all_five_shapes_present():
    assert set(KERNEL_SHAPES) == {
        "vector-add",
        "copy",
        "fill",
        "reduction",
        "gemm",
    }
    assert set(_EXPECTED_IR_COUNTS) == set(KERNEL_SHAPES)


@pytest.mark.parametrize("shape", KERNEL_SHAPES)
def test_build_kernel_validates(shape):
    # build_kernel runs validate_kernel; a bad build would raise.
    module = build_kernel(shape)
    assert module.funcs


def test_unknown_shape_raises():
    with pytest.raises(KeyError):
        build_kernel("softmax")  # not in the first-slice shape set


# --------------------------------------------------------------------------
# cpu-only mode: RUNS, deterministic IR counts, correct split, golden match.
# --------------------------------------------------------------------------


@pytest.mark.parametrize("shape", KERNEL_SHAPES)
def test_cpu_only_ir_node_counts_deterministic(shape):
    rep = run_cpu_only_bench(shape)
    c = rep.ir_node_counts
    expected = _EXPECTED_IR_COUNTS[shape]
    assert (
        c.funcs,
        c.params,
        c.local_buffers,
        c.body_ops,
        c.scalar_params,
        c.buffer_params,
    ) == expected
    # scalar + buffer must partition params exactly.
    assert c.scalar_params + c.buffer_params == c.params
    # Determinism: a second run yields byte-identical dict.
    assert run_cpu_only_bench(shape).to_dict() == rep.to_dict()


@pytest.mark.parametrize("shape", KERNEL_SHAPES)
def test_cpu_only_host_device_split(shape):
    rep = run_cpu_only_bench(shape)
    split = rep.host_device_split
    # The shared front-half is target-neutral and counted once.
    assert split.shared_front_half_nodes == len(SHARED_FRONT_HALF)
    # host=self + device=metal MUST schedule the device finalize.
    assert split.runs_device_finalize is True
    # One launcher stub per kernel func (all shapes are single-func here).
    assert split.host_side_nodes == 1
    # Device-side nodes are the frozen plain-TIR ops (1:1 with body ops).
    assert split.device_side_nodes == _EXPECTED_DEVICE_NODES[shape]
    assert split.device_side_nodes == rep.ir_node_counts.body_ops


@pytest.mark.parametrize("shape", KERNEL_SHAPES)
def test_cpu_only_plain_tir_freeze_ok(shape):
    assert run_cpu_only_bench(shape).plain_tir_freeze_ok is True


@pytest.mark.parametrize("shape", KERNEL_SHAPES)
def test_cpu_only_tvm_oracle_golden_match(shape):
    assert run_cpu_only_bench(shape).tvm_oracle_golden_match is True


@pytest.mark.parametrize("shape", KERNEL_SHAPES)
def test_cpu_only_resources_all_not_measured(shape):
    res = run_cpu_only_bench(shape).to_dict()["resources_not_measured"]
    assert res["launch_latency_ms"] == NOT_MEASURED
    assert res["tflops"] == NOT_MEASURED
    assert res["device_throughput_gbps"] == NOT_MEASURED


# --------------------------------------------------------------------------
# metal-source-only mode: descriptor metadata; RUN or SKIP; never a metallib.
# --------------------------------------------------------------------------


@pytest.mark.parametrize("shape", KERNEL_SHAPES)
def test_metal_source_only_never_claims_metallib_or_launch(shape):
    rep = run_metal_source_only(shape)
    # Regardless of RUN/SKIP, this mode NEVER produces a metallib or a launch.
    assert rep.metallib_produced is False
    assert rep.host_launch_claimed is False
    res = rep.to_dict()["resources_not_measured"]
    assert res["tflops"] == NOT_MEASURED
    assert res["launch_latency_ms"] == NOT_MEASURED


@pytest.mark.parametrize("shape", KERNEL_SHAPES)
def test_metal_source_only_status_matches_toolchain(shape):
    rep = run_metal_source_only(shape)
    if metal_toolchain_available():
        # Toolchain present -> descriptor RUN (source emission still a TODO, so
        # the reason must say so; but it is a RUN, not a skip).
        assert rep.status is RunStatus.RUN
        assert rep.library_name is not None
        # Packaging plan describes the .metal -> .air -> .metallib steps.
        assert rep.packaging_steps == [
            "emit_metal_source",
            "compile_to_air",
            "package_metallib",
        ]
        assert rep.entry_points  # at least one kernel entry point
    else:
        # No toolchain -> SKIPPED_WITH_REASON, but the descriptor metadata was
        # still measured (library name + steps present) and the reason is set.
        assert rep.status is RunStatus.SKIPPED_WITH_REASON
        assert rep.reason
        assert rep.library_name is not None
        assert rep.packaging_steps == [
            "emit_metal_source",
            "compile_to_air",
            "package_metallib",
        ]


# --------------------------------------------------------------------------
# metal-runtime mode: ALWAYS skipped, never a launch claim.
# --------------------------------------------------------------------------


@pytest.mark.parametrize("shape", KERNEL_SHAPES)
def test_metal_runtime_always_skipped(shape):
    rep = run_metal_runtime(shape)
    assert rep.status is RunStatus.SKIPPED_WITH_REASON
    assert rep.reason
    # The reason must name the not-measured placeholder so no reader infers a
    # latency/TFLOPS number was produced.
    assert NOT_MEASURED in rep.reason
    res = rep.to_dict()["resources_not_measured"]
    assert res["launch_latency_ms"] == NOT_MEASURED
    assert res["tflops"] == NOT_MEASURED
    assert res["device_throughput_gbps"] == NOT_MEASURED


def test_metal_runtime_skip_is_unconditional():
    # Even the gemm shape (the one with device-shaped tile ops) is skipped.
    assert run_metal_runtime("gemm").status is RunStatus.SKIPPED_WITH_REASON


# --------------------------------------------------------------------------
# run_all_modes: the three modes agree on the claim boundary.
# --------------------------------------------------------------------------


def test_run_all_modes_claim_boundary():
    out = run_all_modes("gemm")
    assert set(out) == {m.value for m in TileBenchMode}
    # metal-runtime is always a skip.
    assert out["metal-runtime"]["status"] == RunStatus.SKIPPED_WITH_REASON.value
    # metal-source-only never produces a metallib.
    assert out["metal-source-only"]["metallib_produced"] is False
    assert out["metal-source-only"]["host_launch_claimed"] is False
    # cpu-only carries no measured resource.
    cpu_res = out["cpu-only"]["resources_not_measured"]
    assert set(cpu_res.values()) == {NOT_MEASURED}
