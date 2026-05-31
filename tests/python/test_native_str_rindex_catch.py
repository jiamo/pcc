"""str.rindex() + str.index()/rindex() ValueError catchability under no-libpython.

str.rindex had no native lowering (=off fell back to py_cpy_*). Added runtime
py_str_rindex_of (rfind() + ValueError, mirroring py_str_index_of) and its
frontend dispatch. Additionally, the str index/rindex dispatch now emits
_emit_post_call_err_check after the call (like subscript_lowering), so the
ValueError raised on an absent substring is catchable by a surrounding
try/except — previously it propagated uncaught and exited the program.

Compiles + runs under ``--backend self --python-libpython=off`` in DEFAULT
runtime mode (pcc-Python ports — the goal mode).
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


def test_str_rindex_and_index_catch_no_libpython(tmp_path):
    out = _run_pcc_program(
        tmp_path,
        "def main():\n"
        "    print('abcabc'.rindex('c'))\n"            # 5
        "    print('abcabc'.index('c'))\n"             # 2
        "    print('hello world hello'.rindex('hello'))\n"  # 12
        "    try:\n"
        "        'hi'.index('z')\n"
        "    except ValueError:\n"
        "        print('caught-index')\n"
        "    try:\n"
        "        'hi'.rindex('z')\n"
        "    except ValueError:\n"
        "        print('caught-rindex')\n"
        "main()\n",
    )
    assert out.split("\n")[:5] == [
        "5",
        "2",
        "12",
        "caught-index",
        "caught-rindex",
    ], out
