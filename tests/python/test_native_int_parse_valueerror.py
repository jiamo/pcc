"""int(str) invalid input -> ValueError (not silent 0) under no-libpython.

int('xyz') returned 0 silently (a WRONG result, worse than uncatchable):
py_int_from_cstr already returns NULL on invalid input, but the frontend
(numeric_builtin_lowering int(str) paths) unboxed the NULL to 0. Fix: runtime
py_int_from_cstr_or_raise (py_int_parse.c + port .py) raises ValueError when
py_int_from_cstr returns NULL; the frontend routes int(str) to it (both StrType
and DynType paths) and emits the post-call err check so the bad value never
propagates and try/except can catch it. Valid parses are unaffected.

Compiles + runs under ``--backend self --python-libpython=off`` in DEFAULT
runtime mode (pcc-Python ports — the goal mode).
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path


def _build(tmp_path: Path, source: str):
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
    return exe, env


def test_int_parse_valid_and_valueerror_no_libpython(tmp_path):
    exe, env = _build(
        tmp_path,
        "def main():\n"
        "    print(int('42'), int('-5'), int('  7  '), int('1f', 16), int('101', 2))\n"
        "    try:\n"
        "        int('xyz')\n"
        "    except ValueError:\n"
        "        print('caught-int-ValueError')\n"
        "    try:\n"
        "        int('12x')\n"
        "    except ValueError:\n"
        "        print('caught-trailing')\n"
        "main()\n",
    )
    run = subprocess.run([str(exe)], text=True, capture_output=True, timeout=30, env=env)
    assert run.returncode == 0, run.stderr
    assert run.stdout.split("\n")[:3] == [
        "42 -5 7 31 5",
        "caught-int-ValueError",
        "caught-trailing",
    ], run.stdout


def test_int_parse_invalid_raises_uncaught_no_libpython(tmp_path):
    # Without a handler, int('xyz') raises ValueError (exits nonzero) instead of
    # silently yielding 0.
    exe, env = _build(
        tmp_path,
        "def main():\n"
        "    print(int('xyz'))\n"
        "main()\n",
    )
    run = subprocess.run([str(exe)], text=True, capture_output=True, timeout=30, env=env)
    assert run.returncode != 0, "expected ValueError exit, got 0 (silent parse?)"
    assert "ValueError" in run.stderr, run.stderr
