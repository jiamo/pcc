from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

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
from pcc.kernel_ir.tirx_adapter import lower_to_plain_tir
from pcc.kernel_ir.tvm_tilelang_owner import (
    TVM_TILELANG_ORDERED_PASSES,
    TVM_TILELANG_PIPELINE,
    TvmTilelangProviderError,
    compile_with_tvm_tilelang_provider,
    load_tvm_tilelang_provider_config,
)


def _copy_module() -> KernelModule:
    return KernelModule(
        "tilelang_owner_copy",
        funcs=(
            KernelFunc(
                "copy_kernel",
                params=(
                    BufferParam(
                        "src",
                        ScalarType.F32,
                        rank=2,
                        shape=(2, 2),
                        scope=MemoryScope.GLOBAL,
                    ),
                    BufferParam(
                        "dst",
                        ScalarType.F32,
                        rank=2,
                        shape=(2, 2),
                        scope=MemoryScope.GLOBAL,
                    ),
                    ScalarParam("n", ScalarType.U32),
                ),
                body=(
                    KernelOp("parallel", ("src", "dst", "n"), {"extent": 4}),
                    KernelOp("copy", ("src", "dst")),
                ),
                grid=(1,),
                threads=16,
            ),
        ),
    )


def _gemm_module() -> KernelModule:
    return KernelModule(
        "tilelang_owner_gemm",
        funcs=(
            KernelFunc(
                "gemm_kernel",
                params=(
                    BufferParam("A", ScalarType.F16, 2, (8, 8)),
                    BufferParam("B", ScalarType.F16, 2, (8, 8)),
                    BufferParam("C", ScalarType.F32, 2, (8, 8)),
                ),
                locals=(
                    LocalBuffer(
                        "A_shared",
                        ScalarType.F16,
                        (8, 8),
                        MemoryScope.SHARED,
                        Layout.TILE,
                    ),
                    LocalBuffer(
                        "B_shared",
                        ScalarType.F16,
                        (8, 8),
                        MemoryScope.SHARED,
                        Layout.TILE,
                    ),
                    LocalBuffer(
                        "C_local",
                        ScalarType.F32,
                        (8, 8),
                        MemoryScope.FRAGMENT,
                        Layout.TILE,
                    ),
                ),
                body=(
                    KernelOp("fill", ("C_local",), {"value": 0}),
                    KernelOp("copy", ("A", "A_shared"), {"pipeline_extent": 1, "num_stages": 0}),
                    KernelOp("copy", ("B", "B_shared"), {"pipeline_extent": 1, "num_stages": 0}),
                    KernelOp("gemm", ("A_shared", "B_shared", "C_local"), {"pipeline_extent": 1, "num_stages": 0}),
                    KernelOp("copy", ("C_local", "C")),
                ),
                grid=(1, 1),
                threads=32,
            ),
        ),
    )


def _config_or_skip():
    config = load_tvm_tilelang_provider_config()
    required = (
        Path(config.root),
        Path(config.python),
        Path(config.site_packages),
        Path(config.provider_script),
    )
    if not all(path.exists() for path in required):
        pytest.skip("pinned local TVM/TileLang provider is not installed")
    return config


@pytest.mark.parametrize(
    ("module_factory", "semantic_kind"),
    ((_copy_module, "copy"), (_gemm_module, "gemm")),
)
def test_pinned_provider_compiles_canonical_plain_tir_without_fallback(
    tmp_path,
    module_factory,
    semantic_kind,
):
    result = compile_with_tvm_tilelang_provider(
        lower_to_plain_tir(module_factory(), target="metal"),
        tmp_path / semantic_kind,
        config=_config_or_skip(),
    )

    assert result.backend == "tvm-tilelang"
    assert result.semantic_kind == semantic_kind
    assert result.pipeline == TVM_TILELANG_PIPELINE
    assert result.ordered_passes == TVM_TILELANG_ORDERED_PASSES
    assert result.provider_process_links_libpython is True
    assert "kernel void" in result.metal_source
    assert f"kernel void {result.logical_entry}(" in result.metal_source
    assert result.provider_entry == f"{result.logical_entry}_kernel"
    assert result.provider_metal_source != result.metal_source
    assert len(result.metal_source_sha256) == 64
    assert Path(result.request_path).is_file()
    assert Path(result.response_path).is_file()
    assert Path(result.provider_source_path).read_text(encoding="utf-8") == result.provider_metal_source
    assert Path(result.source_path).read_text(encoding="utf-8") == result.metal_source
    assert set(result.artifact_hashes()) >= {
        "canonical_frozen_ir",
        "provider_metal_source",
        "pcc_abi_adapted_metal_source",
        "provider_dependency.libtilelang.dylib",
        "provider_dependency.libtvm_compiler.dylib",
    }


def test_provider_rejects_unallowlisted_pipeline_before_launch(tmp_path):
    with pytest.raises(TvmTilelangProviderError, match="unsupported.*no fallback"):
        compile_with_tvm_tilelang_provider(
            lower_to_plain_tir(_copy_module(), target="metal"),
            tmp_path,
            pipeline="ambient-plugin-pipeline",
        )


def test_provider_unavailable_fails_closed_without_pcc_metal_fallback(tmp_path):
    config = load_tvm_tilelang_provider_config()
    unavailable = replace(config, root=str(tmp_path / "missing-tilelang"))
    with pytest.raises(TvmTilelangProviderError, match="unavailable.*no fallback"):
        compile_with_tvm_tilelang_provider(
            lower_to_plain_tir(_copy_module(), target="metal"),
            tmp_path / "artifacts",
            config=unavailable,
        )


def test_provider_incompatible_pin_fails_closed(tmp_path):
    config = _config_or_skip()
    pin = dict(config.pin)
    sources = dict(pin["source_hashes"])
    sources["VERSION"] = "0" * 64
    pin["source_hashes"] = sources
    incompatible = replace(config, pin=pin)
    with pytest.raises(TvmTilelangProviderError, match="hash mismatch.*no fallback"):
        compile_with_tvm_tilelang_provider(
            lower_to_plain_tir(_copy_module(), target="metal"),
            tmp_path,
            config=incompatible,
        )


def test_provider_isolates_ambient_python_and_plugin_configuration(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setenv("PYTHONPATH", str(tmp_path / "ambient-plugin"))
    monkeypatch.setenv("PYTHONSTARTUP", str(tmp_path / "ambient-startup.py"))
    monkeypatch.setenv("TILELANG_PASS_DIFF", "1")
    config = _config_or_skip()
    result = compile_with_tvm_tilelang_provider(
        lower_to_plain_tir(_copy_module(), target="metal"),
        tmp_path / "artifacts",
        config=config,
    )
    assert result.diagnostics == ()
    assert result.dependencies["tilelang_module"] == str(
        Path(config.root, "tilelang", "__init__.py").resolve()
    )


def test_provider_rejects_unsupported_semantics_without_fallback(tmp_path):
    module = _copy_module()
    func = module.funcs[0]
    unsupported = KernelModule(
        module.name,
        funcs=(
            replace(
                func,
                body=(KernelOp("elementwise_add", ("src", "dst", "dst")),),
            ),
        ),
    )
    with pytest.raises(TvmTilelangProviderError, match="supports only.*no fallback"):
        compile_with_tvm_tilelang_provider(
            lower_to_plain_tir(unsupported, target="metal"),
            tmp_path,
            config=_config_or_skip(),
        )
