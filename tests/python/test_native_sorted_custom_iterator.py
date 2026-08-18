"""sorted() over a length-less iterable (custom __iter__/__next__, generator)
under strict no-libpython.

The pcc-Python port py_obj_sorted's general-iterable branch used an index-based
py_obj_len + py_obj_getitem loop (a no-__len__ source -> n=0 -> [] empty),
unlike the C py_obj_sorted which already used the iterator protocol. Fix: the
port branch now iterates via py_obj_iter/py_obj_next (clearing a terminal
StopIteration), so sorted(<custom iterator>) / sorted(<generator>) work in the
default (port-linked) runtime. Also clears a spurious py_obj_len sizing error.

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
    run_env = {**env, "PCC_GC_BACKEND": "4"}
    r = subprocess.run(
        [str(exe)], text=True, capture_output=True, timeout=30, env=run_env
    )
    assert r.returncode == 0, r.stderr
    return r.stdout


def test_sorted_over_length_less_iterable(tmp_path):
    out = _run(tmp_path,
        "class C:\n"
        "    def __init__(self, vals):\n"
        "        self.vals = vals\n"
        "    def __iter__(self):\n"
        "        self.i = 0\n"
        "        return self\n"
        "    def __next__(self):\n"
        "        if self.i >= len(self.vals):\n"
        "            raise StopIteration\n"
        "        v = self.vals[self.i]\n"
        "        self.i += 1\n"
        "        return v\n"
        "def gen(n):\n"
        "    i = 0\n"
        "    while i < n:\n"
        "        yield n - i\n"
        "        i += 1\n"
        "def main():\n"
        "    print(sorted(C([3, 1, 2, 5, 4])))\n"   # [1, 2, 3, 4, 5]
        "    print(sorted(gen(5)))\n"               # [1, 2, 3, 4, 5]
        "    print(sorted(C([])))\n"                # []
        "    print(sorted([3, 1, 2]))\n"            # [1, 2, 3] (list regression)
        "main()\n")
    assert out.split("\n")[:4] == [
        "[1, 2, 3, 4, 5]", "[1, 2, 3, 4, 5]", "[]", "[1, 2, 3]",
    ], out
