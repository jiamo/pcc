"""Float tuple-unpack typing under strict no-libpython (run-based).

`_infer_assign` propagated the RHS type to a single Name target but left
tuple-unpack sub-targets to plain inference, so `a, b = (1.5, 2.5)` did not type
a/b as float. Storing/reading them with a mismatched type made arithmetic on the
unpacked floats yield <null> (ints/strs happened to work by default). Now
`_infer_assign` propagates each tuple element type to its (flat, all-Name)
sub-target.

Compiles + runs under --backend self --python-libpython=off and asserts output.
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


def test_float_tuple_unpack_arithmetic_native_no_libpython(tmp_path):
    out = _run_pcc_program(
        tmp_path,
        "a = 1.5\n"
        "b = 2.5\n"
        "print(a + b)\n"            # direct (already worked)
        "c, d = (1.5, 2.5)\n"
        "print(c + d)\n"            # tuple-literal unpack (was <null>)
        "e, f = 1.5, 2.5\n"
        "print(e + f)\n"            # bare unpack (was <null>)
        "t = (3.0, 2.0)\n"
        "g, h = t\n"
        "print(g, h, g * 5.0 + h)\n"  # unpack from a Name + arithmetic
        "q, r = divmod(17.0, 5.0)\n"
        "print(q * 5.0 + r)\n",     # divmod-float unpack-then-compute
    )
    assert out.split("\n")[:5] == [
        "4.0",
        "4.0",
        "4.0",
        "3.0 2.0 17.0",
        "17.0",
    ], out


def test_int_str_tuple_unpack_still_native_no_libpython(tmp_path):
    # Regression guard: int/str unpacking (which already worked) stays correct.
    out = _run_pcc_program(
        tmp_path,
        "c, d = (3, 5)\n"
        "print(c + d)\n"
        "s, t = ('a', 'b')\n"
        "print(s + t)\n",
    )
    assert out.split("\n")[:2] == ["8", "ab"], out
