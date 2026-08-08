"""Finite source contracts for conservative code-identified PGO."""

from __future__ import annotations

from dataclasses import dataclass
import json

from pcc.backend.code_profile import (
    CODE_PROFILE_ENV,
    CODE_PROFILE_RUNTIME_ABI_ENV,
    CODE_PROFILE_SCHEMA,
    CODE_PROFILE_SEMANTIC_MODE_ENV,
    CODE_PROFILE_SOURCE_IDENTITY_ENV,
    apply_function_order_profile,
    code_profile_identity,
)
from pcc.backend.self_backend_aarch64_darwin import emit_aarch64_darwin_asm


@dataclass(frozen=True)
class _Function:
    name: str


_IR = """
target triple = "arm64-apple-darwin23.6.0"

define i64 @cold() {
entry:
  ret i64 1
}

define i64 @hot() {
entry:
  ret i64 2
}

define i64 @unsampled() {
entry:
  ret i64 3
}
""".strip()


def _profile(*, code_identity: str | None = None, target: str = "arm64-apple-darwin23.6.0"):
    return {
        "schema": CODE_PROFILE_SCHEMA,
        "source_identity": "sha256:source",
        "code_identity": code_identity or code_profile_identity(_IR),
        "semantic_mode": "no-libpython",
        "runtime_abi": "pcc-py-runtime.v1",
        "target": target,
        "decision": {
            "kind": "function-order",
            "function_samples": [
                {"symbol": "cold", "count": 1},
                {"symbol": "hot", "count": 100},
                {"symbol": "missing", "count": 999},
            ],
        },
    }


def _env(path) -> dict[str, str]:
    return {
        CODE_PROFILE_ENV: str(path),
        CODE_PROFILE_SOURCE_IDENTITY_ENV: "sha256:source",
        CODE_PROFILE_SEMANTIC_MODE_ENV: "no-libpython",
        CODE_PROFILE_RUNTIME_ABI_ENV: "pcc-py-runtime.v1",
    }


def test_matching_profile_orders_only_known_functions_stably(tmp_path):
    path = tmp_path / "profile.json"
    path.write_text(json.dumps(_profile(), sort_keys=True), encoding="utf-8")
    functions = [_Function("cold"), _Function("hot"), _Function("unsampled")]

    ordered, decision = apply_function_order_profile(
        functions,
        ir_text=_IR,
        target="arm64-apple-darwin23.6.0",
        environ=_env(path),
    )

    assert [function.name for function in ordered] == ["hot", "cold", "unsampled"]
    assert decision.status == "matched"
    assert decision.matched_samples == 2
    assert decision.unmatched_samples == 1


def test_missing_corrupt_stale_and_wrong_mode_profiles_leave_aot_order(tmp_path):
    functions = [_Function("cold"), _Function("hot"), _Function("unsampled")]
    cases = []
    corrupt = tmp_path / "corrupt.json"
    corrupt.write_text("{", encoding="utf-8")
    cases.append(_env(corrupt))
    stale = tmp_path / "stale.json"
    stale.write_text(
        json.dumps(_profile(code_identity="sha256:stale")),
        encoding="utf-8",
    )
    cases.append(_env(stale))
    wrong_target = tmp_path / "wrong-target.json"
    wrong_target.write_text(
        json.dumps(_profile(target="x86_64-unknown-linux-gnu")),
        encoding="utf-8",
    )
    cases.append(_env(wrong_target))
    wrong_mode = tmp_path / "wrong-mode.json"
    wrong_mode.write_text(json.dumps(_profile()), encoding="utf-8")
    wrong_mode_env = _env(wrong_mode)
    wrong_mode_env[CODE_PROFILE_SEMANTIC_MODE_ENV] = "libpython"
    cases.append(wrong_mode_env)
    cases.append({CODE_PROFILE_ENV: str(wrong_mode)})
    cases.append({})

    for env in cases:
        ordered, decision = apply_function_order_profile(
            functions,
            ir_text=_IR,
            target="arm64-apple-darwin23.6.0",
            environ=env,
        )
        assert ordered == functions
        assert decision.status != "matched"


def test_invalid_duplicate_negative_and_boolean_counts_are_ignored(tmp_path):
    functions = [_Function("cold"), _Function("hot")]
    bad_samples = (
        [{"symbol": "hot", "count": -1}],
        [{"symbol": "hot", "count": True}],
        [
            {"symbol": "hot", "count": 1},
            {"symbol": "hot", "count": 2},
        ],
    )
    for index, samples in enumerate(bad_samples):
        profile = _profile()
        profile["decision"]["function_samples"] = samples
        path = tmp_path / ("bad-" + str(index) + ".json")
        path.write_text(json.dumps(profile), encoding="utf-8")
        ordered, decision = apply_function_order_profile(
            functions,
            ir_text=_IR,
            target="arm64-apple-darwin23.6.0",
            environ=_env(path),
        )
        assert ordered == functions
        assert decision.status == "invalid"


def test_aarch64_emitter_consumes_matching_function_order_profile(
    tmp_path,
    monkeypatch,
):
    baseline = emit_aarch64_darwin_asm(_IR)
    assert baseline.index("_cold:") < baseline.index("_hot:")

    path = tmp_path / "profile.json"
    path.write_text(json.dumps(_profile()), encoding="utf-8")
    for name, value in _env(path).items():
        monkeypatch.setenv(name, value)
    profiled = emit_aarch64_darwin_asm(_IR)

    assert profiled.index("_hot:") < profiled.index("_cold:")
    assert profiled.index("_cold:") < profiled.index("_unsampled:")


def test_no_profile_is_byte_deterministic_aot(monkeypatch):
    monkeypatch.delenv(CODE_PROFILE_ENV, raising=False)
    first = emit_aarch64_darwin_asm(_IR)
    second = emit_aarch64_darwin_asm(_IR)
    assert first == second
