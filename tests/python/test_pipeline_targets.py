"""Target-triple contracts extracted from the Python pipeline facade."""

from __future__ import annotations

from pathlib import Path

import pytest

from pcc.py_frontend import pipeline
from pcc.py_frontend import pipeline_targets


def test_module_target_text_is_inserted_replaced_and_idempotent(tmp_path: Path):
    path = tmp_path / "module.ll"
    path.write_text("define i32 @f() { ret i32 0 }\n", encoding="utf-8")
    assert pipeline._ensure_llvm_module_target(
        str(path), "arm64-apple-darwin25"
    ) == str(path)
    first = path.read_text(encoding="utf-8")
    assert first.startswith('target triple = "arm64-apple-darwin25"\n')
    pipeline._ensure_llvm_module_target(str(path), "x86_64-unknown-linux-gnu")
    second = path.read_text(encoding="utf-8")
    assert second.count("target triple") == 1
    assert 'target triple = "x86_64-unknown-linux-gnu"' in second
    pipeline._ensure_llvm_module_target(str(path), "x86_64-unknown-linux-gnu")
    assert path.read_text(encoding="utf-8") == second


def test_darwin_target_normalization_and_link_input_discovery(tmp_path: Path):
    assert (
        pipeline._host_target_triple_for_self_backend
        is pipeline_targets.host_target_triple
    )
    assert pipeline._platform_link_flags is pipeline_targets.platform_link_flags
    assert pipeline_targets.platform_link_flags("linux") == [
        "-no-pie",
        "-Wl,--build-id=none",
        "-s",
    ]
    assert pipeline_targets.platform_link_flags("darwin") == []
    assert pipeline_targets.normalize_clang_target_triple(
        "arm64-apple-darwin23.6.0"
    ) == "arm64-apple-macosx14.0.0"
    assert pipeline_targets.normalize_clang_target_triple(
        "x86_64-unknown-linux-gnu"
    ) == "x86_64-unknown-linux-gnu"

    first = tmp_path / "first.ll"
    first.write_text("define i32 @f() { ret i32 0 }\n", encoding="utf-8")
    second = tmp_path / "second.ll"
    second.write_text(
        'target triple = "arm64-apple-darwin25.0.0"\n', encoding="utf-8"
    )
    assert pipeline_targets.link_input_target_triple(
        [str(first), str(second)]
    ) == "arm64-apple-darwin25.0.0"
    assert pipeline_targets.clang_target_triple(
        [str(first)], host_target_triple="unknown-unknown-unknown"
    ) is None


def test_self_backend_and_explicit_target_rewrites_are_fail_closed():
    rewritten = pipeline_targets.self_backend_ir_text(
        'target triple = "unknown-unknown-unknown"\ndefine i32 @f() { ret i32 0 }\n',
        host_target_triple="arm64-apple-darwin25",
    )
    assert 'target triple = "arm64-apple-darwin25"' in rewritten
    assert "unknown-unknown-unknown" not in rewritten

    explicit = pipeline._ir_text_with_target_triple(
        rewritten,
        "x86_64-unknown-linux-gnu",
    )
    assert explicit.startswith('target triple = "x86_64-unknown-linux-gnu"')
    with pytest.raises(pipeline.PyPipelineError, match="invalid.*target triple"):
        pipeline._ir_text_with_target_triple(rewritten, 'bad"triple')
