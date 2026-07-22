from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from pcc.kernel_ir.ds4_primitive import (
    DS4_COPY_REFERENCE_COMMIT,
    DS4_COPY_REFERENCE_SHA256,
    DS4_COPY_REFERENCE_SYMBOL,
    PCC_DS4_COPY_ENTRY,
    Ds4PrimitiveError,
    build_ds4_f32_copy_args,
    build_ds4_f32_copy_module,
    ds4_f32_copy_cpu_oracle,
    validate_ds4_f32_copy_reference,
)
from pcc.kernel_ir.gpu_owner_backend import (
    GPU_BACKEND_PCC_METAL,
    PCC_METAL_SCALAR_PIPELINE,
    get_gpu_backend_driver,
)
from pcc.kernel_ir.metal_finalize import emit_metal_source
from pcc.kernel_ir.metal_source_runtime import (
    STATUS_SKIPPED_WITH_REASON,
    STATUS_SOURCE_RUNTIME_PACKAGE_EXECUTED,
)
from pcc.kernel_ir.tirx_adapter import lower_to_plain_tir


DEFAULT_DS4_ROOT = Path("~/pcc_refs/antirez-ds4-depth1").expanduser()


pytestmark = pytest.mark.pcc_gate(probe="metal")


@pytest.fixture(scope="module")
def ds4_copy_source() -> str:
    root = Path(os.environ.get("PCC_DS4_ROOT", str(DEFAULT_DS4_ROOT))).expanduser()
    assert root.is_dir(), (
        f"pinned ds4 reference is required for the primitive gate: {root}; "
        "absence is not primitive evidence"
    )
    head = (root / ".git/HEAD").read_text(encoding="utf-8").strip()
    if head.startswith("ref: "):
        head = (root / ".git" / head.removeprefix("ref: ")).read_text(
            encoding="utf-8"
        ).strip()
    assert head == DS4_COPY_REFERENCE_COMMIT
    return (root / "metal/cpy.metal").read_text(encoding="utf-8")


def test_pinned_ds4_f32_copy_is_a_bounded_external_oracle(ds4_copy_source: str):
    reference = validate_ds4_f32_copy_reference(ds4_copy_source)
    assert reference.sha256 == DS4_COPY_REFERENCE_SHA256
    assert reference.source_symbol == DS4_COPY_REFERENCE_SYMBOL
    assert reference.source_is_oracle_only is True
    with pytest.raises(Ds4PrimitiveError, match="hash changed"):
        validate_ds4_f32_copy_reference(ds4_copy_source + "\n")


def test_ds4_f32_copy_lowers_through_pcc_kernel_ir_tirx_and_metal_source():
    module = build_ds4_f32_copy_module(rows=3, cols=5)
    plain = lower_to_plain_tir(module, target="metal")
    source = emit_metal_source(plain)
    func = module.funcs[0]
    assert func.name == PCC_DS4_COPY_ENTRY
    assert [(op.op, op.args) for op in func.body] == [
        ("parallel", ("src", "dst", "n")),
        ("copy", ("src", "dst")),
    ]
    assert [op["tir_op"] for op in plain.funcs[0]["ops"]] == [
        "tir.parallel_for",
        "tir.copy_loop",
    ]
    assert f"kernel void {PCC_DS4_COPY_ENTRY}" in source
    assert "const device float* src" in source
    assert "device float* dst" in source
    assert "dst[gid] = src[gid];" in source
    assert DS4_COPY_REFERENCE_SYMBOL not in source


def test_ds4_f32_copy_cpu_oracle_is_exact_and_shape_checked():
    matrix = ((-3.5, 0.0, 1.25), (9.0, -0.125, 65504.0))
    oracle = ds4_f32_copy_cpu_oracle(matrix, rows=2, cols=3)
    assert oracle.outputs["dst"] == matrix
    assert oracle.runtime_launch_executed is False
    assert "not ds4 execution" in oracle.claim_mode
    with pytest.raises(Ds4PrimitiveError, match="expected 3 columns"):
        ds4_f32_copy_cpu_oracle(((1.0, 2.0),), rows=1, cols=3)


def test_ds4_f32_copy_real_metal_readback_matches_cpu_oracle(tmp_path: Path):
    if sys.platform != "darwin":
        pytest.fail("ds4 primitive runtime-source Metal readback requires Darwin")
    matrix = (
        (-11.5, 0.0, 1.25, 42.0),
        (3.0, -7.75, 1024.5, -0.03125),
        (9.5, 8.25, -6.0, 5.125),
    )
    rows, cols = 3, 4
    oracle = ds4_f32_copy_cpu_oracle(matrix, rows=rows, cols=cols)
    execution = get_gpu_backend_driver(GPU_BACKEND_PCC_METAL).execute(
        build_ds4_f32_copy_module(rows=rows, cols=cols),
        build_ds4_f32_copy_args(rows=rows, cols=cols),
        tmp_path,
        target="metal:0",
        pipeline=PCC_METAL_SCALAR_PIPELINE,
        input_matrices={"src": matrix},
        cpu_reference=oracle,
        output_name="dst",
        timeout=90.0,
        launcher_links_libpython=True,
    )
    result = execution.raw_result
    data = result.to_dict()
    if result.status == STATUS_SKIPPED_WITH_REASON:
        pytest.fail(data["reason"])

    assert result.status == STATUS_SOURCE_RUNTIME_PACKAGE_EXECUTED, data
    assert execution.synchronized is True
    assert execution.resources_destroyed is True
    assert execution.manifest.requested_gpu_backend == GPU_BACKEND_PCC_METAL
    assert execution.manifest.actual_gpu_backend == GPU_BACKEND_PCC_METAL
    assert execution.manifest.fallback_used is False
    assert data["runtime_launch_executed"] is True
    assert data["runtime_source_compiled"] is True
    assert data["invocation"]["fence_completed"] is True
    assert data["cpu_comparison"]["status"] == "metal_cpu_oracle_match"
    assert data["cpu_comparison"]["max_abs_error"] == 0.0
    assert data["cpu_comparison"]["readback"]["matrix"] == [
        list(row) for row in matrix
    ]
    assert data["allocations_released"] is True
    assert data["whole_program_gpu"] is False
