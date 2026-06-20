"""GPU-P0-TVM-TIRX-HOST-DEVICE-SPLIT.

This is the first concrete TVM/TIRx -> Metal proof slice:

* a validated Kernel IR module lowers through the TIRx/plain-TIR boundary;
* the Metal path emits an inspectable ``.metal`` source artifact and, when the
  local Metal toolchain is available, real ``.air``/``.metallib`` files;
* the CPU host launch boundary is explicit and mode-labeled;
* no result claims whole-program GPU execution or an executed runtime launch.
"""

from pathlib import Path

import pytest

from pcc.kernel_ir.host_device_split import (
    HostDeviceSplitError,
    build_host_launch_boundaries,
    prove_tirx_metal_host_device_split,
)
from pcc.kernel_ir.ir import (
    BufferParam,
    KernelFunc,
    KernelModule,
    KernelOp,
    LocalBuffer,
    MemoryScope,
    ScalarParam,
    ScalarType,
)
from pcc.kernel_ir.metal_finalize import (
    MetalFinalizeError,
    STATUS_ARTIFACTS_PRODUCED,
    STATUS_SKIPPED_WITH_REASON,
    STATUS_SOURCE_ONLY,
    emit_metal_source,
    finalize_metal,
)


def _copy_module() -> KernelModule:
    func = KernelFunc(
        name="copy_kernel",
        params=(
            BufferParam("src", ScalarType.F32, rank=1, scope=MemoryScope.GLOBAL),
            BufferParam("dst", ScalarType.F32, rank=1, scope=MemoryScope.GLOBAL),
            ScalarParam("n", ScalarType.U32),
        ),
        body=(
            KernelOp("parallel", ("src", "dst"), {"extent": 16}),
            KernelOp("copy", ("src", "dst")),
        ),
        grid=(1,),
        threads=16,
    )
    return KernelModule("copy_mod", funcs=(func,))


def _shared_buffer_module() -> KernelModule:
    func = KernelFunc(
        name="bad_shared_kernel",
        params=(
            BufferParam("src", ScalarType.F32, rank=1, scope=MemoryScope.GLOBAL),
            BufferParam("scratch", ScalarType.F32, rank=1, scope=MemoryScope.SHARED),
        ),
        body=(KernelOp("copy", ("src", "scratch")),),
        grid=(1,),
        threads=16,
    )
    return KernelModule("bad_shared_mod", funcs=(func,))


def _threadgroup_local_module() -> KernelModule:
    func = KernelFunc(
        name="scratch_copy_kernel",
        params=(
            BufferParam("src", ScalarType.F32, rank=1, scope=MemoryScope.GLOBAL),
            BufferParam("dst", ScalarType.F32, rank=1, scope=MemoryScope.GLOBAL),
            ScalarParam("n", ScalarType.U32),
        ),
        locals=(
            LocalBuffer("scratch", ScalarType.F32, shape=(16,), scope=MemoryScope.SHARED),
        ),
        body=(
            KernelOp("parallel", ("src", "dst"), {"extent": 16}),
            KernelOp("copy", ("src", "scratch")),
            KernelOp("barrier", ()),
            KernelOp("copy", ("scratch", "dst")),
        ),
        grid=(1,),
        threads=16,
    )
    return KernelModule("scratch_copy_mod", funcs=(func,))


def _fragment_local_module() -> KernelModule:
    func = KernelFunc(
        name="fragment_kernel",
        params=(
            BufferParam("src", ScalarType.F32, rank=1, scope=MemoryScope.GLOBAL),
            BufferParam("dst", ScalarType.F32, rank=1, scope=MemoryScope.GLOBAL),
        ),
        locals=(
            LocalBuffer("acc", ScalarType.F32, shape=(16,), scope=MemoryScope.FRAGMENT),
        ),
        body=(
            KernelOp("copy", ("src", "acc")),
            KernelOp("copy", ("acc", "dst")),
        ),
        grid=(1,),
        threads=16,
    )
    return KernelModule("fragment_mod", funcs=(func,))


def _reduction_module() -> KernelModule:
    func = KernelFunc(
        name="sum_kernel",
        params=(
            BufferParam("src", ScalarType.F32, rank=1, scope=MemoryScope.GLOBAL),
            BufferParam("out", ScalarType.F32, rank=1, scope=MemoryScope.GLOBAL),
        ),
        locals=(
            LocalBuffer("acc", ScalarType.F32, shape=(16,), scope=MemoryScope.SHARED),
        ),
        body=(
            KernelOp("parallel", ("src", "out"), {"extent": 64}),
            KernelOp("reduce", ("src", "acc"), {"reduction": "sum"}),
            KernelOp("barrier", ()),
            KernelOp("copy", ("acc", "out")),
        ),
        grid=(4,),
        threads=16,
    )
    return KernelModule("sum_mod", funcs=(func,))


def _unsupported_reduction_module() -> KernelModule:
    func = KernelFunc(
        name="max_kernel",
        params=(
            BufferParam("src", ScalarType.F32, rank=1, scope=MemoryScope.GLOBAL),
            BufferParam("out", ScalarType.F32, rank=1, scope=MemoryScope.GLOBAL),
        ),
        locals=(
            LocalBuffer("acc", ScalarType.F32, shape=(16,), scope=MemoryScope.SHARED),
        ),
        body=(
            KernelOp("parallel", ("src", "out"), {"extent": 64}),
            KernelOp("reduce", ("src", "acc"), {"reduction": "max"}),
            KernelOp("copy", ("acc", "out")),
        ),
        grid=(4,),
        threads=16,
    )
    return KernelModule("max_mod", funcs=(func,))


def test_emit_metal_source_is_real_kernel_source():
    source = emit_metal_source(_copy_module())

    assert "kernel void copy_kernel" in source
    assert "const device float* src [[buffer(0)]]" in source
    assert "device float* dst [[buffer(1)]]" in source
    assert "constant uint& n [[buffer(2)]]" in source
    assert "uint gid [[thread_position_in_grid]]" in source
    assert "dst[gid] = src[gid];" in source
    assert "PyObject" not in source
    assert "python" not in source.lower()


def test_emit_metal_source_declares_threadgroup_local_not_host_param():
    source = emit_metal_source(_threadgroup_local_module())

    assert "threadgroup float scratch[16];" in source
    assert "uint tid [[thread_position_in_threadgroup]]" in source
    assert "threadgroup float* scratch [[buffer" not in source
    assert "scratch[tid] = src[gid];" in source
    assert "threadgroup_barrier(mem_flags::mem_threadgroup);" in source
    assert "dst[gid] = scratch[tid];" in source


def test_emit_metal_source_lowers_threadgroup_sum_reduction():
    source = emit_metal_source(_reduction_module())

    assert "kernel void sum_kernel" in source
    assert "const device float* src [[buffer(0)]]" in source
    assert "device float* out [[buffer(1)]]" in source
    assert "threadgroup float acc[16];" in source
    assert "uint tid [[thread_position_in_threadgroup]]" in source
    assert "uint tgid [[threadgroup_position_in_grid]]" in source
    assert "acc[tid] = (gid < 64u) ? src[gid] : 0.0;" in source
    assert "for (uint active = 16u; active > 1u;" in source
    assert "acc[tid] += acc[partner];" in source
    assert "threadgroup_barrier(mem_flags::mem_threadgroup);" in source
    assert "if (tid == 0u) {" in source
    assert "out[tgid] = acc[0];" in source


def test_emit_metal_source_rejects_unimplemented_reduction_kind():
    with pytest.raises(MetalFinalizeError, match="only sum reduction"):
        emit_metal_source(_unsupported_reduction_module())


def test_finalize_writes_metal_source_artifact(tmp_path):
    result = finalize_metal(
        _copy_module(),
        artifact_dir=tmp_path,
        compile_toolchain=False,
    )

    assert result.status == STATUS_SOURCE_ONLY
    assert result.metal_source_produced is True
    assert result.air_produced is False
    assert result.metallib_produced is False
    assert result.to_dict()["host_launch_claimed"] is False
    metal_path = Path(result.artifact_paths["metal_source"])
    assert metal_path.is_file()
    assert metal_path.name == "copy_mod.metal"
    assert "kernel void copy_kernel" in metal_path.read_text(encoding="utf-8")


def test_finalize_writes_threadgroup_local_source_artifact(tmp_path):
    result = finalize_metal(
        _threadgroup_local_module(),
        artifact_dir=tmp_path,
        compile_toolchain=False,
    )

    assert result.status == STATUS_SOURCE_ONLY
    metal_path = Path(result.artifact_paths["metal_source"])
    source = metal_path.read_text(encoding="utf-8")
    assert "kernel void scratch_copy_kernel" in source
    assert "threadgroup float scratch[16];" in source
    assert "scratch[tid] = src[gid];" in source


def test_finalize_writes_threadgroup_reduction_source_artifact(tmp_path):
    result = finalize_metal(
        _reduction_module(),
        artifact_dir=tmp_path,
        compile_toolchain=False,
    )

    assert result.status == STATUS_SOURCE_ONLY
    metal_path = Path(result.artifact_paths["metal_source"])
    source = metal_path.read_text(encoding="utf-8")
    assert "kernel void sum_kernel" in source
    assert "threadgroup float acc[16];" in source
    assert "out[tgid] = acc[0];" in source


def test_finalize_writes_fragment_thread_storage_source_artifact(tmp_path):
    result = finalize_metal(
        _fragment_local_module(),
        artifact_dir=tmp_path,
        compile_toolchain=False,
    )

    assert result.status == STATUS_SOURCE_ONLY
    metal_path = Path(result.artifact_paths["metal_source"])
    source = metal_path.read_text(encoding="utf-8")
    assert "kernel void fragment_kernel" in source
    assert "thread float acc[16];" in source
    assert "dst[gid] = acc[tid];" in source


def test_finalize_produces_metallib_when_toolchain_available(tmp_path):
    result = finalize_metal(
        _copy_module(),
        artifact_dir=tmp_path,
        compile_toolchain=True,
        timeout=60.0,
    )

    if result.status == STATUS_SKIPPED_WITH_REASON:
        assert result.reason
        assert result.metal_source_produced is True
        assert result.metallib_produced is False
        return

    assert result.status == STATUS_ARTIFACTS_PRODUCED
    assert result.metal_source_produced is True
    assert result.air_produced is True
    assert result.metallib_produced is True
    for key in ("metal_source", "air", "metallib"):
        path = Path(result.artifact_paths[key])
        assert path.is_file()
        assert path.stat().st_size > 0
    assert result.to_dict()["host_launch_claimed"] is False


def test_host_launch_boundary_is_cpu_owned_and_not_whole_program_gpu():
    proof = build_host_launch_boundaries(_copy_module(), host="self", device="metal")
    data = proof.to_dict()

    assert data["host"]["backend"] == "self"
    assert data["host"]["host_finalize"] == "self_host_finalize"
    assert data["device"]["device"] == "metal"
    assert data["device"]["device_finalize"] == "metal_device_finalize"
    assert data["ordinary_python_runs_on_host"] is True
    assert data["whole_program_gpu"] is False

    launch = data["launches"][0]
    assert launch["kernel_entry"] == "copy_kernel"
    assert launch["launcher_symbol"] == "__pcc_launch_copy_kernel_metal"
    assert launch["host_launch_boundary_proven"] is True
    assert launch["runtime_launch_executed"] is False
    assert [b["name"] for b in launch["arg_bindings"]] == ["src", "dst", "n"]
    assert [b["kind"] for b in launch["arg_bindings"]] == ["buffer", "buffer", "scalar"]
    assert [b["address_space"] for b in launch["arg_bindings"]] == [
        "device",
        "device",
        "constant",
    ]
    assert launch["device_locals"] == []


def test_threadgroup_local_is_device_allocation_not_launch_arg():
    proof = build_host_launch_boundaries(_threadgroup_local_module(), host="self", device="metal")
    launch = proof.to_dict()["launches"][0]

    assert [b["name"] for b in launch["arg_bindings"]] == ["src", "dst", "n"]
    assert launch["device_locals"] == [
        {
            "name": "scratch",
            "dtype": "f32",
            "scope": "shared",
            "shape": [16],
            "address_space": "threadgroup",
        }
    ]


def test_reduction_local_is_device_allocation_not_launch_arg():
    proof = build_host_launch_boundaries(_reduction_module(), host="self", device="metal")
    launch = proof.to_dict()["launches"][0]

    assert [b["name"] for b in launch["arg_bindings"]] == ["src", "out"]
    assert launch["device_locals"] == [
        {
            "name": "acc",
            "dtype": "f32",
            "scope": "shared",
            "shape": [16],
            "address_space": "threadgroup",
        }
    ]


def test_fragment_local_boundary_is_recorded_and_source_uses_thread_storage():
    proof = build_host_launch_boundaries(_fragment_local_module(), host="self", device="metal")
    launch = proof.to_dict()["launches"][0]

    assert [b["name"] for b in launch["arg_bindings"]] == ["src", "dst"]
    assert launch["device_locals"] == [
        {
            "name": "acc",
            "dtype": "f32",
            "scope": "fragment",
            "shape": [16],
            "address_space": "fragment",
        }
    ]

    source = emit_metal_source(_fragment_local_module())
    assert "kernel void fragment_kernel" in source
    assert "thread float acc[16];" in source
    assert "thread float* acc [[buffer" not in source
    assert "acc[tid] = src[gid];" in source
    assert "dst[gid] = acc[tid];" in source


def test_host_launch_boundary_rejects_threadgroup_storage_as_host_arg():
    with pytest.raises(HostDeviceSplitError, match="non-global buffer"):
        build_host_launch_boundaries(_shared_buffer_module(), host="self", device="metal")


def test_combined_proof_keeps_artifact_and_launch_boundary_separate(tmp_path):
    result = prove_tirx_metal_host_device_split(
        _copy_module(),
        artifact_dir=str(tmp_path),
        compile_toolchain=False,
    )
    data = result.to_dict()

    assert data["claim_mode"] == "Metal source/metallib artifact plus host launch boundary"
    assert data["whole_program_gpu"] is False
    assert data["runtime_launch_executed"] is False
    assert data["device_artifacts"]["metal_source_produced"] is True
    assert data["device_artifacts"]["metallib_produced"] is False
    assert data["device_artifacts"]["host_launch_claimed"] is False
    assert data["host_device_split"]["ordinary_python_runs_on_host"] is True
    assert data["host_device_split"]["launches"][0]["host_launch_boundary_proven"] is True
