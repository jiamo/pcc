"""Container repr (list/tuple/dict/set) must format each element with __repr__,
not __str__ — under strict no-libpython.

`py_format_repr` special-cased only str/bytes and then fell through to the str
formatter (`py_format` → `py_obj_str`), so a class defining BOTH __str__ and
__repr__ rendered its __str__ inside a list (`[C-str]`) where CPython uses
__repr__ (`[C-repr]`). The container formatters already recurse via
py_format_repr; the fix dispatches __repr__ (py_obj_repr) for instance tags
inside py_format_repr (C py_print_fmt.c + pcc-Python port py_print_fmt.py).

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


def test_container_repr_dispatches_repr(tmp_path):
    out = _run(tmp_path,
        "class Both:\n"
        "    def __init__(self, n):\n"
        "        self.n = n\n"
        "    def __repr__(self):\n"
        "        return 'R' + str(self.n)\n"
        "    def __str__(self):\n"
        "        return 'S' + str(self.n)\n"
        "def main():\n"
        "    xs = [Both(1), Both(2)]\n"
        "    print(xs)\n"                       # [R1, R2]  (list repr -> __repr__)
        "    print((Both(3), Both(4)))\n"       # (R3, R4)  (tuple repr)
        "    print(str(Both(5)))\n"             # S5        (str -> __str__)
        "    print(Both(6))\n"                  # S6        (print -> __str__)
        "    print(repr(Both(7)))\n"            # R7        (repr -> __repr__)
        "    print([str(Both(8))])\n"           # ['S8']    (explicit str, then list-repr of a str)
        "main()\n")
    assert out.split("\n")[:6] == [
        "[R1, R2]", "(R3, R4)", "S5", "S6", "R7", "['S8']",
    ], out
