"""Native bin()/hex()/oct() builtins under strict no-libpython (run-based).

These builtins were listed in the frontend's builtin set but had no direct-call
lowering, so `bin(5)` / `hex(255)` / `oct(8)` raised `NameError` at runtime under
`--python-libpython=off`. Now lowered to runtime `py_builtin_bin/hex/oct`
(py_dunder.c), which produce CPython-exact base-prefixed strings.

Compiles + runs under `--backend self --python-libpython=off` and asserts the
exact output.
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
    # DEFAULT mode (pcc_py ports) — goal mode; py_builtin_bin/hex/oct
    # mirrored in both py_dunder.c and py_dunder.py.
    build = subprocess.run(
        [
            "uv", "run", "pcc", "--backend", "self", "--python-libpython=off",
            "--ir-scaffold=on", str(src), "-o", str(exe),
        ],
        text=True, capture_output=True, timeout=300, env=env,
    )
    assert build.returncode == 0, build.stderr
    run = subprocess.run(
        [str(exe)], text=True, capture_output=True, timeout=30, env=env
    )
    assert run.returncode == 0, run.stderr
    return run.stdout


def test_bin_hex_oct_native_no_libpython(tmp_path):
    out = _run_pcc_program(
        tmp_path,
        "print(bin(5), hex(255), oct(8))\n"
        "print(bin(0), hex(0), oct(0))\n"
        "print(bin(-5), hex(-255), oct(-8))\n"
        "print(hex(4096), bin(1023))\n"
        "x = 254\n"
        "print(hex(x), bin(x + 1))\n",
    )
    assert out.split("\n")[:5] == [
        "0b101 0xff 0o10",
        "0b0 0x0 0o0",
        "-0b101 -0xff -0o10",
        "0x1000 0b1111111111",
        "0xfe 0b11111111",
    ], out
