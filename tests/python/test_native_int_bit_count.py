"""int.bit_count() under strict no-libpython (run-based).

int.bit_count() (the population-count sibling of int.bit_length()) routed
through the libpython numeric-method fallback, so it was rejected under
--python-libpython=off (PCC-PY-COMPILE-001). Added a native branch (next to the
bit_length branch) calling runtime py_int_bit_count (py_int_core.c + port .py):
number of set bits in abs(value), 0 for 0, exact for bignums via popcount of
each base-2^32 limb (negatives match their absolute value, matching CPython).
Returns an int.

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


def test_int_bit_count_native_no_libpython(tmp_path):
    out = _run_pcc_program(
        tmp_path,
        "def main():\n"
        "    print((255).bit_count(), (0).bit_count(), (1).bit_count())\n"      # 8 0 1
        "    print((-255).bit_count(), (1024).bit_count())\n"                   # 8 1
        "    print((2 ** 100).bit_count())\n"                                   # 1 (bignum, single bit)
        "    print((2 ** 100 - 1).bit_count())\n"                              # 100 (all-ones bignum, multi-limb)
        "    x = 1000000\n"
        "    print(x.bit_count())\n"                                            # 7
        "    print((2 ** 64 + 5).bit_count())\n"                               # 3 (bignum)
        "main()\n",
    )
    assert out.split("\n")[:6] == [
        "8 0 1",
        "8 1",
        "1",
        "100",
        "7",
        "3",
    ], out
