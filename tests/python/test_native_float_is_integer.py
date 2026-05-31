"""float.is_integer() under strict no-libpython (run-based).

The numeric-method dispatch (method_call_expression_lowering) routed all
int/float methods through the libpython fallback, so float.is_integer() was
rejected under --python-libpython=off. Added a native branch (before that
fallback) calling runtime py_float_is_integer (py_obj_stubs.c + port .py):
finite-and-no-fractional-part, avoiding math.h (|v|>=2^53 is always integral,
otherwise the int64 round-trip is exact). Returns a bool.

Compiles + runs under ``--backend self --python-libpython=off`` in DEFAULT
runtime mode (pcc-Python ports — the goal mode) and asserts CPython-exact output.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path


def _run_pcc_program(tmp_path: Path, source: str) -> str:
    src = tmp_path / "prog.py"
    src.write_text(source, encoding="utf-8")
    exe = tmp_path / "prog_bin"
    env = os.environ.copy()
    env.pop("LC_ALL", None)
    build = subprocess.run(
        [
            "uv", "run", "pcc", "--backend", "self", "--python-libpython=off",
            "--ir-scaffold=on", str(src), "-o", str(exe),
        ],
        text=True, capture_output=True, timeout=420, env=env,
    )
    assert build.returncode == 0, build.stderr
    run = subprocess.run(
        [str(exe)], text=True, capture_output=True, timeout=30, env=env
    )
    assert run.returncode == 0, run.stderr
    return run.stdout


def test_float_is_integer_native_no_libpython(tmp_path):
    out = _run_pcc_program(
        tmp_path,
        "def main():\n"
        "    print((5.0).is_integer(), (5.5).is_integer())\n"     # True False
        "    print((0.0).is_integer(), (-3.0).is_integer())\n"    # True True
        "    print((1e20).is_integer())\n"                        # True (>=2^53)
        "    x = 10.0\n"
        "    print((x / 3.0).is_integer(), (x / 2.0).is_integer())\n"  # False True
        "main()\n",
    )
    assert out.split("\n")[:4] == [
        "True False",
        "True True",
        "True",
        "False True",
    ], out
