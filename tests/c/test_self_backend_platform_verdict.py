from __future__ import annotations

import ast
from pathlib import Path

from pcc.backend import resolve_backend
from pcc.backend.self_backend_targets import (
    STATUS_SELF_TARGET_SUPPORTED,
    STATUS_SELF_TARGET_UNSUPPORTED,
    classify_self_backend_target_triple,
)


def test_self_backend_registry_is_supported_without_legacy_override():
    config = resolve_backend("self")
    assert config.kind == "self"
    assert config.supported is True
    assert "emit-object" in config.capabilities
    assert config.cache_signature().endswith(":support")


def test_supported_platform_verdict_is_capability_only():
    verdict = classify_self_backend_target_triple("arm64-apple-darwin23.6.0")
    assert verdict.to_dict() == {
        "triple": "arm64-apple-darwin23.6.0",
        "status": STATUS_SELF_TARGET_SUPPORTED,
        "target_identity": "self-aarch64-darwin-v0",
        "reason": "resolved to registered emitter 'self-aarch64-darwin-v0'",
        "backend_executed": False,
        "runtime_executed": False,
    }


def test_unsupported_platform_verdict_cannot_be_runtime_proof():
    verdict = classify_self_backend_target_triple("wasm32-unknown-unknown")
    assert verdict.status == STATUS_SELF_TARGET_UNSUPPORTED
    assert verdict.supported is False
    assert verdict.target_identity is None
    assert verdict.backend_executed is False
    assert verdict.runtime_executed is False
    assert verdict.skip_reason() == (
        "UNSUPPORTED[self-backend:wasm32-unknown-unknown]: no registered "
        "self-backend emitter matches 'wasm32-unknown-unknown'; "
        "backend_executed=false; runtime_executed=false"
    )


def test_vector_parity_family_uses_registry_platform_verdict_source_guard():
    path = Path(__file__).with_name("test_llvm_self_vector_parity.py")
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "classify_self_backend_target_triple"
    ]
    assert len(calls) == 1
    assert "is_supported_self_backend_target_triple" not in source
    assert "self backend target not supported" not in source
