from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest


pytestmark = [pytest.mark.integration, pytest.mark.pcc_gate(env="PCC_RUN_NUMPY_L5_INTEGRATION")]

REPO = Path(__file__).resolve().parents[2]
NUMPY_ROOT = REPO / "projects" / "numpy-2.4.4"


def _require_numpy_l5_gate_enabled() -> None:
    if os.environ.get("PCC_RUN_NUMPY_L5_INTEGRATION") != "1":
        pytest.fail("set PCC_RUN_NUMPY_L5_INTEGRATION=1 to run the real NumPy L5 package gate")
    if not NUMPY_ROOT.exists():
        pytest.fail(f"real NumPy source tree is required: {NUMPY_ROOT}")


def test_real_numpy_l5_build_smoke_and_pcc_native_abi_blocker(tmp_path):
    _require_numpy_l5_gate_enabled()

    site = tmp_path / "numpy-site"
    cache = tmp_path / "numpy-cache"
    env = os.environ.copy()
    env.pop("LC_ALL", None)
    env["PCC_PACKAGE_BUILD_TIMEOUT"] = os.environ.get("PCC_PACKAGE_BUILD_TIMEOUT", "900")

    install = subprocess.run(
        [
            "uv",
            "run",
            "pcc",
            "-m",
            "pip",
            "install",
            str(NUMPY_ROOT),
            "--abi",
            "cpython-compat",
            "--target",
            str(site),
            "--cache-dir",
            str(cache),
        ],
        text=True,
        capture_output=True,
        timeout=1200,
        env=env,
        cwd=REPO,
    )
    assert install.returncode == 0, install.stderr or install.stdout
    report = json.loads(install.stdout)
    assert report["ok"] is True
    assert report["installs"][0]["build_report"]["actions"][0]["status"] == "passed"

    smoke_env = env.copy()
    smoke_env["PYTHONPATH"] = str(site)
    smoke = subprocess.run(
        [
            "uv",
            "run",
            "python",
            "-c",
            (
                "import numpy as np; "
                "print(np.__version__); "
                "print((np.array([1,2,3]) + 1).tolist())"
            ),
        ],
        text=True,
        capture_output=True,
        timeout=120,
        env=smoke_env,
        cwd=REPO,
    )
    assert smoke.returncode == 0, smoke.stderr
    assert smoke.stdout.splitlines()[-1] == "[2, 3, 4]"

    main = tmp_path / "numpy_import_smoke.py"
    exe = tmp_path / "numpy_import_smoke_bin"
    main.write_text(
        "import numpy as np\n"
        "print((np.array([1, 2, 3]) + 1).tolist())\n",
        encoding="utf-8",
    )
    pcc_env = env.copy()
    pcc_env["PCC_PACKAGE_SITE"] = str(site)
    pcc_native = subprocess.run(
        [
            "uv",
            "run",
            "pcc",
            "--backend",
            "self",
            "--python-libpython=off",
            "--ir-scaffold=on",
            str(main),
            "-o",
            str(exe),
        ],
        text=True,
        capture_output=True,
        timeout=240,
        env=pcc_env,
        cwd=REPO,
    )
    assert pcc_native.returncode != 0
    assert "PCC-PKG-004" in (pcc_native.stderr + pcc_native.stdout)

    if os.environ.get("PCC_RUN_NUMPY_L5_PCC1") == "1":
        raw_pcc1 = os.environ.get("PCC_CURRENT_PCC1")
        if not raw_pcc1:
            pytest.fail("PCC_RUN_NUMPY_L5_PCC1=1 requires PCC_CURRENT_PCC1")
        pcc1 = Path(raw_pcc1)
        if not pcc1.is_absolute():
            pcc1 = REPO / pcc1
        if not pcc1.exists():
            pytest.fail(f"PCC_CURRENT_PCC1 does not exist: {pcc1}")
        pcc1_env = pcc_env.copy()
        pcc1_env["PCC_HOST_PYTHON"] = "/usr/bin/false"
        pcc1_native = subprocess.run(
            [
                str(pcc1),
                "--backend",
                "self",
                "--python-libpython=off",
                "--ir-scaffold=on",
                str(main),
                "-o",
                str(tmp_path / "numpy_import_smoke_pcc1_bin"),
            ],
            text=True,
            capture_output=True,
            timeout=240,
            env=pcc1_env,
            cwd=REPO,
        )
        assert pcc1_native.returncode != 0
        assert "PCC-PKG-004" in (pcc1_native.stderr + pcc1_native.stdout)
