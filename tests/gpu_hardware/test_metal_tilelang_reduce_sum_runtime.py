"""GPU_LEVEL_4 gate for the bounded TileLang static row ``reduce_sum``.

The host harness may prove a real runtime-source Metal device result.  It does
not prove pcc1-native launch, five-GC parity, full TileLang execution, softmax,
norm, attention, or whole-program GPU execution.
"""

from __future__ import annotations

import os

import pytest

from pcc.kernel_ir.cpu_reference import execute_static_row_reduce_sum_reference
from pcc.kernel_ir.gpu_claims import (
    GpuClaimLevel,
    classify_metal_source_runtime_package_result,
    require_device_result_or_skip,
)
from pcc.kernel_ir.hmm_fence import PccBufferHandle, PccPackedArgs
from pcc.kernel_ir.metal_finalize import emit_metal_source
from pcc.kernel_ir.metal_source_runtime import (
    STATUS_SKIPPED_WITH_REASON,
    run_metal_source_runtime_package,
)
from pcc.kernel_ir.tilelang_import import import_tilelang_source
from tests.kernel.test_tilelang_reduce_sum import TILELANG_STATIC_ROW_REDUCE_SUM


pytestmark = pytest.mark.pcc_gate(probe="metal")


def test_tilelang_static_row_reduce_sum_real_metal_device_result_or_reason(tmp_path):
    module = import_tilelang_source(
        TILELANG_STATIC_ROW_REDUCE_SUM,
        outer_function="row_reduce_sum",
        prim_func="reduce_sum_kernel",
        constants={"rows": 3, "width": 5, "dtype": "float32"},
        module_name="tilelang_static_row_reduce_sum_level4",
    )
    input_matrix = (
        (1.0, -2.0, 3.0, 4.0, -1.0),
        (0.5, 0.25, -0.75, 2.0, 1.0),
        (-4.0, -3.0, -2.0, -1.0, 10.0),
    )
    args = PccPackedArgs(launch_device="metal:0")
    args.add_buffer(PccBufferHandle(nbytes=3 * 5 * 4, dtype="f32", device="metal:0"))
    args.add_buffer(PccBufferHandle(nbytes=3 * 1 * 4, dtype="f32", device="metal:0"))
    oracle = execute_static_row_reduce_sum_reference(module, {"A": input_matrix})

    result = run_metal_source_runtime_package(
        module,
        args,
        tmp_path,
        metal_source=emit_metal_source(module),
        input_matrices={"A": input_matrix},
        cpu_reference=oracle,
        output_name="Out",
        timeout=90.0,
    )
    evidence = classify_metal_source_runtime_package_result(
        "tilelang_static_row_reduce_sum",
        result.to_dict(),
    )
    checked = require_device_result_or_skip(
        evidence,
        strict=os.environ.get("PCC_GPU_HARDWARE_STRICT") == "1",
    )
    if checked.status == STATUS_SKIPPED_WITH_REASON:
        assert checked.proven is False
        assert checked.reason
        return

    assert checked.level is GpuClaimLevel.GPU_LEVEL_4_DEVICE_RESULT
    assert checked.device_result_proven is True
    assert checked.runtime_launch_executed is True
    assert checked.runtime_source_compiled is True
    assert checked.fence_completed is True
    assert checked.cpu_oracle_matched is True
    assert checked.pcc1_native_executed is False
    assert checked.gc_backend_parity == ()
    assert checked.whole_program_gpu is False
