from __future__ import annotations

import pytest

from pcc.kernel_ir.metal_finalize import emit_metal_source
from pcc.kernel_ir.tilelang_import import (
    TILELANG_WGMMA_LAYOUT_REFERENCE_COMMIT,
    TILELANG_WGMMA_LAYOUT_REFERENCE_PATH,
    TILELANG_WGMMA_LAYOUT_REFERENCE_SHA256,
    TileLangImportError,
    import_tilelang_source,
)
from pcc.kernel_ir.tirx_adapter import TirxAdapterError, lower_to_plain_tir


TILELANG_WGMMA_LAYOUT = """
import tilelang
import tilelang.language as T

def advanced_layout(M, N, continuity=8, k_major=True):
    @T.prim_func
    def layout_kernel(C: T.Tensor((M, N), T.float16)):
        with T.Kernel(1, threads=32) as bx:
            A_shared = T.alloc_shared((M, N), T.float16)
            T.annotate_layout({
                A_shared: tilelang.layout.make_wgmma_swizzled_layout(
                    A_shared, continuity=continuity, k_major=k_major
                )
            })
            T.copy(C, A_shared)
            T.copy(A_shared, C)
    return layout_kernel
"""


def _module(source: str = TILELANG_WGMMA_LAYOUT, **overrides: object):
    constants: dict[str, object] = {"M": 8, "N": 16, "continuity": 8, "k_major": True}
    constants.update(overrides)
    return import_tilelang_source(
        source,
        outer_function="advanced_layout",
        prim_func="layout_kernel",
        constants=constants,
        module_name="tilelang_wgmma_layout_identity",
    )


def test_wgmma_layout_preserves_upstream_owner_pass_and_reference_identity():
    module = _module()
    op = module.funcs[0].body[0]

    assert op.op == "layout_annotation"
    assert op.args == ("A_shared",)
    assert op.attrs == {
        "entries": 1,
        "layout_helper": "tilelang.layout.make_wgmma_swizzled_layout",
        "layout_owner": "tilelang",
        "layout_pass": "tilelang.transform.LayoutInference",
        "required_target": "cuda-sm90-wgmma",
        "metal_legal": False,
        "wgmma": True,
        "continuity": 8,
        "k_major": True,
        "reference_commit": TILELANG_WGMMA_LAYOUT_REFERENCE_COMMIT,
        "reference_path": TILELANG_WGMMA_LAYOUT_REFERENCE_PATH,
        "reference_sha256": TILELANG_WGMMA_LAYOUT_REFERENCE_SHA256,
    }
    assert module.funcs[0].locals[0].layout.value == "tile"


def test_wgmma_layout_freezes_for_cuda_without_erasing_pass_identity():
    plain = lower_to_plain_tir(_module(), target="cuda")
    op = plain.funcs[0]["ops"][0]

    assert op["tir_op"] == "tir.annotate_layout"
    assert op["args"] == ["A_shared"]
    assert op["attrs"]["layout_owner"] == "tilelang"
    assert op["attrs"]["layout_pass"] == "tilelang.transform.LayoutInference"
    assert op["attrs"]["required_target"] == "cuda-sm90-wgmma"
    assert op["attrs"]["wgmma"] is True


def test_wgmma_layout_is_explicitly_target_illegal_for_metal():
    module = _module()
    with pytest.raises(TirxAdapterError, match="CUDA-only assumption.*wgmma.*target is 'metal'"):
        lower_to_plain_tir(module, target="metal")
    with pytest.raises(TirxAdapterError, match="CUDA-only assumption.*wgmma.*target is 'metal'"):
        emit_metal_source(module)


def test_wgmma_layout_defaults_preserve_upstream_none_continuity_as_minus_one():
    source = TILELANG_WGMMA_LAYOUT.replace(
        "A_shared, continuity=continuity, k_major=k_major",
        "A_shared",
    )
    op = _module(source).funcs[0].body[0]
    assert op.attrs["continuity"] == -1
    assert op.attrs["k_major"] is True


def test_wgmma_layout_rejects_non_boolean_k_major():
    with pytest.raises(TileLangImportError, match="k_major must be a static boolean"):
        _module(k_major=1)


def test_wgmma_layout_rejects_non_shared_target():
    source = TILELANG_WGMMA_LAYOUT.replace(
        "T.alloc_shared((M, N), T.float16)",
        "T.alloc_fragment((M, N), T.float16)",
    )
    with pytest.raises(TileLangImportError, match="rank-2 shared buffer"):
        _module(source)


def test_wgmma_layout_rejects_multiple_annotation_entries():
    source = TILELANG_WGMMA_LAYOUT.replace(
        "A_shared = T.alloc_shared((M, N), T.float16)",
        "A_shared = T.alloc_shared((M, N), T.float16)\n"
        "            B_shared = T.alloc_shared((M, N), T.float16)",
    ).replace(
        "A_shared: tilelang.layout.make_wgmma_swizzled_layout(",
        "B_shared: tilelang.layout.make_swizzled_layout(B_shared),\n"
        "                A_shared: tilelang.layout.make_wgmma_swizzled_layout(",
    )
    with pytest.raises(TileLangImportError, match="requires exactly one annotation entry"):
        _module(source)
