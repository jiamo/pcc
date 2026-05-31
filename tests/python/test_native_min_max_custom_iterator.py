"""min()/max() over a custom __iter__/__next__ iterator under no-libpython.

min()/max() of a single iterable only accepted List/Tuple/Dyn args; a ClassType
(custom iterator) bailed -> a generic name lookup ("NameError: name 'min'/'max'"
at runtime). Fix (frontend, follow-up to #41/#42): materialise a ClassType arg
to a list via the iterator protocol, then run the existing index-based min/max
fold over the list. Matches CPython min/max via iter().

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


def test_min_max_over_custom_iterator(tmp_path):
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
        "def main():\n"
        "    print(max(C([3, 7, 2, 5])))\n"     # 7
        "    print(min(C([3, 7, 2, 5])))\n"     # 2
        "    print(max(C([42])))\n"             # 42
        "    print(min(C([-1, -9, -3])))\n"     # -9
        "    print(max([10, 4, 8]))\n"          # 10 (list regression)
        "    print(min([10, 4, 8]))\n"          # 4 (list regression)
        "main()\n")
    assert out.split("\n")[:6] == ["7", "2", "42", "-9", "10", "4"], out
