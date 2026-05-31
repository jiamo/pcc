"""str.maketrans(x, y) under strict no-libpython (run-based).

str.maketrans (a builtin-type staticmethod) routed through the libpython
fallback. Added a native branch (unary_call_lowering._maybe_emit_builtin_type_method)
+ runtime py_str_maketrans (py_str_accessors.c + port .py): the two-arg form
builds {ord(x[i]): ord(y[i])} (x and y must have equal length, else ValueError).
Together with the str.translate slice this makes the common
``s.translate(str.maketrans(a, b))`` pattern work natively. Byte/ASCII-oriented.

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


def test_str_maketrans_and_translate_pattern_no_libpython(tmp_path):
    out = _run_pcc_program(
        tmp_path,
        "def main():\n"
        "    t = str.maketrans('abc', 'xyz')\n"
        "    print('aabbcc'.translate(t))\n"                          # 'xxyyzz'
        "    print('hello world'.translate(str.maketrans('lo', 'LO')))\n"  # 'heLLO wOrLd'
        "    print(sorted(str.maketrans('ab', 'xy').items()))\n"      # [(97, 120), (98, 121)]
        "    try:\n"
        "        str.maketrans('ab', 'xyz')\n"
        "    except ValueError:\n"
        "        print('caught-ValueError')\n"
        "main()\n",
    )
    assert out.split("\n")[:4] == [
        "xxyyzz",
        "heLLO wOrLd",
        "[(97, 120), (98, 121)]",
        "caught-ValueError",
    ], out
