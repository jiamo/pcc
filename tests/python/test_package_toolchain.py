from __future__ import annotations

import json
import os
import stat
import subprocess
from pathlib import Path

from pcc1_gate import repo_root

import pytest

from pcc1_gate import find_current_pcc1, skip_or_fail_no_current_pcc1

from pcc.package.toolchain import toolchain_report


REPO = repo_root()
def _find_current_pcc1() -> Path | None:
    return find_current_pcc1(REPO)


def _touch_exe(path: Path) -> None:
    path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


def _write_failing_exe(path: Path) -> None:
    path.write_text("#!/bin/sh\necho shim failed >&2\nexit 127\n", encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


def _write_version_exe(path: Path, output: str) -> None:
    path.write_text(f"#!/bin/sh\nprintf '%s\\n' '{output}'\nexit 0\n", encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


def _write_fake_toolchain(tmp_path: Path) -> tuple[Path, Path]:
    bin_dir = tmp_path / "bin"
    lib_dir = tmp_path / "lib"
    bin_dir.mkdir()
    lib_dir.mkdir()
    for name in ("cc", "c++", "gfortran", "cython", "f2py"):
        _touch_exe(bin_dir / name)
    (lib_dir / "libopenblas.a").write_text("", encoding="utf-8")
    (lib_dir / "liblapack.a").write_text("", encoding="utf-8")
    return bin_dir, lib_dir


def test_toolchain_report_finds_fake_required_components(tmp_path):
    bin_dir, lib_dir = _write_fake_toolchain(tmp_path)
    report = toolchain_report(
        search_paths=[str(bin_dir)],
        library_dirs=[str(lib_dir)],
        require_fortran=True,
        require_blas=True,
        require_lapack=True,
        require_cython=True,
        require_f2py=True,
    )
    assert report["ok"] is True
    assert report["tools"]["fortran_compiler"]["path"] == str(bin_dir / "gfortran")
    assert report["tools"]["cython"]["path"] == str(bin_dir / "cython")
    assert report["libraries"]["blas"]["path"] == str(lib_dir / "libopenblas.a")
    assert report["libraries"]["lapack"]["path"] == str(lib_dir / "liblapack.a")


def test_toolchain_report_diagnoses_missing_required_components(tmp_path):
    empty_bin = tmp_path / "empty-bin"
    empty_lib = tmp_path / "empty-lib"
    empty_bin.mkdir()
    empty_lib.mkdir()
    report = toolchain_report(
        search_paths=[str(empty_bin)],
        library_dirs=[str(empty_lib)],
        require_fortran=True,
        require_blas=True,
        require_lapack=True,
        require_cython=True,
        require_f2py=True,
    )
    assert report["ok"] is False
    assert {diag["code"] for diag in report["diagnostics"]} == {
        "PCC-PKG-MISSING-FORTRAN",
        "PCC-PKG-MISSING-BLAS",
        "PCC-PKG-MISSING-LAPACK",
        "PCC-PKG-MISSING-CYTHON",
        "PCC-PKG-MISSING-F2PY",
    }


def test_toolchain_report_rejects_broken_executable_shims(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _write_failing_exe(bin_dir / "cython")

    report = toolchain_report(
        search_paths=[str(bin_dir)],
        require_cython=True,
    )
    assert report["ok"] is False
    assert report["tools"]["cython"]["path"] == str(bin_dir / "cython")
    assert report["tools"]["cython"]["found"] is False
    assert report["tools"]["cython"]["probe_ok"] is False
    assert "shim failed" in report["tools"]["cython"]["probe_output"]
    assert report["diagnostics"][0]["code"] == "PCC-PKG-MISSING-CYTHON"


def test_toolchain_report_checks_cython_minimum_version(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _write_version_exe(bin_dir / "cython", "Cython version 0.29.36")

    report = toolchain_report(
        search_paths=[str(bin_dir)],
        require_cython=True,
        min_cython_version="3.0.6",
    )
    assert report["ok"] is False
    assert report["tools"]["cython"]["found"] is True
    assert report["diagnostics"][0]["code"] == "PCC-PKG-CYTHON-VERSION-TOO-OLD"
    assert report["diagnostics"][0]["minimum"] == "3.0.6"


def test_pcc_package_toolchain_cli(tmp_path):
    bin_dir, lib_dir = _write_fake_toolchain(tmp_path)
    env = os.environ.copy()
    env.pop("LC_ALL", None)
    proc = subprocess.run(
        [
            "uv",
            "run",
            "pcc",
            "-m",
            "pcc.package",
            "toolchain",
            "--search-path",
            str(bin_dir),
            "--library-dir",
            str(lib_dir),
            "--require-fortran",
            "--require-blas",
            "--require-lapack",
            "--require-cython",
            "--require-f2py",
            "--json",
        ],
        check=True,
        text=True,
        capture_output=True,
        timeout=60,
        env=env,
    )
    report = json.loads(proc.stdout)
    assert report["ok"] is True
    assert report["tools"]["f2py"]["found"] is True


def test_pcc1_toolchain_cli_does_not_need_host_python(tmp_path):
    pcc1 = _find_current_pcc1()
    if pcc1 is None:
        skip_or_fail_no_current_pcc1("no current pcc1 binary with native toolchain shim")
    bin_dir, lib_dir = _write_fake_toolchain(tmp_path)
    env = os.environ.copy()
    env.pop("LC_ALL", None)
    env["PCC_HOST_PYTHON"] = "/usr/bin/false"
    proc = subprocess.run(
        [
            str(pcc1),
            "-m",
            "pcc.package",
            "toolchain",
            "--search-path",
            str(bin_dir),
            "--library-dir",
            str(lib_dir),
            "--require-fortran",
            "--require-blas",
            "--require-lapack",
            "--require-cython",
            "--require-f2py",
            "--json",
        ],
        check=True,
        text=True,
        capture_output=True,
        timeout=30,
        env=env,
    )
    report = json.loads(proc.stdout)
    assert report["ok"] is True
    assert report["tools"]["fortran_compiler"]["found"] is True
    assert report["libraries"]["blas"]["found"] is True


def test_pcc1_toolchain_rejects_broken_executable_shims(tmp_path):
    pcc1 = _find_current_pcc1()
    if pcc1 is None:
        skip_or_fail_no_current_pcc1("no current pcc1 binary with native toolchain shim")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _write_failing_exe(bin_dir / "cython")
    env = os.environ.copy()
    env.pop("LC_ALL", None)
    env["PCC_HOST_PYTHON"] = "/usr/bin/false"
    proc = subprocess.run(
        [
            str(pcc1),
            "-m",
            "pcc.package",
            "toolchain",
            "--search-path",
            str(bin_dir),
            "--require-cython",
            "--json",
        ],
        text=True,
        capture_output=True,
        timeout=30,
        env=env,
    )
    assert proc.returncode == 2
    report = json.loads(proc.stdout)
    assert report["tools"]["cython"]["found"] is False
    assert report["tools"]["cython"]["probe_ok"] is False
    assert "shim failed" in report["tools"]["cython"]["probe_output"]


def test_pcc1_toolchain_checks_cython_minimum_version(tmp_path):
    pcc1 = _find_current_pcc1()
    if pcc1 is None:
        skip_or_fail_no_current_pcc1("no current pcc1 binary with native toolchain shim")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _write_version_exe(bin_dir / "cython", "Cython version 0.29.36")
    env = os.environ.copy()
    env.pop("LC_ALL", None)
    env["PCC_HOST_PYTHON"] = "/usr/bin/false"
    proc = subprocess.run(
        [
            str(pcc1),
            "-m",
            "pcc.package",
            "toolchain",
            "--search-path",
            str(bin_dir),
            "--require-cython",
            "--min-cython-version",
            "3.0.6",
            "--json",
        ],
        text=True,
        capture_output=True,
        timeout=30,
        env=env,
    )
    assert proc.returncode == 2
    report = json.loads(proc.stdout)
    assert report["tools"]["cython"]["found"] is True
    assert report["diagnostics"][0]["code"] == "PCC-PKG-CYTHON-VERSION-TOO-OLD"
