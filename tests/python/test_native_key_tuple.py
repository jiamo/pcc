"""Tuple key= (multi-key sort) for sorted()/min()/max() under no-libpython.

Extends the inline key= machinery (#56 sorted, #57 min/max) to a tuple body
``key=lambda x: (x.a, x.b)`` — the multi-key sort idiom. _sorted_key_spec_from_lambda
now recognises a TupleExpr whose components are each a simple attr/index subspec
('tuple', (subspec,...)); _emit_key_of builds a tuple of the component keys
(py_tuple_new + py_tuple_set_item) and the runtime py_obj_lt orders tuples
lexicographically (verified: pcc already sorts plain tuples correctly). Both the
sorted insertion sort and the min/max object fold pick this up for free.

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


def test_sorted_tuple_key(tmp_path):
    out = _run(tmp_path,
        "class P:\n"
        "    def __init__(self, last, first):\n"
        "        self.last = last\n"
        "        self.first = first\n"
        "def main():\n"
        "    ppl = [P('Smith', 'Bob'), P('Smith', 'Alice'), P('Jones', 'Carol')]\n"
        "    print([(p.last, p.first) for p in sorted(ppl, key=lambda p: (p.last, p.first))])\n"
        "    pairs = [(2, 'b'), (1, 'z'), (2, 'a'), (1, 'a')]\n"
        "    print(sorted(pairs, key=lambda x: (x[0], x[1])))\n"
        "main()\n")
    assert out.split("\n")[:2] == [
        "[('Jones', 'Carol'), ('Smith', 'Alice'), ('Smith', 'Bob')]",
        "[(1, 'a'), (1, 'z'), (2, 'a'), (2, 'b')]",
    ], out


def test_min_max_tuple_key(tmp_path):
    out = _run(tmp_path,
        "class P:\n"
        "    def __init__(self, last, first):\n"
        "        self.last = last\n"
        "        self.first = first\n"
        "def main():\n"
        "    ppl = [P('Smith', 'Bob'), P('Smith', 'Alice'), P('Jones', 'Carol')]\n"
        "    print(min(ppl, key=lambda p: (p.last, p.first)).first)\n"   # Carol (Jones<Smith)
        "    print(max(ppl, key=lambda p: (p.last, p.first)).first)\n"   # Bob (Smith,Bob)
        "main()\n")
    assert out.split("\n")[:2] == ["Carol", "Bob"], out


def test_single_key_regression(tmp_path):
    # Single attr/index key (#56/#57) must still work after the tuple refactor.
    out = _run(tmp_path,
        "class P:\n"
        "    def __init__(self, age):\n"
        "        self.age = age\n"
        "def main():\n"
        "    ppl = [P(30), P(25), P(40)]\n"
        "    print([p.age for p in sorted(ppl, key=lambda p: p.age)])\n"  # [25,30,40]
        "    print(min(ppl, key=lambda p: p.age).age)\n"                  # 25
        "    print(sorted([(2,'b'),(1,'a')], key=lambda x: x[0]))\n"      # [(1,'a'),(2,'b')]
        "main()\n")
    assert out.split("\n")[:3] == [
        "[25, 30, 40]", "25", "[(1, 'a'), (2, 'b')]",
    ], out
