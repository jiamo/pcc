"""Real MLX/Metal DLPack ownership round-trip.

This gate proves a focused tensor interoperability boundary. It does not claim
that pcc executes an entire Python program, or this test's arithmetic, itself.
"""

from __future__ import annotations

import gc
import os
import struct
import sys

import pytest

from pcc.kernel_ir.hmm_fence import PccBufferHandle, PccFenceToken, PccPackedArgs
from pcc.kernel_ir.ir import (
    BufferParam,
    KernelFunc,
    KernelModule,
    KernelOp,
    MemoryScope,
    ScalarType,
)
from pcc.kernel_ir.metal_buffer import (
    STATUS_NATIVE_BUFFER_ALLOCATIONS_READY,
    STATUS_NATIVE_BUFFER_RUNTIME_LOAD_VALIDATED,
    STATUS_SKIPPED_WITH_REASON,
    allocate_metal_native_buffers_for_plan,
    build_metal_native_buffer_runtime_artifacts,
    write_metal_native_buffer,
)
from pcc.kernel_ir.metal_dlpack import (
    STATUS_METAL_DLPACK_NATIVE_RELEASED,
    STATUS_METAL_DLPACK_RECLAIM_PENDING,
    MetalDlpackTensorOwner,
    export_metal_dlpack_protocol,
    import_metal_dlpack_py_capsule,
)
from pcc.kernel_ir.metal_launch import plan_metal_launch


def _strict_hardware() -> bool:
    return os.environ.get("PCC_GPU_HARDWARE_STRICT") == "1"


def _unavailable(reason: str) -> None:
    if _strict_hardware():
        pytest.fail(reason)
    pytest.fail(reason)


def _mlx_or_skip():
    try:
        import mlx.core as mx
    except ImportError as exc:
        pytest.fail(f"mlx gate selected but the MLX import failed: {exc}")
    if sys.platform != "darwin":
        _unavailable("MLX kDLMetal round-trip requires Darwin")
    if not mx.metal.is_available():
        _unavailable("MLX reports that Metal is unavailable")
    return mx


def _matrix_module() -> KernelModule:
    func = KernelFunc(
        name="dlpack_framework_copy",
        params=(
            BufferParam("A", ScalarType.F32, rank=2, shape=(2, 3), scope=MemoryScope.GLOBAL),
            BufferParam("C", ScalarType.F32, rank=2, shape=(2, 3), scope=MemoryScope.GLOBAL),
        ),
        body=(
            KernelOp("parallel", ("A", "C"), {"extent": 6}),
            KernelOp("copy", ("A", "C")),
        ),
        grid=(1,),
        threads=32,
    )
    return KernelModule("dlpack_framework_mod", funcs=(func,))


def _matrix_plan():
    args = PccPackedArgs(launch_device="metal:0")
    args.add_buffer(PccBufferHandle(nbytes=24, dtype="f32", device="metal:0"))
    args.add_buffer(PccBufferHandle(nbytes=24, dtype="f32", device="metal:0"))
    return plan_metal_launch(_matrix_module(), args)


@pytest.mark.pcc_gate(dep="mlx")
@pytest.mark.pcc_gate(probe="metal")
def test_owned_metal_tensor_roundtrips_through_mlx_dlpack(tmp_path):
    mx = _mlx_or_skip()
    runtime = build_metal_native_buffer_runtime_artifacts(
        tmp_path / "native_buffer_runtime",
        compile_runtime=True,
        link_runtime_library=True,
        validate_symbols=True,
        timeout=90.0,
    )
    if runtime.status == STATUS_SKIPPED_WITH_REASON:
        _unavailable(runtime.reason)
    assert runtime.status == STATUS_NATIVE_BUFFER_RUNTIME_LOAD_VALIDATED
    assert runtime.library_path is not None

    plan = _matrix_plan()
    allocations = allocate_metal_native_buffers_for_plan(runtime.library_path, plan)
    if allocations.status == STATUS_SKIPPED_WITH_REASON:
        _unavailable(allocations.reason)
    assert allocations.status == STATUS_NATIVE_BUFFER_ALLOCATIONS_READY

    owner = MetalDlpackTensorOwner(allocations, plan)
    release_fence = PccFenceToken()
    protocol = None
    array = None
    result = None
    framework_capsule = None
    framework_import = None
    try:
        allocation = next(item for item in allocations.allocations if item.name == "A")
        values = (1.25, -2.5, 3.75, 4.0, 5.5, -6.25)
        write_metal_native_buffer(
            runtime.library_path,
            allocation.native_mtlbuffer_ptr,
            struct.pack("<6f", *values),
        )

        # The host write is already complete. This separate, intentionally
        # incomplete token guards native reclamation after MLX drops its alias.
        protocol = export_metal_dlpack_protocol(
            owner.export("A"), release_fence=release_fence
        )
        assert protocol.__dlpack_device__() == (8, 0)
        array = mx.from_dlpack(protocol, copy=False)
        mx.eval(array)

        assert array.__dlpack_device__() == (8, 0)
        assert tuple(array.shape) == (2, 3)
        assert array.dtype == mx.float32
        assert array.tolist() == [list(values[:3]), list(values[3:])]

        result = array * 2.0 + 1.0
        mx.eval(result)
        assert result.tolist() == [
            [value * 2.0 + 1.0 for value in values[:3]],
            [value * 2.0 + 1.0 for value in values[3:]],
        ]

        # Export the MLX view back through DLPack and prove it still names the
        # same owned MTLBuffer rather than a copied host allocation.
        framework_capsule = array.__dlpack__()
        framework_import = import_metal_dlpack_py_capsule(
            framework_capsule,
            expected_dtype="f32",
            expected_shape=(2, 3),
            expected_device="metal:0",
        )
        assert (
            framework_import.imported.native_mtlbuffer_ptr
            == allocation.native_mtlbuffer_ptr
        )
        framework_fence = PccFenceToken()
        framework_fence.complete()
        framework_import.deleter(framework_fence)

        mx.synchronize()
        result = None
        array = None
        framework_import = None
        framework_capsule = None
        gc.collect()

        pending = owner.reclaim_completed()
        assert pending.status == STATUS_METAL_DLPACK_RECLAIM_PENDING
        assert pending.pending_count == 1
        assert allocation.handle_id not in allocations.to_dict()["released_handle_ids"]

        release_fence.complete()
        reclaimed = owner.reclaim_completed()
        assert reclaimed.status == STATUS_METAL_DLPACK_NATIVE_RELEASED
        assert reclaimed.reclaimed_handle_ids == (allocation.handle_id,)
        assert allocation.handle_id in allocations.to_dict()["released_handle_ids"]
    finally:
        result = None
        array = None
        framework_import = None
        framework_capsule = None
        gc.collect()
        # Release the unexported C buffer, and the exported buffer after a
        # successful or already-delivered framework deleter.
        release_fence.complete()
        owner.reclaim_completed()
        if not owner.to_dict()["active_aliases"]:
            allocations.release_all()

