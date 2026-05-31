"""str()/print()/format() of an instance must fall back to __repr__ when the
class defines only __repr__ (no __str__) — under strict no-libpython.

CPython's object.__str__ delegates to __repr__, so a class that defines only
__repr__ (the common debug-class idiom) prints via __repr__. pcc's py_obj_str
returned NULL when py_user_str_dispatch found no __str__, so print(obj) showed
`<object tag=N>` and str(obj)/f"{obj}"/"x"+str(obj) showed `<null>`.

Fix: py_obj_str (C py_obj_stubs.c + pcc-Python port py_obj_stubs.py) falls back
to py_obj_repr when __str__ is absent (NULL with no pending error); a __str__
that actually raised still propagates its error.

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


def test_str_falls_back_to_repr(tmp_path):
    out = _run(tmp_path,
        "class OnlyRepr:\n"
        "    def __repr__(self):\n"
        "        return 'OR<' + str(self.n) + '>'\n"
        "    def __init__(self, n):\n"
        "        self.n = n\n"
        "class Both:\n"
        "    def __repr__(self):\n"
        "        return 'B-repr'\n"
        "    def __str__(self):\n"
        "        return 'B-str'\n"
        "def main():\n"
        "    a = OnlyRepr(5)\n"
        "    print(a)\n"                       # OR<5>  (print -> str -> repr)
        "    print(str(a))\n"                  # OR<5>
        "    print(repr(a))\n"                 # OR<5>
        "    print(f'{a}')\n"                  # OR<5>  (format -> str -> repr)
        "    print('v=' + str(a))\n"           # v=OR<5>
        "    print('{}'.format(a))\n"          # OR<5>
        "    b = Both()\n"
        "    print(b)\n"                       # B-str  (str uses __str__)
        "    print(str(b))\n"                  # B-str
        "    print(repr(b))\n"                 # B-repr (repr uses __repr__)
        "    print(f'{b}')\n"                  # B-str
        "main()\n")
    assert out.split("\n")[:10] == [
        "OR<5>", "OR<5>", "OR<5>", "OR<5>", "v=OR<5>", "OR<5>",
        "B-str", "B-str", "B-repr", "B-str",
    ], out
