"""Phase 1 exit gate: every pcc/py_runtime/src/*.c must compile through pcc
--emit-obj.

This is the first step of the equivalence chain that ends at Phase 4. If pcc
cannot emit an object from its own C runtime source, it cannot serve as the
middle oracle for pcc-Python runtime validation.

Upgrade path (task #166): once a differential oracle harness exists (Phase 0),
this test should also assert that the pcc-emitted object's behavior is
byte-equivalent to the cc-emitted object.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
RUNTIME_SRC = REPO_ROOT / "pcc" / "py_runtime" / "src"
RUNTIME_INC = REPO_ROOT / "pcc" / "py_runtime" / "include"


def _runtime_sources() -> list[Path]:
    return sorted(RUNTIME_SRC.glob("*.c"))


@pytest.mark.parametrize(
    "src",
    _runtime_sources(),
    ids=lambda p: p.name,
)
def test_pcc_emits_object_for_runtime_source(tmp_path, src):
    pcc_bin = shutil.which("pcc")
    if not pcc_bin:
        pytest.skip("pcc CLI (cli_core) not on PATH")
    obj_path = tmp_path / (src.stem + ".o")
    cmd = [
        pcc_bin,
        f"--cpp-arg=-I{RUNTIME_INC}",
        f"--cpp-arg=-I{RUNTIME_SRC}",
        "--emit-obj",
        str(obj_path),
        str(src),
    ]
    env = dict(os.environ)
    env.pop("LC_ALL", None)
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=120,
        cwd=str(REPO_ROOT),
        env=env,
    )
    assert result.returncode == 0, (
        f"pcc --emit-obj {src.name} failed:\n"
        f"STDOUT:\n{result.stdout}\n"
        f"STDERR:\n{result.stderr}"
    )
    assert obj_path.is_file(), f"missing object file {obj_path}"
    assert obj_path.stat().st_size > 0, f"empty object file {obj_path}"


def test_runtime_source_inventory_has_expected_files():
    """Smoke check that we haven't accidentally broken the glob."""
    names = {p.name for p in _runtime_sources()}
    # Required anchors for the 4 formerly-blocked files and the allocator.
    required = {
        "py_class.c",
        "py_int.c",
        "py_print_fmt.c",
        "py_str.c",
        "py_obj.c",
        "py_tuple.c",
        "py_obj_ops_compare.c",
        "py_obj_ops_dispatch.c",
    }
    missing = required - names
    assert not missing, f"runtime source inventory missing: {missing}"
