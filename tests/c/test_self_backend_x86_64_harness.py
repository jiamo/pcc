from __future__ import annotations

import subprocess
from pathlib import Path

from scripts import run_self_backend_linux_x86_64_c_testsuite as harness


def _completed(returncode: int, stdout: str = "", stderr: str = ""):
    return subprocess.CompletedProcess(
        args=["probe"], returncode=returncode, stdout=stdout, stderr=stderr,
    )


def test_self_strict_exact_mode_passes_matching_cases(monkeypatch, capsys):
    monkeypatch.setattr(harness, "exact_match_cases", lambda limit: ["00001.c"])
    monkeypatch.setattr(harness, "c_testsuite_case_path", lambda name: Path(name))
    monkeypatch.setattr(
        harness, "_compile_and_run_native",
        lambda case, timeout: _completed(7, "ok\n", ""),
    )
    monkeypatch.setattr(
        harness, "_compile_and_run_pcc_self_x86_64",
        lambda case, timeout: _completed(7, "ok\n", ""),
    )
    monkeypatch.setattr(harness, "read_expected_output", lambda case: "ok\n")

    assert harness._check_self_strict_exact_bucket(1, 1) == 0
    assert "strict-exact bucket passed: 1 cases" in capsys.readouterr().out


def test_self_strict_exact_mode_rejects_unsupported_boundaries(
    monkeypatch, capsys,
):
    monkeypatch.setattr(harness, "exact_match_cases", lambda limit: ["00002.c"])
    monkeypatch.setattr(harness, "c_testsuite_case_path", lambda name: Path(name))
    monkeypatch.setattr(
        harness, "_compile_and_run_native",
        lambda case, timeout: _completed(0, "", ""),
    )
    monkeypatch.setattr(
        harness, "_compile_and_run_pcc_self_x86_64",
        lambda case, timeout: _completed(
            1, "", "x86_64 self backend not translated yet\n",
        ),
    )
    monkeypatch.setattr(harness, "read_expected_output", lambda case: "")

    assert harness._check_self_strict_exact_bucket(1, 1) == 1
    out = capsys.readouterr().out
    assert "00002.c: returncode native=0 self=1" in out
    assert "not translated yet" in out

