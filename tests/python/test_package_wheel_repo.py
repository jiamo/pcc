from __future__ import annotations

import json
import os
import subprocess
import zipfile
from pathlib import Path

from pcc1_gate import repo_root

import pytest

from pcc1_gate import find_current_pcc1, skip_or_fail_no_current_pcc1

from pcc.package.metadata import current_platform_tag
from pcc.package.wheel_repo import repository_report


REPO = repo_root()
def _find_current_pcc1() -> Path | None:
    return find_current_pcc1(REPO)


def _write_wheel(path: Path, *, native_text: str = "", compressed: bool = False) -> Path:
    dist = path.name[:-4].rsplit("-", 3)[0]
    compression = zipfile.ZIP_DEFLATED if compressed else zipfile.ZIP_STORED
    with zipfile.ZipFile(path, "w", compression=compression) as zf:
        zf.writestr("demo_pkg/__init__.py", "VALUE = 1\n")
        if native_text:
            zf.writestr("demo_pkg/native.so", native_text)
        zf.writestr(f"{dist}.dist-info/METADATA", "Name: demo-pkg\n")
    return path


def test_repository_report_accepts_pure_and_pcc_native_wheels(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _write_wheel(repo / "demo_pkg-0.1-py3-none-any.whl")
    _write_wheel(repo / f"native_pkg-0.1-pcc3-pcc_native-{current_platform_tag()}.whl")

    report = repository_report(repo, write_manifest=True)
    assert report["ok"] is True
    assert report["artifact_count"] == 2
    assert {row["compatibility_reason"] for row in report["artifacts"]} == {
        "pure_python_wheel",
        "pcc_native_wheel",
    }
    manifest = Path(str(report["manifest_path"]))
    assert manifest.exists()
    assert json.loads(manifest.read_text(encoding="utf-8"))["schema"] == "pcc.wheel-repository.v1"


def test_repository_report_blocks_incompatible_or_libpython_wheels(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _write_wheel(repo / "bad_pkg-0.1-cp313-cp313-macosx_14_0_arm64.whl")
    _write_wheel(
        repo / f"native_pkg-0.1-pcc3-pcc_native-{current_platform_tag()}.whl",
        native_text="/usr/local/lib/libpython3.13.dylib\n",
        compressed=True,
    )

    report = repository_report(repo)
    assert report["ok"] is False
    assert {diag["code"] for diag in report["diagnostics"]} == {
        "PCC-REPO-TAG-INCOMPATIBLE",
        "PCC-PKG-003",
        "PCC-PKG-004",
    }


def test_pcc_package_wheel_repo_cli_adds_artifact_and_writes_manifest(tmp_path):
    artifact = _write_wheel(tmp_path / "demo_pkg-0.1-py3-none-any.whl")
    repo = tmp_path / "repo"
    env = os.environ.copy()
    env.pop("LC_ALL", None)
    proc = subprocess.run(
        [
            "uv",
            "run",
            "pcc",
            "-m",
            "pcc.package",
            "wheel-repo",
            "--root",
            str(repo),
            "--add",
            str(artifact),
            "--write-manifest",
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
    assert (repo / artifact.name).exists()
    assert Path(report["manifest_path"]).exists()


def test_pcc1_wheel_repo_does_not_need_host_python(tmp_path):
    pcc1 = _find_current_pcc1()
    if pcc1 is None:
        skip_or_fail_no_current_pcc1("no current pcc1 binary with native wheel-repo shim")
    repo = tmp_path / "repo"
    repo.mkdir()
    _write_wheel(repo / "demo_pkg-0.1-py3-none-any.whl")
    env = os.environ.copy()
    env.pop("LC_ALL", None)
    env["PCC_HOST_PYTHON"] = "/usr/bin/false"
    proc = subprocess.run(
        [
            str(pcc1),
            "-m",
            "pcc.package",
            "wheel-repo",
            "--root",
            str(repo),
            "--write-manifest",
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
    assert report["artifact_count"] == 1
    assert report["artifacts"][0]["compatibility_reason"] == "pure_python_wheel"
    assert Path(report["manifest_path"]).exists()
