"""str.expandtabs() under strict no-libpython (run-based).

str.expandtabs had no native lowering, so under --backend self
--python-libpython=off it fell back to py_cpy_* helpers and the compile
hard-errored. Added runtime py_str_expandtabs (py_str_accessors.c + port .py):
column-aware '\\t' -> spaces up to the next tabsize boundary, with '\\n'/'\\r'
resetting the column; frontend dispatch handles expandtabs() (default tabsize 8)
and expandtabs(n) in both the StrType and DynType paths.

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


def test_str_expandtabs_native_no_libpython(tmp_path):
    out = _run_pcc_program(
        tmp_path,
        "def main():\n"
        "    print(repr('a\\tbb\\tc'.expandtabs(4)))\n"      # 'a   bb  c'
        "    print(repr('a\\tb'.expandtabs()))\n"            # default 8
        "    print(repr('1\\t2\\n12\\t3'.expandtabs(4)))\n"  # \n resets column
        "    print(repr('no tabs'.expandtabs()))\n"          # unchanged
        "    print(repr('\\t'.expandtabs(1)))\n"             # ' '
        "main()\n",
    )
    assert out.split("\n")[:5] == [
        "'a   bb  c'",
        "'a       b'",
        "'1   2\\n12  3'",
        "'no tabs'",
        "' '",
    ], out
