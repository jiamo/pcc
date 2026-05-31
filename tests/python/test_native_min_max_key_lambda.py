"""min()/max() with key=<simple lambda> under strict no-libpython.

Sibling of sorted(key=) (#56). min(xs, key=lambda x: x.attr) forced the
libpython fallback (a key= callable needs first-class-function boxing, which
no-libpython pcc lacks).

Fix (frontend, no fn boxing): reuse the sorted() key machinery
(_sorted_key_spec_from_lambda + _emit_key_of, attr-chain / int-subscript only)
in an object-accumulator fold (_emit_min_max_by_key_fold) that materialises the
iterable to a list and tracks the extreme ELEMENT, comparing inline-extracted
keys via py_obj_lt. Strict ``<`` keeps the FIRST extreme element (CPython
stability). A non-simple key lambda / non-lambda key / unknown kwarg falls
through to the libpython path.

Runs under ``--backend self --python-libpython=off`` in DEFAULT runtime mode.
"""
from __future__ import annotations
import os, subprocess


def _run(tmp_path, source):
    src = tmp_path / "p.py"; src.write_text(source, encoding="utf-8")
    exe = tmp_path / "p_bin"; env = os.environ.copy(); env.pop("LC_ALL", None)
    b = subprocess.run(["uv","run","pcc","--backend","self","--python-libpython=off","--ir-scaffold=on",str(src),"-o",str(exe)], text=True, capture_output=True, timeout=420, env=env)
    assert b.returncode == 0, b.stderr
    r = subprocess.run([str(exe)], text=True, capture_output=True, timeout=30, env=env)
    assert r.returncode == 0, r.stderr
    return r.stdout


def test_min_max_key_attr_and_index(tmp_path):
    out = _run(tmp_path,
        "class P:\n"
        "    def __init__(self, name, age):\n"
        "        self.name = name\n"
        "        self.age = age\n"
        "def main():\n"
        "    ppl = [P('Carol', 30), P('Alice', 25), P('Bob', 35)]\n"
        "    print(min(ppl, key=lambda x: x.age).name)\n"   # Alice
        "    print(max(ppl, key=lambda x: x.age).name)\n"   # Bob
        "    items = [('a', 3), ('b', 1), ('c', 2)]\n"
        "    print(min(items, key=lambda kv: kv[1]))\n"      # ('b', 1)
        "    print(max(items, key=lambda kv: kv[1]))\n"      # ('a', 3)
        "main()\n")
    assert out.split("\n")[:4] == [
        "Alice", "Bob", "('b', 1)", "('a', 3)",
    ], out


def test_min_max_key_tie_single_default(tmp_path):
    out = _run(tmp_path,
        "class P:\n"
        "    def __init__(self, s, n):\n"
        "        self.s = s\n"
        "        self.n = n\n"
        "def main():\n"
        "    ps = [P('x', 3), P('y', 3), P('z', 1)]\n"
        "    print(min(ps, key=lambda p: p.n).s)\n"                   # z
        "    print(max(ps, key=lambda p: p.n).s)\n"                   # x (first of tied max)
        "    print(min([P('solo', 9)], key=lambda p: p.n).s)\n"       # solo
        "    print(min([], key=lambda p: p.n, default='DEF'))\n"      # DEF
        "main()\n")
    assert out.split("\n")[:4] == ["z", "x", "solo", "DEF"], out


def test_min_max_no_key_regression(tmp_path):
    # min/max without key= must keep their existing paths (int fold, str
    # py_obj_min_max, and the #55 custom-__lt__ object fold).
    out = _run(tmp_path,
        "class Ver:\n"
        "    def __init__(self, v):\n"
        "        self.v = v\n"
        "    def __lt__(self, other):\n"
        "        return self.v < other.v\n"
        "def main():\n"
        "    print(min([5, 2, 8]))\n"                     # 2
        "    print(max(['pear', 'apple']))\n"            # pear
        "    print(min([Ver(3), Ver(1), Ver(2)]).v)\n"    # 1
        "main()\n")
    assert out.split("\n")[:3] == ["2", "pear", "1"], out
