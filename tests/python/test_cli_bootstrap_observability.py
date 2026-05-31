from __future__ import annotations

import json
import subprocess
from pathlib import Path


def test_bootstrap_cli_parses_observability_flags():
    from pcc.cli_bootstrap import parse_bootstrap_cli_args

    parsed, status, err = parse_bootstrap_cli_args([
        "--diagnostic-format", "json",
        "--profile-json", "profile.json",
        "--explain-fallback",
        "--backend", "self",
        "-o", "out",
        "prog.py",
    ])

    assert status == 0, err
    assert parsed is not None
    assert parsed[-3:] == ("json", "profile.json", True)


def test_bootstrap_cli_writes_profile_json_on_success(monkeypatch, tmp_path):
    import pcc.cli_bootstrap as cli

    src = tmp_path / "prog.py"
    src.write_text("print('ok')\n", encoding="utf-8")
    out = tmp_path / "prog.out"
    profile = tmp_path / "profile.json"
    calls = []

    def fake_compile(*args, **kwargs):
        calls.append((args, kwargs))
        Path(args[1]).write_text("fake", encoding="utf-8")

    monkeypatch.setattr(cli, "_compile_python", fake_compile)
    status = cli.bootstrap_cli_main([
        "--profile-json", str(profile),
        "-o", str(out),
        str(src),
    ])

    assert status == 0
    assert calls
    data = json.loads(profile.read_text(encoding="utf-8"))
    assert data["schema"] == "pcc.profile.v1"
    assert data["metadata"]["entry"] == "cli_bootstrap"
    assert data["metadata"]["time_unit"] == "seconds"
    assert "phase_totals_s" in data


def test_bootstrap_cli_formats_hard_error_as_json(monkeypatch, tmp_path, capsys):
    import pcc.cli_bootstrap as cli

    src = tmp_path / "bad.py"
    src.write_text("print('bad')\n", encoding="utf-8")
    profile = tmp_path / "profile.json"

    def fake_compile(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(cli, "_compile_python", fake_compile)
    status = cli.bootstrap_cli_main([
        "--diagnostic-format=json",
        "--profile-json", str(profile),
        "--explain-fallback",
        "--emit-llvm",
        str(src),
    ])

    assert status == 1
    err = capsys.readouterr().err
    data = json.loads(err)
    assert data["schema"] == "pcc.diagnostics.v1"
    diag = data["diagnostics"][0]
    assert diag["code"] == "PCC-PY-COMPILE-001"
    assert diag["phase"] == "python-frontend"
    assert "boom" in diag["message"]
    assert "fallback_explain" in "\n".join(diag["notes"])
    assert profile.exists()


def test_bootstrap_cli_pytest_mode_launches_tests(monkeypatch):
    import pcc.cli_bootstrap as cli

    calls = []

    def fake_run(cmd, *, check):
        calls.append((cmd, check))
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(cli.subprocess, "run", fake_run)
    monkeypatch.setattr(cli.sys, "executable", "/tmp/pcc1")

    status = cli.bootstrap_cli_main(["--pytest", "tests", "-q", "-n0"])

    assert status == 0
    assert calls == [(
        [
            "env",
            "-u",
            "LC_ALL",
            "PCC1_BINARY=/tmp/pcc1",
            "uv",
            "run",
            "pytest",
            "tests",
            "-q",
            "-n0",
        ],
        True,
    )]


def test_bootstrap_cli_pytest_mode_defaults_to_tests(monkeypatch):
    import pcc.cli_bootstrap as cli

    calls = []

    def fake_run(cmd, *, check):
        calls.append((cmd, check))
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(cli.subprocess, "run", fake_run)
    monkeypatch.setattr(cli.sys, "executable", "/tmp/pcc1")

    status = cli.bootstrap_cli_main(["--pytest"])

    assert status == 0
    assert calls[0][0][-1] == "tests"


def test_bootstrap_cli_pytest_mode_reports_failure(monkeypatch, capsys):
    import pcc.cli_bootstrap as cli

    def fake_run(cmd, *, check):
        raise subprocess.CalledProcessError(1, cmd)

    monkeypatch.setattr(cli.subprocess, "run", fake_run)

    status = cli.bootstrap_cli_main(["--pytest", "tests"])

    assert status == 1
    assert "pcc1 pytest run failed" in capsys.readouterr().err


def test_bootstrap_cli_c_delegation_uses_host_python_full_cli(monkeypatch):
    import pcc.cli_bootstrap as cli

    calls = []

    def fake_run(cmd, *, check):
        calls.append((cmd, check))
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.delenv("PCC_HOST_PCC", raising=False)
    monkeypatch.setenv("PCC_HOST_PYTHON", "/usr/bin/python3")
    monkeypatch.setattr(cli.subprocess, "run", fake_run)

    status = cli.bootstrap_cli_main(["hello.c", "--cpp-arg=-DTEST=1"])

    assert status == 0
    assert calls == [(
        [
            "/usr/bin/python3",
            "-m",
            "pcc.pcc",
            "hello.c",
            "--cpp-arg=-DTEST=1",
        ],
        True,
    )]
