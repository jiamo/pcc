from __future__ import annotations

from dataclasses import replace

import pytest

from pcc.kernel_ir.cpu_reference import CpuReferenceResult
from pcc.kernel_ir.gpu_owner_backend import (
    GPU_BACKEND_PCC_METAL,
    GPU_BACKEND_TVM_TILELANG,
    PCC_METAL_SCALAR_PIPELINE,
    GpuBackendError,
    GpuBackendUnavailable,
    GpuOwnerManifest,
    PccMetalGpuBackendDriver,
    TvmTilelangGpuBackendDriver,
    get_gpu_backend_driver,
    pcc_metal_owner_identity_fields,
    tvm_tilelang_owner_identity_fields,
    validate_gpu_owner_identity,
)
from pcc.kernel_ir.tvm_tilelang_owner import TVM_TILELANG_PIPELINE
from pcc.kernel_ir.hmm_fence import PccBufferHandle, PccPackedArgs
from pcc.kernel_ir.ir import (
    BufferParam,
    KernelFunc,
    KernelModule,
    KernelOp,
    MemoryScope,
    ScalarParam,
    ScalarType,
)


def _copy_module(*, cuda_only: bool = False) -> KernelModule:
    attrs = {"cp_async": True} if cuda_only else {}
    return KernelModule(
        "owner_copy",
        funcs=(
            KernelFunc(
                "copy_kernel",
                params=(
                    BufferParam(
                        "src",
                        ScalarType.F32,
                        rank=1,
                        shape=(4,),
                        scope=MemoryScope.GLOBAL,
                    ),
                    BufferParam(
                        "dst",
                        ScalarType.F32,
                        rank=1,
                        shape=(4,),
                        scope=MemoryScope.GLOBAL,
                    ),
                    ScalarParam("n", ScalarType.U32),
                ),
                body=(
                    KernelOp("parallel", ("src", "dst", "n"), {"extent": 4}),
                    KernelOp("copy", ("src", "dst"), attrs),
                ),
                grid=(1,),
                threads=16,
            ),
        ),
    )


def _copy_args() -> PccPackedArgs:
    args = PccPackedArgs(launch_device="metal:0")
    args.add_buffer(PccBufferHandle(nbytes=16, dtype="f32", device="metal:0"))
    args.add_buffer(PccBufferHandle(nbytes=16, dtype="f32", device="metal:0"))
    args.add_scalar("u32", 4)
    return args


def _fake_level4_result() -> dict[str, object]:
    return {
        "status": "metal_source_runtime_package_executed",
        "runtime_launch_executed": True,
        "runtime_source_compiled": True,
        "allocations_released": True,
        "whole_program_gpu": False,
        "finalize": {"metallib_produced": False},
        "invocation": {
            "status": "metal_source_runtime_invoked",
            "bridge_function_called": True,
            "fence_completed": True,
        },
        "cpu_comparison": {"status": "metal_cpu_oracle_match"},
        "source_bridge": {},
    }


def test_gpu_owner_selection_is_explicit_and_fail_closed():
    driver = get_gpu_backend_driver("pcc-metal")
    assert driver.backend_id == GPU_BACKEND_PCC_METAL

    with pytest.raises(GpuBackendError, match="not a GPU execution-owner"):
        get_gpu_backend_driver("auto")
    provider = get_gpu_backend_driver("tvm-tilelang")
    assert provider.backend_id == GPU_BACKEND_TVM_TILELANG
    with pytest.raises(GpuBackendUnavailable, match="no fallback"):
        get_gpu_backend_driver("llvm")


def test_pcc_metal_capabilities_reject_pyobjects_and_non_metal_targets():
    driver = PccMetalGpuBackendDriver()
    capabilities = driver.capabilities("metal:0")
    assert capabilities.supported is True
    assert capabilities.accepts_pyobjects is False
    assert PCC_METAL_SCALAR_PIPELINE in capabilities.compile_modes
    assert driver.capabilities("cuda:0").supported is False
    with pytest.raises(GpuBackendError, match="no fallback"):
        driver.validate(_copy_module(), "cuda:0")


def test_pcc_metal_cuda_only_input_fails_before_codegen(tmp_path):
    def forbidden_emitter(_module):
        raise AssertionError("codegen must not run after fail-closed validation")

    driver = PccMetalGpuBackendDriver(
        source_emitters={PCC_METAL_SCALAR_PIPELINE: forbidden_emitter}
    )
    with pytest.raises(ValueError, match="CUDA-only"):
        driver.compile(
            _copy_module(cuda_only=True),
            "metal:0",
            PCC_METAL_SCALAR_PIPELINE,
            tmp_path,
        )


def test_tvm_tilelang_cuda_only_input_fails_before_provider(tmp_path):
    def forbidden_provider(*_args, **_kwargs):
        raise AssertionError("provider must not run after fail-closed validation")

    driver = TvmTilelangGpuBackendDriver(provider_compiler=forbidden_provider)
    with pytest.raises(ValueError, match="CUDA-only"):
        driver.compile(
            _copy_module(cuda_only=True),
            "metal:0",
            TVM_TILELANG_PIPELINE,
            tmp_path,
        )


def test_pcc_metal_owner_driver_runs_common_boundary_without_fallback(tmp_path):
    calls = []

    def fake_runtime_runner(module, packed_args, artifact_dir, **kwargs):
        calls.append((module, packed_args, artifact_dir, kwargs))
        return _fake_level4_result()

    driver = PccMetalGpuBackendDriver(runtime_runner=fake_runtime_runner)
    src = ((1.0, 2.0), (3.0, 4.0))
    cpu = CpuReferenceResult(
        entry="copy_kernel",
        outputs={"dst": src},
        tiles_executed=1,
        k_tiles=1,
    )
    execution = driver.execute(
        _copy_module(),
        _copy_args(),
        tmp_path,
        target="metal:0",
        pipeline=PCC_METAL_SCALAR_PIPELINE,
        input_matrices={"src": src},
        cpu_reference=cpu,
        output_name="dst",
        launcher_links_libpython=True,
    )

    assert len(calls) == 1
    assert calls[0][3]["metal_source"].startswith("#include <metal_stdlib>")
    manifest = execution.manifest
    assert manifest.requested_gpu_backend == "pcc-metal"
    assert manifest.actual_gpu_backend == "pcc-metal"
    assert manifest.semantic_ir_owner == "pcc-kernel-ir-tirx"
    assert manifest.codegen_owner == "pcc-metal-finalizer"
    assert manifest.runtime_owner == "pcc-metal-runtime-source"
    assert manifest.fallback_used is False
    assert manifest.provider_process_links_libpython is False
    assert manifest.claim_level == "GPU_LEVEL_4_DEVICE_RESULT"
    assert set(manifest.artifact_hashes) >= {
        "canonical_frozen_ir",
        "metal_source",
    }
    assert execution.synchronized is True
    assert execution.resources_destroyed is True


def test_tvm_tilelang_owner_driver_runs_common_boundary_without_fallback(tmp_path):
    calls = []

    def fake_runtime_runner(module, packed_args, artifact_dir, **kwargs):
        calls.append((module, packed_args, artifact_dir, kwargs))
        return _fake_level4_result()

    driver = TvmTilelangGpuBackendDriver(runtime_runner=fake_runtime_runner)
    src = ((1.0, 2.0), (3.0, 4.0))
    cpu = CpuReferenceResult(
        entry="copy_kernel",
        outputs={"dst": src},
        tiles_executed=1,
        k_tiles=1,
    )
    execution = driver.execute(
        _copy_module(),
        _copy_args(),
        tmp_path,
        target="metal:0",
        pipeline=TVM_TILELANG_PIPELINE,
        input_matrices={"src": src},
        cpu_reference=cpu,
        output_name="dst",
        launcher_links_libpython=False,
    )

    assert len(calls) == 1
    source = calls[0][3]["metal_source"]
    assert "kernel void copy_kernel(" in source
    assert "src [[ buffer(0) ]]" in source
    assert "dst [[ buffer(1) ]]" in source
    manifest = execution.manifest
    assert manifest.requested_gpu_backend == "tvm-tilelang"
    assert manifest.actual_gpu_backend == "tvm-tilelang"
    assert manifest.semantic_ir_owner == "pcc-kernel-ir-tirx"
    assert manifest.codegen_owner == "pinned-tvm-tilelang-provider"
    assert manifest.runtime_owner == "pcc-metal-runtime-source"
    assert manifest.fallback_used is False
    assert manifest.launcher_links_libpython is False
    assert manifest.provider_process_links_libpython is True
    assert manifest.pass_pipeline_identity == TVM_TILELANG_PIPELINE
    assert set(manifest.artifact_hashes) >= {
        "canonical_frozen_ir",
        "provider_metal_source",
        "pcc_abi_adapted_metal_source",
    }
    assert execution.synchronized is True
    assert execution.resources_destroyed is True


def test_gpu_owner_manifest_rejects_identity_mismatch_and_fallback():
    manifest = GpuOwnerManifest(
        requested_gpu_backend="pcc-metal",
        actual_gpu_backend="pcc-metal",
        semantic_ir_owner="pcc-kernel-ir-tirx",
        codegen_owner="pcc-metal-finalizer",
        runtime_owner="pcc-metal-runtime-source",
        target="metal:0",
        provider_identity="pcc-metal-owner-driver-v1",
        canonical_frozen_ir_sha256="a" * 64,
        pass_pipeline_identity=PCC_METAL_SCALAR_PIPELINE,
        artifact_hashes={"metal_source": "b" * 64},
        fallback_used=False,
        launcher_links_libpython=True,
        provider_process_links_libpython=False,
        claim_level="GPU_LEVEL_1_SOURCE",
        gate_result="source",
    )
    with pytest.raises(GpuBackendError, match="does not match"):
        replace(manifest, actual_gpu_backend="tvm-tilelang")
    with pytest.raises(GpuBackendError, match="never record fallback"):
        replace(manifest, fallback_used=True)


def test_launcher_owner_identity_requires_requested_actual_match_and_no_fallback():
    result = pcc_metal_owner_identity_fields(launcher_links_libpython=False)
    checked = validate_gpu_owner_identity(
        result,
        requested_gpu_backend="pcc-metal",
    )
    assert checked["actual_gpu_backend"] == "pcc-metal"

    with pytest.raises(GpuBackendError, match="actual owner"):
        validate_gpu_owner_identity(
            {**result, "actual_gpu_backend": "tvm-tilelang"},
            requested_gpu_backend="pcc-metal",
        )
    with pytest.raises(GpuBackendError, match="fallback_used=false"):
        validate_gpu_owner_identity(
            {**result, "fallback_used": True},
            requested_gpu_backend="pcc-metal",
        )

    provider = tvm_tilelang_owner_identity_fields(
        launcher_links_libpython=False,
        provider_identity="pcc-tvm-tilelang-metal-owner-v1",
    )
    checked_provider = validate_gpu_owner_identity(
        provider,
        requested_gpu_backend="tvm-tilelang",
    )
    assert checked_provider["provider_process_links_libpython"] is True
