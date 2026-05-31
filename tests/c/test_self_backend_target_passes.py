from __future__ import annotations

import pytest

from pcc.backend import BackendUnavailable
from pcc.backend.self_backend_dispatch import emit_self_asm
from pcc.backend.self_backend_target_passes import (
    resolve_self_target_pass_names,
    resolve_self_target_pass_transport,
    run_self_target_pass_pipeline,
)


def test_self_target_passes_default_off():
    assert resolve_self_target_pass_names("") == ()
    assert resolve_self_target_pass_names("off") == ()
    assert resolve_self_target_pass_names("default") == ()


def test_self_target_pass_transport_text_default():
    assert resolve_self_target_pass_transport("") == "text"
    assert resolve_self_target_pass_transport("text") == "text"
    assert resolve_self_target_pass_transport("memory") == "memory"


def test_self_target_pass_strips_trailing_whitespace():
    asm = "one   \n  two\t\nthree\n"

    out = run_self_target_pass_pipeline(
        asm,
        "self-aarch64-darwin-v0",
        raw_passes="strip-trailing-whitespace",
        raw_transport="text",
    )

    assert out == "one\n  two\nthree\n"


def test_self_target_pass_memory_transport_runs_before_asm_text():
    assert (
        run_self_target_pass_pipeline(
            "ret   \n",
            "self-aarch64-darwin-v0",
            raw_passes="all",
            raw_transport="memory",
        )
        == "ret   \n"
    )
    assert resolve_self_target_pass_names("all", transport="memory") == (
        "verify-prepared-module",
    )


def test_self_target_pass_unknown_name_fails():
    with pytest.raises(BackendUnavailable, match="unknown self target pass"):
        resolve_self_target_pass_names("not-a-pass")


def test_self_target_memory_pass_rejects_text_only_pass():
    with pytest.raises(BackendUnavailable, match="unknown self target pass"):
        resolve_self_target_pass_names(
            "strip-trailing-whitespace",
            transport="memory",
        )


def test_emit_self_asm_runs_explicit_target_text_pass(monkeypatch):
    ir_text = """
target triple = "arm64-apple-darwin23.6.0"

define i32 @main() {
entry:
  ret i32 42
}
""".strip()

    monkeypatch.setenv(
        "PCC_SELF_TARGET_PASSES",
        "strip-trailing-whitespace",
    )
    asm_text = emit_self_asm(ir_text)

    assert "_main:" in asm_text
    assert "movz w0, #42" in asm_text
    assert all(not line.endswith((" ", "\t")) for line in asm_text.splitlines())


def test_emit_self_asm_runs_explicit_target_memory_pass(monkeypatch):
    ir_text = """
target triple = "arm64-apple-darwin23.6.0"

define i32 @main() {
entry:
  ret i32 42
}
""".strip()

    monkeypatch.setenv("PCC_SELF_TARGET_PASSES", "all")
    monkeypatch.setenv("PCC_SELF_TARGET_PASS_TRANSPORT", "memory")
    asm_text = emit_self_asm(ir_text)

    assert "_main:" in asm_text
    assert "movz w0, #42" in asm_text
