"""str.translate(table) under strict no-libpython (run-based).

str.translate had no native lowering (=off fell back to py_cpy_*). Added runtime
py_str_translate (py_str_accessors.c + port .py): byte-level two-pass map of
each char through the table dict {ord: ord|str|None} — absent keeps the char,
None deletes it, an int gives that byte, a str inserts its bytes. Frontend
string_method_lowering dispatch in both the StrType and DynType paths.

Byte/ASCII-oriented like the other str helpers (non-ASCII codepoint keys are a
follow-on). Compiles + runs under ``--backend self --python-libpython=off`` in
DEFAULT runtime mode (pcc-Python ports — the goal mode).
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


def test_str_translate_native_no_libpython(tmp_path):
    out = _run_pcc_program(
        tmp_path,
        "def main():\n"
        "    print('abc'.translate({97: 120}))\n"          # 'xbc' (a->x)
        "    print('hello'.translate({108: None}))\n"      # 'heo' (delete l)
        "    print('abc'.translate({97: 'XY'}))\n"         # 'XYbc' (str value)
        "    print('abc'.translate({}))\n"                 # 'abc' (empty table)
        "    print('banana'.translate({97: 111, 110: None}))\n"  # 'booo' (a->o x3, del n x2)
        "main()\n",
    )
    assert out.split("\n")[:5] == [
        "xbc",
        "heo",
        "XYbc",
        "abc",
        "booo",
    ], out
