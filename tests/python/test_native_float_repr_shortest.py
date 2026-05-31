"""CPython shortest-round-trip float repr under strict no-libpython (run-based).

In DEFAULT runtime mode the float repr/str/print path went through
``py_float_format_fixed(o, 6)`` (fixed 6 decimal places), so ``print(10/3)``
produced ``3.333333`` instead of CPython's shortest-round-trip
``3.3333333333333335`` (and ``0.1 + 0.2`` printed ``0.300000`` instead of
``0.30000000000000004``). Exact-short values (2.5, 5.0) happened to match.

The fix adds a shared C helper ``py_float_repr_shortest`` (py_format.c, an
OBJ_PY_CC_HELPERS file compiled as C in both runtime tiers): it emits the
shortest decimal string whose ``strtod`` round-trips back to the same double
(try ``%.*g`` precision 1..17), handles inf/nan, and appends ``.0`` for
integer-valued floats. The pcc-Python ports (py_print_fmt.py::_format_float and
py_obj_stubs.py::_float_str) and the C print path
(py_print_fmt.c::py_format_float) all delegate to it.

Compiles + runs under ``--backend self --python-libpython=off`` in DEFAULT
runtime mode (pcc-Python ports — the goal mode) and asserts CPython-exact
output.
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
    # DEFAULT mode (pcc_py ports) — the no-libpython goal mode. The shortest-
    # repr helper is a C-helper (py_format.c) reached by both tiers; the ports
    # delegate to it.
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


def test_float_repr_shortest_no_libpython(tmp_path):
    out = _run_pcc_program(
        tmp_path,
        "def main():\n"
        "    print(10 / 3)\n"          # 3.3333333333333335
        "    print(1 / 3)\n"           # 0.3333333333333333
        "    print(0.1 + 0.2)\n"       # 0.30000000000000004
        "    print(0.1)\n"             # 0.1
        "    print(2.5, 5.0, 3.14)\n"  # 2.5 5.0 3.14
        "    print(3.0)\n"             # 3.0
        "    print(float(7))\n"        # 7.0
        "    print(-2.5)\n"            # -2.5
        "main()\n",
    )
    assert out.split("\n")[:8] == [
        "3.3333333333333335",
        "0.3333333333333333",
        "0.30000000000000004",
        "0.1",
        "2.5 5.0 3.14",
        "3.0",
        "7.0",
        "-2.5",
    ], out


def test_float_repr_dyntype_division_no_libpython(tmp_path):
    # The truediv slice + shortest repr together: a DynType division now prints
    # the full-precision CPython value, not 6-decimal-truncated.
    out = _run_pcc_program(
        tmp_path,
        "class C:\n"
        "    def __init__(self):\n"
        "        self.v = 10\n"
        "def main():\n"
        "    c = C()\n"
        "    print(c.v / 3)\n"   # 3.3333333333333335
        "main()\n",
    )
    assert out.split("\n")[0] == "3.3333333333333335", out
