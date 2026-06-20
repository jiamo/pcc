"""K-P0-METAL-TVM-FINALIZE — descriptor/golden + SKIPPED_WITH_REASON.

Asserts: the finalize emits inspectable packaging descriptors, degrades to
SKIPPED_WITH_REASON when Xcode Metal tooling is absent, and NEVER claims a host
launch or a produced .metallib.
"""

import json

import pytest

from pcc.kernel_ir.ir import BufferParam, KernelFunc, KernelModule, KernelOp, ScalarType
from pcc.kernel_ir.metal_finalize import (
    STATUS_DESCRIPTOR_ONLY,
    STATUS_SKIPPED_WITH_REASON,
    STATUS_SOURCE_ONLY,
    MetalFinalizeError,
    finalize_dump,
    finalize_metal,
)
from pcc.kernel_ir.tirx_adapter import lower_to_plain_tir


def _module():
    func = KernelFunc(
        name="gemm_kernel",
        params=(
            BufferParam("a", ScalarType.F16, rank=2),
            BufferParam("b", ScalarType.F16, rank=2),
            BufferParam("c", ScalarType.F32, rank=2),
        ),
        body=(KernelOp("gemm", ("a", "b", "c")),),
        grid=(8, 8),
        threads=128,
    )
    return KernelModule("gemm_mod", funcs=(func,))


def _main_copy_module():
    func = KernelFunc(
        name="main",
        params=(
            BufferParam("src", ScalarType.F32, rank=1),
            BufferParam("dst", ScalarType.F32, rank=1),
        ),
        body=(
            KernelOp("parallel", ("src", "dst"), {"extent": 8}),
            KernelOp("copy", ("src", "dst")),
        ),
        grid=(1,),
        threads=8,
    )
    return KernelModule("main_mod", funcs=(func,))


def test_skipped_when_toolchain_absent():
    result = finalize_metal(_module(), toolchain_available=False)
    assert result.status == STATUS_SKIPPED_WITH_REASON
    assert result.skipped is True
    assert "not found" in result.reason
    # A descriptor is still produced for inspection.
    assert result.descriptor is not None
    assert result.descriptor.entry_points == ["gemm_kernel"]


def test_descriptor_only_when_toolchain_present():
    result = finalize_metal(_module(), toolchain_available=True)
    assert result.status == STATUS_DESCRIPTOR_ONLY
    assert result.skipped is False
    # Even with a toolchain, this slice does not produce a real .metallib.
    d = result.to_dict()
    assert d["metallib_produced"] is False
    assert d["host_launch_claimed"] is False


def test_descriptor_packaging_steps():
    result = finalize_metal(_module(), toolchain_available=False)
    steps = [s["step"] for s in result.descriptor.steps]
    assert steps == ["emit_metal_source", "compile_to_air", "package_metallib"]
    arts = result.descriptor.to_dict()["artifacts"]
    assert arts["metal_source"] == "gemm_mod.metal"
    assert arts["air"] == "gemm_mod.air"
    assert arts["metallib"] == "gemm_mod.metallib"


def test_metal_entry_name_legalizes_logical_main(tmp_path):
    result = finalize_metal(
        _main_copy_module(),
        toolchain_available=True,
        artifact_dir=tmp_path,
        compile_toolchain=False,
    )

    assert result.status == STATUS_SOURCE_ONLY
    assert result.descriptor.entry_points == ["pcc_main_kernel"]
    assert "kernel void pcc_main_kernel(" in result.metal_source
    assert "kernel void main(" not in result.metal_source


def test_never_claims_launch_or_metallib():
    for avail in (True, False):
        d = finalize_metal(_module(), toolchain_available=avail).to_dict()
        assert d["host_launch_claimed"] is False
        assert d["metallib_produced"] is False


def test_accepts_prefrozen_plain_tir_module():
    plain = lower_to_plain_tir(_module(), target="metal")
    result = finalize_metal(plain, toolchain_available=False)
    assert result.descriptor.entry_points == ["gemm_kernel"]


def test_rejects_non_metal_plain_tir():
    plain = lower_to_plain_tir(_module(), target="cuda")
    with pytest.raises(MetalFinalizeError, match="requires target 'metal'"):
        finalize_metal(plain, toolchain_available=False)


def test_finalize_dump_roundtrips():
    text = finalize_dump(_module(), toolchain_available=False)
    parsed = json.loads(text)
    assert parsed["status"] == STATUS_SKIPPED_WITH_REASON
    assert parsed["metallib_produced"] is False
    assert finalize_dump(_module(), toolchain_available=False) == text
