"""str.maketrans + str.translate composition under strict no-libpython.

Companion to test_native_str_translate.py (literal dict tables) and
test_native_str_maketrans.py (maketrans 2-arg build). This exercises the
realistic ``s.translate(str.maketrans(...))`` pipeline end to end, including a
chained translate that deletes chars (None mapping) and a pure dict-form
translate with an ord->str expansion, with unmapped chars passing through
unchanged.

Both frontend lowering paths route single-arg ``translate`` to the runtime
``py_str_translate`` (py_str_accessors.c + its pcc-Python port), and
``str.maketrans`` (2-arg) to ``py_str_maketrans``. Compiles + runs under
``--backend self --python-libpython=off`` in DEFAULT runtime mode (pcc-Python
ports — the goal mode).
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


def test_str_maketrans_translate_compose_no_libpython(tmp_path):
    out = _run_pcc_program(
        tmp_path,
        "def main():\n"
        # maketrans 2-arg build, then translate: pure char->char remap,
        # unmapped chars (spaces, 't', ...) pass through.
        "    t = str.maketrans('aeiou', 'AEIOU')\n"
        "    print('translate this vowel string'.translate(t))\n"
        # maketrans-produced table ('l'->'L', 'o'->'O'), then a chained
        # translate that deletes spaces via a None mapping.
        "    t2 = str.maketrans('lo', 'LO')\n"
        "    print('hello world foo'.translate(t2).translate({32: None}))\n"
        # pure dict form: ord('-')->'__' (str expansion), ord('b')->None
        # (deletion), everything else passes through.
        "    print('a-b-c'.translate({45: '__', 98: None}))\n"
        "main()\n",
    )
    assert out.split("\n")[:3] == [
        "trAnslAtE thIs vOwEl strIng",
        "heLLOwOrLdfOO",
        "a____c",
    ], out
