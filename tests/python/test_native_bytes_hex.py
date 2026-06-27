"""bytes.hex() under strict no-libpython (run-based).

bytes.hex() had no native lowering (=off errored). Added runtime py_bytes_hex
(py_bytes.c for cc mode + the pcc-Python port py_obj_stubs.py which provides the
bytes runtime in default mode): lowercase two-hex-digits-per-byte string.
Frontend dispatch alongside bytes.decode (BytesType/ByteArrayType).

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


def test_bytes_hex_native_no_libpython(tmp_path):
    out = _run_pcc_program(
        tmp_path,
        "def main():\n"
        "    print(b'abc'.hex())\n"                  # 616263
        "    print(b'\\x00\\xff\\x10'.hex())\n"      # 00ff10
        "    print(b''.hex())\n"                     # (empty)
        "    print(bytes([1, 2, 255]).hex())\n"      # 0102ff
        "    print('hello'.encode().hex())\n"        # 68656c6c6f
        "main()\n",
    )
    assert out.split("\n")[:5] == [
        "616263",
        "00ff10",
        "",
        "0102ff",
        "68656c6c6f",
    ], out


def test_bytes_upper_native_no_libpython(tmp_path):
    out = _run_pcc_program(
        tmp_path,
        "def main():\n"
        "    print(b'hello'.upper())\n"
        "    print(b'a1z'.upper())\n"
        "    print(bytearray(b'abz').upper())\n"
        "    print(b''.upper())\n"
        "    print(b'abc'.upper().decode('utf-8'))\n"
        "main()\n",
    )
    assert out.splitlines() == [
        "b'HELLO'",
        "b'A1Z'",
        "bytearray(b'ABZ')",
        "b''",
        "ABC",
    ], out
