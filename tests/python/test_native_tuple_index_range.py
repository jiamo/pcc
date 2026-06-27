"""tuple.index(x, start[, stop]) 2/3-arg form under no-libpython.

tuple.index previously only had a 1-arg native lowering; the 2/3-arg form
(``t.index(x, start)`` / ``t.index(x, start, stop)``) was rejected by the
tuple-method guard and forced the libpython fallback
(PCC-PY-COMPILE-001, py_cpy_* call-sites for the method). Added runtime
py_tuple_index_range(t, item, start, stop) (a C-only OBJ_PY_CC_HELPERS
helper, no port), widened the frontend guard in tuple_zip_lowering.py to
accept 2/3 positional args, and appended the ABI decl in runtime_abi.py.

The search-window index/stop follow CPython slice-index clamping: a negative
value is relative to len (floored at 0), and stop is capped at len. A missing
item within the window raises ValueError (catchable via _emit_post_call_err_check).

Compiles + runs under ``--backend self --python-libpython=off`` in DEFAULT
runtime mode (pcc-Python ports — the goal mode) and diffs against CPython.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path


_PROG = (
    "def main():\n"
    "    t = (1, 2, 3, 2, 2)\n"
    "    print(t.index(2, 2))\n"        # 3
    "    print(t.index(2, 1, 3))\n"     # 1
    "    print(t.index(2))\n"           # 1
    "    print(t.index(2, -3))\n"       # 3  (start clamps: -3+5=2 -> first 2 at idx 3? -> 3)
    "    print(t.index(2, 0, 100))\n"   # 1  (stop capped at len)
    "    print(t.index(2, 0, -3))\n"    # 1  (stop -3+5=2 -> window [0,2) -> idx 1)
    "    try:\n"
    "        t.index(2, 3, 4)\n"        # only idx 3 (== 2) is inside [3,4) -> found at 3
    "    except ValueError:\n"
    "        print('unexpected-VE')\n"
    "    else:\n"
    "        print(t.index(2, 3, 4))\n"  # 3
    "    try:\n"
    "        t.index(2, 100)\n"         # start beyond len -> not found
    "    except ValueError:\n"
    "        print('caught-range')\n"
    "    try:\n"
    "        t.index(9, 0, 5)\n"        # absent -> not found
    "    except ValueError:\n"
    "        print('caught-absent')\n"
    "main()\n"
)


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
    # No libpython fallback: the generated program must be self-contained.
    assert "PCC-PY-COMPILE-001" not in build.stderr, build.stderr
    run = subprocess.run(
        [str(exe)], text=True, capture_output=True, timeout=30, env=env
    )
    assert run.returncode == 0, run.stderr
    return run.stdout


def test_tuple_index_range_no_libpython(tmp_path):
    out = _run_pcc_program(tmp_path, _PROG)
    # Diff against CPython reference for the identical program.
    ref = subprocess.run(
        ["python3", "-c", _PROG], text=True, capture_output=True, timeout=30
    )
    assert ref.returncode == 0, ref.stderr
    assert out == ref.stdout, f"pcc={out!r} cpython={ref.stdout!r}"
    assert out.split("\n")[:9] == [
        "3",
        "1",
        "1",
        "3",
        "1",
        "1",
        "3",
        "caught-range",
        "caught-absent",
    ], out
