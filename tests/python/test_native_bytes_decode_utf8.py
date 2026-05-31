"""bytes.decode("utf-8") (with an explicit encoding arg) under no-libpython.

`b.decode()` (no args) worked, but `b.decode("utf-8")` — the common explicit
form — raised NotImplementedError("bytes.decode() with arguments is not
supported yet"). Since pcc str is UTF-8 internally and decode() defaults to
utf-8, an explicit "utf-8" encoding (+ optional "strict" errors) is identical
to the no-arg form.

Fix (frontend): accept a literal utf-8 encoding ("utf-8"/"UTF-8"/"utf8",
positional or `encoding=` kwarg) and an optional "strict" errors arg, routing to
the existing py_bytes_decode; any other encoding / error mode still falls back.

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


def test_bytes_decode_utf8_arg_no_libpython(tmp_path):
    out = _run_pcc_program(
        tmp_path,
        "def main():\n"
        "    b = 'caf\\u00e9'.encode('utf-8')\n"
        "    print(b.decode())\n"                      # café
        "    print(b.decode('utf-8'))\n"               # café
        "    print(b.decode('UTF-8'))\n"               # café
        "    print(b.decode('utf8'))\n"                # café
        "    print(b.decode('utf-8', 'strict'))\n"     # café
        "    print(b.decode(encoding='utf-8'))\n"      # café
        "    print(b'hello'.decode('utf-8'))\n"        # hello
        "    print(b'hello'.decode('utf-8') == 'hello')\n"  # True
        "main()\n",
    )
    assert out.split("\n")[:8] == [
        "café", "café", "café", "café", "café", "café", "hello", "True",
    ], out
