"""Bounded static TileLang ``T.reduce_sum`` -> Kernel IR -> Metal source.

These are source/CPU-oracle tests for the pcc-owned TileLang-shaped subset.
They do not execute the upstream TileLang runtime and do not claim a device
result; the dedicated hardware test owns that higher claim boundary.
"""

from __future__ import annotations

import pytest

from pcc.kernel_ir.cpu_reference import execute_static_row_reduce_sum_reference
from pcc.kernel_ir.ir import Layout, MemoryScope, ScalarType
from pcc.kernel_ir.metal_finalize import MetalFinalizeError, emit_metal_source
from pcc.kernel_ir.tilelang_compat import Support, classify
from pcc.kernel_ir.tilelang_import import (
    TILELANG_REDUCE_SUM_REFERENCE_COMMIT,
    TILELANG_REDUCE_SUM_REFERENCE_PATH,
    TILELANG_REDUCE_SUM_REFERENCE_SHA256,
    TILELANG_SOURCE_SUBSET_CLAIM,
    TileLangImportError,
    import_tilelang_source,
    tilelang_source_import_claim_of,
)
from pcc.kernel_ir.tirx_adapter import lower_to_plain_tir


TILELANG_STATIC_ROW_REDUCE_SUM = """
import tilelang
import tilelang.language as T

@tilelang.jit
def row_reduce_sum(rows=3, width=5, dtype=T.float32, accum_dtype=T.float32):
    @T.prim_func
    def reduce_sum_kernel(
        A: T.Tensor((rows, width), dtype),
        Out: T.Tensor((rows, 1), accum_dtype),
    ):
        with T.Kernel(rows, threads=width) as row:
            T.reduce_sum(A, Out, dim=-1, clear=True, batch=1)
    return reduce_sum_kernel
"""


def _import_reduce_sum(**constants: object):
    values = {
        "rows": 3,
        "width": 5,
        "dtype": "float32",
        "accum_dtype": "float32",
    }
    values.update(constants)
    return import_tilelang_source(
        TILELANG_STATIC_ROW_REDUCE_SUM,
        outer_function="row_reduce_sum",
        prim_func="reduce_sum_kernel",
        constants=values,
        module_name="tilelang_static_row_reduce_sum",
    )


def test_tilelang_reduce_sum_is_an_explicit_bounded_compat_construct():
    info = classify("T.reduce_sum")

    assert info.support is Support.ACCEPTED
    assert info.pcc_construct == "KernelOp('reduce', reduction='sum')"
    assert "bounded static" in info.reason
    assert classify("T.reduce_max").support is Support.REJECTED
    assert TILELANG_REDUCE_SUM_REFERENCE_COMMIT == "dff136d4da552389b0a41f394edfa1a9fe47a590"
    assert TILELANG_REDUCE_SUM_REFERENCE_PATH == "tilelang/language/reduce_op.py"
    assert TILELANG_REDUCE_SUM_REFERENCE_SHA256 == (
        "cc1a7f2d9f67bf40378934416d214c42e8241ff22143377f8fd392d0c314b8c1"
    )


def test_import_expands_static_row_reduce_sum_to_explicit_shared_scratch():
    module = _import_reduce_sum()
    func = module.funcs[0]

    assert tilelang_source_import_claim_of(module)["mode"] == TILELANG_SOURCE_SUBSET_CLAIM
    assert func.grid == (3,)
    assert func.threads == 5
    assert [(param.name, param.dtype, param.shape) for param in func.params] == [
        ("A", ScalarType.F32, (3, 5)),
        ("Out", ScalarType.F32, (3, 1)),
    ]
    assert len(func.locals) == 1
    scratch = func.locals[0]
    assert scratch.name == "__pcc_reduce_sum_scratch_0"
    assert scratch.dtype is ScalarType.F32
    assert scratch.shape == (5,)
    assert scratch.scope is MemoryScope.SHARED
    assert scratch.layout is Layout.TILE
    assert [(op.op, op.args) for op in func.body] == [
        ("reduce", ("A", scratch.name)),
        ("copy", (scratch.name, "Out")),
    ]
    assert func.body[0].attrs == {
        "reduction": "sum",
        "dim": 1,
        "clear": True,
        "batch": 1,
        "extent": 15,
        "row_count": 3,
        "row_width": 5,
        "source_shape": [3, 5],
        "output_shape": [3, 1],
        "import_kind": "tilelang.reduce_sum.static_row.v1",
    }
    assert func.body[1].attrs == {"reduction_output": True}


def test_imported_reduce_sum_freezes_and_emits_threadgroup_metal_source():
    module = _import_reduce_sum()
    plain = lower_to_plain_tir(module, target="metal")

    assert [op["tir_op"] for op in plain.funcs[0]["ops"]] == [
        "tir.reduce_loop",
        "tir.copy_loop",
    ]
    source = emit_metal_source(module)
    assert "kernel void reduce_sum_kernel" in source
    assert "const device float* A [[buffer(0)]]" in source
    assert "device float* Out [[buffer(1)]]" in source
    assert "threadgroup float __pcc_reduce_sum_scratch_0[5];" in source
    assert (
        "__pcc_reduce_sum_scratch_0[tid] = (gid < 15u) ? A[gid] : 0.0;"
        in source
    )
    assert "for (uint active = 5u; active > 1u;" in source
    assert "__pcc_reduce_sum_scratch_0[tid] += __pcc_reduce_sum_scratch_0[partner];" in source
    assert "Out[tgid] = __pcc_reduce_sum_scratch_0[0];" in source
    assert "PyObject" not in source


@pytest.mark.parametrize("drift", ["metadata", "output_shape", "copy_marker"])
def test_metal_finalizer_rejects_frozen_row_reduce_sum_contract_drift(drift: str):
    plain = lower_to_plain_tir(_import_reduce_sum(), target="metal")
    if drift == "metadata":
        plain.funcs[0]["ops"][0]["attrs"]["row_width"] = 4
    elif drift == "output_shape":
        plain.funcs[0]["params"][1]["shape"] = [3, 2]
    else:
        plain.funcs[0]["ops"][1]["attrs"] = {}

    with pytest.raises(MetalFinalizeError, match="imported row reduce_sum"):
        emit_metal_source(plain)


def test_static_row_reduce_sum_cpu_oracle_matches_rows():
    module = _import_reduce_sum(dtype="float16")
    inputs = {
        "A": (
            (1.0, -2.0, 3.0, 4.0, -1.0),
            (0.5, 0.25, -0.75, 2.0, 1.0),
            (-4.0, -3.0, -2.0, -1.0, 10.0),
        )
    }

    result = execute_static_row_reduce_sum_reference(module, inputs)

    assert result.outputs == {"Out": ((5.0,), (3.0,), (0.0,))}
    assert result.tiles_executed == 3
    assert result.runtime_launch_executed is False
    assert "CPU oracle" in result.claim_mode


def test_reduce_sum_accepts_keyword_output_without_running_tilelang():
    source = TILELANG_STATIC_ROW_REDUCE_SUM.replace(
        "T.reduce_sum(A, Out, dim=-1, clear=True, batch=1)",
        "T.reduce_sum(A, out=Out, dim=1)",
    )

    module = import_tilelang_source(
        source,
        outer_function="row_reduce_sum",
        prim_func="reduce_sum_kernel",
        constants={"rows": 2, "width": 4},
    )

    assert module.funcs[0].body[0].attrs["import_kind"] == "tilelang.reduce_sum.static_row.v1"


@pytest.mark.parametrize(
    ("source", "constants", "message"),
    [
        (
            TILELANG_STATIC_ROW_REDUCE_SUM.replace("dim=-1", "dim=0"),
            {"rows": 3, "width": 5},
            "only the last dimension",
        ),
        (
            TILELANG_STATIC_ROW_REDUCE_SUM.replace("clear=True", "clear=False"),
            {"rows": 3, "width": 5},
            "requires clear=True",
        ),
        (
            TILELANG_STATIC_ROW_REDUCE_SUM.replace("batch=1", "batch=2"),
            {"rows": 3, "width": 5},
            "requires batch=1",
        ),
        (
            TILELANG_STATIC_ROW_REDUCE_SUM.replace(
                "with T.Kernel(rows, threads=width)",
                "with T.Kernel(rows, threads=4)",
            ),
            {"rows": 3, "width": 5},
            "requires threads=5",
        ),
        (
            TILELANG_STATIC_ROW_REDUCE_SUM.replace(
                "with T.Kernel(rows, threads=width)",
                "with T.Kernel(rows + 1, threads=width)",
            ),
            {"rows": 3, "width": 5},
            "requires grid=\\(3,\\)",
        ),
        (
            TILELANG_STATIC_ROW_REDUCE_SUM.replace(
                "Out: T.Tensor((rows, 1), accum_dtype)",
                "Out: T.Tensor((rows, 2), accum_dtype)",
            ),
            {"rows": 3, "width": 5},
            r"output shape must be \(3, 1\)",
        ),
        (
            TILELANG_STATIC_ROW_REDUCE_SUM,
            {"rows": 3, "width": 5, "accum_dtype": "float16"},
            "output dtype must be float32",
        ),
        (
            TILELANG_STATIC_ROW_REDUCE_SUM,
            {"rows": 3, "width": 5, "dtype": "int32"},
            "input dtype must be float16 or float32",
        ),
        (
            TILELANG_STATIC_ROW_REDUCE_SUM.replace(
                "T.reduce_sum(A, Out, dim=-1, clear=True, batch=1)",
                "T.reduce_sum(A[0, 0], Out, dim=-1, clear=True, batch=1)",
            ),
            {"rows": 3, "width": 5},
            "requires whole-buffer input/output names",
        ),
        (
            TILELANG_STATIC_ROW_REDUCE_SUM.replace(
                "T.reduce_sum(A, Out, dim=-1, clear=True, batch=1)",
                "T.reduce_max(A, Out, dim=-1, clear=True, batch=1)",
            ),
            {"rows": 3, "width": 5},
            "only bounded static last-dimension T.reduce_sum",
        ),
    ],
)
def test_static_row_reduce_sum_fails_closed_outside_finite_contract(
    source: str,
    constants: dict[str, object],
    message: str,
):
    with pytest.raises(TileLangImportError, match=message):
        import_tilelang_source(
            source,
            outer_function="row_reduce_sum",
            prim_func="reduce_sum_kernel",
            constants=constants,
        )
