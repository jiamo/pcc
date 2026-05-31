"""tuple()/set()/frozenset() over a custom __iter__/__next__ iterator under
strict no-libpython.

These builtins only accepted List/Tuple/Dict/Dyn/Str args; a ClassType (custom
iterator) bailed -> the libpython fallback (hard error under
--python-libpython=off). Fix (frontend, follow-up to #41): route a ClassType arg
through the iterator protocol — build a list via _emit_list_append_via_iter,
then materialise the tuple (py_tuple_new + copy) / set (py_set_new + py_set_add).
frozenset shares set()'s lowering. Matches CPython tuple(x)/set(x) via iter(x).

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


def test_tuple_set_frozenset_over_custom_iterator(tmp_path):
    out = _run(tmp_path,
        "class C:\n"
        "    def __init__(self, n):\n"
        "        self.n = n\n"
        "    def __iter__(self):\n"
        "        self.i = 0\n"
        "        return self\n"
        "    def __next__(self):\n"
        "        if self.i >= self.n:\n"
        "            raise StopIteration\n"
        "        v = self.i\n"
        "        self.i += 1\n"
        "        return v\n"
        "def main():\n"
        "    print(tuple(C(3)))\n"               # (0, 1, 2)
        "    print(tuple(C(0)))\n"               # ()
        "    print(sorted(set(C(4))))\n"         # [0, 1, 2, 3]
        "    print(sorted(frozenset(C(3))))\n"   # [0, 1, 2]
        "    print(len(set(C(5))))\n"            # 5
        "    print(tuple([1, 2, 3]))\n"          # (1, 2, 3) regression
        "    print(sorted(set([3, 1, 2, 1])))\n" # [1, 2, 3] regression
        "main()\n")
    assert out.split("\n")[:7] == [
        "(0, 1, 2)", "()", "[0, 1, 2, 3]", "[0, 1, 2]", "5",
        "(1, 2, 3)", "[1, 2, 3]",
    ], out
