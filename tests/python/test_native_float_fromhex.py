"""float.fromhex(s) under strict no-libpython (run-based).

float.fromhex is a builtin-type classmethod that previously routed through the
libpython fallback (PCC-PY-COMPILE-001 under --python-libpython=off; the
generated IR still called py_cpy_*). Added a native branch
(unary_call_lowering._maybe_emit_builtin_type_method, next to the bytes.fromhex
arm) plus a runtime helper py_float_fromhex (the production pcc-Python owner is
py_obj_stubs.py; py_float_fromhex.c remains the host-C/pcc-C oracle).

The helper mirrors CPython Objects/floatobject.c::float_fromhex exactly: a
hexadecimal floating-point grammar with an OPTIONAL 0x prefix and a binary
('p') exponent, so a bare "1.5" parses as the hex value 1 + 5/16 = 1.3125 (NOT
decimal 1.5). inf/infinity/nan (case-insensitive, optional sign) are accepted;
a malformed string raises ValueError and an out-of-range magnitude raises
OverflowError.

Compiles + runs under ``--backend self --python-libpython=off`` in DEFAULT
runtime mode (pcc-Python ports — the goal mode). Values below are diffed
against CPython float.fromhex.
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


def test_float_fromhex_values_no_libpython(tmp_path):
    out = _run_pcc_program(
        tmp_path,
        "def main():\n"
        "    print(float.fromhex('0x1.8p3'))\n"                    # 12.0
        "    print(float.fromhex('1.0'))\n"                        # 1.0
        "    print(float.fromhex('0x1p10'))\n"                     # 1024.0
        "    print(float.fromhex('-0x1.5p2'))\n"                   # -5.25
        "    print(float.fromhex('  0x1p4  '))\n"                  # 16.0
        "    print(float.fromhex('0x.8p1'))\n"                     # 1.0
        "    print(float.fromhex('inf'))\n"                        # inf
        "    print(float.fromhex('-nan'))\n"                       # nan
        "    print(float.fromhex('1.5'))\n"                        # 1.3125 (hex!)
        "    print(float.fromhex('0x1.921fb54442d18p+1'))\n"       # 3.141592653589793
        "    print(float.fromhex('0x0.0000000000001p-1022'))\n"    # min subnormal
        "    print(float.fromhex('0x1.00000000000008p0'))\n"       # half-even down
        "    print(float.fromhex('0x1.000000000000081p0'))\n"      # above tie
        "    print(float.fromhex('0x0.00000000000008p-1022'))\n"   # underflow tie
        "    print(float.fromhex('-0x0p0'))\n"                     # signed zero
        "main()\n",
    )
    assert out.split("\n")[:15] == [
        "12.0",
        "1.0",
        "1024.0",
        "-5.25",
        "16.0",
        "1.0",
        "inf",
        "nan",
        "1.3125",
        "3.141592653589793",
        "5e-324",
        "1.0",
        "1.0000000000000002",
        "0.0",
        "-0.0",
    ], out


def test_float_fromhex_error_cases_no_libpython(tmp_path):
    out = _run_pcc_program(
        tmp_path,
        "def main():\n"
        "    try:\n"
        "        float.fromhex('0x')\n"                            # ValueError
        "    except ValueError:\n"
        "        print('caught-ValueError')\n"
        "    try:\n"
        "        float.fromhex('0x1.8p3junk')\n"                   # ValueError
        "    except ValueError:\n"
        "        print('caught-ValueError-2')\n"
        "    try:\n"
        "        float.fromhex('0x1p10000')\n"                     # OverflowError
        "    except OverflowError:\n"
        "        print('caught-OverflowError')\n"
        "main()\n",
    )
    assert out.split("\n")[:3] == [
        "caught-ValueError",
        "caught-ValueError-2",
        "caught-OverflowError",
    ], out
