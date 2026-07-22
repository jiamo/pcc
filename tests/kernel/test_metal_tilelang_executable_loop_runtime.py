from __future__ import annotations

import sys

import pytest

from pcc.kernel_ir.cpu_reference import execute_static_indexed_reference
from pcc.kernel_ir.hmm_fence import PccBufferHandle, PccPackedArgs
from pcc.kernel_ir.metal_finalize import emit_metal_source
from pcc.kernel_ir.metal_source_runtime import (
    STATUS_SKIPPED_WITH_REASON,
    STATUS_SOURCE_RUNTIME_PACKAGE_EXECUTED,
    run_metal_source_runtime_package,
)
from pcc.kernel_ir.tilelang_import import import_tilelang_source
from tests.kernel.test_tilelang_executable_loop_bodies import (
    TILELANG_PARALLEL_ADD,
    TILELANG_VECTORIZED_SCALE,
)


pytestmark = pytest.mark.pcc_gate(probe="metal")


@pytest.mark.parametrize("kind", ["parallel", "vectorized"])
def test_executable_scheduled_loop_real_metal_readback(tmp_path, kind: str):
    if sys.platform != "darwin":
        pytest.fail("TileLang executable-loop runtime requires Darwin Metal")
    if kind == "parallel":
        module = import_tilelang_source(
            TILELANG_PARALLEL_ADD,
            outer_function="parallel_add",
            prim_func="add_kernel",
            constants={"M": 3, "N": 5, "threads": 8},
        )
        a = tuple(tuple(float(row * 5 + col) for col in range(5)) for row in range(3))
        b = tuple(tuple(float(20 - row - col) for col in range(5)) for row in range(3))
        inputs = {"A": a, "B": b}
        shape = (3, 5)
        buffer_count = 3
    else:
        module = import_tilelang_source(
            TILELANG_VECTORIZED_SCALE,
            outer_function="vectorized_scale",
            prim_func="scale_kernel",
            constants={"N": 7, "threads": 4},
        )
        inputs = {"A": ((-2.0, -1.5, 0.0, 1.25, 2.5, 3.0, 9.0),)}
        shape = (1, 7)
        buffer_count = 2
    args = PccPackedArgs(launch_device="metal:0")
    for _ in range(buffer_count):
        args.add_buffer(
            PccBufferHandle(
                nbytes=shape[0] * shape[1] * 4,
                dtype="f32",
                device="metal:0",
            )
        )
    oracle = execute_static_indexed_reference(module, inputs)
    result = run_metal_source_runtime_package(
        module,
        args,
        tmp_path,
        metal_source=emit_metal_source(module),
        input_matrices=inputs,
        cpu_reference=oracle,
        output_name="C",
        timeout=90.0,
    )
    data = result.to_dict()
    if result.status == STATUS_SKIPPED_WITH_REASON:
        pytest.fail(data["reason"])
    assert result.status == STATUS_SOURCE_RUNTIME_PACKAGE_EXECUTED, data
    assert data["runtime_launch_executed"] is True
    assert data["invocation"]["fence_completed"] is True
    assert data["cpu_comparison"]["status"] == "metal_cpu_oracle_match"
    assert data["cpu_comparison"]["max_abs_error"] == 0.0
    assert data["allocations_released"] is True
