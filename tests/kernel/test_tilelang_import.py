"""TileLang Python-DSL subset import into pcc Kernel IR."""

import pytest

from pcc.kernel_ir.host_device_split import build_host_launch_boundaries
from pcc.kernel_ir.ir import (
    BufferParam,
    KernelFunc,
    KernelModule,
    KernelOp,
    Layout,
    LocalBuffer,
    MemoryScope,
    ScalarType,
    dump_kernel,
)
from pcc.kernel_ir.metal_finalize import (
    MetalFinalizeError,
    STATUS_SOURCE_ONLY,
    emit_metal_source,
    finalize_metal,
)
from pcc.kernel_ir.tilelang_import import TileLangImportError, import_tilelang_source
from pcc.kernel_ir.tirx_adapter import lower_to_plain_tir
from pcc.kernel_ir.tvm_oracle import project_to_tir_shape


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


def _import_matmul():
    return import_tilelang_source(
        TILELANG_METAL_MATMUL,
        outer_function="matmul_simdgroup",
        prim_func="gemm_kernel",
        constants={"M": 128, "N": 256, "K": 64},
        module_name="tilelang_metal_matmul",
    )


def test_imports_tilelang_metal_matmul_shape_into_kernel_ir():
    module = _import_matmul()
    func = module.funcs[0]

    assert module.name == "tilelang_metal_matmul"
    assert func.name == "gemm_kernel"
    assert func.grid == (4, 2)
    assert func.threads == 128
    assert [(p.name, p.dtype, p.rank, p.shape) for p in func.params] == [
        ("A", ScalarType.F16, 2, (128, 64)),
        ("B", ScalarType.F16, 2, (64, 256)),
        ("C", ScalarType.F32, 2, (128, 256)),
    ]
    assert [(l.name, l.dtype, l.shape, l.scope, l.layout) for l in func.locals] == [
        ("A_shared", ScalarType.F16, (64, 32), MemoryScope.SHARED, Layout.TILE),
        ("B_shared", ScalarType.F16, (32, 64), MemoryScope.SHARED, Layout.TILE),
        ("C_local", ScalarType.F32, (64, 64), MemoryScope.FRAGMENT, Layout.TILE),
    ]
    assert [(op.op, op.args) for op in func.body] == [
        ("fill", ("C_local",)),
        ("copy", ("A", "A_shared")),
        ("copy", ("B", "B_shared")),
        ("gemm", ("A_shared", "B_shared", "C_local")),
        ("copy", ("C_local", "C")),
    ]
    assert func.body[1].attrs == {"pipeline_extent": 2, "num_stages": 0}
    assert "PyObject" not in dump_kernel(module)


def test_imported_tilelang_matmul_freezes_to_plain_tir_and_keeps_shapes():
    module = _import_matmul()
    plain = lower_to_plain_tir(module, target="metal")
    fn = plain.funcs[0]

    assert [op["tir_op"] for op in fn["ops"]] == [
        "tir.fill_loop",
        "tir.copy_loop",
        "tir.copy_loop",
        "tir.gemm_expand",
        "tir.copy_loop",
    ]
    assert fn["params"][0]["shape"] == [128, 64]
    assert fn["locals"][2]["scope"] == "fragment"
    assert fn["locals"][2]["shape"] == [64, 64]


def test_disabled_use_swizzle_survives_import_and_plain_tir_freeze():
    source = TILELANG_METAL_MATMUL.replace(
        "T.clear(C_local)",
        "T.use_swizzle(panel_size=10, enable=False)\n            T.clear(C_local)",
    )
    module = import_tilelang_source(
        source,
        outer_function="matmul_simdgroup",
        prim_func="gemm_kernel",
        constants={"M": 128, "N": 256, "K": 64},
        module_name="tilelang_swizzle_disabled",
    )
    swizzle = module.funcs[0].body[0]

    assert swizzle.op == "swizzle"
    assert swizzle.attrs == {"panel_size": 10, "enable": False}

    plain = lower_to_plain_tir(module, target="metal")
    assert plain.funcs[0]["ops"][0] == {
        "tir_op": "tir.use_swizzle",
        "args": [],
        "attrs": {"panel_size": 10, "enable": False},
    }


def test_empty_annotate_layout_survives_import_and_plain_tir_freeze():
    source = TILELANG_METAL_MATMUL.replace(
        "T.clear(C_local)",
        "T.annotate_layout({})\n            T.clear(C_local)",
    )
    module = import_tilelang_source(
        source,
        outer_function="matmul_simdgroup",
        prim_func="gemm_kernel",
        constants={"M": 128, "N": 256, "K": 64},
        module_name="tilelang_annotate_layout_empty",
    )
    annotation = module.funcs[0].body[0]

    assert annotation.op == "layout_annotation"
    assert annotation.attrs == {"entries": 0}

    plain = lower_to_plain_tir(module, target="metal")
    assert plain.funcs[0]["ops"][0] == {
        "tir_op": "tir.annotate_layout",
        "args": [],
        "attrs": {"entries": 0},
    }


def test_swizzled_annotate_layout_updates_local_buffer_layout_metadata():
    source = TILELANG_METAL_MATMUL.replace(
        "T.clear(C_local)",
        (
            "T.annotate_layout({\n"
            "                A_shared: tilelang.layout.make_swizzled_layout(A_shared),\n"
            "                B_shared: make_swizzled_layout(B_shared),\n"
            "            })\n"
            "            T.clear(C_local)"
        ),
    )
    module = import_tilelang_source(
        source,
        outer_function="matmul_simdgroup",
        prim_func="gemm_kernel",
        constants={"M": 128, "N": 256, "K": 64},
        module_name="tilelang_annotate_layout_swizzled",
    )
    func = module.funcs[0]

    assert [(local.name, local.layout) for local in func.locals] == [
        ("A_shared", Layout.SWIZZLED),
        ("B_shared", Layout.SWIZZLED),
        ("C_local", Layout.TILE),
    ]
    assert func.body[0].op == "fill"

    plain = lower_to_plain_tir(module, target="metal")
    assert [local["layout"] for local in plain.funcs[0]["locals"]] == [
        "swizzled",
        "swizzled",
        "tile",
    ]
    proj = project_to_tir_shape(module)
    assert [local["layout"] for local in proj["functions"][0]["alloc_buffers"]] == [
        "swizzled",
        "swizzled",
        "tile",
    ]


def test_imported_tilelang_matmul_projects_to_tvm_shape_with_buffer_shapes():
    proj = project_to_tir_shape(_import_matmul())
    fn = proj["functions"][0]

    assert fn["buffer_map"][0]["buffer"] == {
        "name": "A",
        "dtype": "float16",
        "ndim": 2,
        "scope": "global",
        "shape": [128, 64],
    }
    assert fn["alloc_buffers"] == [
        {"name": "A_shared", "dtype": "float16", "shape": [64, 32], "scope": "shared", "layout": "tile"},
        {"name": "B_shared", "dtype": "float16", "shape": [32, 64], "scope": "shared", "layout": "tile"},
        {"name": "C_local", "dtype": "float32", "shape": [64, 64], "scope": "fragment", "layout": "tile"},
    ]


def test_imported_tilelang_matmul_host_boundary_has_only_global_params():
    proof = build_host_launch_boundaries(_import_matmul(), host="self", device="metal")
    launch = proof.to_dict()["launches"][0]

    assert [arg["name"] for arg in launch["arg_bindings"]] == ["A", "B", "C"]
    assert [arg["rank"] for arg in launch["arg_bindings"]] == [2, 2, 2]
    assert [local["name"] for local in launch["device_locals"]] == [
        "A_shared",
        "B_shared",
        "C_local",
    ]
    assert launch["runtime_launch_executed"] is False


def test_imported_tilelang_matmul_emits_scalar_tiled_metal_gemm_source():
    source = emit_metal_source(_import_matmul())

    assert "kernel void gemm_kernel" in source
    assert "const device half* A [[buffer(0)]]" in source
    assert "const device half* B [[buffer(1)]]" in source
    assert "device float* C [[buffer(2)]]" in source
    assert "uint3 tid3 [[thread_position_in_threadgroup]]" in source
    assert "uint3 tgid [[threadgroup_position_in_grid]]" in source
    assert "uint tid = tid3.x;" in source
    assert "threadgroup half A_shared[2048];" in source
    assert "threadgroup half B_shared[2048];" in source
    assert "for (uint tile_linear_base = 0u; tile_linear_base < 4096u; tile_linear_base += 128u)" in source
    assert "for (uint ko = 0u; ko < 2u; ++ko)" in source
    assert "A_shared[a_shared_idx] = (a_row < 128u && a_col < 64u) ? A[(a_row * 64u) + a_col] : half(0.0);" in source
    assert "B_shared[b_shared_idx] = (b_row < 64u && b_col < 256u) ? B[(b_row * 256u) + b_col] : half(0.0);" in source
    assert "threadgroup_barrier(mem_flags::mem_threadgroup);" in source
    assert "acc += float(A_shared[((local_m * 32u) + kk)]) * float(B_shared[((kk * 64u) + local_n)]);" in source
    assert "C[(row * 256u) + col] = (float)acc;" in source
    assert "simdgroup" not in source
    assert "PyObject" not in source


def test_imported_tilelang_matmul_finalize_writes_metal_source_artifact(tmp_path):
    result = finalize_metal(_import_matmul(), artifact_dir=tmp_path, compile_toolchain=False)

    assert result.status == STATUS_SOURCE_ONLY
    assert result.metal_source_produced is True
    assert result.metallib_produced is False
    path = tmp_path / "tilelang_metal_matmul.metal"
    assert path.is_file()
    source = path.read_text(encoding="utf-8")
    assert "kernel void gemm_kernel" in source
    assert "threadgroup half A_shared[2048];" in source


def test_unshaped_gemm_stays_rejected_for_metal_source():
    module = KernelModule(
        "unshaped_gemm",
        funcs=(
            KernelFunc(
                name="gemm_kernel",
                params=(
                    BufferParam("A", ScalarType.F16, rank=2),
                    BufferParam("B", ScalarType.F16, rank=2),
                    BufferParam("C", ScalarType.F32, rank=2),
                ),
                locals=(
                    LocalBuffer("A_shared", ScalarType.F16, shape=(64, 32), scope=MemoryScope.SHARED, layout=Layout.TILE),
                    LocalBuffer("B_shared", ScalarType.F16, shape=(32, 64), scope=MemoryScope.SHARED, layout=Layout.TILE),
                    LocalBuffer("C_local", ScalarType.F32, shape=(64, 64), scope=MemoryScope.FRAGMENT, layout=Layout.TILE),
                ),
                body=(
                    KernelOp("fill", ("C_local",), {"value": 0}),
                    KernelOp("copy", ("A", "A_shared"), {"pipeline_extent": 2, "num_stages": 0}),
                    KernelOp("copy", ("B", "B_shared"), {"pipeline_extent": 2, "num_stages": 0}),
                    KernelOp("gemm", ("A_shared", "B_shared", "C_local"), {"pipeline_extent": 2, "num_stages": 0}),
                    KernelOp("copy", ("C_local", "C")),
                ),
                grid=(1, 1),
                threads=128,
            ),
        ),
    )

    with pytest.raises(MetalFinalizeError, match="requires static shape metadata"):
        emit_metal_source(module)


def test_importer_rejects_unknown_tilelang_constructs():
    source = TILELANG_METAL_MATMUL.replace(
        "T.clear(C_local)",
        "T.some_future_intrinsic()",
    )
    with pytest.raises(TileLangImportError, match="not supported"):
        import_tilelang_source(
            source,
            outer_function="matmul_simdgroup",
            prim_func="gemm_kernel",
            constants={"M": 128, "N": 256, "K": 64},
        )


def test_importer_rejects_nonempty_annotate_layout():
    source = TILELANG_METAL_MATMUL.replace(
        "T.clear(C_local)",
        "T.annotate_layout({A_shared: B_shared})",
    )
    with pytest.raises(TileLangImportError, match="make_swizzled_layout"):
        import_tilelang_source(
            source,
            outer_function="matmul_simdgroup",
            prim_func="gemm_kernel",
            constants={"M": 128, "N": 256, "K": 64},
        )


def test_importer_rejects_mismatched_swizzled_layout_target():
    source = TILELANG_METAL_MATMUL.replace(
        "T.clear(C_local)",
        "T.annotate_layout({A_shared: tilelang.layout.make_swizzled_layout(B_shared)})",
    )
    with pytest.raises(TileLangImportError, match="does not match"):
        import_tilelang_source(
            source,
            outer_function="matmul_simdgroup",
            prim_func="gemm_kernel",
            constants={"M": 128, "N": 256, "K": 64},
        )


def test_importer_requires_static_shape_constants():
    with pytest.raises(TileLangImportError, match="unknown symbolic value 'M'"):
        import_tilelang_source(
            TILELANG_METAL_MATMUL,
            outer_function="matmul_simdgroup",
            prim_func="gemm_kernel",
            constants={"N": 256, "K": 64},
        )
