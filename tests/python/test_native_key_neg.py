"""Negated key= (descending sort) for sorted()/min()/max() under no-libpython.

key=lambda kv: -kv[1] (sort by value descending) and the mixed-sign tuple
key=lambda kv: (-kv[1], kv[0]) (count desc, then name asc) — extremely common
(word frequency, leaderboards) — fell back to libpython: the #56-#59 key spec
only recognised attr/index/strmethod, a UnaryOp '-' body bailed.

Fix (extends the inline key= machinery): _simple_key_subspec recognises
``-<attr/index>`` -> ('neg', subspec); _emit_key_of emits the generic
``0 - subkey`` via py_obj_sub (correct for int and float keys). Because neg is
a scalar subspec, it also composes inside a tuple key. Both the sorted
insertion sort and the min/max object fold pick it up.

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


def test_sorted_neg_scalar_and_tuple_key(tmp_path):
    out = _run(tmp_path,
        "def main():\n"
        "    counts = {'the': 3, 'cat': 2, 'ran': 2, 'dog': 1}\n"
        "    print(sorted(counts.items(), key=lambda kv: -kv[1]))\n"
        "    print(sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])))\n"
        "main()\n")
    assert out.split("\n")[:2] == [
        "[('the', 3), ('cat', 2), ('ran', 2), ('dog', 1)]",
        "[('the', 3), ('cat', 2), ('ran', 2), ('dog', 1)]",
    ], out


def test_neg_attr_key_sorted_min_max(tmp_path):
    out = _run(tmp_path,
        "class P:\n"
        "    def __init__(self, name, score):\n"
        "        self.name = name\n"
        "        self.score = score\n"
        "def main():\n"
        "    ppl = [P('a', 10), P('b', 30), P('c', 20)]\n"
        "    print([p.name for p in sorted(ppl, key=lambda p: -p.score)])\n"  # b,c,a
        "    print(min(ppl, key=lambda p: -p.score).name)\n"                  # b (max score)
        "    print(max(ppl, key=lambda p: -p.score).name)\n"                  # a (min score)
        "main()\n")
    assert out.split("\n")[:3] == ["['b', 'c', 'a']", "b", "a"], out


def test_positive_key_regression(tmp_path):
    out = _run(tmp_path,
        "def main():\n"
        "    counts = {'the': 3, 'cat': 2, 'dog': 1}\n"
        "    print(sorted(counts.items(), key=lambda kv: kv[1]))\n"   # ascending
        "main()\n")
    assert out.split("\n")[0] == "[('dog', 1), ('cat', 2), ('the', 3)]", out


def test_identity_and_neg_identity_key_sorted(tmp_path):
    out = _run(tmp_path,
        "def main():\n"
        "    print(sorted([3, 1, 2], key=lambda v: v))\n"
        "    print(sorted([3, 1, 2], key=lambda v: -v))\n"
        "main()\n")
    assert out.split("\n")[:2] == ["[1, 2, 3]", "[3, 2, 1]"], out


def test_builtin_len_key_sorted_min_max(tmp_path):
    out = _run(tmp_path,
        "def main():\n"
        "    words = ['bb', 'a', 'ccc']\n"
        "    print(sorted(words, key=len))\n"
        "    print(max(words, key=len), min(words, key=len))\n"
        "main()\n")
    assert out.split("\n")[:2] == ["['a', 'bb', 'ccc']", "ccc a"], out


def test_list_sort_with_key_in_place(tmp_path):
    out = _run(tmp_path,
        "def main():\n"
        "    pairs = [(1, 'b'), (2, 'a')]\n"
        "    pairs.sort(key=lambda t: t[1])\n"
        "    print(pairs)\n"
        "    words = ['bb', 'a', 'ccc']\n"
        "    words.sort(key=len, reverse=True)\n"
        "    print(words)\n"
        "main()\n")
    assert out.split("\n")[:2] == [
        "[(2, 'a'), (1, 'b')]",
        "['ccc', 'bb', 'a']",
    ], out
