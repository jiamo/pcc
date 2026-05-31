"""min()/max() over a str (and a list of str) under strict no-libpython.

min()/max() of a single iterable only had an int-accumulator fast path
(ListType/TupleType/DynType/ClassType of ints); a str arg (or list-of-str)
bailed -> a generic name lookup ("NameError: name 'min'/'max'"). Fix: a generic
runtime helper py_obj_min_max(iterable, want_max) (py_obj_min_max.c, an
OBJ_PY_CC_HELPERS C file) that iterates and compares elements with py_obj_lt;
the frontend routes a str / list-of-str arg to it. Returns the extreme element.

Runs under ``--backend self --python-libpython=off`` in DEFAULT runtime mode.
"""
from __future__ import annotations
import os, subprocess
from pathlib import Path


def _run(tmp_path, source):
    src = tmp_path / "p.py"; src.write_text(source, encoding="utf-8")
    exe = tmp_path / "p_bin"; env = os.environ.copy(); env.pop("LC_ALL", None)
    b = subprocess.run(["uv","run","pcc","--backend","self","--python-libpython=off","--ir-scaffold=on",str(src),"-o",str(exe)], text=True, capture_output=True, timeout=420, env=env)
    assert b.returncode == 0, b.stderr
    r = subprocess.run([str(exe)], text=True, capture_output=True, timeout=30, env=env)
    assert r.returncode == 0, r.stderr
    return r.stdout


def test_min_max_over_str_and_list_of_str(tmp_path):
    out = _run(tmp_path,
        "def main():\n"
        "    print(max('abc'))\n"                          # c
        "    print(min('abc'))\n"                          # a
        "    print(max('banana'))\n"                       # n
        "    print(max(['banana', 'apple', 'cherry']))\n"  # cherry
        "    print(min(['banana', 'apple', 'cherry']))\n"  # apple
        "    print(min([3, 1, 2]))\n"                       # 1 (int regression)
        "    print(max([5, 9, 2]))\n"                       # 9 (int regression)
        "main()\n")
    assert out.split("\n")[:7] == [
        "c", "a", "n", "cherry", "apple", "1", "9",
    ], out
