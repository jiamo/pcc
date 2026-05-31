"""Native set symmetric difference (a ^ b) under strict no-libpython (run-based).

The set binop lowering (binary_op_lowering.py) handled |, &, - for sets but NOT
^, so `a ^ b` fell through to the integer xor path and returned an empty set.
Added runtime py_set_symmetric_difference (py_set.c) + the `^` branch in the set
binop block.

Compiles + runs under --backend self --python-libpython=off and asserts output.
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
    # DEFAULT mode (pcc_py ports) — the no-libpython goal mode; py_set_
    # symmetric_difference is mirrored in both py_set.c and py_set.py.
    build = subprocess.run(
        [
            "uv", "run", "pcc", "--backend", "self", "--python-libpython=off",
            "--ir-scaffold=on", str(src), "-o", str(exe),
        ],
        text=True, capture_output=True, timeout=300, env=env,
    )
    assert build.returncode == 0, build.stderr
    run = subprocess.run(
        [str(exe)], text=True, capture_output=True, timeout=30, env=env
    )
    assert run.returncode == 0, run.stderr
    return run.stdout


def test_set_symmetric_difference_native_no_libpython(tmp_path):
    out = _run_pcc_program(
        tmp_path,
        "a = {1, 2, 3}\n"
        "b = {2, 3, 4}\n"
        "print(sorted(a ^ b))\n"
        "print(sorted({1, 2} ^ {3, 4}))\n"
        "print(sorted({1, 2, 3} ^ {1, 2, 3}))\n"
        "print(sorted({1, 2} ^ set()))\n"
        "c = {1, 2, 3}\n"
        "c ^= {2, 3, 4}\n"
        "print(sorted(c))\n"
        "print(sorted(a | b), sorted(a & b), sorted(a - b))\n",
    )
    assert out.split("\n")[:6] == [
        "[1, 4]",
        "[1, 2, 3, 4]",
        "[]",
        "[1, 2]",
        "[1, 4]",
        "[1, 2, 3, 4] [2, 3] [1]",
    ], out
