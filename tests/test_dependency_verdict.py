from __future__ import annotations

import ast
from pathlib import Path

from pcc.dependency_verdict import (
    STATUS_AVAILABLE,
    STATUS_UNAVAILABLE,
    probe_artifact_dependency,
    probe_executable_dependency,
    probe_first_executable_dependency,
    probe_platform_capability,
)


def test_missing_executable_is_unavailable_and_never_feature_proof():
    verdict = probe_executable_dependency("missing-opt", resolver=lambda _name: None)
    assert verdict.to_dict() == {
        "dependency": "executable:missing-opt",
        "status": STATUS_UNAVAILABLE,
        "resolved_path": None,
        "reason": "'missing-opt' was not found on PATH",
        "feature_claimed": False,
        "runtime_executed": False,
    }
    assert verdict.skip_reason() == (
        "UNAVAILABLE[executable:missing-opt]: 'missing-opt' was not found on PATH; "
        "feature_claimed=false; runtime_executed=false"
    )


def test_available_executable_records_path_without_claiming_the_feature():
    verdict = probe_executable_dependency(
        "opt", resolver=lambda _name: "/toolchain/bin/opt"
    )
    assert verdict.status == STATUS_AVAILABLE
    assert verdict.available is True
    assert verdict.resolved_path == "/toolchain/bin/opt"
    assert verdict.feature_claimed is False
    assert verdict.runtime_executed is False


def test_lower_expect_family_uses_structured_opt_verdict_source_guard():
    root = Path(__file__).resolve().parent
    paths = [
        root / "c" / "test_ir_passes_lower_expect_real.py",
        root / "c" / "test_ir_passes_lower_expect_semantic_oracle.py",
    ]
    for path in paths:
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source)
        calls = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "probe_executable_dependency"
        ]
        assert len(calls) == 1, path
        assert ast.literal_eval(calls[0].args[0]) == "opt"
        assert "shutil.which(\"opt\")" not in source
        assert 'pytest.skip("requires LLVM opt")' not in source


def test_first_executable_alternative_set_reports_whole_set_when_missing():
    verdict = probe_first_executable_dependency(
        ("cc", "clang", "gcc"), resolver=lambda _name: None
    )
    assert verdict.status == STATUS_UNAVAILABLE
    assert verdict.dependency == "executable:cc|clang|gcc"
    assert "cc|clang|gcc" in verdict.reason
    assert verdict.feature_claimed is False and verdict.runtime_executed is False


def test_first_executable_alternative_set_takes_first_hit_in_order():
    verdict = probe_first_executable_dependency(
        ("cc", "clang"),
        resolver=lambda name: "/usr/bin/clang" if name == "clang" else None,
    )
    assert verdict.status == STATUS_AVAILABLE
    assert verdict.dependency == "executable:clang"
    assert verdict.resolved_path == "/usr/bin/clang"


def test_missing_artifact_is_unavailable_and_never_behavior_proof(tmp_path):
    missing = tmp_path / "libpy_runtime_pcc_py.a"
    verdict = probe_artifact_dependency(missing, kind="runtime-archive")
    assert verdict.status == STATUS_UNAVAILABLE
    assert verdict.dependency == f"runtime-archive:{missing}"
    assert verdict.feature_claimed is False and verdict.runtime_executed is False
    present = tmp_path / "present.a"
    present.write_bytes(b"!<arch>\n")
    verdict2 = probe_artifact_dependency(present, kind="runtime-archive")
    assert verdict2.status == STATUS_AVAILABLE
    assert verdict2.resolved_path == str(present)


def test_platform_capability_classifies_platform_separately_from_feature():
    unsupported = probe_platform_capability(
        "posix-process-groups",
        supported=False,
        detail="process-group timeout is a POSIX bootstrap harness guard",
    )
    assert unsupported.status == STATUS_UNAVAILABLE
    assert unsupported.dependency == "capability:posix-process-groups"
    assert unsupported.feature_claimed is False
    supported = probe_platform_capability(
        "macos-arm64-bootstrap-baseline",
        supported=True,
        detail="capture platform matches the authoritative baseline",
    )
    assert supported.status == STATUS_AVAILABLE
    assert supported.feature_claimed is False
