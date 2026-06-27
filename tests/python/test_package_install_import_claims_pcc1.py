"""PKG-P0-INSTALL-IMPORT-SEPARATION pcc1 binary gate.

The in-process installer (``pcc.package.install``) already records install vs
import as SEPARATE, non-promoting manifest fields -- ``install_success``,
``import_attempted`` (False), ``import_success`` (None), and a never-true
``native_package_claim`` -- plus honest ``wheel_tags``. See the host-side gate
in ``test_package_install_import_claims.py``.

This gate proves the pcc1 *no-libpython* native pip-install mirror
(``cli_bootstrap._native_install_manifest_json`` behind ``-m pip install``,
non-dry-run) emits the SAME separated fields, so an install SUCCESS from the
compiled pcc1 binary can never silently imply import success, pcc-native ABI
support, or no-libpython package support.

Claim boundary under test (generic, no package-name special cases):
  * A pure-Python wheel install SUCCEEDS (``install_success`` true) but
    ``import_attempted`` is false and ``import_success`` is null -- import is a
    distinct, un-run gate -- and ``native_package_claim`` stays false.
  * A CPython-ABI wheel under ``--abi=pcc-native`` is rejected, yet the
    manifest still separates the claims: ``native_package_claim`` false,
    ``import_attempted`` false, ``import_success`` null, and honest
    ``wheel_tags`` recorded regardless of the install verdict.

The pcc1 binary is exercised with ``PCC_HOST_PYTHON=/usr/bin/false`` so the
native install path is proven to run without any host CPython fallback.
"""

from __future__ import annotations

import json
import os
import subprocess
import zipfile
from pathlib import Path

from pcc1_gate import find_current_pcc1, skip_or_fail_no_current_pcc1


def _repo_root() -> Path:
    here = Path(__file__).resolve()
    for parent in (here, *here.parents):
        if (parent / "AGENTS.md").is_file():
            return parent
    raise RuntimeError("could not locate repo root (AGENTS.md not found walking up)")


REPO_ROOT = _repo_root()


def _write_pure_python_wheel(path: Path) -> Path:
    """A trivial pure-Python wheel: ``py3-none-any``, only ``.py`` payloads."""
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("purepkg/__init__.py", "VALUE = 1\n")
        zf.writestr("purepkg/core.py", "def answer() -> int:\n    return 42\n")
        zf.writestr("purepkg-0.1.dist-info/METADATA", "Name: purepkg\nVersion: 0.1\n")
    return path


def _write_cpython_extension_wheel(path: Path) -> Path:
    """A wheel whose filename tag AND member ``.so`` name declare a CPython ABI.

    The pcc1 native scanner extracts the wheel and inspects the *member* ``.so``
    name for the CPython extension ABI, so the ``cpython-313`` marker must live
    on the member, not only on the outer ``cp313-cp313`` filename tag.
    """
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("extpkg/__init__.py", "VALUE = 1\n")
        zf.writestr(
            "extpkg/_ext.cpython-313-x86_64-linux-gnu.so",
            "cpython extension placeholder\n",
        )
        zf.writestr("extpkg-0.1.dist-info/METADATA", "Name: extpkg\nVersion: 0.1\n")
    return path


def _run_pcc1_pip_install(pcc1: Path, wheel: Path, tmp_path: Path, abi: str):
    """Run the pcc1 no-libpython native pip install (non-dry-run) on a wheel.

    Returns ``(proc, top_level_report, install_manifest)`` where the manifest is
    the single ``installs[0]`` element produced by
    ``_native_install_manifest_json``.
    """
    site = tmp_path / "site"
    cache = tmp_path / "cache"
    env = os.environ.copy()
    env.pop("LC_ALL", None)
    # Prove the native install path runs with no host CPython fallback.
    env["PCC_HOST_PYTHON"] = "/usr/bin/false"
    # Belt-and-suspenders: keep any default site/cache fallback inside tmp too.
    env["PCC_PACKAGE_SITE"] = str(site)
    env["PCC_PACKAGE_CACHE"] = str(cache)
    proc = subprocess.run(
        [
            str(pcc1),
            "-m",
            "pip",
            "install",
            str(wheel),
            "--target",
            str(site),
            "--cache-dir",
            str(cache),
            "--abi=" + abi,
        ],
        text=True,
        capture_output=True,
        timeout=120,
        env=env,
    )
    assert proc.stdout, (
        "pcc1 pip install produced no stdout (abi=" + abi + "); stderr=" + proc.stderr
    )
    report = json.loads(proc.stdout)
    assert report["command"] == "install"
    assert report["dry_run"] is False
    installs = report["installs"]
    assert len(installs) == 1, "expected exactly one install manifest, got " + repr(
        installs
    )
    return proc, report, installs[0]


def test_pcc1_pip_install_pure_py_records_install_without_import(tmp_path):
    pcc1 = find_current_pcc1(REPO_ROOT)
    if pcc1 is None:
        skip_or_fail_no_current_pcc1(
            "no current pcc1 binary with native install/import claim separation"
        )
    wheel = _write_pure_python_wheel(tmp_path / "purepkg-0.1-py3-none-any.whl")

    proc, report, manifest = _run_pcc1_pip_install(pcc1, wheel, tmp_path, "pcc-native")

    # A pure-Python wheel installs cleanly under pcc-native.
    assert proc.returncode == 0
    assert report["ok"] is True
    assert manifest["ok"] is True
    assert manifest["abi_mode"] == "pcc-native"
    assert manifest["manifest_schema"] == "pcc.package-manifest.v1"
    assert manifest["capability_profile"]["execution_mode"] == "pcc-native"

    # Install success is recorded, but import is a SEPARATE, un-run gate:
    # attempted false / success null. It never implies a native package claim.
    assert manifest["install_success"] is True
    assert manifest["import_attempted"] is False
    assert manifest["import_success"] is None
    assert manifest["install_native_package_claim"] is False
    assert manifest["linkage_native_package_claim"] is False
    assert manifest["native_package_claim"] is False

    # Honest wheel tags derived from the resolved artifact name.
    assert manifest["wheel_tags"]["python_tag"] == "py3"
    assert manifest["wheel_tags"]["abi_tag"] == "none"
    assert manifest["wheel_tags"]["platform_tag"] == "any"

    # The payload was actually placed on disk (a real local install skeleton).
    assert (tmp_path / "site" / "purepkg" / "__init__.py").exists()


def test_pcc1_pip_install_cpython_abi_never_claims_native(tmp_path):
    pcc1 = find_current_pcc1(REPO_ROOT)
    if pcc1 is None:
        skip_or_fail_no_current_pcc1(
            "no current pcc1 binary with native install/import claim separation"
        )
    wheel = _write_cpython_extension_wheel(
        tmp_path / "extpkg-0.1-cp313-cp313-linux_x86_64.whl"
    )

    proc, report, manifest = _run_pcc1_pip_install(pcc1, wheel, tmp_path, "pcc-native")

    # A CPython-ABI wheel is rejected under pcc-native (no-libpython) ABI...
    assert manifest["abi_mode"] == "pcc-native"
    assert manifest["manifest_schema"] == "pcc.package-manifest.v1"
    assert manifest["capability_profile"]["execution_mode"] == "pcc-native"
    assert manifest["uses_cpython_extension_abi"] is True
    assert manifest["ok"] is False
    assert manifest["install_success"] is False

    # ...but the claim separation holds regardless of the install verdict: a
    # CPython-ABI artifact is NEVER a pcc-native package claim, and import was
    # neither attempted nor claimed successful.
    assert manifest["native_package_claim"] is False
    assert manifest["install_native_package_claim"] is False
    assert manifest["linkage_native_package_claim"] is False
    assert manifest["import_attempted"] is False
    assert manifest["import_success"] is None

    # Wheel/ABI tags are still recorded honestly from the artifact name.
    assert manifest["wheel_tags"]["python_tag"] == "cp313"
    assert manifest["wheel_tags"]["abi_tag"] == "cp313"
    assert manifest["wheel_tags"]["platform_tag"] == "linux_x86_64"
