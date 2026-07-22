"""Pinned M2-NUMPY-L5 integration gate.

Proves NumPy array construction + scalar addition runs end-to-end through a
self-backend, no-libpython pcc1 binary against the pcc-native NumPy site, and
that the identical artifact yields the correct result under PCC_GC_BACKEND=0..4.

Env-gated like L4/L5-build so it never runs in the normal suite: set
``PCC_RUN_NUMPY_L5_ARRAY_INTEGRATION=1`` to enable. Reuses the prebuilt
pcc-native NumPy core site (``build/head-truth/numpy-core/site``) and a self-host
pcc1 (``PCC1_BINARY`` env override, else ``build/bootstrap/pcc1``); skips with an
explicit reason when a prerequisite is missing rather than fabricating success.

The program uses ``[int(x) for x in np.array([1,2,3]) + 1]`` — exercising the
two runtime features L5 required under no-libpython: the C-extension sequence
protocol (PySequence_Check accepts a cext ndarray so PySeqIter iteration is
driven) and numpy-scalar unboxing (py_cext_number_to_i64 via nb_int).
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.pcc_gate(env="PCC_RUN_NUMPY_L5_ARRAY_INTEGRATION")]

REPO = Path(__file__).resolve().parents[2]
NUMPY_SITE = REPO / "build" / "head-truth" / "numpy-core" / "site"
NUMPY_ROOT = REPO / "projects" / "numpy-2.4.4"
MESON_BUILD = NUMPY_ROOT / "build" / "pcc-package" / "meson-build"
EXPECTED = "[2, 3, 4]"


def _pcc1_binary() -> Path:
    env_path = os.environ.get("PCC1_BINARY")
    if env_path:
        return Path(env_path)
    return REPO / "build" / "bootstrap" / "pcc1"


def _require_gate_enabled() -> Path:
    if os.environ.get("PCC_RUN_NUMPY_L5_ARRAY_INTEGRATION") != "1":
        pytest.fail("set PCC_RUN_NUMPY_L5_ARRAY_INTEGRATION=1 to run the real NumPy L5 array gate")
    pcc1 = _pcc1_binary()
    if not pcc1.is_file():
        pytest.fail(f"self-host pcc1 binary required: {pcc1}")
    if not (NUMPY_SITE / "numpy" / "_core").is_dir():
        pytest.fail(f"pcc-native NumPy core site required: {NUMPY_SITE}")
    return pcc1


def test_numpy_l5_array_add_through_pcc1_all_gc_backends(tmp_path):
    pcc1 = _require_gate_enabled()

    main = tmp_path / "main.py"
    main.write_text(
        "import numpy as np\n"
        "print([int(x) for x in np.array([1, 2, 3]) + 1])\n",
        encoding="utf-8",
    )
    app = tmp_path / "numpy_l5_app"

    compile_env = os.environ.copy()
    compile_env.pop("LC_ALL", None)
    compile_env["PCC_PACKAGE_SITE"] = os.pathsep.join(
        [str(NUMPY_SITE), str(MESON_BUILD), str(NUMPY_ROOT)]
    )
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
        timeout=600,
        env=compile_env,
    )
    assert compile_proc.returncode == 0, (
        f"pcc1 numpy L5 compile failed:\n{compile_proc.stdout}\n{compile_proc.stderr}"
    )
    assert app.is_file(), "pcc1 produced no L5 binary"

    # The identical artifact must produce [2, 3, 4] under every GC backend,
    # run isolated (no host Python, no package-site env).
    for backend in ("0", "1", "2", "3", "4"):
        run_env = os.environ.copy()
        run_env.pop("LC_ALL", None)
        run_env["PCC_GC_BACKEND"] = backend
        run_env["PCC_HOST_PYTHON"] = "/usr/bin/false"
        run_env["PYTHONPATH"] = ""
        run_env["PCC_PACKAGE_SITE"] = ""
        run_proc = subprocess.run(
            [str(app)], text=True, capture_output=True, timeout=120, env=run_env
        )
        assert run_proc.returncode == 0, (
            f"GC{backend}: exit {run_proc.returncode}\n{run_proc.stdout}\n{run_proc.stderr}"
        )
        assert run_proc.stdout.strip() == EXPECTED, f"GC{backend}: {run_proc.stdout!r}"

    otool = subprocess.run(
        ["otool", "-L", str(app)], text=True, capture_output=True, timeout=60
    )
    assert otool.returncode == 0, otool.stderr
    lowered = otool.stdout.lower()
    assert "libpython" not in lowered, otool.stdout
    assert "python3" not in lowered, otool.stdout
