"""Default pcc1 package environment: install once, then compile without path flags."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess

import pytest

REPO = Path(__file__).resolve().parents[2]

_PCC1_GATE_PATH = Path(
    os.environ.get("PCC1_BINARY", str(REPO / "build" / "bootstrap" / "pcc1"))
).expanduser()
pytestmark = [
    pytest.mark.integration,
    pytest.mark.pcc_gate(probe="pcc1"),
]
NUMPY_EXPECTED_VERSION_PREFIX = "2.4."


def _pcc1_binary() -> Path:
    path = Path(
        os.environ.get("PCC1_BINARY", str(REPO / "build" / "bootstrap" / "pcc1"))
    ).expanduser()
    if not path.is_file():
        pytest.fail(f"current self-host pcc1 binary required: {path}")
    return path.resolve()


def _environment(tmp_path: Path) -> dict[str, str]:
    env = os.environ.copy()
    for name in (
        "LC_ALL",
        "PCC_DATA_HOME",
        "PCC_ENVIRONMENT",
        "PCC_PACKAGE_SITE",
        "XDG_DATA_HOME",
    ):
        env.pop(name, None)
    env["VIRTUAL_ENV"] = str(tmp_path / "venv")
    env["PYTHONPATH"] = ""
    return env


def _install_report(process: subprocess.CompletedProcess[str]) -> dict[str, object]:
    marker = '{"command": "install"'
    start = process.stdout.find(marker)
    assert start >= 0, "pcc1 install emitted no JSON report:\n" + process.stdout[-4000:]
    return json.loads(process.stdout[start:])


def _selected_environment(pcc1: Path, env: dict[str, str]) -> dict[str, object]:
    process = subprocess.run(
        [str(pcc1), "env", "info", "--json"],
        text=True,
        capture_output=True,
        timeout=30,
        env=env,
    )
    assert process.returncode == 0, process.stdout + process.stderr
    return json.loads(process.stdout)


def _assert_no_libpython(path: Path) -> None:
    linkage = subprocess.run(
        ["otool", "-L", str(path)],
        text=True,
        capture_output=True,
        timeout=30,
    )
    assert linkage.returncode == 0, linkage.stderr
    lowered = linkage.stdout.lower()
    assert "libpython" not in lowered, linkage.stdout
    assert "python3" not in lowered, linkage.stdout


def test_generic_package_default_install_is_immediately_importable(tmp_path):
    pcc1 = _pcc1_binary()
    env = _environment(tmp_path)
    source = tmp_path / "generic-source"
    package = source / "default_env_demo"
    package.mkdir(parents=True)
    (source / "pyproject.toml").write_text(
        '[project]\nname = "default-env-demo"\nversion = "1.0.0"\n',
        encoding="utf-8",
    )
    (package / "__init__.py").write_text("VALUE = 42\n", encoding="utf-8")

    install = subprocess.run(
        [str(pcc1), "-m", "pip", "install", str(source)],
        text=True,
        capture_output=True,
        timeout=120,
        env=env,
    )
    assert install.returncode == 0, install.stdout + install.stderr
    report = _install_report(install)
    assert report["ok"] is True, json.dumps(report, sort_keys=True)

    selected = _selected_environment(pcc1, env)
    site = Path(str(selected["selected_site_packages"]))
    installed = Path(str(report["installs"][0]["installed_path"]))
    assert installed.is_relative_to(site)
    assert selected["selection_reason"] == "virtual-env"
    assert selected["override_provenance"]["PCC_PACKAGE_SITE"] is False

    main = tmp_path / "main.py"
    main.write_text(
        "import default_env_demo\nprint(default_env_demo.VALUE)\n",
        encoding="utf-8",
    )
    app = tmp_path / "generic-app"
    compile_process = subprocess.run(
        [str(pcc1), str(main), "-o", str(app)],
        text=True,
        capture_output=True,
        timeout=180,
        env=env,
    )
    assert compile_process.returncode == 0, (
        compile_process.stdout + compile_process.stderr
    )

    run_env = env.copy()
    run_env["PCC_HOST_PYTHON"] = "/usr/bin/false"
    run_process = subprocess.run(
        [str(app)], text=True, capture_output=True, timeout=30, env=run_env
    )
    assert run_process.returncode == 0, run_process.stdout + run_process.stderr
    assert run_process.stdout == "42\n"
    _assert_no_libpython(app)


@pytest.mark.pcc_gate(env="PCC_RUN_PCC1_DEFAULT_ENV_NUMPY")
def test_numpy_default_install_then_array_add_without_package_path(tmp_path):
    if os.environ.get("PCC_RUN_PCC1_DEFAULT_ENV_NUMPY") != "1":
        pytest.fail(
            "set PCC_RUN_PCC1_DEFAULT_ENV_NUMPY=1 to run the default-env NumPy gate"
        )
    pcc1 = _pcc1_binary()
    env = _environment(tmp_path)
    env["PCC_PACKAGE_CACHE"] = str(
        REPO / "build" / "test-package-cache" / "default-env"
    )

    install = subprocess.run(
        [str(pcc1), "-m", "pip", "install", "numpy"],
        text=True,
        capture_output=True,
        timeout=720,
        env=env,
    )
    assert install.returncode == 0, install.stdout + install.stderr
    report = _install_report(install)
    assert report["ok"] is True, json.dumps(report, sort_keys=True)
    assert report["acquisitions"][0]["acquire_mode"] == "owned"
    assert report["acquisitions"][0]["host_assisted"] is False
    assert report["acquisitions"][0]["hash_verified"] is True
    assert (
        report["acquisitions"][0]["build_isolation"]
        == "delegated-to-pcc-native-builder"
    )
    resolved_version = str(report["acquisitions"][0]["resolved_version"])
    assert resolved_version.startswith(NUMPY_EXPECTED_VERSION_PREFIX)

    selected = _selected_environment(pcc1, env)
    site = Path(str(selected["selected_site_packages"]))
    installed = Path(str(report["installs"][0]["installed_path"]))
    assert installed.is_relative_to(site)
    assert list(site.glob("numpy/**/*.pcc*-pcc_native-*.so"))
    assert not list(site.glob("numpy/**/*.cpython-*.so"))

    main = tmp_path / "numpy_main.py"
    main.write_text(
        "import numpy as np\n"
        "print(np.__version__)\n"
        "print([int(x) for x in np.array([1, 2, 3]) + 1])\n",
        encoding="utf-8",
    )
    app = tmp_path / "numpy-app"
    compile_process = subprocess.run(
        [str(pcc1), str(main), "-o", str(app)],
        text=True,
        capture_output=True,
        timeout=600,
        env=env,
    )
    assert compile_process.returncode == 0, (
        compile_process.stdout + compile_process.stderr
    )

    for backend in ("0", "1", "2", "3", "4"):
        run_env = env.copy()
        run_env["PCC_GC_BACKEND"] = backend
        run_env["PCC_HOST_PYTHON"] = "/usr/bin/false"
        run_env.pop("PCC_PACKAGE_CACHE", None)
        run_process = subprocess.run(
            [str(app)], text=True, capture_output=True, timeout=60, env=run_env
        )
        assert (
            run_process.returncode == 0
        ), f"GC{backend}: {run_process.stdout}\n{run_process.stderr}"
        assert run_process.stdout.splitlines() == [resolved_version, "[2, 3, 4]"]
    _assert_no_libpython(app)
