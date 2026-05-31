"""Native round() banker's rounding under strict no-libpython (run-based).

CPython's round() uses round-half-to-even (banker's rounding): round(2.5)==2,
round(0.5)==0, round(-1.5)==-2. pcc's round() lowering used floor(x + 0.5)
(round half away from zero), so round(2.5) gave 3. Now lowered via libm rint()
(default FP mode = round to nearest, ties to even), matching CPython.

Compiles + runs under --backend self --python-libpython=off and asserts exact
output.

KNOWN LIMITATION (not asserted here): round(x, ndigits) for ties that are not
exactly representable (e.g. round(2.675, 2) -> CPython 2.67, pcc 2.68) differs,
because the x*10**n scaling introduces float error (2.675*100 == 267.5000...006).
CPython avoids this with a correctly-rounded decimal algorithm. This was already
the behaviour before the banker's fix and is a separate follow-on.
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
    env["PCC_RUNTIME_CC"] = "cc"
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


def test_round_one_arg_bankers_native_no_libpython(tmp_path):
    out = _run_pcc_program(
        tmp_path,
        "print(round(2.5), round(3.5), round(0.5), round(1.5))\n"
        "print(round(-0.5), round(-1.5), round(-2.5))\n"
        "print(round(2.4), round(2.6), round(0.0))\n",
    )
    assert out.split("\n")[:3] == [
        "2 4 0 2",
        "0 -2 -2",
        "2 3 0",
    ], out


def test_round_two_arg_bankers_native_no_libpython(tmp_path):
    out = _run_pcc_program(
        tmp_path,
        "print(round(3.14159, 2), round(123.456, 1))\n"
        "print(round(2.5, 0), round(0.125, 2))\n",
    )
    assert out.split("\n")[:2] == [
        "3.14 123.5",
        "2.0 0.12",
    ], out
