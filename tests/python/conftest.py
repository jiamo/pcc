"""Shared fixtures for tests/python.

Currently provides ``pcc_py_runtime_archive``: build the pcc-Python runtime
archive that a standalone ``pcc1`` links by default, if it is missing.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).absolute().parents[2]
# The archive a standalone pcc1 links by default (PCC_RUNTIME_CC=pcc /
# PCC_RUNTIME_HIGH=py).
_PCC_PY_RUNTIME_ARCHIVE = _REPO / "pcc" / "py_runtime" / "libpy_runtime_pcc_py.a"


@pytest.fixture(scope="session")
def pcc_py_runtime_archive(tmp_path_factory):
    """Ensure ``libpy_runtime_pcc_py.a`` exists, building it via host pcc.

    A standalone ``pcc1`` (the bootstrapped native binary) links this archive
    by default but CANNOT rebuild it itself: its on-demand build shells out to
    a host ``pcc`` / ``ir_to_obj`` that are absent from its standalone
    environment, so it only prints "warning: failed to build py_runtime
    (subprocess.run failed)" and then emits a binary that fails the final link
    with ``Undefined symbols: _py_list_append, _py_int_from_i64, ...``.

    That makes every pcc1-driven test (smoke, pytest-capable, ...) fail en
    masse whenever the archive is absent — a fresh checkout, or a tree where
    someone ran ``rm libpy_runtime*.a`` to force a runtime ``.c`` rebuild.
    Building it here (via host pcc, which has the toolchain) makes those tests
    exercise pcc1's codegen, not the build environment.

    Non-autouse on purpose: only the pcc1 test modules that request it pay the
    one-time build cost; it is a fast no-op when the archive already exists.
    """
    if _PCC_PY_RUNTIME_ARCHIVE.is_file():
        return _PCC_PY_RUNTIME_ARCHIVE
    probe_dir = tmp_path_factory.mktemp("pcc_py_runtime_prep")
    probe = probe_dir / "rt_probe.py"
    probe.write_text(
        "def main() -> None:\n    print(1)\n\n\n"
        "if __name__ == '__main__':\n    main()\n",
        encoding="utf-8",
    )
    env = {**os.environ, "PCC_RUNTIME_CC": "pcc", "PCC_RUNTIME_HIGH": "py"}
    env.pop("LC_ALL", None)
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pcc",
            str(probe),
            "-o",
            str(probe_dir / "rt_probe.out"),
            "--python-libpython=off",
            "--ir-scaffold=on",
        ],
        capture_output=True,
        text=True,
        timeout=600.0,
        cwd=str(_REPO),
        env=env,
    )
    assert _PCC_PY_RUNTIME_ARCHIVE.is_file(), (
        "failed to prebuild the pcc-Python runtime archive required by pcc1 "
        f"tests ({_PCC_PY_RUNTIME_ARCHIVE}); host pcc exit {result.returncode}\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    return _PCC_PY_RUNTIME_ARCHIVE
