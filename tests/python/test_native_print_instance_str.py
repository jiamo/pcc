"""print(<user instance>) routes through __str__ under strict no-libpython.

Before this fix, ``print(obj)`` for a user-class instance rendered the opaque
``<object tag=N>`` even when the class defined ``__str__`` — the print formatter
``_format`` (py_print_fmt.py port / py_print_fmt.c) had no case for instance
tags and fell through to the unknown-tag fallback. ``str(obj)`` already worked
(py_obj_str dispatches __str__), so this was a print-path-only gap, the same
shape as the #19 exception fix (which only added the PY_TYPE_EXC case).

Fix: ``_format``'s default branch now calls ``py_obj_str(o)`` first (which
dispatches __str__ then __repr__) and writes the result; only a NULL result
falls back to the libpython hook then ``<object tag=N>``. This is recursion-safe:
the tags py_obj_str routes back through py_format_obj_to_str (float/none/list/
tuple/dict/set/bytes) are all handled above the default branch, so the default
only reaches py_obj_str's py_user_str_dispatch path.

Covers only deterministic cases. ``print([obj, obj])`` is intentionally omitted:
CPython renders list elements via __repr__ which, absent a __repr__, includes a
non-deterministic memory address (``<Pt object at 0x...>``) — not diff-stable,
and a separate container-repr concern.

Runs under ``--backend self --python-libpython=off`` in DEFAULT runtime mode
(pcc-Python ports; py_print_fmt is a PY_MODULES port).
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


def test_print_instance_routes_through_dunder_str_no_libpython(tmp_path):
    out = _run_pcc_program(
        tmp_path,
        "class Pt:\n"
        "    def __init__(self, x, y):\n"
        "        self.x = x\n"
        "        self.y = y\n"
        "    def __str__(self):\n"
        "        return f'Pt({self.x}, {self.y})'\n"
        "class Animal:\n"
        "    def __init__(self, kind):\n"
        "        self.kind = kind\n"
        "    def __str__(self):\n"
        "        return 'Animal:' + self.kind\n"
        "def main():\n"
        "    p = Pt(3, 4)\n"
        "    print(p)\n"                                          # Pt(3, 4)
        "    print('p =', p)\n"                                   # p = Pt(3, 4)
        "    print(str(p))\n"                                     # Pt(3, 4)
        "    print(f'<{p}>')\n"                                   # <Pt(3, 4)>
        "    print(Animal('cat'))\n"                              # Animal:cat
        "    print('; '.join(str(x) for x in [Pt(1, 1), Pt(2, 2)]))\n"  # Pt(1, 1); Pt(2, 2)
        "main()\n",
    )
    assert out.split("\n")[:6] == [
        "Pt(3, 4)",
        "p = Pt(3, 4)",
        "Pt(3, 4)",
        "<Pt(3, 4)>",
        "Animal:cat",
        "Pt(1, 1); Pt(2, 2)",
    ], out
