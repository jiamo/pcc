"""list.sort(reverse=True) (in-place descending) under strict no-libpython.

`list.sort()` worked but `list.sort(reverse=True)` fell back: `_maybe_emit_list_method`
bailed on ANY kwargs. Fix: allow `sort(reverse=<bool const>)` through the kwargs
guard and, in the sort handler, reverse the sorted result (py_list_reverse)
before writing it back. Mirrors the sorted(reverse=) handling (#11).
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


def test_list_sort_reverse_no_libpython(tmp_path):
    out = _run(tmp_path,
        "def main():\n"
        "    l = [3, 1, 4, 1, 5, 9, 2, 6]\n"
        "    l.sort(reverse=True)\n"
        "    print(l)\n"                       # [9, 6, 5, 4, 3, 2, 1, 1]
        "    l2 = [3, 1, 4, 1, 5]\n"
        "    l2.sort(reverse=False)\n"
        "    print(l2)\n"                       # [1, 1, 3, 4, 5]
        "    l3 = [3, 1, 2]\n"
        "    l3.sort()\n"
        "    print(l3)\n"                       # [1, 2, 3]
        "    w = ['banana', 'apple', 'cherry']\n"
        "    w.sort(reverse=True)\n"
        "    print(w)\n"                        # ['cherry', 'banana', 'apple']
        "main()\n")
    assert out.split("\n")[:4] == [
        "[9, 6, 5, 4, 3, 2, 1, 1]", "[1, 1, 3, 4, 5]", "[1, 2, 3]",
        "['cherry', 'banana', 'apple']",
    ], out
