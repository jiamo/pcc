"""K-P0-TIRX-ADAPTER — freeze tile semantics into plain TIR + Metal negative.

Asserts: copy/fill/parallel/gemm freeze into plain-TIR ops with the
``plain_tir_freeze`` marker (golden), and CUDA-only assumptions are REJECTED for
a Metal target (fail fast, never silently degrade).
"""

import json

import pytest

from pcc.kernel_ir.ir import (
    BufferParam,
    KernelFunc,
    KernelModule,
    KernelOp,
    LocalBuffer,
    MemoryScope,
    ScalarType,
)
from pcc.kernel_ir.tirx_adapter import (
    PLAIN_TIR_FREEZE_MARKER,
    TirxAdapterError,
    freeze_dump,
    lower_to_plain_tir,
)


def _mod_with_ops(*ops):
    func = KernelFunc(
        name="k",
        params=(
            BufferParam("a", ScalarType.F16, rank=2),
            BufferParam("b", ScalarType.F16, rank=2),
            BufferParam("c", ScalarType.F32, rank=2),
        ),
        body=tuple(ops),
        grid=(8, 8),
        threads=128,
    )
    return KernelModule("gemm_mod", funcs=(func,))


def test_freeze_copy_fill_parallel():
    m = _mod_with_ops(
        KernelOp("parallel", ("a",), {"extent": 64}),
        KernelOp("fill", ("c",), {"value": 0}),
        KernelOp("copy", ("a", "b")),
    )
    plain = lower_to_plain_tir(m, target="metal")
    assert plain.marker == PLAIN_TIR_FREEZE_MARKER
    tir_ops = [op["tir_op"] for op in plain.funcs[0]["ops"]]
    assert tir_ops == ["tir.parallel_for", "tir.fill_loop", "tir.copy_loop"]


def test_freeze_gemm():
    m = _mod_with_ops(KernelOp("gemm", ("a", "b", "c")))
    plain = lower_to_plain_tir(m, target="metal")
    assert plain.funcs[0]["ops"][0]["tir_op"] == "tir.gemm_expand"


def test_freeze_structured_indexed_program_ops():
    func = KernelFunc(
        name="indexed",
        params=(
            BufferParam("out", ScalarType.F32, rank=1),
        ),
        body=(
            KernelOp(
                "scalar_assign",
                (),
                {
                    "target": "i",
                    "dtype": "u32",
                    "declare": True,
                    "expr": {"kind": "thread_id_x"},
                },
            ),
            KernelOp(
                "indexed_store",
                ("out", "i"),
                {
                    "index": {"kind": "name", "name": "i"},
                    "value": {"kind": "literal", "value": 1.0},
                },
            ),
        ),
    )
    plain = lower_to_plain_tir(
        KernelModule("indexed_mod", funcs=(func,)), target="metal"
    )
    assert [op["tir_op"] for op in plain.funcs[0]["ops"]] == [
        "tir.scalar_assign",
        "tir.indexed_store",
    ]


def test_freeze_preserves_device_locals():
    func = KernelFunc(
        name="scratch",
        params=(
            BufferParam("src", ScalarType.F32, rank=1),
            BufferParam("dst", ScalarType.F32, rank=1),
        ),
        locals=(
            LocalBuffer("tile", ScalarType.F32, shape=(16,), scope=MemoryScope.SHARED),
        ),
        body=(
            KernelOp("copy", ("src", "tile")),
            KernelOp("copy", ("tile", "dst")),
        ),
    )
    plain = lower_to_plain_tir(KernelModule("scratch_mod", funcs=(func,)), target="metal")

    assert plain.funcs[0]["params"][0]["name"] == "src"
    assert plain.funcs[0]["locals"] == [
        {
            "kind": "local_buffer",
            "name": "tile",
            "dtype": "f32",
            "rank": 1,
            "shape": [16],
            "scope": "shared",
            "layout": "row_major",
        }
    ]


def test_metal_rejects_cuda_only_attr():
    m = _mod_with_ops(KernelOp("gemm", ("a", "b", "c"), {"wgmma": True}))
    with pytest.raises(TirxAdapterError, match="CUDA-only"):
        lower_to_plain_tir(m, target="metal")


def test_metal_rejects_cp_async_attr():
    m = _mod_with_ops(KernelOp("copy", ("a", "b"), {"cp_async": True}))
    with pytest.raises(TirxAdapterError, match="CUDA-only"):
        lower_to_plain_tir(m, target="metal")


def test_metal_rejects_async_copy_primitive():
    m = _mod_with_ops(KernelOp("copy_async", ("a", "b")))
    with pytest.raises(TirxAdapterError, match="async"):
        lower_to_plain_tir(m, target="metal")


def test_cuda_target_permits_cuda_only_attr():
    # The same construct that is rejected on metal is fine on a cuda target:
    # the rejection is target-specific, not a blanket ban.
    m = _mod_with_ops(KernelOp("gemm", ("a", "b", "c"), {"wgmma": True}))
    plain = lower_to_plain_tir(m, target="cuda")
    assert plain.target == "cuda"
    assert plain.funcs[0]["ops"][0]["tir_op"] == "tir.gemm_expand"


def test_freeze_dump_roundtrips():
    m = _mod_with_ops(
        KernelOp("fill", ("c",), {"value": 0}),
        KernelOp("gemm", ("a", "b", "c")),
    )
    text = freeze_dump(m, target="metal")
    parsed = json.loads(text)
    assert parsed["marker"] == PLAIN_TIR_FREEZE_MARKER
    assert parsed["plain_tir"] is True
    assert parsed["target"] == "metal"
    assert freeze_dump(m, target="metal") == text  # deterministic
