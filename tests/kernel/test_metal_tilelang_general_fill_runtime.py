from __future__ import annotations

import sys

import pytest

from pcc.kernel_ir.cpu_reference import execute_static_fill_reference
from pcc.kernel_ir.hmm_fence import PccBufferHandle, PccPackedArgs
from pcc.kernel_ir.metal_finalize import emit_metal_source
from pcc.kernel_ir.metal_source_runtime import (
    STATUS_SKIPPED_WITH_REASON,
    STATUS_SOURCE_RUNTIME_PACKAGE_EXECUTED,
    run_metal_source_runtime_package,
)
from pcc.kernel_ir.tilelang_import import import_tilelang_source


TILELANG_STATIC_FILL = """
import tilelang
import tilelang.language as T

pytestmark = pytest.mark.pcc_gate(probe="metal")


@tilelang.jit
def fill_matrix(M, N, value=1.25, dtype=T.float32, threads=32):
    @T.prim_func
    def fill_kernel(C: T.Tensor((M, N), dtype)):
        with T.Kernel(T.ceildiv(M * N, threads), threads=threads) as bx:
            T.fill(C, value)
    return fill_kernel
"""


@pytest.mark.parametrize(
    ("dtype", "value", "bytes_per_element"),
    [("float32", -3.25, 4), ("float16", 1.1, 2)],
)
def test_tilelang_nonzero_fill_real_metal_readback(
    tmp_path, dtype: str, value: float, bytes_per_element: int
):
    if sys.platform != "darwin":
        pytest.fail("TileLang general-fill runtime requires Darwin Metal")
    module = import_tilelang_source(
        TILELANG_STATIC_FILL,
        outer_function="fill_matrix",
        prim_func="fill_kernel",
        constants={"M": 3, "N": 5, "dtype": dtype, "value": value, "threads": 8},
        module_name=f"tilelang_fill_{dtype}_runtime",
    )
    args = PccPackedArgs(launch_device="metal:0")
    args.add_buffer(
        PccBufferHandle(
            nbytes=3 * 5 * bytes_per_element,
            dtype={"float16": "f16", "float32": "f32"}[dtype],
            device="metal:0",
        )
    )
    oracle = execute_static_fill_reference(module)
    result = run_metal_source_runtime_package(
        module,
        args,
        tmp_path,
        metal_source=emit_metal_source(module),
        input_matrices={},
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
