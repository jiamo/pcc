from __future__ import annotations

from pcc import cli_core


def test_self_backend_clamps_vectorizing_opt_levels_by_default(monkeypatch):
    monkeypatch.delenv("PCC_BACKEND", raising=False)
    monkeypatch.delenv("PCC_SELF_BACKEND_VECTORIZE", raising=False)

    assert cli_core._effective_self_backend_opt_level("self", 2) == 0
    assert cli_core._effective_self_backend_opt_level("self", 3) == 0
    assert cli_core._effective_self_backend_opt_level("llvm", 2) == 2


def test_self_backend_clamp_honors_backend_env(monkeypatch):
    monkeypatch.setenv("PCC_BACKEND", "self")
    monkeypatch.delenv("PCC_SELF_BACKEND_VECTORIZE", raising=False)

    assert cli_core._effective_self_backend_opt_level(None, 2) == 0


def test_self_backend_vectorizers_can_be_explicitly_reenabled(monkeypatch):
    monkeypatch.setenv("PCC_SELF_BACKEND_VECTORIZE", "on")

    assert cli_core._effective_self_backend_opt_level("self", 2) == 2
