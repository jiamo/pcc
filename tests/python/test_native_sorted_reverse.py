"""sorted(x, reverse=True) under strict no-libpython (run-based).

sorted() was only lowered for the no-kwargs form (call_expression_lowering
``len(args)==1 and not expr.kwargs``), so sorted(x, reverse=True) fell back to
py_cpy_* and the compile hard-errored under --python-libpython=off. Now a
constant ``reverse=<bool>`` kwarg is handled natively: py_obj_sorted(x) then, if
reverse=True, py_list_reverse on the result (both already exist). key= (a
first-class function) and a non-constant reverse still fall through.

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


def test_sorted_reverse_native_no_libpython(tmp_path):
    out = _run_pcc_program(
        tmp_path,
        "def main():\n"
        "    print(sorted([3, 1, 2], reverse=True))\n"     # [3, 2, 1]
        "    print(sorted([3, 1, 2], reverse=False))\n"    # [1, 2, 3]
        "    print(sorted([3, 1, 2]))\n"                   # [1, 2, 3] (no regression)
        "    print(sorted('cba', reverse=True))\n"         # ['c', 'b', 'a']
        "    print(sorted({3, 1, 2}, reverse=True))\n"     # [3, 2, 1]
        "main()\n",
    )
    assert out.split("\n")[:5] == [
        "[3, 2, 1]",
        "[1, 2, 3]",
        "[1, 2, 3]",
        "['c', 'b', 'a']",
        "[3, 2, 1]",
    ], out
