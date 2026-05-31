"""DynType-receiver .is_integer()/.bit_length() under strict no-libpython.

float.is_integer()/int.bit_length() were lowered natively only for statically
FloatType/IntType receivers. A *boxed* numeric value from a dynamic expression —
e.g. ``(total / count).is_integer()`` where ``/`` lowers to py_obj_truediv (a
boxed float), or ``sum(xs).bit_length()`` — has a DynType receiver, which
reached the dynamic getattr dispatch (py_obj_getattr on a boxed float/int has no
method table) and raised ``AttributeError: is_integer``. Found by a realistic
multi-idiom integration program (the same methodology that surfaced the DynType
truediv bug).

Fix: the DynType method-call path (method_call_expression_lowering) now handles
.is_integer()/.bit_length() natively via py_float_is_integer / py_int_bit_length
(which read the object by type tag) before the getattr-based dispatch.

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


def test_dyntype_is_integer_and_bit_length_no_libpython(tmp_path):
    out = _run_pcc_program(
        tmp_path,
        "def main():\n"
        "    vals = [10, 20, 30, 40]\n"
        "    total = 0\n"
        "    for v in vals:\n"
        "        total = total + v\n"
        "    # total / len -> py_obj_truediv -> boxed float (DynType receiver)\n"
        "    print((total / len(vals)).is_integer())\n"   # 100/4=25.0 -> True
        "    print((total / 3).is_integer())\n"           # 100/3 -> False
        "    # sum(...) is a DynType int receiver for .bit_length()\n"
        "    print(sum(vals).bit_length())\n"             # 100 -> 7
        "    print((total * 2).bit_length())\n"           # 200 -> 8
        "main()\n",
    )
    assert out.split("\n")[:4] == [
        "True",
        "False",
        "7",
        "8",
    ], out
