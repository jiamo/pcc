"""Claim-boundary tests for the pcc TileLang source-subset importer.

These tests enforce the distinction from
GPU-P0-TILELANG-NATIVE-NAMING-BOUNDARY: a runtime ``import tilelang`` is
package / cpython-compat work (label ``tilelang-package-cpython-compat``), while
``import_tilelang_source`` / ``import_tilelang_file`` are COMPILER-SIDE SOURCE
PARSING ONLY (label ``pcc-tilelang-source-subset``). The source-subset importer
must never be described as, or silently act as, a pcc-native ``import
tilelang``.
"""

import sys

import pytest

from pcc.kernel_ir.tilelang_import import (
    TILELANG_PACKAGE_CPYTHON_COMPAT_CLAIM,
    TILELANG_SOURCE_SUBSET_CLAIM,
    TileLangImportError,
    assert_not_native_import_tilelang_claim,
    import_tilelang_source,
    tilelang_source_import_claim,
    tilelang_source_import_claim_of,
)


# A working matmul shape matching tests/kernel/test_tilelang_import.py: the DSL
# subset the importer actually accepts (T.prim_func / T.Kernel / T.copy / T.gemm).
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


# A perfectly ordinary Python module that merely *mentions* `import tilelang`.
# This stands in for a normal pcc1-compilable `kernel.py`; nothing here is a
# kernel region, so it must not be silently reinterpreted as Kernel IR.
NORMAL_PY_WITH_IMPORT_TILELANG = """
import tilelang
import tilelang.language as T


def main():
    value = 1 + 2
    return value
"""


def _import_matmul():
    return import_tilelang_source(
        TILELANG_METAL_MATMUL,
        outer_function="matmul_simdgroup",
        prim_func="gemm_kernel",
        constants={"M": 128, "N": 256, "K": 64},
        module_name="tilelang_metal_matmul_claim",
    )


def test_normal_pcc1_kernel_py_does_not_silently_reinterpret_import_tilelang_as_kernel_ir():
    # 1. No implicit interception exists. The source-subset importer must not
    #    register any import hook (meta_path finder or path hook) that would
    #    reroute a plain `import tilelang` statement into Kernel IR.
    for finder in sys.meta_path:
        origin = getattr(type(finder), "__module__", "") or ""
        assert "tilelang_import" not in origin, (
            f"a meta_path finder from {origin!r} would intercept imports"
        )
    for hook in sys.path_hooks:
        origin = getattr(hook, "__module__", "") or ""
        assert "tilelang_import" not in origin, (
            f"a path hook from {origin!r} would intercept imports"
        )

    # 2. The importer is an explicit function, not an import hook: it cannot be
    #    invoked without explicit arguments (source, plus routing kwargs).
    with pytest.raises(TypeError):
        import_tilelang_source()  # type: ignore[call-arg]

    # 3. A normal module that only *mentions* `import tilelang` is NOT silently
    #    turned into a KernelModule. With no @T.prim_func / T.Kernel region the
    #    source-subset importer fails closed instead of fabricating Kernel IR.
    with pytest.raises(TileLangImportError):
        import_tilelang_source(NORMAL_PY_WITH_IMPORT_TILELANG)

    # 4. The real matmul only becomes Kernel IR through the EXPLICIT entrypoint
    #    with explicit routing args; it is never reached by plain `import`.
    module = _import_matmul()
    assert module.funcs[0].name == "gemm_kernel"


def test_explicit_kernel_import_mode_required_for_tilelang_source_subset_routing():
    # Routing to the source subset happens only via the explicit call, and the
    # resulting module carries honest claim metadata.
    module = _import_matmul()

    claim = tilelang_source_import_claim_of(module)
    assert claim["mode"] == TILELANG_SOURCE_SUBSET_CLAIM == "pcc-tilelang-source-subset"
    assert claim["is_pcc_native_import_tilelang"] is False
    assert claim["executes_tilelang_runtime"] is False
    assert claim["links_libpython"] is False
    assert claim["not_this_claim"] == TILELANG_PACKAGE_CPYTHON_COMPAT_CLAIM

    # The standalone claim helper agrees with the stamped metadata.
    standalone = tilelang_source_import_claim()
    assert standalone["mode"] == TILELANG_SOURCE_SUBSET_CLAIM
    assert standalone["is_pcc_native_import_tilelang"] is False

    # The two labels are distinct strings and must never collapse into one.
    assert TILELANG_PACKAGE_CPYTHON_COMPAT_CLAIM != TILELANG_SOURCE_SUBSET_CLAIM

    # A raw KernelModule that did not come from this importer has no claim, so
    # nobody can silently treat an arbitrary module as a proven source subset.
    class _Fake:
        pass

    with pytest.raises(TileLangImportError):
        tilelang_source_import_claim_of(_Fake())  # type: ignore[arg-type]

    # The guard rejects native-`import tilelang` prose about this path...
    for bad in (
        "pcc supports pcc-native import tilelang",
        "this path natively imports tilelang at runtime",
        "the compiler executes the tilelang runtime",
        f"this is the {TILELANG_PACKAGE_CPYTHON_COMPAT_CLAIM} path",
    ):
        with pytest.raises(TileLangImportError):
            assert_not_native_import_tilelang_claim(bad)

    # ...but accepts an honest source-subset description.
    assert_not_native_import_tilelang_claim(
        "import_tilelang_source performs a compiler-side AST parse of a TileLang "
        "DSL subset into pcc Kernel IR; no TileLang package is loaded and this is "
        f"not a pcc-native import claim ({TILELANG_SOURCE_SUBSET_CLAIM})."
    )
