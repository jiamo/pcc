"""list.index(x, start[, end]) under strict no-libpython.

Previously only the 1-arg ``lst.index(x)`` form lowered natively; the range
forms ``lst.index(x, start)`` / ``lst.index(x, start, end)`` had len(args) != 1
so both frontend lowering sites (the dyn path and the typed-list path in
list_method_lowering.py) returned None and fell back to the libpython
py_cpy_* surface — a hard PCC-PY-COMPILE-001 error under
``--python-libpython=off``. Fix: a range-aware runtime helper
``py_list_index_range(lst, item, start, end)`` (py_list.c + the pcc-Python port
py_list.py) that CPython-clamps the bounds, scans the half-open window
[start, end), and raises ValueError when absent; both frontend index sites now
route the 2/3-arg forms through it and check py_err_occurred() after the call.

Runs under ``--backend self --python-libpython=off`` in DEFAULT runtime mode,
so the linked implementation is the pcc-Python PORT (not the cc C source) —
that is what proves the port mirror is correct.
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


def test_list_index_range_native_no_libpython(tmp_path):
    # Cross-checked against CPython (python3): every printed line below matches
    # the reference interpreter's output.
    out = _run_pcc_program(
        tmp_path,
        "def main():\n"
        "    xs = [1, 2, 3, 2, 1]\n"
        "    print(xs.index(2, 2))\n"          # 3  (skip the first 2 at idx 1)
        "    print(xs.index(2, 2, 4))\n"       # 3
        "    print(xs.index(1, 0, 5))\n"       # 0
        "    print(xs.index(2))\n"             # 1  (1-arg path unchanged)
        "    print(xs.index(1, -1))\n"         # 4  (negative start)
        "    print(xs.index(2, -3, -1))\n"     # 3  (negative start + end)
        "    print(xs.index(1, -100))\n"       # 0  (start clamps to 0)
        "    print(xs.index(1, 0, 100))\n"     # 0  (end clamps to len)
        "    try:\n"
        "        xs.index(3, 3)\n"             # the only 3 is at idx 2 < start
        "        print('NO RAISE a')\n"
        "    except ValueError:\n"
        "        print('ValueError a')\n"
        "    try:\n"
        "        xs.index(3, 3, 1)\n"          # start > end -> empty window
        "        print('NO RAISE b')\n"
        "    except ValueError:\n"
        "        print('ValueError b')\n"
        "    try:\n"
        "        xs.index(1, 0, -100)\n"       # end clamps to 0 -> empty
        "        print('NO RAISE c')\n"
        "    except ValueError:\n"
        "        print('ValueError c')\n"
        "main()\n",
    )
    assert out.split("\n")[:11] == [
        "3", "3", "0", "1", "4", "3", "0", "0",
        "ValueError a", "ValueError b", "ValueError c",
    ], out
