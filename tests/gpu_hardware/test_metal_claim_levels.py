"""Hardware-facing GPU claim-level gates for Kernel IR Metal.

These tests intentionally live outside ``tests/kernel`` so the hardware matrix
can be run and reported separately. By default an unavailable Metal runtime is
accepted only as in-band ``SKIPPED_WITH_REASON`` evidence. Set
``PCC_GPU_HARDWARE_STRICT=1`` on a Metal runner to require
``GPU_LEVEL_4_DEVICE_RESULT``.
"""

from __future__ import annotations

import os
import sys

import pytest

from pcc.kernel_ir.cpu_reference import (
    CpuReferenceResult,
    execute_scalar_tiled_gemm_reference,
)
from pcc.kernel_ir.gpu_claims import (
    GpuClaimError,
    GpuClaimLevel,
    classify_metal_source_runtime_package_result,
    require_device_result_or_skip,
)
from pcc.kernel_ir.gpu_owner_backend import (
    GPU_BACKEND_PCC_METAL,
    GPU_BACKEND_TVM_TILELANG,
    PCC_METAL_SCALAR_PIPELINE,
    get_gpu_backend_driver,
    validate_gpu_owner_identity,
)
from pcc.kernel_ir.tvm_tilelang_owner import TVM_TILELANG_PIPELINE
from pcc.kernel_ir.hmm_fence import PccBufferHandle, PccPackedArgs
from pcc.kernel_ir.ir import (
    BufferParam,
    KernelFunc,
    KernelModule,
    KernelOp,
    Layout,
    LocalBuffer,
    MemoryScope,
    ScalarParam,
    ScalarType,
)
from pcc.kernel_ir.metal_finalize import emit_metal_simdgroup_gemm_source, emit_metal_source
from pcc.kernel_ir.metal_source_runtime import (
    STATUS_SKIPPED_WITH_REASON,
    STATUS_SOURCE_RUNTIME_INVOKED,
    STATUS_SOURCE_RUNTIME_PACKAGE_EXECUTED,
    run_metal_source_runtime_package,
    verify_metal_source_runtime_package_manifest,
    write_metal_source_runtime_package_manifest,
)
from pcc.kernel_ir.tilelang_import import import_tilelang_source


TILELANG_METAL_MATMUL = """
import tilelang
import tilelang.language as T

@tilelang.jit
def matmul_simdgroup(M, N, K, block_M=64, block_N=64, block_K=32, dtype=T.float16, accum_dtype=T.float32):
    @T.prim_func
    def gemm_kernel(
        A: T.Tensor((M, K), dtype),
        B: T.Tensor((K, N), dtype),
        C: T.Tensor((M, N), accum_dtype),
    ):
        with T.Kernel(T.ceildiv(N, block_N), T.ceildiv(M, block_M), threads=128) as (bx, by):
            A_shared = T.alloc_shared((block_M, block_K), dtype, scope="shared")
            B_shared = T.alloc_shared((block_K, block_N), dtype, scope="shared")
            C_local = T.alloc_fragment((block_M, block_N), accum_dtype)
            T.clear(C_local)
            for ko in T.Pipelined(T.ceildiv(K, block_K), num_stages=0):
                T.copy(A[by * block_M, ko * block_K], A_shared)
                T.copy(B[ko * block_K, bx * block_N], B_shared)
                T.gemm(A_shared, B_shared, C_local)
            T.copy(C_local, C[by * block_M, bx * block_N])
    return gemm_kernel
"""


TILELANG_VECTORIZED_ABC_COPY_MATMUL = """
import tilelang
import tilelang.language as T

@tilelang.jit
def matmul_vectorized_abc_copy(M, N, K, block_M=8, block_N=8, block_K=8, dtype=T.float16, accum_dtype=T.float32):
    @T.prim_func
    def gemm_kernel(
        A: T.Tensor((M, K), dtype),
        B: T.Tensor((K, N), dtype),
        C: T.Tensor((M, N), accum_dtype),
    ):
        with T.Kernel(T.ceildiv(N, block_N), T.ceildiv(M, block_M), threads=32) as (bx, by):
            A_shared = T.alloc_shared((block_M, block_K), dtype)
            B_shared = T.alloc_shared((block_K, block_N), dtype)
            C_local = T.alloc_fragment((block_M, block_N), accum_dtype)
            T.clear(C_local)
            for ko in T.serial(T.ceildiv(K, block_K)):
                for i in T.Parallel(block_M):
                    for kk in T.vectorized(block_K):
                        T.copy(A[by * block_M + i, ko * block_K + kk], A_shared)
                for kk in T.Parallel(block_K):
                    for j in T.vectorized(block_N):
                        T.copy(B[ko * block_K + kk, bx * block_N + j], B_shared)
                T.gemm(A_shared, B_shared, C_local)
            for i in T.Parallel(block_M):
                for j in T.vectorized(block_N):
                    T.copy(C_local, C[by * block_M + i, bx * block_N + j])
    return gemm_kernel
"""


def _complete_level4_record() -> dict[str, object]:
    return {
        "status": STATUS_SOURCE_RUNTIME_PACKAGE_EXECUTED,
        "runtime_launch_executed": True,
        "runtime_source_compiled": True,
        "whole_program_gpu": False,
        "finalize": {"metallib_produced": False},
        "invocation": {
            "status": STATUS_SOURCE_RUNTIME_INVOKED,
            "bridge_function_called": True,
            "fence_completed": True,
        },
        "cpu_comparison": {"status": "metal_cpu_oracle_match"},
    }


def test_level4_classifier_accepts_only_complete_mode_labeled_device_result():
    evidence = classify_metal_source_runtime_package_result(
        "complete_level4",
        _complete_level4_record(),
    )

    assert evidence.level is GpuClaimLevel.GPU_LEVEL_4_DEVICE_RESULT
    assert evidence.device_result_proven is True
    assert evidence.metallib_produced is False
    assert evidence.pcc1_native_executed is False
    assert evidence.gc_backend_parity == ()
    assert evidence.whole_program_gpu is False


@pytest.mark.parametrize(
    ("section", "field", "value"),
    [
        (None, "runtime_launch_executed", False),
        (None, "runtime_source_compiled", False),
        ("invocation", "fence_completed", False),
        ("cpu_comparison", "status", "metal_cpu_oracle_mismatch"),
    ],
)
def test_level4_classifier_rejects_partial_or_forged_device_result(
    section: str | None,
    field: str,
    value: object,
):
    record = _complete_level4_record()
    target = record if section is None else record[section]
    assert isinstance(target, dict)
    target[field] = value

    evidence = classify_metal_source_runtime_package_result("partial_level4", record)

    assert evidence.device_result_proven is False
    with pytest.raises(GpuClaimError, match="did not prove GPU_LEVEL_4_DEVICE_RESULT"):
        require_device_result_or_skip(evidence, strict=True)


def test_level4_classifier_rejects_whole_program_gpu_overclaim():
    record = _complete_level4_record()
    record["whole_program_gpu"] = True

    with pytest.raises(GpuClaimError, match="whole-program GPU execution"):
        classify_metal_source_runtime_package_result("overclaim", record)


def _strict_hardware() -> bool:
    return os.environ.get("PCC_GPU_HARDWARE_STRICT") == "1"


def _matrix_copy_module() -> KernelModule:
    func = KernelFunc(
        name="copy_kernel",
        params=(
            BufferParam("src", ScalarType.F32, rank=2, shape=(2, 2), scope=MemoryScope.GLOBAL),
            BufferParam("dst", ScalarType.F32, rank=2, shape=(2, 2), scope=MemoryScope.GLOBAL),
            ScalarParam("n", ScalarType.U32),
        ),
        body=(
            KernelOp("parallel", ("src", "dst", "n"), {"extent": 4}),
            KernelOp("copy", ("src", "dst")),
        ),
        grid=(1,),
        threads=16,
    )
    return KernelModule("gpu_level_copy_mod", funcs=(func,))


def _matrix_copy_args() -> PccPackedArgs:
    args = PccPackedArgs(launch_device="metal:0")
    args.add_buffer(PccBufferHandle(nbytes=16, dtype="f32", device="metal:0"))
    args.add_buffer(PccBufferHandle(nbytes=16, dtype="f32", device="metal:0"))
    args.add_scalar("u32", 4)
    return args


def _tilelang_module(*, m: int = 5, n: int = 7, k: int = 3) -> KernelModule:
    return import_tilelang_source(
        TILELANG_METAL_MATMUL,
        outer_function="matmul_simdgroup",
        prim_func="gemm_kernel",
        constants={"M": m, "N": n, "K": k},
        module_name="gpu_level_tilelang_matmul",
    )


def _tilelang_vectorized_abc_copy_module(*, m: int = 5, n: int = 7, k: int = 16) -> KernelModule:
    return import_tilelang_source(
        TILELANG_VECTORIZED_ABC_COPY_MATMUL,
        outer_function="matmul_vectorized_abc_copy",
        prim_func="gemm_kernel",
        constants={"M": m, "N": n, "K": k},
        module_name="gpu_level_tilelang_vectorized_abc_copy",
    )


def _tilelang_vectorized_nonzero_serial_module(*, m: int = 5, n: int = 7, k: int = 24) -> KernelModule:
    source = TILELANG_VECTORIZED_ABC_COPY_MATMUL.replace(
        "for ko in T.serial(T.ceildiv(K, block_K)):",
        "for ko in T.serial(1, T.ceildiv(K, block_K)):",
    )
    return import_tilelang_source(
        source,
        outer_function="matmul_vectorized_abc_copy",
        prim_func="gemm_kernel",
        constants={"M": m, "N": n, "K": k},
        module_name="gpu_level_tilelang_vectorized_nonzero_serial",
    )


def _gemm_inputs(m: int, n: int, k: int):
    a = tuple(tuple(float(((i * 3) + kk) % 7 - 3) for kk in range(k)) for i in range(m))
    b = tuple(tuple(float(((kk * 2) - j) % 9 - 4) for j in range(n)) for kk in range(k))
    return a, b


def _gemm_inputs_transposed(*, m: int, n: int, k: int):
    a = tuple(tuple(float(((kk * 3) + i) % 7 - 3) for i in range(m)) for kk in range(k))
    b = tuple(tuple(float(((j * 2) - kk) % 9 - 4) for kk in range(k)) for j in range(n))
    return a, b


def _gemm_args(*, m: int, n: int, k: int) -> PccPackedArgs:
    args = PccPackedArgs(launch_device="metal:0")
    args.add_buffer(PccBufferHandle(nbytes=m * k * 2, dtype="f16", device="metal:0"))
    args.add_buffer(PccBufferHandle(nbytes=k * n * 2, dtype="f16", device="metal:0"))
    args.add_buffer(PccBufferHandle(nbytes=m * n * 4, dtype="f32", device="metal:0"))
    return args


def _simdgroup_micro_gemm_module() -> KernelModule:
    return _simdgroup_gemm_module(m=8, n=8, k=8, block_m=8, block_n=8, threads=32)


def _simdgroup_gemm_module(
    *,
    m: int,
    n: int,
    k: int,
    block_m: int,
    block_n: int,
    threads: int,
    split_k: int = 1,
    split_k_span_mode: str | None = None,
    output_atomic: bool = False,
    transpose_a: bool = False,
    transpose_b: bool = False,
) -> KernelModule:
    block_k = 8
    full_k_tiles = (k + block_k - 1) // block_k
    split_span = k
    if output_atomic:
        if split_k_span_mode == "ceildiv" and split_k > 0:
            split_span = (k + split_k - 1) // split_k
        elif split_k_span_mode == "floor_div" and split_k > 0:
            split_span = k // split_k
        else:
            split_span = k // split_k if split_k > 0 and k % split_k == 0 else k
    selected_k_tiles = (split_span + block_k - 1) // block_k
    copy_attrs = {"pipeline_extent": full_k_tiles, "num_stages": 0}
    if output_atomic and split_k_span_mode is not None:
        copy_attrs = {
            **copy_attrs,
            "split_k_span_mode": split_k_span_mode,
            "split_k_span": split_span,
            "split_k_axis_var": "bz",
        }
    gemm_attrs = {"pipeline_extent": selected_k_tiles, "num_stages": 0}
    if transpose_a:
        gemm_attrs["transpose_A"] = True
    if transpose_b:
        gemm_attrs["transpose_B"] = True
    output_op = (
        KernelOp("atomic_add", ("C", "C_local"))
        if output_atomic
        else KernelOp("copy", ("C_local", "C"))
    )
    grid = (
        ((n + block_n - 1) // block_n, (m + block_m - 1) // block_m, split_k)
        if output_atomic
        else ((n + block_n - 1) // block_n, (m + block_m - 1) // block_m)
    )
    func = KernelFunc(
        name="gemm_kernel",
        params=(
            BufferParam(
                "A",
                ScalarType.F16,
                rank=2,
                shape=(k, m) if transpose_a else (m, k),
                scope=MemoryScope.GLOBAL,
            ),
            BufferParam(
                "B",
                ScalarType.F16,
                rank=2,
                shape=(n, k) if transpose_b else (k, n),
                scope=MemoryScope.GLOBAL,
            ),
            BufferParam("C", ScalarType.F32, rank=2, shape=(m, n), scope=MemoryScope.GLOBAL),
        ),
        locals=(
            LocalBuffer(
                "A_shared",
                ScalarType.F16,
                shape=(block_k, block_m) if transpose_a else (block_m, block_k),
                scope=MemoryScope.SHARED,
                layout=Layout.TILE,
            ),
            LocalBuffer(
                "B_shared",
                ScalarType.F16,
                shape=(block_n, block_k) if transpose_b else (block_k, block_n),
                scope=MemoryScope.SHARED,
                layout=Layout.TILE,
            ),
            LocalBuffer(
                "C_local",
                ScalarType.F32,
                shape=(block_m, block_n),
                scope=MemoryScope.FRAGMENT,
                layout=Layout.TILE,
            ),
        ),
        body=(
            KernelOp("fill", ("C_local",), {"value": 0}),
            KernelOp("copy", ("A", "A_shared"), copy_attrs),
            KernelOp("copy", ("B", "B_shared"), copy_attrs),
            KernelOp("gemm", ("A_shared", "B_shared", "C_local"), gemm_attrs),
            output_op,
        ),
        grid=grid,
        threads=threads,
    )
    return KernelModule("gpu_level_simdgroup_mod", funcs=(func,))


def _assert_claim_level_4_or_skip(evidence):
    checked = require_device_result_or_skip(evidence, strict=_strict_hardware())
    if checked.status == STATUS_SKIPPED_WITH_REASON:
        assert checked.proven is False
        assert checked.reason
        return checked

    assert checked.level is GpuClaimLevel.GPU_LEVEL_4_DEVICE_RESULT
    assert checked.device_result_proven is True
    assert checked.runtime_launch_executed is True
    assert checked.runtime_source_compiled is True
    assert checked.fence_completed is True
    assert checked.cpu_oracle_matched is True
    assert checked.whole_program_gpu is False
    assert checked.pcc1_native_executed is False
    assert checked.gc_backend_parity == ()
    return checked


def _assert_device_result_manifest_round_trip(result, checked):
    if not checked.device_result_proven:
        return
    manifest_path = write_metal_source_runtime_package_manifest(result)
    manifest = verify_metal_source_runtime_package_manifest(manifest_path)

    assert manifest["status"] == "metal_source_runtime_package_executed"
    assert manifest["runtime_launch_executed"] is True
    assert manifest["runtime_source_compiled"] is True
    assert manifest["whole_program_gpu"] is False
    assert manifest["result"]["invocation"]["status"] == "metal_source_runtime_invoked"
    assert manifest["result"]["invocation"]["fence_completed"] is True
    assert manifest["result"]["cpu_comparison"]["status"] == "metal_cpu_oracle_match"
    assert manifest["result"]["finalize"]["metallib_produced"] is False
    assert "finalize.metal_source" in manifest["artifacts"]
    assert "native_buffer_runtime.library" in manifest["artifacts"]
    assert "source_bridge.library" in manifest["artifacts"]


def _assert_pcc_metal_owner(execution):
    manifest = execution.manifest
    assert manifest.requested_gpu_backend == GPU_BACKEND_PCC_METAL
    assert manifest.actual_gpu_backend == GPU_BACKEND_PCC_METAL
    assert manifest.semantic_ir_owner == "pcc-kernel-ir-tirx"
    assert manifest.codegen_owner == "pcc-metal-finalizer"
    assert manifest.runtime_owner == "pcc-metal-runtime-source"
    assert manifest.target == "metal:0"
    assert manifest.fallback_used is False
    assert manifest.launcher_links_libpython is True
    assert manifest.provider_process_links_libpython is False
    assert manifest.whole_program_gpu is False
    assert len(manifest.canonical_frozen_ir_sha256) == 64
    assert any(name.startswith("runtime.") for name in manifest.artifact_hashes)
    return execution.raw_result


def _assert_tvm_tilelang_owner(execution):
    manifest = execution.manifest
    assert manifest.requested_gpu_backend == GPU_BACKEND_TVM_TILELANG
    assert manifest.actual_gpu_backend == GPU_BACKEND_TVM_TILELANG
    assert manifest.semantic_ir_owner == "pcc-kernel-ir-tirx"
    assert manifest.codegen_owner == "pinned-tvm-tilelang-provider"
    assert manifest.runtime_owner == "pcc-metal-runtime-source"
    assert manifest.target == "metal:0"
    assert manifest.fallback_used is False
    assert manifest.launcher_links_libpython is True
    assert manifest.provider_process_links_libpython is True
    assert manifest.whole_program_gpu is False
    assert len(manifest.canonical_frozen_ir_sha256) == 64
    assert set(manifest.artifact_hashes) >= {
        "provider_metal_source",
        "pcc_abi_adapted_metal_source",
    }
    validate_gpu_owner_identity(
        manifest.to_dict(),
        requested_gpu_backend=GPU_BACKEND_TVM_TILELANG,
    )
    return execution.raw_result


def _readback_matrix(result):
    data = result.to_dict()
    return data["cpu_comparison"]["readback"]["matrix"]


def test_gpu_level4_copy_runtime_source_device_result_or_skip(tmp_path):
    if sys.platform != "darwin" and _strict_hardware():
        raise AssertionError("strict Metal hardware gate requires Darwin")

    src = ((1.0, 2.0), (3.0, 4.0))
    module = _matrix_copy_module()
    cpu = CpuReferenceResult(entry="copy_kernel", outputs={"dst": src}, tiles_executed=1, k_tiles=1)
    execution = get_gpu_backend_driver(GPU_BACKEND_PCC_METAL).execute(
        module,
        _matrix_copy_args(),
        tmp_path,
        target="metal:0",
        pipeline=PCC_METAL_SCALAR_PIPELINE,
        input_matrices={"src": src},
        cpu_reference=cpu,
        output_name="dst",
        timeout=90.0,
        launcher_links_libpython=True,
    )
    result = _assert_pcc_metal_owner(execution)

    evidence = classify_metal_source_runtime_package_result("copy", result)
    checked = _assert_claim_level_4_or_skip(evidence)
    if checked.device_result_proven:
        assert checked.metallib_produced is False
        _assert_device_result_manifest_round_trip(result, checked)


def test_gpu_level4_tilelang_gemm_runtime_source_device_result_or_skip(tmp_path):
    if sys.platform != "darwin" and _strict_hardware():
        raise AssertionError("strict Metal hardware gate requires Darwin")

    m, n, k = 5, 7, 3
    module = _tilelang_module(m=m, n=n, k=k)
    a, b = _gemm_inputs(m, n, k)
    cpu = execute_scalar_tiled_gemm_reference(module, {"A": a, "B": b})
    execution = get_gpu_backend_driver(GPU_BACKEND_PCC_METAL).execute(
        module,
        _gemm_args(m=m, n=n, k=k),
        tmp_path,
        target="metal:0",
        pipeline=PCC_METAL_SCALAR_PIPELINE,
        input_matrices={"A": a, "B": b},
        cpu_reference=cpu,
        output_name="C",
        timeout=90.0,
        launcher_links_libpython=True,
    )
    result = _assert_pcc_metal_owner(execution)

    evidence = classify_metal_source_runtime_package_result("tilelang_scalar_gemm", result)
    checked = _assert_claim_level_4_or_skip(evidence)
    if checked.device_result_proven:
        assert checked.metallib_produced is False
        _assert_device_result_manifest_round_trip(result, checked)


def test_gpu_level4_tvm_tilelang_copy_matches_pcc_metal_owner(tmp_path):
    if sys.platform != "darwin" and _strict_hardware():
        raise AssertionError("strict Metal hardware gate requires Darwin")

    src = ((1.0, 2.0), (3.0, 4.0))
    module = _matrix_copy_module()
    cpu = CpuReferenceResult(
        entry="copy_kernel",
        outputs={"dst": src},
        tiles_executed=1,
        k_tiles=1,
    )
    common = {
        "target": "metal:0",
        "input_matrices": {"src": src},
        "cpu_reference": cpu,
        "output_name": "dst",
        "timeout": 90.0,
        "launcher_links_libpython": True,
    }
    provider_execution = get_gpu_backend_driver(GPU_BACKEND_TVM_TILELANG).execute(
        module,
        _matrix_copy_args(),
        tmp_path / "tvm_tilelang",
        pipeline=TVM_TILELANG_PIPELINE,
        **common,
    )
    pcc_execution = get_gpu_backend_driver(GPU_BACKEND_PCC_METAL).execute(
        module,
        _matrix_copy_args(),
        tmp_path / "pcc_metal",
        pipeline=PCC_METAL_SCALAR_PIPELINE,
        **common,
    )
    provider_result = _assert_tvm_tilelang_owner(provider_execution)
    pcc_result = _assert_pcc_metal_owner(pcc_execution)
    provider_checked = _assert_claim_level_4_or_skip(
        classify_metal_source_runtime_package_result(
            "tvm_tilelang_copy",
            provider_result,
        )
    )
    pcc_checked = _assert_claim_level_4_or_skip(
        classify_metal_source_runtime_package_result("pcc_metal_copy", pcc_result)
    )
    if provider_checked.device_result_proven and pcc_checked.device_result_proven:
        assert _readback_matrix(provider_result) == _readback_matrix(pcc_result)


def test_gpu_level4_tvm_tilelang_gemm_matches_pcc_metal_owner(tmp_path):
    if sys.platform != "darwin" and _strict_hardware():
        raise AssertionError("strict Metal hardware gate requires Darwin")

    m = n = k = 8
    module = _simdgroup_micro_gemm_module()
    a, b = _gemm_inputs(m, n, k)
    cpu = execute_scalar_tiled_gemm_reference(module, {"A": a, "B": b})
    common = {
        "target": "metal:0",
        "input_matrices": {"A": a, "B": b},
        "cpu_reference": cpu,
        "output_name": "C",
        "timeout": 90.0,
        "launcher_links_libpython": True,
    }
    provider_execution = get_gpu_backend_driver(GPU_BACKEND_TVM_TILELANG).execute(
        module,
        _gemm_args(m=m, n=n, k=k),
        tmp_path / "tvm_tilelang",
        pipeline=TVM_TILELANG_PIPELINE,
        **common,
    )
    pcc_execution = get_gpu_backend_driver(GPU_BACKEND_PCC_METAL).execute(
        module,
        _gemm_args(m=m, n=n, k=k),
        tmp_path / "pcc_metal",
        pipeline=PCC_METAL_SCALAR_PIPELINE,
        **common,
    )
    provider_result = _assert_tvm_tilelang_owner(provider_execution)
    pcc_result = _assert_pcc_metal_owner(pcc_execution)
    provider_checked = _assert_claim_level_4_or_skip(
        classify_metal_source_runtime_package_result(
            "tvm_tilelang_gemm",
            provider_result,
        )
    )
    pcc_checked = _assert_claim_level_4_or_skip(
        classify_metal_source_runtime_package_result("pcc_metal_gemm", pcc_result)
    )
    if provider_checked.device_result_proven and pcc_checked.device_result_proven:
        assert _readback_matrix(provider_result) == _readback_matrix(pcc_result)


def test_gpu_level4_tilelang_vectorized_abc_copy_runtime_source_device_result_or_skip(tmp_path):
    if sys.platform != "darwin" and _strict_hardware():
        raise AssertionError("strict Metal hardware gate requires Darwin")

    m, n, k = 5, 7, 16
    module = _tilelang_vectorized_abc_copy_module(m=m, n=n, k=k)
    a, b = _gemm_inputs(m, n, k)
    cpu = execute_scalar_tiled_gemm_reference(module, {"A": a, "B": b})
    result = run_metal_source_runtime_package(
        module,
        _gemm_args(m=m, n=n, k=k),
        tmp_path,
        metal_source=emit_metal_source(module),
        input_matrices={"A": a, "B": b},
        cpu_reference=cpu,
        output_name="C",
        timeout=90.0,
    )

    evidence = classify_metal_source_runtime_package_result(
        "tilelang_vectorized_abc_copy",
        result,
    )
    checked = _assert_claim_level_4_or_skip(evidence)
    if checked.device_result_proven:
        assert checked.metallib_produced is False
        _assert_device_result_manifest_round_trip(result, checked)


def test_gpu_level4_tilelang_vectorized_nonzero_serial_device_result_or_skip(tmp_path):
    if sys.platform != "darwin" and _strict_hardware():
        raise AssertionError("strict Metal hardware gate requires Darwin")

    m, n, k = 5, 7, 24
    module = _tilelang_vectorized_nonzero_serial_module(m=m, n=n, k=k)
    a, b = _gemm_inputs(m, n, k)
    cpu = execute_scalar_tiled_gemm_reference(module, {"A": a, "B": b})
    result = run_metal_source_runtime_package(
        module,
        _gemm_args(m=m, n=n, k=k),
        tmp_path,
        metal_source=emit_metal_source(module),
        input_matrices={"A": a, "B": b},
        cpu_reference=cpu,
        output_name="C",
        timeout=90.0,
    )

    evidence = classify_metal_source_runtime_package_result(
        "tilelang_vectorized_nonzero_serial",
        result,
    )
    checked = _assert_claim_level_4_or_skip(evidence)
    if checked.device_result_proven:
        assert checked.metallib_produced is False
        _assert_device_result_manifest_round_trip(result, checked)


def test_gpu_level4_simdgroup_gemm_runtime_source_device_result_or_skip(tmp_path):
    if sys.platform != "darwin" and _strict_hardware():
        raise AssertionError("strict Metal hardware gate requires Darwin")

    module = _simdgroup_micro_gemm_module()
    a, b = _gemm_inputs(8, 8, 8)
    cpu = execute_scalar_tiled_gemm_reference(module, {"A": a, "B": b})
    result = run_metal_source_runtime_package(
        module,
        _gemm_args(m=8, n=8, k=8),
        tmp_path,
        metal_source=emit_metal_simdgroup_gemm_source(module),
        input_matrices={"A": a, "B": b},
        cpu_reference=cpu,
        output_name="C",
        timeout=90.0,
    )

    evidence = classify_metal_source_runtime_package_result("simdgroup_gemm_8x8", result)
    checked = _assert_claim_level_4_or_skip(evidence)
    if checked.device_result_proven:
        assert checked.metallib_produced is False
        _assert_device_result_manifest_round_trip(result, checked)


def test_gpu_level4_simdgroup_gemm_32_splitk_transpose_edge_tail_device_result_or_skip(tmp_path):
    if sys.platform != "darwin" and _strict_hardware():
        raise AssertionError("strict Metal hardware gate requires Darwin")

    m, n, k = 31, 63, 17
    module = _simdgroup_gemm_module(
        m=m,
        n=n,
        k=k,
        block_m=32,
        block_n=64,
        threads=1024,
        split_k=4,
        split_k_span_mode="ceildiv",
        output_atomic=True,
        transpose_a=True,
        transpose_b=True,
    )
    a, b = _gemm_inputs_transposed(m=m, n=n, k=k)
    cpu = execute_scalar_tiled_gemm_reference(module, {"A": a, "B": b})
    args = PccPackedArgs(launch_device="metal:0")
    args.add_buffer(PccBufferHandle(nbytes=k * m * 2, dtype="f16", device="metal:0"))
    args.add_buffer(PccBufferHandle(nbytes=n * k * 2, dtype="f16", device="metal:0"))
    args.add_buffer(PccBufferHandle(nbytes=m * n * 4, dtype="f32", device="metal:0"))
    result = run_metal_source_runtime_package(
        module,
        args,
        tmp_path,
        metal_source=emit_metal_simdgroup_gemm_source(module),
        input_matrices={"A": a, "B": b},
        cpu_reference=cpu,
        output_name="C",
        timeout=90.0,
    )

    evidence = classify_metal_source_runtime_package_result(
        "simdgroup_gemm_32_splitk_transpose_edge_tail",
        result,
    )
    checked = _assert_claim_level_4_or_skip(evidence)
    if checked.device_result_proven:
        assert checked.metallib_produced is False
        _assert_device_result_manifest_round_trip(result, checked)
