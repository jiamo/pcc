"""Generic object `-` / `*` (py_obj_sub / py_obj_mul) on boxed floats, no-libpython.

Sibling of #26 (py_float_add). `-` and `*` on a boxed float (a DynType value
that is a float at runtime — instance attribute, true-division result) against
an int or another DynType were WRONG: there was no `py_obj_sub` / `py_obj_mul`
runtime function and no DynType dispatch for `-`/`*` in binary_op_lowering, so
they fell to `_emit_binop_int` and misread the boxed-float pointer (`x - 1`
gave -1, `x * 2` gave 0).

Fix: add `py_obj_sub` / `py_obj_mul` (port py_obj_ops_dispatch.py + C) that
dispatch by tag (int/bool -> py_int_sub/mul; any-float-numeric ->
py_float_sub/mul which coerce the other operand; for mul, sequence * int ->
py_str/list/tuple_repeat), plus new `py_float_sub` / `py_float_mul` (port +
C, mirror py_float_add), plus a DynType `-`/`*` dispatch in binary_op_lowering
placed AFTER the native-set block so set difference and list/tuple repetition
still win.

Scope: boxed-float COMPARISON (`<` / `>` against int) is a separate path and
remains a follow-up (see docs/investigations/boxed-float-dyntype-sub-mul-compare-wrong.md).
This test covers `-` and `*` only.

Runs under ``--backend self --python-libpython=off`` in DEFAULT runtime mode.
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


def test_obj_sub_mul_boxed_float_no_libpython(tmp_path):
    out = _run_pcc_program(
        tmp_path,
        "class T:\n"
        "    def __init__(self, c):\n"
        "        self._c = c\n"
        "    def diff(self, x):\n"
        "        return self._c - x\n"
        "    def scale(self, k):\n"
        "        return self._c * k\n"
        "def main():\n"
        "    t = T(100.0)\n"
        "    print(t._c - 10)\n"        # 90.0   boxed-float - int
        "    print(t._c * 2)\n"         # 200.0  boxed-float * int
        "    print(t.diff(5.0))\n"      # 95.0   DynType - DynType(float)
        "    print(t.scale(1.5))\n"     # 150.0  DynType * DynType(float)
        "    print(t._c / 4 - 1)\n"     # 24.0   (truediv result) - int
        "    print(t._c / 4 * 3)\n"     # 75.0   (truediv result) * int
        "    ti = T(100)\n"
        "    print(ti._c - 10)\n"       # 90     boxed-int - int (regression)
        "    print(ti._c * 3)\n"        # 300    boxed-int * int (regression)
        "    print([0] * 3)\n"          # [0, 0, 0]  list repeat (regression)
        "    print('ab' * 2)\n"         # abab   str repeat (regression)
        "    print((1, 2) * 2)\n"       # (1, 2, 1, 2)  tuple repeat (regression)
        "    a = {1, 2, 3}\n"
        "    print(sorted(a - {2, 3}))\n"  # [1]  set difference (regression)
        "main()\n",
    )
    assert out.split("\n")[:12] == [
        "90.0", "200.0", "95.0", "150.0", "24.0", "75.0",
        "90", "300", "[0, 0, 0]", "abab", "(1, 2, 1, 2)", "[1]",
    ], out
