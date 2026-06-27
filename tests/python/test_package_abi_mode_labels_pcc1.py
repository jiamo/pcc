"""PKG-P0-ABI-MODE-LABELS pcc1 binary gate.

The in-process ``linkage_report`` already carries explicit execution-mode
labels (``execution_mode`` / ``native_package_claim``) on the top-level result
and every per-artifact scan (see ``test_package_abi_mode_labels.py``). This
gate proves the pcc1 *no-libpython* CLI mirror
(``cli_bootstrap._native_linkage_json`` behind ``-m pcc.package.linkage``)
emits the same labels, so an A-mode (libpython / cpython-compat) compatibility
SUCCESS can never silently promote to a B-mode (no-libpython / pcc-native)
native package claim when the linkage runs through the compiled pcc1 binary.

Claim boundary under test (generic, no package-name special cases):
  * ``--abi=libpython`` on a CPython-ABI wheel -> ``execution_mode ==
    "cpython-compat"`` with ``native_package_claim`` false (compat never
    claims native), on both the top-level report and every scan.
  * ``--abi=pcc-native`` on the identical CPython-ABI wheel -> rejected with a
    PCC-PKG-004 diagnostic, ``execution_mode == "pcc-native"``, and still
    ``native_package_claim`` false.

The pcc1 binary is exercised with ``PCC_HOST_PYTHON=/usr/bin/false`` so the
linkage path is proven to run without any host CPython fallback.
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


def _make_cpython_abi_wheel(tmp_path: Path) -> Path:
    """Build a realistic CPython binary wheel fixture in-process.

    The bundled extension member name declares a CPython extension ABI
    (``cpython-313``) and links ``libpython3.13`` -- exactly what a real
    CPython binary wheel does. The pcc1 native archive scanner extracts the
    wheel and inspects the *member* ``.so`` name, so the CPython ABI marker
    must live on the member, not only on the outer wheel filename. Built purely
    with the in-process ``zipfile`` API; no subprocess / shell out.
    """

    wheel = tmp_path / "foo-1.0-cp313-cp313-macosx.whl"
    with zipfile.ZipFile(wheel, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("foo/__init__.py", "VALUE = 1\n")
        # CPython-ABI extension member that links libpython (cpython-compat).
        zf.writestr(
            "foo/_foo.cpython-313-darwin.so",
            "\x7fELF\x00...linked against /usr/local/lib/libpython3.13.dylib\x00",
        )
        zf.writestr("foo-1.0.dist-info/METADATA", "Name: foo\nVersion: 1.0\n")
    return wheel


def _run_pcc1_linkage(pcc1: Path, abi_mode: str, artifact: Path):
    env = os.environ.copy()
    env.pop("LC_ALL", None)
    # Prove the native linkage path runs with no host CPython fallback.
    env["PCC_HOST_PYTHON"] = "/usr/bin/false"
    proc = subprocess.run(
        [
            str(pcc1),
            "-m",
            "pcc.package.linkage",
            "--abi=" + abi_mode,
            "--artifact",
            str(artifact),
            "--json",
        ],
        text=True,
        capture_output=True,
        timeout=60,
        env=env,
    )
    assert proc.stdout, (
        "pcc1 linkage produced no stdout (abi=" + abi_mode + "); stderr=" + proc.stderr
    )
    return proc, json.loads(proc.stdout)


def _run_pcc1_linkage_empty(pcc1: Path, abi_mode: str):
    env = os.environ.copy()
    env.pop("LC_ALL", None)
    # Prove the native linkage path runs with no host CPython fallback.
    env["PCC_HOST_PYTHON"] = "/usr/bin/false"
    proc = subprocess.run(
        [
            str(pcc1),
            "-m",
            "pcc.package.linkage",
            "--abi=" + abi_mode,
            "--json",
        ],
        text=True,
        capture_output=True,
        timeout=60,
        env=env,
    )
    assert proc.stdout, (
        "pcc1 empty linkage produced no stdout (abi="
        + abi_mode
        + "); stderr="
        + proc.stderr
    )
    return proc, json.loads(proc.stdout)


def test_pcc1_linkage_libpython_reports_cpython_compat_no_native_claim(tmp_path):
    pcc1 = find_current_pcc1(REPO_ROOT)
    if pcc1 is None:
        skip_or_fail_no_current_pcc1(
            "no current pcc1 binary with native linkage ABI-mode labels"
        )
    wheel = _make_cpython_abi_wheel(tmp_path)

    proc, report = _run_pcc1_linkage(pcc1, "libpython", wheel)

    # libpython abi: the compatibility runner accepts the CPython wheel.
    assert proc.returncode == 0
    assert report["ok"] is True
    # ...labeled explicitly as cpython-compat...
    assert report["execution_mode"] == "cpython-compat"
    # ...but it NEVER promotes to a native package claim.
    assert report["native_package_claim"] is False
    assert report["capability_profile"]["execution_mode"] == "cpython-compat"

    # Per-scan results carry the same non-promoting labels.
    assert report["scans"], "expected at least one artifact scan"
    for scan in report["scans"]:
        assert scan["execution_mode"] == "cpython-compat"
        assert scan["native_package_claim"] is False


def test_pcc1_linkage_pcc_native_cpython_abi_rejected_no_native_claim(tmp_path):
    pcc1 = find_current_pcc1(REPO_ROOT)
    if pcc1 is None:
        skip_or_fail_no_current_pcc1(
            "no current pcc1 binary with native linkage ABI-mode labels"
        )
    wheel = _make_cpython_abi_wheel(tmp_path)

    proc, report = _run_pcc1_linkage(pcc1, "pcc-native", wheel)

    # pcc-native (no-libpython) abi: the identical CPython wheel is rejected.
    assert proc.returncode == 2
    assert report["ok"] is False
    assert report["execution_mode"] == "pcc-native"
    # The CPython-ABI marker on the wheel member fired PCC-PKG-004...
    assert report["uses_cpython_extension_abi"] is True
    codes = {diag.get("code") for diag in report["diagnostics"]}
    assert "PCC-PKG-004" in codes
    # ...so a rejected CPython-ABI artifact never earns a native package claim.
    assert report["native_package_claim"] is False
    assert report["capability_profile"]["execution_mode"] == "pcc-native"

    # Per-scan results agree: pcc-native mode, no native claim.
    assert report["scans"], "expected at least one artifact scan"
    for scan in report["scans"]:
        assert scan["execution_mode"] == "pcc-native"
        assert scan["native_package_claim"] is False


def test_pcc1_linkage_empty_scan_never_claims_native_package_support():
    pcc1 = find_current_pcc1(REPO_ROOT)
    if pcc1 is None:
        skip_or_fail_no_current_pcc1(
            "no current pcc1 binary with native linkage empty-scan claim labels"
        )

    proc, report = _run_pcc1_linkage_empty(pcc1, "pcc-native")

    assert proc.returncode == 0
    assert report["ok"] is True
    assert report["execution_mode"] == "pcc-native"
    assert report["scans"] == []
    assert report["native_package_claim"] is False
