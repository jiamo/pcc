"""Native divmod() with float args under strict no-libpython (run-based).

divmod() lowered both args via _emit_expr_as_i64 unconditionally, so
divmod(7.5, 2) truncated to (3, 1) instead of (3.0, 1.5). Now a float branch
(when either arg is FloatType) computes (floor(a/b), a - floor(a/b)*b) as floats,
with the matching tuple[float, float] return type in type_infer.py.

Compiles + runs under --backend self --python-libpython=off and asserts output.

NOTE: this asserts divmod's own result (direct use + unpack-then-print). It does
NOT assert arithmetic on unpacked float elements (e.g. `q, r = divmod(7.5, 2);
q*2+r`), which hits a SEPARATE pre-existing bug: arithmetic on floats unpacked
from any tuple (even `a, b = (1.5, 2.5); a+b`) yields <null> under =off. That
float-tuple-unpack/unbox gap is orthogonal to divmod and tracked separately.
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


def test_divmod_float_native_no_libpython(tmp_path):
    out = _run_pcc_program(
        tmp_path,
        "print(divmod(7, 3), divmod(-7, 3), divmod(7, -3))\n"
        "print(divmod(7.5, 2), divmod(-7.5, 2), divmod(7.5, 2.5))\n"
        "print(divmod(10.0, 3.0), divmod(1.5, 0.5))\n"
        "q, r = divmod(17.0, 5.0)\n"
        "print(q, r)\n",
    )
    assert out.split("\n")[:4] == [
        "(2, 1) (-3, 2) (-3, -2)",
        "(3.0, 1.5) (-4.0, 0.5) (3.0, 0.0)",
        "(3.0, 1.0) (3.0, 0.0)",
        "3.0 2.0",
    ], out
