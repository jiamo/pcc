"""list.copy() under strict no-libpython.

Previously the native list-method fast paths in list_method_lowering.py covered
extend/insert/remove/index/count/sort/append/pop/clear/reverse but NOT copy, and
``py_list_copy`` was absent from py_list.c / py_list.py / runtime_abi.py. So
``lst.copy()`` fell through to dynamic getattr and was rejected under
``--python-libpython=off`` (PCC-PY-COMPILE-001). Fix: a shallow-copy runtime
helper ``py_list_copy(src)`` (py_list.c + the pcc-Python port py_list.py) that
allocates a fresh list and incref-copies each element; both frontend list-method
lowering sites (the typed-list path and the dyn path in list_method_lowering.py)
now route ``copy`` through it.

Runs under ``--backend self --python-libpython=off`` in DEFAULT runtime mode, so
the linked implementation is the pcc-Python PORT (not the cc C source) — that is
what proves the port mirror is correct.
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


def test_list_copy_native_no_libpython(tmp_path):
    # Cross-checked against CPython (python3): every printed line below matches
    # the reference interpreter's output.
    out = _run_pcc_program(
        tmp_path,
        "def main():\n"
        "    a = [1, 2, 3]\n"
        "    b = a.copy()\n"
        "    print(b)\n"                     # [1, 2, 3]
        "    print(a == b)\n"                # True   (equal contents)
        "    print(a is b)\n"               # False  (distinct identity)
        "    b.append(4)\n"                  # mutating the copy...
        "    print(a)\n"                     # [1, 2, 3]  ...leaves the source
        "    print(b)\n"                     # [1, 2, 3, 4]
        "    inner = [9]\n"
        "    c = [inner, 100]\n"
        "    d = c.copy()\n"
        "    d[0].append(10)\n"              # shallow: inner element is shared
        "    print(c)\n"                     # [[9, 10], 100]
        "    print(c[0] is d[0])\n"          # True
        "    print([].copy())\n"             # []  (empty-source copy)
        "    print(len([7, 8].copy()))\n"    # 2   (copy of an owned temp list)
        "main()\n",
    )
    assert out.split("\n")[:9] == [
        "[1, 2, 3]",
        "True",
        "False",
        "[1, 2, 3]",
        "[1, 2, 3, 4]",
        "[[9, 10], 100]",
        "True",
        "[]",
        "2",
    ], out
