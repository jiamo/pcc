"""int.bit_length() under strict no-libpython (run-based).

The numeric-method dispatch routed int methods through the libpython fallback,
so int.bit_length() was rejected under --python-libpython=off. Added a native
branch (reusing the float.is_integer numeric-method branch) calling runtime
py_int_bit_length (py_int_core.c + port .py): bits to represent abs(value),
exact for bignums via (ndigits-1)*32 + bits in the top base-2^32 digit. Returns
an int.

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


def test_int_bit_length_native_no_libpython(tmp_path):
    out = _run_pcc_program(
        tmp_path,
        "def main():\n"
        "    print((255).bit_length(), (0).bit_length(), (1).bit_length())\n"  # 8 0 1
        "    print((-5).bit_length(), (1024).bit_length())\n"                  # 3 11
        "    print((2 ** 100).bit_length())\n"                                 # 101 (bignum)
        "    x = 1000000\n"
        "    print(x.bit_length())\n"                                          # 20
        "    print((2 ** 64).bit_length())\n"                                  # 65 (bignum)
        "main()\n",
    )
    assert out.split("\n")[:5] == [
        "8 0 1",
        "3 11",
        "101",
        "20",
        "65",
    ], out
