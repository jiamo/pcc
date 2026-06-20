from __future__ import annotations

import pytest

from pcc.kernel_ir.cpu_reference import execute_static_indexed_reference
from pcc.kernel_ir.metal_finalize import emit_metal_source
from pcc.kernel_ir.tilelang_import import TileLangImportError, import_tilelang_source
from pcc.kernel_ir.tirx_adapter import lower_to_plain_tir


TILELANG_PARALLEL_ADD = """
import tilelang
import tilelang.language as T

@tilelang.jit
def parallel_add(M, N, threads=8):
    @T.prim_func
    def add_kernel(
        A: T.Tensor((M, N), T.float32),
        B: T.Tensor((M, N), T.float32),
        C: T.Tensor((M, N), T.float32),
    ):
        with T.Kernel(T.ceildiv(M * N, threads), threads=threads) as bx:
            for i, j in T.Parallel(M, N):
                C[i, j] = A[i, j] + B[i, j]
    return add_kernel
"""

TILELANG_VECTORIZED_SCALE = """
import tilelang
import tilelang.language as T

@tilelang.jit
def vectorized_scale(N, threads=8):
    @T.prim_func
    def scale_kernel(
        A: T.Tensor((1, N), T.float32),
        C: T.Tensor((1, N), T.float32),
    ):
        with T.Kernel(T.ceildiv(N, threads), threads=threads) as bx:
            for i in T.vectorized(N):
                C[0, i] = A[0, i] * 2.0
    return scale_kernel
"""


def _import_parallel():
    return import_tilelang_source(
        TILELANG_PARALLEL_ADD,
        outer_function="parallel_add",
        prim_func="add_kernel",
        constants={"M": 3, "N": 5, "threads": 8},
    )


def _import_vectorized():
    return import_tilelang_source(
        TILELANG_VECTORIZED_SCALE,
        outer_function="vectorized_scale",
        prim_func="scale_kernel",
        constants={"N": 7, "threads": 4},
    )


@pytest.mark.parametrize(
    ("module_factory", "loop_kind", "extent", "statement"),
    [
        (_import_parallel, "T.Parallel", 15, "C[gid] = (A[gid] + B[gid]);"),
        (_import_vectorized, "T.vectorized", 7, "C[gid] = (A[gid] * 2.0);"),
    ],
)
def test_scheduled_assignment_reuses_indexed_ir_and_metal_lowering(
    module_factory, loop_kind: str, extent: int, statement: str
):
    module = module_factory()
    assert [op.op for op in module.funcs[0].body] == ["parallel", "indexed_store"]
    assert module.funcs[0].body[0].attrs == {
        "extent": extent,
        "loop_kind": loop_kind,
        "loop_extents": [3, 5] if loop_kind == "T.Parallel" else [7],
    }
    plain = lower_to_plain_tir(module, target="metal")
    assert [op["tir_op"] for op in plain.funcs[0]["ops"]] == [
        "tir.parallel_for",
        "tir.indexed_store",
    ]
    source = emit_metal_source(plain)
    assert f"if (gid >= {extent}u)" in source
    assert statement in source


def test_parallel_add_and_vectorized_scale_cpu_oracles():
    a = tuple(tuple(float(row * 5 + col) for col in range(5)) for row in range(3))
    b = tuple(tuple(float(20 - row - col) for col in range(5)) for row in range(3))
    add = execute_static_indexed_reference(_import_parallel(), {"A": a, "B": b})
    assert add.outputs["C"] == tuple(
        tuple(a[row][col] + b[row][col] for col in range(5)) for row in range(3)
    )
    vector = ((-2.0, -1.5, 0.0, 1.25, 2.5, 3.0, 9.0),)
    scale = execute_static_indexed_reference(_import_vectorized(), {"A": vector})
    assert scale.outputs["C"] == (tuple(value * 2.0 for value in vector[0]),)


@pytest.mark.parametrize(
    "bad_statement",
    [
        "C[0, 0] = A[0, 0] + B[0, 0]",
        "C[j, i] = A[j, i] + B[j, i]",
        "C[i, j] = max(A[i, j], B[i, j])",
    ],
)
def test_noncanonical_parallel_bodies_remain_fail_closed(bad_statement: str):
    source = TILELANG_PARALLEL_ADD.replace(
        "C[i, j] = A[i, j] + B[i, j]",
        bad_statement,
    )
    with pytest.raises(TileLangImportError, match="canonical indexed assignment"):
        import_tilelang_source(
            source,
            outer_function="parallel_add",
            prim_func="add_kernel",
            constants={"M": 3, "N": 5, "threads": 8},
        )
