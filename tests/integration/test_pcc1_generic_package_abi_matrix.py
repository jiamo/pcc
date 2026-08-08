"""Generic pure/native package ABI matrix through one current pcc1."""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile

import pytest

from tests.integration.pcc_native_e2e import install_pcc_native
from tests.python.pcc1_gate import find_current_pcc1


pytestmark = [pytest.mark.integration, pytest.mark.pcc_gate(probe="pcc1")]

REPO = Path(__file__).resolve().parents[2]
WHEEL_DIR = REPO / "tests" / "fixtures" / "packages"
NUMPY_SITE = REPO / "build" / "head-truth" / "numpy-core" / "site"
NUMPY_ROOT = REPO / "projects" / "numpy-2.4.4"
NUMPY_BUILD = NUMPY_ROOT / "build" / "pcc-package" / "meson-build"
SIMPLEJSON_SOURCE = REPO / "build" / "m1-site" / "simplejson-4.1.1"


def _current_pcc1() -> Path:
    pcc1 = find_current_pcc1(REPO)
    assert pcc1 is not None, "receipt-current pcc1 is required"
    return pcc1


def _hostless_env(*, package_site: str = "") -> dict[str, str]:
    env = os.environ.copy()
    for name in ("LC_ALL", "PYTHONPATH"):
        env.pop(name, None)
    env["PCC_HOST_PYTHON"] = "/usr/bin/false"
    env["PCC_HOST_PCC"] = "/usr/bin/false"
    env["PCC_PACKAGE_SITE"] = package_site
    return env


def _assert_no_libpython(executable: Path) -> None:
    if sys.platform == "darwin":
        command = ["otool", "-L", str(executable)]
    elif sys.platform.startswith("linux"):
        command = ["readelf", "-d", str(executable)]
    else:
        pytest.fail("generic package ABI matrix supports Darwin and Linux")
    result = subprocess.run(
        command,
        cwd=REPO,
        text=True,
        capture_output=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    lowered = (result.stdout + result.stderr).lower()
    assert "libpython" not in lowered
    assert "python.framework" not in lowered


def _compile_and_run_gc_matrix(
    pcc1: Path,
    tmp_path: Path,
    *,
    label: str,
    source_text: str,
    package_site: str,
    expected_stdout: str,
) -> None:
    source = tmp_path / (label + ".py")
    executable = tmp_path / label
    source.write_text(source_text, encoding="utf-8")
    environment = _hostless_env(package_site=package_site)
    compiled = subprocess.run(
        [
            str(pcc1),
            "--backend",
            "self",
            "--python-libpython=off",
            "--ir-scaffold=on",
            str(source),
            "-o",
            str(executable),
        ],
        cwd=REPO,
        env=environment,
        text=True,
        capture_output=True,
        timeout=600,
    )
    assert compiled.returncode == 0, compiled.stdout + compiled.stderr
    _assert_no_libpython(executable)

    observed: dict[str, str] = {}
    for backend in range(5):
        run_env = _hostless_env()
        run_env["PCC_GC_BACKEND"] = str(backend)
        ran = subprocess.run(
            [str(executable)],
            cwd=REPO,
            env=run_env,
            text=True,
            capture_output=True,
            timeout=60,
        )
        assert ran.returncode == 0, (
            f"{label} GC{backend} failed:\n{ran.stdout}{ran.stderr}"
        )
        assert ran.stderr == "", f"{label} GC{backend}: {ran.stderr}"
        assert ran.stdout == expected_stdout
        observed[str(backend)] = ran.stdout
    assert len(set(observed.values())) == 1


def test_pure_python_wheel_uses_explicit_non_native_capability_profile_gc0_to_gc4(
    tmp_path: Path,
) -> None:
    pcc1 = _current_pcc1()
    wheel = WHEEL_DIR / "wheel-0.45.1-py3-none-any.whl"
    assert wheel.is_file(), f"missing pinned wheel fixture: {wheel}"
    site = tmp_path / "site"
    report = install_pcc_native(
        pcc1,
        "wheel",
        find_links=[WHEEL_DIR],
        target=site,
        timeout=300,
    )
    manifest = report["installs"][0]
    assert manifest["manifest_schema"] == "pcc.package-manifest.v1"
    assert manifest["capability_profile"]["execution_mode"] == "pcc-native"
    assert manifest["install_success"] is True
    assert manifest["uses_cpython_extension_abi"] is False
    assert manifest["native_package_claim"] is False
    _compile_and_run_gc_matrix(
        pcc1,
        tmp_path,
        label="pure_wheel",
        source_text="import wheel\nprint(wheel.__version__)\n",
        package_site=str(site),
        expected_stdout="0.45.1\n",
    )


def test_real_simplejson_source_extension_is_pcc_native_gc0_to_gc4(
    tmp_path: Path,
) -> None:
    pcc1 = _current_pcc1()
    assert (SIMPLEJSON_SOURCE / "simplejson" / "_speedups.c").is_file(), (
        "pinned simplejson 4.1.1 source must be acquired before this matrix: "
        + str(SIMPLEJSON_SOURCE)
    )
    source = tmp_path / "simplejson-4.1.1"
    shutil.copytree(
        SIMPLEJSON_SOURCE,
        source,
        ignore=shutil.ignore_patterns(
            "*.so",
            "*.dylib",
            "*.pyd",
            "pcc-package.json",
            "build",
            "__pycache__",
            "*.egg-info",
        ),
    )
    sdist = tmp_path / "simplejson-4.1.1.tar.gz"
    with tarfile.open(sdist, "w:gz") as archive:
        archive.add(source, arcname="simplejson-4.1.1")
    site = tmp_path / "site"
    report = install_pcc_native(
        pcc1,
        str(sdist),
        find_links=(),
        target=site,
        timeout=600,
    )
    manifest = report["installs"][0]
    assert manifest["manifest_schema"] == "pcc.package-manifest.v1"
    assert manifest["capability_profile"]["execution_mode"] == "pcc-native"
    assert manifest["install_success"] is True
    assert manifest["uses_cpython_extension_abi"] is False
    assert manifest["linkage_native_package_claim"] is True
    assert manifest["build_report"]["build_ownership"] == "owned"
    assert manifest["build_report"]["host_assisted"] is False
    _compile_and_run_gc_matrix(
        pcc1,
        tmp_path,
        label="simplejson_package",
        package_site=str(site),
        source_text=(
            "import simplejson\n"
            "import simplejson.decoder as decoder\n"
            "import simplejson.encoder as encoder\n"
            "import simplejson.scanner as scanner\n"
            "native = (scanner.c_make_scanner is not None "
            "and decoder.c_scanstring is not None "
            "and encoder.c_make_encoder is not None)\n"
            "payload = {'items': [1, 'two', None], 'ok': True}\n"
            "encoded = simplejson.dumps(payload, separators=(',', ':'), "
            "sort_keys=True)\n"
            "print('native', native)\n"
            "print(encoded)\n"
            "print(simplejson.loads(encoded) == payload)\n"
        ),
        expected_stdout=(
            "native True\n"
            '{"items":[1,"two",null],"ok":true}\n'
            "True\n"
        ),
    )


def test_numpy_uses_same_pcc_native_loader_and_gc_contract_gc0_to_gc4(
    tmp_path: Path,
) -> None:
    pcc1 = _current_pcc1()
    manifest_path = NUMPY_SITE / "numpy" / "pcc-package.json"
    assert manifest_path.is_file(), (
        "pcc-native NumPy site must be built before this matrix: "
        + str(NUMPY_SITE)
    )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["manifest_schema"] == "pcc.package-manifest.v1"
    assert manifest["capability_profile"]["execution_mode"] == "pcc-native"
    assert manifest["uses_cpython_extension_abi"] is False
    package_site = os.pathsep.join(
        [str(NUMPY_SITE), str(NUMPY_BUILD), str(NUMPY_ROOT)]
    )
    _compile_and_run_gc_matrix(
        pcc1,
        tmp_path,
        label="numpy_package",
        package_site=package_site,
        source_text=(
            "import numpy as np\n"
            "a = np.array([1, 2, 3])\n"
            "print(np.__version__)\n"
            "print([int(x) for x in a + 1])\n"
        ),
        expected_stdout="2.4.4\n[2, 3, 4]\n",
    )
