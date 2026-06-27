"""List out-of-range subscript raises IndexError under strict no-libpython.

py_list_get returned NULL on out-of-range WITHOUT raising (a TODO), so `a[5]`
on a 3-element list silently produced <null> instead of raising IndexError —
code relying on IndexError for bounds broke silently. Added py_list_get_checked
(raises IndexError on out-of-range; internal range-walkers like the iterator
keep the NULL-returning py_list_get) + the frontend err-check on the list
subscript-load paths (exact_int_lowering._emit_subscript_load_object and
subscript_lowering).

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


def test_list_out_of_range_raises_indexerror_native_no_libpython(tmp_path):
    out = _run_pcc_program(
        tmp_path,
        "a = [10, 20, 30]\n"
        "print(a[0], a[2], a[-1], a[-3])\n"          # in-bounds (pos + neg)
        "for bad in [3, 5, 100, -4, -100]:\n"
        "    try:\n"
        "        v = a[bad]\n"
        "        print('NO RAISE', bad)\n"
        "    except IndexError:\n"
        "        print('IndexError', bad)\n"
        "try:\n"
        "    e = [][0]\n"
        "except IndexError:\n"
        "    print('empty IndexError')\n",
    )
    assert out.split("\n")[:7] == [
        "10 30 30 10",
        "IndexError 3",
        "IndexError 5",
        "IndexError 100",
        "IndexError -4",
        "IndexError -100",
        "empty IndexError",
    ], out


def test_list_iteration_not_broken_by_index_check_native_no_libpython(tmp_path):
    # The iterator + comprehensions use the NULL-returning py_list_get for
    # end-of-range; the IndexError change must not break them.
    out = _run_pcc_program(
        tmp_path,
        "a = [10, 20, 30]\n"
        "total = 0\n"
        "for x in a:\n"
        "    total = total + x\n"
        "print(total)\n"
        "print([y * 2 for y in a])\n"
        "print(sum(a), len(a))\n",
    )
    assert out.split("\n")[:3] == ["60", "[20, 40, 60]", "60 3"], out
