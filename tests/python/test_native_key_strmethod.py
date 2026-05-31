"""No-arg str-method key= (case-insensitive sort etc.) under no-libpython.

Extends the inline key= machinery (#56/#57/#58) to ``key=lambda s: s.lower()``
and the other no-arg str transforms (upper/casefold/title/capitalize/swapcase/
strip), for sorted()/min()/max() over a list whose element type is statically
str. _simple_key_subspec returns ('strmethod', name) gated on a StrType element
(threaded from the call site as elem_ty); _emit_key_of emits py_str_<name>(elem).
A non-str element / unknown type rejects the strmethod shape -> the key lambda
falls back to the libpython path (correct).

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


def test_sorted_case_insensitive(tmp_path):
    out = _run(tmp_path,
        "def main():\n"
        "    words = ['Banana', 'apple', 'Cherry', 'date']\n"
        "    print(sorted(words, key=lambda s: s.lower()))\n"
        "    print(sorted(['XYZ', 'abc'], key=lambda s: s.upper()))\n"
        "main()\n")
    assert out.split("\n")[:2] == [
        "['apple', 'Banana', 'Cherry', 'date']",
        "['abc', 'XYZ']",
    ], out


def test_min_max_str_method_key(tmp_path):
    out = _run(tmp_path,
        "def main():\n"
        "    words = ['Banana', 'apple', 'Cherry', 'date']\n"
        "    print(min(words, key=lambda s: s.lower()))\n"   # apple
        "    print(max(words, key=lambda s: s.lower()))\n"   # date
        "main()\n")
    assert out.split("\n")[:2] == ["apple", "date"], out


def test_key_regressions_intact(tmp_path):
    # attr/index/tuple/no-key paths unaffected by the strmethod addition.
    out = _run(tmp_path,
        "class P:\n"
        "    def __init__(self, n):\n"
        "        self.n = n\n"
        "def main():\n"
        "    print([p.n for p in sorted([P(3), P(1), P(2)], key=lambda x: x.n)])\n"  # [1,2,3]
        "    print(sorted([(2,'b'),(1,'a')], key=lambda x: x[0]))\n"                  # [(1,'a'),(2,'b')]
        "    print(sorted([5, 2, 8]))\n"                                              # [2,5,8]
        "main()\n")
    assert out.split("\n")[:3] == [
        "[1, 2, 3]", "[(1, 'a'), (2, 'b')]", "[2, 5, 8]",
    ], out
