from __future__ import annotations

import struct

import pytest

from pcc.kernel_ir.cpu_reference import (
    KernelCpuReferenceError,
    execute_static_fill_reference,
)
from pcc.kernel_ir.metal_finalize import MetalFinalizeError, emit_metal_source
from pcc.kernel_ir.tilelang_import import TileLangImportError, import_tilelang_source
from pcc.kernel_ir.tirx_adapter import lower_to_plain_tir


TILELANG_STATIC_FILL = """
import tilelang
import tilelang.language as T

@tilelang.jit
def fill_matrix(M, N, value=1.25, dtype=T.float32, threads=32):
    @T.prim_func
    def fill_kernel(C: T.Tensor((M, N), dtype)):
        with T.Kernel(T.ceildiv(M * N, threads), threads=threads) as bx:
            T.fill(C, value)
    return fill_kernel
"""


def _module(*, dtype: str, value: int | float):
    return import_tilelang_source(
        TILELANG_STATIC_FILL,
        outer_function="fill_matrix",
        prim_func="fill_kernel",
        constants={"M": 3, "N": 5, "dtype": dtype, "value": value, "threads": 8},
        module_name=f"tilelang_fill_{dtype}",
    )


@pytest.mark.parametrize(
    ("dtype", "value", "expected", "literal"),
    [
        ("float32", -3.25, -3.25, "float(-3.25)"),
        (
            "float16",
            1.1,
            struct.unpack("<e", struct.pack("<e", 1.1))[0],
            "half(1.099609375)",
        ),
    ],
)
def test_nonzero_fill_has_dtype_correct_ir_source_and_cpu_oracle(
    dtype: str, value: float, expected: float, literal: str
):
    module = _module(dtype=dtype, value=value)
    fill = module.funcs[0].body[0]
    assert fill.op == "fill"
    assert fill.attrs["value"] == value
    plain = lower_to_plain_tir(module, target="metal")
    assert plain.funcs[0]["ops"][0]["tir_op"] == "tir.fill_loop"
    source = emit_metal_source(plain)
    assert f"C[gid] = {literal};" in source
    assert "if (gid >= 15u)" in source
    oracle = execute_static_fill_reference(plain)
    assert oracle.outputs["C"] == tuple(tuple(expected for _ in range(5)) for _ in range(3))


@pytest.mark.parametrize(
    ("dtype", "value", "message"),
    [
        ("int8", 128, "outside"),
        ("uint16", -1, "outside"),
        ("float16", 1e100, "representable|non-finite"),
    ],
)
def test_unrepresentable_fill_fails_before_device_execution(
    dtype: str, value: int | float, message: str
):
    module = _module(dtype=dtype, value=value)
    with pytest.raises(MetalFinalizeError, match=message):
        emit_metal_source(module)
    with pytest.raises(KernelCpuReferenceError, match=message):
        execute_static_fill_reference(module)


def test_fill_keywords_and_nonstatic_values_remain_fail_closed():
    keyword = TILELANG_STATIC_FILL.replace("T.fill(C, value)", "T.fill(C, value=1)")
    with pytest.raises(TileLangImportError, match="keyword"):
        import_tilelang_source(
            keyword,
            outer_function="fill_matrix",
            prim_func="fill_kernel",
            constants={"M": 3, "N": 5},
        )
