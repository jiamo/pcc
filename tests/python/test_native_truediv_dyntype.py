"""True division (``/``) on dynamically-typed operands under no-libpython.

A DynType operand (e.g. a class attribute ``self.v`` inferred as a boxed
object) could not use the static int/float ``/`` fast path, so the frontend
routed ``obj.attr / n`` to the ``__truediv__`` dunder. A tagged int has no
``__truediv__`` attribute (py_obj_getattr returns missing for tagged ints),
so ``c.v / 3`` raised ``AttributeError: __truediv__`` at runtime — even though
``c.v - 3`` / ``c.v * 3`` / ``c.v // 3`` / ``c.v % 3`` / ``c.v ** 3`` all
worked (those reach the generic int path).

The fix adds a generic runtime ``py_obj_truediv`` (mirrored in
py_obj_ops_dispatch.c and the pcc-Python port py_obj_ops_dispatch.py): numeric
operands divide as doubles (always yielding a float, like CPython); anything
else defers to ``__truediv__``. The frontend routes DynType ``/`` there via
_emit_binop_value instead of the string-named dunder.

Assertions use equality/``int()`` and exact-short float reprs so they validate
the division *value* (the truediv fix) and stay robust to the separate
no-libpython float-repr gap (``10/3`` prints ``3.333333`` rather than CPython's
shortest-round-trip ``3.3333333333333335`` — a distinct runtime limitation,
not a truediv bug). Compiles + runs under ``--backend self
--python-libpython=off`` in DEFAULT runtime mode (pcc-Python ports — the goal
mode).
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
    # DEFAULT mode (pcc_py ports) — the no-libpython goal mode. py_obj_truediv
    # is mirrored in both py_obj_ops_dispatch.c and the port py_obj_ops_dispatch.py.
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


def test_truediv_dyntype_attr_no_libpython(tmp_path):
    # c.v / c.w are DynType (boxed) class attributes. ``/`` must yield a float
    # and NOT raise AttributeError: __truediv__.
    out = _run_pcc_program(
        tmp_path,
        "class C:\n"
        "    def __init__(self):\n"
        "        self.v = 10\n"
        "        self.w = 4\n"
        "def main():\n"
        "    c = C()\n"
        "    print(c.v / c.w == 2.5)\n"     # DynType / DynType -> 2.5 -> True
        "    print(c.v / 4 == 2.5)\n"       # DynType / int    -> 2.5 -> True
        "    print(c.v / 2 == 5.0)\n"       # exact            -> 5.0 -> True
        "    print(c.v / 2.0 == 5.0)\n"     # DynType / float  -> 5.0 -> True
        "    print(int(c.v / c.w))\n"       # int(2.5)         -> 2
        "    print(c.v / 2)\n"              # exact-short repr -> 5.0
        "    # other operators must still work on the same DynType attr:\n"
        "    print(c.v + 3, c.v - 3, c.v * 3, c.v // 3, c.v % 3, c.v ** 2)\n"
        "main()\n",
    )
    assert out.split("\n")[:7] == [
        "True",
        "True",
        "True",
        "True",
        "2",
        "5.0",
        "13 7 30 3 1 100",
    ], out


def test_truediv_dyntype_average_pattern_no_libpython(tmp_path):
    # The realistic shape that surfaced the bug: sum / count where the operands
    # flow through containers (DynType). 100/4 == 25.0 has an exact short repr.
    out = _run_pcc_program(
        tmp_path,
        "def main():\n"
        "    vals = [10, 20, 30, 40]\n"
        "    total = 0\n"
        "    for v in vals:\n"
        "        total = total + v\n"
        "    print(total / len(vals))\n"       # 25.0
        "    print(total / len(vals) == 25.0)\n"  # True
        "main()\n",
    )
    assert out.split("\n")[:2] == ["25.0", "True"], out
