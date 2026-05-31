"""str.rpartition() under strict no-libpython (run-based).

str.partition worked but str.rpartition had no native lowering (=off fell back
to py_cpy_*). Added runtime py_str_rpartition (py_str_accessors.c + port .py):
splits on the LAST occurrence of sep, returning ('', '', s) when sep is absent
(the original goes at the END, unlike partition's (s, '', '')). Frontend
string_method_lowering dispatch in both the StrType and DynType paths.

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


def test_str_rpartition_native_no_libpython(tmp_path):
    out = _run_pcc_program(
        tmp_path,
        "def main():\n"
        "    print('a.b.c'.rpartition('.'))\n"        # ('a.b', '.', 'c')
        "    print('a.b.c'.partition('.'))\n"         # ('a', '.', 'b.c') (no regression)
        "    print('abc'.rpartition('x'))\n"          # ('', '', 'abc')
        "    print('hello world'.rpartition(' '))\n"  # ('hello', ' ', 'world')
        "    print('one'.rpartition('-'))\n"          # ('', '', 'one')
        "main()\n",
    )
    assert out.split("\n")[:5] == [
        "('a.b', '.', 'c')",
        "('a', '.', 'b.c')",
        "('', '', 'abc')",
        "('hello', ' ', 'world')",
        "('', '', 'one')",
    ], out
