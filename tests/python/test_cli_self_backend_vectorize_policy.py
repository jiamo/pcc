from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

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


def test_self_backend_clamp_warning_is_visible(monkeypatch, capsys):
    monkeypatch.delenv("PCC_BACKEND", raising=False)
    monkeypatch.delenv("PCC_SELF_BACKEND_VECTORIZE", raising=False)

    effective = cli_core._effective_self_backend_opt_level("self", 2)
    cli_core._warn_if_self_backend_opt_level_clamped("self", 2, effective)

    captured = capsys.readouterr()
    assert captured.out == ""
    assert "--backend=self requested -O2 but is using -O0" in captured.err
    assert "LLVM vectorizer output safely" in captured.err
    assert "PCC_SELF_BACKEND_VECTORIZE=1" in captured.err


def test_self_backend_clamp_warning_is_suppressed_when_not_clamped(monkeypatch, capsys):
    monkeypatch.setenv("PCC_SELF_BACKEND_VECTORIZE", "on")

    effective = cli_core._effective_self_backend_opt_level("self", 2)
    cli_core._warn_if_self_backend_opt_level_clamped("self", 2, effective)

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""


def test_self_backend_clamp_warning_reaches_c_cli(tmp_path):
    repo_root = Path(__file__).resolve().parents[2]
    source = tmp_path / "main.c"
    output = tmp_path / "main.ll"
    source.write_text("int main(void) { return 0; }\n", encoding="utf-8")
    env = os.environ.copy()
    env.pop("PCC_BACKEND", None)
    env.pop("PCC_SELF_BACKEND_VECTORIZE", None)

    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "pcc",
            "--backend",
            "self",
            "-O2",
            f"--emit-llvm={output}",
            str(source),
        ],
        cwd=repo_root,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert proc.returncode == 0, proc.stderr
    assert output.is_file()
    assert "--backend=self requested -O2 but is using -O0" in proc.stderr
