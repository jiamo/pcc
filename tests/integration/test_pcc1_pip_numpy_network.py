"""Command-shaped online acquisition -> pcc-native NumPy -> strict runtime gate."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.pcc_gate(env="PCC_RUN_PCC1_PIP_NUMPY_NETWORK")]

REPO = Path(__file__).resolve().parents[2]
def _require_enabled() -> Path:
    if os.environ.get("PCC_RUN_PCC1_PIP_NUMPY_NETWORK") != "1":
        pytest.fail(
            "set PCC_RUN_PCC1_PIP_NUMPY_NETWORK=1 to run online NumPy acquisition"
        )
    pcc1 = Path(
        os.environ.get("PCC1_BINARY", str(REPO / "build" / "bootstrap" / "pcc1"))
    ).expanduser()
    if not pcc1.is_file():
        pytest.fail(f"current self-host pcc1 binary required: {pcc1}")
    return pcc1.resolve()


def _last_json_object(output: str) -> dict[str, object]:
    marker = '{"command": "install"'
    start = output.find(marker)
    if start >= 0:
        return json.loads(output[start:])
    pytest.fail("pcc1 install emitted no JSON report:\n" + output[-4000:])


def test_pcc1_pip_install_numpy_from_network_then_array_add(tmp_path):
    pcc1 = _require_enabled()
    site = tmp_path / "site"
    cache = tmp_path / "cache"
    install_env = os.environ.copy()
    install_env.pop("LC_ALL", None)
    install = subprocess.run(
        [
            str(pcc1),
            "-m",
            "pip",
            "install",
            "numpy",
            "--target",
            str(site),
            "--cache-dir",
            str(cache),
        ],
        text=True,
        capture_output=True,
        timeout=480,
        env=install_env,
    )
    assert install.returncode == 0, install.stdout + install.stderr
    report = _last_json_object(install.stdout)
    assert report["ok"] is True, json.dumps(report, sort_keys=True)
    acquisition = report["acquisitions"][0]
    assert acquisition["acquire_mode_requested"] == "auto"
    assert acquisition["acquire_mode"] == "host"
    assert acquisition["host_assisted"] is True
    assert acquisition["target_python"] == "3.11"
    resolved_version = str(acquisition["resolved_version"])
    assert resolved_version.startswith("2.4.")
    assert acquisition["sha256"]
    assert Path(str(acquisition["artifact_path"])).is_file()

    extensions = list(site.glob("numpy/**/*.pcc*-pcc_native-*.so"))
    assert extensions, "install produced no pcc-native NumPy extension"
    assert not list(site.glob("numpy/**/*.cpython-*.so"))

    main = tmp_path / "main.py"
    main.write_text(
        "import numpy as np\n"
        "print(np.__version__)\n"
        "print([int(x) for x in np.array([1, 2, 3]) + 1])\n",
        encoding="utf-8",
    )
    app = tmp_path / "numpy_network_app"
    compile_env = os.environ.copy()
    compile_env.pop("LC_ALL", None)
    compile_env["PCC_PACKAGE_SITE"] = str(site)
    compile_env["PYTHONPATH"] = ""
    # This gate intentionally exercises the explicitly host-assisted
    # acquisition mode.  Compilation may use that same host interpreter to
    # locate stdlib source (for example copyreg); the produced artifact is
    # independently checked below with host Python and the package site both
    # unavailable.
    compile_proc = subprocess.run(
        [
            str(pcc1),
            "--backend",
            "self",
            "--python-libpython=off",
            "--ir-scaffold=on",
            str(main),
            "-o",
            str(app),
        ],
        text=True,
        capture_output=True,
        timeout=300,
        env=compile_env,
    )
    assert compile_proc.returncode == 0, compile_proc.stdout + compile_proc.stderr

    for backend in ("0", "1", "2", "3", "4"):
        run_env = os.environ.copy()
        run_env.pop("LC_ALL", None)
        run_env["PCC_GC_BACKEND"] = backend
        run_env["PCC_HOST_PYTHON"] = "/usr/bin/false"
        run_env["PCC_PACKAGE_SITE"] = ""
        run_env["PYTHONPATH"] = ""
        run = subprocess.run(
            [str(app)], text=True, capture_output=True, timeout=60, env=run_env
        )
        assert run.returncode == 0, f"GC{backend}: {run.stdout}\n{run.stderr}"
        assert run.stdout.splitlines() == [resolved_version, "[2, 3, 4]"], (
            f"GC{backend}: {run.stdout!r}"
        )

    linkage = subprocess.run(
        ["otool", "-L", str(app)], text=True, capture_output=True, timeout=30
    )
    assert linkage.returncode == 0, linkage.stderr
    lowered = linkage.stdout.lower()
    assert "libpython" not in lowered
    assert "python3" not in lowered
