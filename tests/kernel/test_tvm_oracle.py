"""K-P0-TVM-CXX-ORACLE — one bounded TVM TIR-shape seam + golden compare."""

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
from pcc.kernel_ir.tvm_oracle import (
    matches_oracle,
    project_to_tir_shape,
    tir_shape_dump,
)


def _module():
    func = KernelFunc(
        name="saxpy",
        params=(
            ScalarParam("alpha", ScalarType.F32),
            BufferParam("x", ScalarType.F32, rank=1, scope=MemoryScope.GLOBAL),
        ),
        body=(KernelOp("copy", ("x",)),),
    )
    return KernelModule("saxpy_mod", funcs=(func,))


def test_projection_matches_hand_written_oracle():
    # This hand-written oracle IS the TVM TIR-object-shape reference. If pcc's
    # projection drifts from the documented shape, this test fails.
    oracle = {
        "ir_module": "saxpy_mod",
        "tir_object_shape_version": 1,
        "functions": [
            {
                "prim_func": "saxpy",
                "params": [
                    {"var": "alpha", "dtype": "float32"},
                    {"var": "x", "dtype": "handle"},
                ],
                "buffer_map": [
                    {
                        "var": "x",
                        "buffer": {
                            "name": "x",
                            "dtype": "float32",
                            "ndim": 1,
                            "scope": "global",
                        },
                    }
                ],
                "body": [{"stmt": "copy", "args": ["x"]}],
            }
        ],
    }
    assert project_to_tir_shape(_module()) == oracle
    assert matches_oracle(_module(), oracle)


def test_scalar_param_becomes_tir_var_not_handle():
    proj = project_to_tir_shape(_module())
    params = proj["functions"][0]["params"]
    assert params[0] == {"var": "alpha", "dtype": "float32"}
    # buffer param becomes a "handle" var + a buffer_map entry
    assert params[1]["dtype"] == "handle"


def test_device_local_buffers_project_to_tir_alloc_buffers():
    func = KernelFunc(
        name="scratch",
        params=(
            BufferParam("src", ScalarType.F32, rank=1, scope=MemoryScope.GLOBAL),
            BufferParam("dst", ScalarType.F32, rank=1, scope=MemoryScope.GLOBAL),
        ),
        locals=(
            LocalBuffer("tile", ScalarType.F32, shape=(16,), scope=MemoryScope.SHARED),
        ),
        body=(
            KernelOp("copy", ("src", "tile")),
            KernelOp("copy", ("tile", "dst")),
        ),
    )
    proj = project_to_tir_shape(KernelModule("scratch_mod", funcs=(func,)))
    fn = proj["functions"][0]

    assert [p["var"] for p in fn["params"]] == ["src", "dst"]
    assert fn["alloc_buffers"] == [
        {
            "name": "tile",
            "dtype": "float32",
            "shape": [16],
            "scope": "shared",
            "layout": "row_major",
        }
    ]


def test_dtype_mapping_uses_tvm_spelling():
    m = KernelModule(
        "m",
        funcs=(
            KernelFunc(
                name="k",
                params=(
                    ScalarParam("i", ScalarType.I64),
                    ScalarParam("b", ScalarType.BOOL),
                ),
                body=(KernelOp("fill", ()),),
            ),
        ),
    )
    proj = project_to_tir_shape(m)
    dtypes = [p["dtype"] for p in proj["functions"][0]["params"]]
    assert dtypes == ["int64", "bool"]  # TVM spellings, not pcc tags


def test_dump_roundtrips():
    text = tir_shape_dump(_module())
    assert tir_shape_dump(_module()) == text
