"""Hex/oct/bin integer literals containing the digit 'e'/'E' (e.g. 0xDEADBEEF).

A hex literal whose digits include 'e'/'E' (``0xdeadbeef``, ``0xface``, ``0x1e``)
failed to compile: ``ValueError: invalid literal for int() with base 10:
'adbeef'``. The number-token classifier in py_parse.py
(``if "." in clean or "e" in clean.lower(): <float>``) treated any token with an
'e' as a float, so a hex literal was routed to the float parser, which split it
at the 'e' as a decimal exponent. ``0xff`` / ``0xABC`` / ``0xa`` (no 'e') worked.

Fix: classify a ``0x`` / ``0o`` / ``0b`` prefixed token as an int BEFORE the
'.'/'e' float test (its 'e' is a hex digit, not a float exponent).

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


def test_hex_literal_with_e_digit_no_libpython(tmp_path):
    out = _run_pcc_program(
        tmp_path,
        "def main():\n"
        "    print(0xdeadbeef)\n"          # 3735928559
        "    print(0xface, 0xfe, 0xcafe)\n"  # 64206 254 51966
        "    print(0x1e, 0xe, 0xE)\n"       # 30 14 14
        "    print(0xDEADBEEF)\n"           # 3735928559
        "    print(0xff + 0xee)\n"          # 493
        "    print(0xCAFE & 0xFF)\n"        # 254
        "    print(0o17, 0b1010)\n"         # 15 10 (oct/bin regression)
        "    print(255, 1e3)\n"             # 255 1000.0 (decimal + float-exp regression)
        "main()\n",
    )
    assert out.split("\n")[:8] == [
        "3735928559",
        "64206 254 51966",
        "30 14 14",
        "3735928559",
        "493",
        "254",
        "15 10",
        "255 1000.0",
    ], out
