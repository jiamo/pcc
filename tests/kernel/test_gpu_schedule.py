from __future__ import annotations

import hashlib
import json
from dataclasses import replace

import pytest

from pcc.kernel_ir.cpu_reference import CpuReferenceResult
from pcc.kernel_ir.gpu_owner_backend import (
    PCC_METAL_SCALAR_PIPELINE,
    GpuBackendError,
    PccMetalGpuBackendDriver,
    TvmTilelangGpuBackendDriver,
)
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
from pcc.kernel_ir.schedule import (
    BindThreads,
    KernelSchedule,
    KernelScheduleError,
    apply_kernel_schedule,
)
from pcc.kernel_ir.tirx_adapter import lower_to_plain_tir
from pcc.kernel_ir.tvm_tilelang_owner import (
    TVM_TILELANG_ORDERED_PASSES,
    TVM_TILELANG_PIPELINE,
    TVM_TILELANG_PROVIDER_IDENTITY,
    TvmTilelangCompileResult,
)


def _copy_module(*, threads: int = 16) -> KernelModule:
    return KernelModule(
        "scheduled_copy",
        funcs=(
            KernelFunc(
                "copy_kernel",
                params=(
                    BufferParam(
                        "src",
                        ScalarType.F32,
                        rank=2,
                        shape=(2, 2),
                        scope=MemoryScope.GLOBAL,
                    ),
                    BufferParam(
                        "dst",
                        ScalarType.F32,
                        rank=2,
                        shape=(2, 2),
                        scope=MemoryScope.GLOBAL,
                    ),
                    ScalarParam("n", ScalarType.U32),
                ),
                body=(
                    KernelOp("parallel", ("src", "dst", "n"), {"extent": 4}),
                    KernelOp("copy", ("src", "dst")),
                ),
                grid=(1,),
                threads=threads,
            ),
        ),
    )


def _schedule(module: KernelModule, *, threads: int = 32) -> KernelSchedule:
    return KernelSchedule.for_module(
        module,
        target="metal:0",
        steps=(
            BindThreads(
                function="copy_kernel",
                expected_threads=module.funcs[0].threads,
                threads=threads,
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


def _canonical_plain_digest(plain) -> str:
    encoded = json.dumps(
        plain.to_dict(),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _fake_provider(plain, artifact_dir, *, pipeline, config):
    del artifact_dir, config
    source = "#include <metal_stdlib>\nkernel void copy_kernel() {}\n"
    source_digest = hashlib.sha256(source.encode("utf-8")).hexdigest()
    return TvmTilelangCompileResult(
        backend="tvm-tilelang",
        target="metal",
        pipeline=pipeline,
        ordered_passes=TVM_TILELANG_ORDERED_PASSES,
        provider_identity=TVM_TILELANG_PROVIDER_IDENTITY,
        canonical_frozen_ir_sha256=_canonical_plain_digest(plain),
        semantic_kind="copy",
        logical_entry="copy_kernel",
        provider_entry="copy_kernel_kernel",
        provider_metal_source=source,
        provider_metal_source_sha256=source_digest,
        metal_source=source,
        metal_source_sha256=source_digest,
        tilelang_revision="test",
        tilelang_version="test",
        tvm_revision="test",
        tvm_version="test",
        provider_process_links_libpython=True,
        dependencies={"content_sha256": {}},
        diagnostics=(),
        request_path="request.json",
        response_path="response.json",
        provider_source_path="provider.metal",
        source_path="adapted.metal",
    )


def test_schedule_replay_is_deterministic_content_addressed_and_non_mutating():
    module = _copy_module()
    schedule = _schedule(module)

    first = apply_kernel_schedule(module, schedule, target="metal:0")
    second = apply_kernel_schedule(module, schedule, target="metal:9")

    assert module.funcs[0].threads == 16
    assert first.module.funcs[0].threads == 32
    assert first.to_dict() == second.to_dict()
    assert first.schedule_sha256 == schedule.sha256
    assert first.input_kernel_ir_sha256 != first.output_kernel_ir_sha256
    assert first.trace == (
        {
            "kind": "bind_threads",
            "function": "copy_kernel",
            "before": 16,
            "after": 32,
        },
    )


def test_schedule_rejects_stale_payload_selector_target_and_thread_limits():
    module = _copy_module()
    schedule = _schedule(module)

    with pytest.raises(KernelScheduleError, match="digest is stale"):
        apply_kernel_schedule(_copy_module(threads=8), schedule, target="metal:0")

    wrong_expected = KernelSchedule.for_module(
        module,
        target="metal",
        steps=(BindThreads("copy_kernel", expected_threads=8, threads=32),),
    )
    with pytest.raises(KernelScheduleError, match="expected.*8 threads, got 16"):
        apply_kernel_schedule(module, wrong_expected, target="metal:0")

    unknown_function = KernelSchedule.for_module(
        module,
        target="metal",
        steps=(BindThreads("missing", expected_threads=16, threads=32),),
    )
    with pytest.raises(KernelScheduleError, match="matched 0 functions"):
        apply_kernel_schedule(module, unknown_function, target="metal:0")

    with pytest.raises(KernelScheduleError, match="does not match"):
        apply_kernel_schedule(module, schedule, target="cuda:0")

    with pytest.raises(KernelScheduleError, match=r"\[1, 1024\]"):
        BindThreads("copy_kernel", expected_threads=16, threads=1025)

    with pytest.raises(KernelScheduleError, match="duplicate schedule selector"):
        KernelSchedule.for_module(
            module,
            target="metal",
            steps=(
                BindThreads("copy_kernel", expected_threads=16, threads=32),
                BindThreads("copy_kernel", expected_threads=32, threads=64),
            ),
        )


def test_both_owner_adapters_apply_the_same_schedule_before_freeze(tmp_path):
    module = _copy_module()
    schedule = _schedule(module)
    pcc_driver = PccMetalGpuBackendDriver(
        source_emitters={PCC_METAL_SCALAR_PIPELINE: lambda _plain: "metal source"}
    )
    tvm_driver = TvmTilelangGpuBackendDriver(provider_compiler=_fake_provider)

    pcc_artifacts = pcc_driver.compile(
        module,
        "metal:0",
        PCC_METAL_SCALAR_PIPELINE,
        tmp_path / "pcc",
        schedule=schedule,
    )
    tvm_artifacts = tvm_driver.compile(
        module,
        "metal:0",
        TVM_TILELANG_PIPELINE,
        tmp_path / "tvm",
        schedule=schedule,
    )

    assert pcc_artifacts.frozen_module.funcs[0]["threads"] == 32
    assert tvm_artifacts.frozen_module.funcs[0]["threads"] == 32
    assert (
        pcc_artifacts.canonical_frozen_ir_sha256
        == tvm_artifacts.provider_result.canonical_frozen_ir_sha256
    )
    assert pcc_artifacts.schedule_plan_sha256 == schedule.sha256
    assert tvm_artifacts.schedule_plan_sha256 == schedule.sha256
    assert pcc_artifacts.schedule_trace == tvm_artifacts.schedule_trace

    unscheduled = pcc_driver.compile(
        module,
        "metal:0",
        PCC_METAL_SCALAR_PIPELINE,
        tmp_path / "unscheduled",
    )
    assert unscheduled.frozen_module.funcs[0]["threads"] == 16
    assert unscheduled.schedule_plan_sha256 is None
    assert unscheduled.artifact_id != pcc_artifacts.artifact_id

    frozen = lower_to_plain_tir(module, target="metal")
    with pytest.raises(GpuBackendError, match="after plain-TIR freeze.*no fallback"):
        pcc_driver.validate(frozen, "metal:0", schedule=schedule)
    with pytest.raises(GpuBackendError, match="after plain-TIR freeze.*no fallback"):
        tvm_driver.validate(frozen, "metal:0", schedule=schedule)


@pytest.mark.parametrize(
    "driver",
    (
        PccMetalGpuBackendDriver(
            runtime_runner=lambda *_args, **_kwargs: _fake_level4_result()
        ),
        TvmTilelangGpuBackendDriver(
            runtime_runner=lambda *_args, **_kwargs: _fake_level4_result(),
            provider_compiler=_fake_provider,
        ),
    ),
)
def test_owner_manifest_records_schedule_digest(driver, tmp_path):
    module = _copy_module()
    schedule = _schedule(module)
    cpu = CpuReferenceResult(
        entry="copy_kernel",
        outputs={"dst": ((1.0, 2.0), (3.0, 4.0))},
        tiles_executed=1,
        k_tiles=1,
    )
    pipeline = (
        PCC_METAL_SCALAR_PIPELINE
        if isinstance(driver, PccMetalGpuBackendDriver)
        else TVM_TILELANG_PIPELINE
    )

    execution = driver.execute(
        module,
        _copy_args(),
        tmp_path,
        target="metal:0",
        pipeline=pipeline,
        input_matrices={"src": ((1.0, 2.0), (3.0, 4.0))},
        cpu_reference=cpu,
        output_name="dst",
        launcher_links_libpython=False,
        schedule=schedule,
    )

    assert execution.manifest.schedule_plan_sha256 == schedule.sha256
    assert execution.manifest.artifact_hashes["schedule_plan"] == schedule.sha256
    assert execution.manifest.schedule_trace == (
        {
            "kind": "bind_threads",
            "function": "copy_kernel",
            "before": 16,
            "after": 32,
        },
    )
    assert execution.manifest.fallback_used is False


def test_schedule_failure_never_reaches_either_owner_codegen(tmp_path):
    module = _copy_module()
    stale = replace(_schedule(module), input_kernel_ir_sha256="0" * 64)

    def forbidden(*_args, **_kwargs):
        raise AssertionError("owner codegen must not run after schedule rejection")

    drivers = (
        (
            PccMetalGpuBackendDriver(
                source_emitters={PCC_METAL_SCALAR_PIPELINE: forbidden}
            ),
            PCC_METAL_SCALAR_PIPELINE,
        ),
        (
            TvmTilelangGpuBackendDriver(provider_compiler=forbidden),
            TVM_TILELANG_PIPELINE,
        ),
    )
    for driver, pipeline in drivers:
        with pytest.raises(GpuBackendError, match="digest is stale.*no fallback"):
            driver.compile(
                module,
                "metal:0",
                pipeline,
                tmp_path / driver.backend_id,
                schedule=stale,
            )
