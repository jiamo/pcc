"""float `+` via the generic object path (py_float_add) under strict no-libpython.

``py_float_add`` was an unimplemented stub (TODO phase3) in BOTH the C runtime
(py_obj_stubs.c) and the DEFAULT-mode port (py_obj_stubs.py: ``return null()``),
so any float addition routed through the generic ``py_obj_add`` path returned
null. This breaks the very common pattern of adding to a boxed float — e.g. the
result of true-division on a DynType operand: ``obj.attr / n + m`` (a computed
property / unit conversion / average), or ``total = total + x`` float
accumulation, or ``int + float_literal`` where the int is a DynType.

Fix: implement ``py_float_add`` in both the port and C as
``py_float_from_f64(py_float_to_f64(a) + py_float_to_f64(b))`` guarded to numeric
operands (int/bool/float via py_float_to_f64); a non-numeric operand returns
null so the caller surfaces the error.

Scope note: ``-`` / ``*`` / comparison on a boxed float (DynType) go through
different paths and remain separate follow-ups (documented in
docs/investigations/sequence-builtins-len-getitem-not-iterator-protocol.md's
sibling notes). This test covers ``+`` only.

Runs under ``--backend self --python-libpython=off`` in DEFAULT runtime mode
(pcc-Python ports; py_obj_stubs is a PY_MODULES port).
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


def test_float_add_generic_path_no_libpython(tmp_path):
    out = _run_pcc_program(
        tmp_path,
        "class T:\n"
        "    def __init__(self, c):\n"
        "        self._c = c\n"
        "    @property\n"
        "    def fahrenheit(self):\n"
        "        return self._c * 9 / 5 + 32\n"
        "def main():\n"
        "    t = T(100)\n"
        "    print(t.fahrenheit)\n"          # 212.0  (boxed-float + int via property)
        "    print(t._c / 5 + 32)\n"          # 52.0   (DynType truediv + int)
        "    print(t._c + 0.5)\n"             # 100.5  (DynType int + float literal)
        "    x = t._c / 4\n"
        "    print(x + x)\n"                  # 50.0   (float + float)
        "    print(x + 1)\n"                  # 26.0   (float + int)
        "    print(1 + x)\n"                  # 26.0   (int + float)
        "    total = 0.0\n"
        "    for v in [1.5, 2.5, 3.0]:\n"
        "        total = total + v\n"
        "    print(total)\n"                  # 7.0    (float accumulation)
        "main()\n",
    )
    assert out.split("\n")[:7] == [
        "212.0", "52.0", "100.5", "50.0", "26.0", "26.0", "7.0",
    ], out
