"""sorted(xs, key=<simple lambda>) under strict no-libpython.

sorted(xs, key=lambda x: x.attr) / key=lambda x: x[N] used to force the
libpython fallback: a key= callable needs first-class-function boxing, which
no-libpython pcc does not have (the lambda lowered to a CPython
attrgetter/itemgetter).

Fix (frontend, no first-class fn): when key= is a simple single-param lambda
whose body is an attribute chain (x.a.b) or an integer subscript (x[N]),
build a COPY of the list (sorted() is non-mutating) and insertion-sort it
comparing INLINE-extracted keys via py_obj_lt (py_obj_getattr / py_obj_getitem
+ runtime ordering, correct for int/str/float keys). A non-simple key lambda or
a non-lambda key (key=str.lower) still falls through to the libpython path.

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


def test_sorted_key_attr(tmp_path):
    out = _run(tmp_path,
        "class P:\n"
        "    def __init__(self, name, age):\n"
        "        self.name = name\n"
        "        self.age = age\n"
        "def main():\n"
        "    ppl = [P('Carol', 30), P('Alice', 25), P('Bob', 35)]\n"
        "    print([p.name for p in sorted(ppl, key=lambda x: x.age)])\n"            # by int attr
        "    print([p.name for p in sorted(ppl, key=lambda x: x.name)])\n"           # by str attr
        "    print([p.age for p in sorted(ppl, key=lambda x: x.age, reverse=True)])\n"  # reverse
        "main()\n")
    assert out.split("\n")[:3] == [
        "['Alice', 'Carol', 'Bob']",   # by age: 25, 30, 35
        "['Alice', 'Bob', 'Carol']",   # by name
        "[35, 30, 25]",                # by age, reversed
    ], out


def test_sorted_key_index(tmp_path):
    out = _run(tmp_path,
        "def main():\n"
        "    items = [('a', 3), ('b', 1), ('c', 2)]\n"
        "    print(sorted(items, key=lambda kv: kv[1]))\n"   # by tuple index
        "main()\n")
    assert out.split("\n")[0] == "[('b', 1), ('c', 2), ('a', 3)]", out


def test_sorted_plain_regression(tmp_path):
    # sorted() without key= must keep the existing primitive path.
    out = _run(tmp_path,
        "def main():\n"
        "    print(sorted([3, 1, 2]))\n"
        "    print(sorted(['pear', 'apple']))\n"
        "    print(sorted([3, 1, 2], reverse=True))\n"
        "main()\n")
    assert out.split("\n")[:3] == [
        "[1, 2, 3]", "['apple', 'pear']", "[3, 2, 1]",
    ], out
