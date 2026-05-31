"""sum()/list() over a custom __iter__/__next__ iterator under no-libpython.

`sum(CustomIterable())` raised "NameError: name 'sum' is not defined" and
`list(CustomIterable())` returned `[]`: the sum()/list() builtin lowerings only
accepted List/Tuple/Dyn-typed args and routed a ClassType arg to a
py_obj_len + py_obj_getitem (index) path (no __len__ -> empty / bail to a
name lookup). Fix: route a ClassType arg through the iterator protocol
(_emit_sum_via_iter / _emit_list_append_via_iter), matching CPython's sum()/
list() which iterate via iter(). Frontend-only (numeric_builtin_lowering +
list_builtin_lowering).

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


def test_sum_list_over_custom_iterator(tmp_path):
    out = _run(tmp_path,
        "class Counter:\n"
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
        "    print(sum(Counter(5)))\n"            # 10
        "    print(list(Counter(3)))\n"           # [0, 1, 2]
        "    print([x for x in Counter(4)])\n"    # [0, 1, 2, 3]
        "    print(sum(Counter(5), 100))\n"       # 110 (start)
        "    print(sum([1, 2, 3]))\n"             # 6 (list regression)
        "    print(list((7, 8, 9)))\n"            # [7, 8, 9] (tuple regression)
        "main()\n")
    assert out.split("\n")[:6] == [
        "10", "[0, 1, 2]", "[0, 1, 2, 3]", "110", "6", "[7, 8, 9]",
    ], out
