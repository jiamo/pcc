from __future__ import annotations

import json
import os
import subprocess
import zipfile
from pathlib import Path

import pytest

from pcc1_gate import find_current_pcc1, skip_or_fail_no_current_pcc1

from pcc.package.linkage import linkage_report, scan_artifact, scan_link_command


REPO = Path(__file__).resolve().parents[2]
def _find_current_pcc1() -> Path | None:
    return find_current_pcc1(REPO)


def test_scan_link_command_detects_libpython_edges():
    scan = scan_link_command("cc -shared module.o -L/opt/lib -lpython3.13 -o demo.so")
    assert scan["links_libpython"] is True
    assert any("lpython" in edge for edge in scan["link_libpython_edges"])
    assert scan["diagnostics"][0]["code"] == "PCC-PKG-003"


def test_scan_artifact_detects_libpython_bytes(tmp_path):
    artifact = tmp_path / "demo.so"
    artifact.write_bytes(b"ELF\0.../usr/lib/libpython3.13.dylib\0")
    clean = tmp_path / "clean.so"
    clean.write_bytes(b"ELF\0...libpcc_runtime.dylib\0")
    assert scan_artifact(artifact)["links_libpython"] is True
    assert scan_artifact(clean)["links_libpython"] is False


def test_scan_artifact_detects_cpython_extension_abi_filename(tmp_path):
    artifact = tmp_path / "demo_pkg" / "_demo.cpython-314-darwin.so"
    artifact.parent.mkdir()
    artifact.write_bytes(b"ELF\0...libpcc_runtime.dylib\0")

    scan = scan_artifact(artifact)

    assert scan["links_libpython"] is False
    assert scan["uses_cpython_extension_abi"] is True
    assert scan["diagnostics"][0]["code"] == "PCC-PKG-004"


def test_scan_artifact_detects_libpython_inside_compressed_wheel(tmp_path):
    wheel = tmp_path / "demo_pkg-0.1-py3-none-any.whl"
    with zipfile.ZipFile(wheel, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("demo_pkg/__init__.py", "VALUE = 1\n")
        zf.writestr("demo_pkg/native.so", "/usr/local/lib/libpython3.13.dylib\n")
        zf.writestr("demo_pkg-0.1.dist-info/METADATA", "Name: demo-pkg\n")

    scan = scan_artifact(wheel)
    assert scan["links_libpython"] is True
    assert scan["archive_scans"][0]["kind"] == "archive_member"
    assert scan["diagnostics"][0]["code"] == "PCC-PKG-003"


def test_scan_artifact_detects_cpython_extension_abi_inside_wheel(tmp_path):
    wheel = tmp_path / "demo_pkg-0.1-py3-none-any.whl"
    with zipfile.ZipFile(wheel, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("demo_pkg/__init__.py", "VALUE = 1\n")
        zf.writestr("demo_pkg/_demo.cpython-314-darwin.so", "libpcc_runtime\n")
        zf.writestr("demo_pkg-0.1.dist-info/METADATA", "Name: demo-pkg\n")

    scan = scan_artifact(wheel)

    assert scan["links_libpython"] is False
    assert scan["uses_cpython_extension_abi"] is True
    assert scan["archive_scans"][0]["uses_cpython_extension_abi"] is True
    assert scan["diagnostics"][0]["code"] == "PCC-PKG-004"


def test_static_archive_debug_paths_do_not_count_as_runtime_libpython(tmp_path):
    archive = tmp_path / "libdemo.a"
    archive.write_bytes(b"!<arch>\n.../Python.framework/Versions/3.14/include/python3.14\0")
    assert scan_artifact(archive)["links_libpython"] is False

    root_report = linkage_report(roots=[str(tmp_path)], abi_mode="pcc-native")
    assert root_report["ok"] is True
    assert root_report["links_libpython"] is False


def test_bootstrap_native_artifact_scan_uses_small_tool_output(monkeypatch, tmp_path):
    from pcc import cli_bootstrap

    artifact = tmp_path / "demo.so"
    artifact.write_bytes(b"ELF\0" + (b"x" * 4_000_000))
    commands: list[str] = []

    def fake_command_output(command: str, label: str) -> str:
        commands.append(command)
        assert label == "linkage_scan"
        return "/usr/local/lib/libpython3.14.dylib" if "strings -a" in command else ""

    monkeypatch.setattr(cli_bootstrap, "_native_command_output_line", fake_command_output)

    edge = cli_bootstrap._native_artifact_mentions_libpython(str(artifact))

    assert edge == "libpython3.14.dylib"
    assert any("otool -L" in command for command in commands)
    assert any("strings -a" in command for command in commands)


def test_linkage_report_blocks_pcc_native_but_allows_explicit_libpython_mode(tmp_path):
    artifact = tmp_path / "demo.so"
    artifact.write_text("linked against Python.framework/Versions/3.13/Python", encoding="utf-8")
    report = linkage_report(artifacts=[str(artifact)], abi_mode="pcc-native")
    assert report["ok"] is False
    assert report["links_libpython"] is True
    assert report["no_libpython_runtime"] is False
    assert report["diagnostics"][0]["code"] == "PCC-PKG-003"

    compat = linkage_report(artifacts=[str(artifact)], abi_mode="libpython")
    assert compat["ok"] is True
    assert compat["links_libpython"] is True
    assert compat["no_libpython_runtime"] is False


def test_linkage_report_blocks_cpython_extension_abi_in_pcc_native_mode(tmp_path):
    artifact = tmp_path / "_demo.cpython-314-darwin.so"
    artifact.write_text("libpcc_runtime", encoding="utf-8")

    report = linkage_report(artifacts=[str(artifact)], abi_mode="pcc-native")

    assert report["ok"] is False
    assert report["links_libpython"] is False
    assert report["uses_cpython_extension_abi"] is True
    assert report["no_libpython_runtime"] is False
    assert report["diagnostics"][0]["code"] == "PCC-PKG-004"

    compat = linkage_report(artifacts=[str(artifact)], abi_mode="cpython-compat")
    assert compat["ok"] is True
    assert compat["uses_cpython_extension_abi"] is True
    assert compat["no_libpython_runtime"] is False


def test_bootstrap_native_linkage_blocks_cpython_extension_abi_in_pcc_native_mode(tmp_path):
    from pcc import cli_bootstrap

    artifact = tmp_path / "_demo.cpython-314-darwin.so"
    artifact.write_text("libpcc_runtime", encoding="utf-8")

    report = json.loads(cli_bootstrap._native_linkage_json([str(artifact)], [], [], "pcc-native"))

    assert report["ok"] is False
    assert report["links_libpython"] is False
    assert report["uses_cpython_extension_abi"] is True
    assert report["no_libpython_runtime"] is False
    assert report["diagnostics"][0]["code"] == "PCC-PKG-004"

    compat = json.loads(cli_bootstrap._native_linkage_json([str(artifact)], [], [], "cpython-compat"))
    assert compat["ok"] is True
    assert compat["uses_cpython_extension_abi"] is True
    assert compat["no_libpython_runtime"] is False


def test_pcc_package_linkage_cli_scans_root(tmp_path):
    root = tmp_path / "site"
    root.mkdir()
    (root / "clean.so").write_bytes(b"libpcc_runtime")
    (root / "bad.so").write_bytes(b"libpython3.13.dylib")
    env = os.environ.copy()
    env.pop("LC_ALL", None)
    proc = subprocess.run(
        [
            "uv",
            "run",
            "pcc",
            "-m",
            "pcc.package",
            "linkage",
            "--root",
            str(root),
            "--command",
            "cc -shared x.o -o x.so",
            "--json",
        ],
        text=True,
        capture_output=True,
        timeout=60,
        env=env,
    )
    assert proc.returncode == 2
    report = json.loads(proc.stdout)
    assert report["links_libpython"] is True
    assert any("libpython" in edge.lower() for edge in report["link_libpython_edges"])


def test_pcc1_linkage_cli_does_not_need_host_python(tmp_path):
    pcc1 = _find_current_pcc1()
    if pcc1 is None:
        skip_or_fail_no_current_pcc1("no current pcc1 binary with native linkage shim")
    artifact = tmp_path / "demo.so"
    artifact.write_text("/usr/local/lib/libpython3.13.dylib", encoding="utf-8")
    env = os.environ.copy()
    env.pop("LC_ALL", None)
    env["PCC_HOST_PYTHON"] = "/bin/false"
    proc = subprocess.run(
        [
            str(pcc1),
            "-m",
            "pcc.package",
            "linkage",
            "--artifact",
            str(artifact),
            "--command",
            "cc -shared x.o -lpython3.13 -o x.so",
            "--json",
        ],
        text=True,
        capture_output=True,
        timeout=30,
        env=env,
    )
    assert proc.returncode == 2
    report = json.loads(proc.stdout)
    assert report["ok"] is False
    assert report["links_libpython"] is True
    assert report["diagnostics"][0]["code"] == "PCC-PKG-003"
