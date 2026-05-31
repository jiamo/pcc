"""min()/max() over a list of objects with a user __lt__ (no-libpython).

Sibling of the sorted() fix (#54). min([Ver(3),Ver(1),Ver(2)]).v raised
``AttributeError: v``: _maybe_emit_min_max_iter took the i64-accumulator fold
for a ListType(ClassType) arg — it read instance pointers as integers
(comparing addresses) and returned an i64, so the result was not a Ver. The
py_obj_min_max route is equally __lt__-blind (runtime cmp_threeway
pointer-compares instances).

Fix (frontend): when the element class has a resolvable __lt__, route min()/max()
to a static-__lt__ object fold (_emit_min_max_obj_lt_fold) — a linear scan whose
per-pair compare uses _emit_direct_method_value_call (the same static dunder
resolution the `<` operator and #54 use), returning the extreme ELEMENT object.
int/str/custom-iterator args keep their existing paths.

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


def test_min_max_custom_lt(tmp_path):
    out = _run(tmp_path,
        "class Ver:\n"
        "    def __init__(self, v):\n"
        "        self.v = v\n"
        "    def __lt__(self, other):\n"
        "        return self.v < other.v\n"
        "def main():\n"
        "    xs = [Ver(3), Ver(1), Ver(2)]\n"
        "    print(min(xs).v)\n"                          # 1
        "    print(max(xs).v)\n"                          # 3
        "    print(min([Ver(5), Ver(9), Ver(2)]).v)\n"    # 2
        "    print(max([Ver(5), Ver(9), Ver(2)]).v)\n"    # 9
        "    print(min([Ver(7)]).v)\n"                    # 7 (single element)
        "main()\n")
    assert out.split("\n")[:5] == ["1", "3", "2", "9", "7"], out


def test_min_max_primitive_regression(tmp_path):
    # int (i64 fold) / str (py_obj_min_max) / default= must keep working.
    out = _run(tmp_path,
        "def main():\n"
        "    print(min([5, 2, 8, 1]))\n"          # 1
        "    print(max([5, 2, 8, 1]))\n"          # 8
        "    print(min(['pear', 'apple']))\n"     # apple
        "    print(max(['pear', 'apple']))\n"     # pear
        "    print(min([3, 7, 2], default=99))\n" # 2
        "    print(min([], default=99))\n"        # 99
        "main()\n")
    assert out.split("\n")[:6] == ["1", "8", "apple", "pear", "2", "99"], out


def test_min_max_custom_iterator_regression(tmp_path):
    # A custom __iter__/__next__ class arg (#43) must NOT be hijacked by the
    # list-of-class object fold (its arg is a Call, not a ListExpr).
    out = _run(tmp_path,
        "class Counter:\n"
        "    def __init__(self, n):\n"
        "        self.n = n\n"
        "        self.i = 0\n"
        "    def __iter__(self):\n"
        "        return self\n"
        "    def __next__(self):\n"
        "        if self.i >= self.n:\n"
        "            raise StopIteration\n"
        "        self.i += 1\n"
        "        return self.i\n"
        "def main():\n"
        "    print(min(Counter(5)))\n"   # 1
        "    print(max(Counter(5)))\n"   # 5
        "main()\n")
    assert out.split("\n")[:2] == ["1", "5"], out
