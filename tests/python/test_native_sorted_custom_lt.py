"""sorted() over a list of objects with a user __lt__ (no-libpython).

sorted([Ver(3), Ver(1), Ver(2)]) used to return the list UNORDERED: the
builtin sorted() routes through the runtime py_obj_sorted, whose comparison
primitive (py_obj_cmp_threeway) pointer-compares instances and never
dispatches a Python __lt__. (The `<` operator and list.sort() already worked
— they use the frontend's static method resolution.) Three runtime-level
attempts to teach cmp_threeway to dispatch __lt__ failed (a bound-method
double-self bug in py_obj_call_method1, then two reached-but-ineffective
class-lookup variants — see docs/investigations/
sorted-min-max-custom-lt-not-used-no-libpython.md).

Fix (frontend): when sorted()'s argument is a list whose element class has a
resolvable __lt__, route to the SAME static-__lt__ insertion sort that
list.sort() uses (_emit_list_sort_with_dunder_lt), on a COPY of the list
(sorted() is non-mutating). This sidesteps the runtime comparison primitive
entirely. Non-class / int / str lists keep py_obj_sorted.

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


def test_sorted_custom_lt(tmp_path):
    out = _run(tmp_path,
        "class Ver:\n"
        "    def __init__(self, v):\n"
        "        self.v = v\n"
        "    def __lt__(self, other):\n"
        "        return self.v < other.v\n"
        "def main():\n"
        "    print([x.v for x in sorted([Ver(3), Ver(1), Ver(2)])])\n"   # [1, 2, 3]
        "    items = [Ver(5), Ver(2), Ver(8), Ver(1)]\n"
        "    print([x.v for x in sorted(items)])\n"                      # [1, 2, 5, 8]
        "    print([x.v for x in items])\n"                              # [5, 2, 8, 1] (non-mutating)
        "    print([x.v for x in sorted([Ver(3), Ver(1), Ver(2)], reverse=True)])\n"  # [3, 2, 1]
        "main()\n")
    assert out.split("\n")[:4] == [
        "[1, 2, 3]", "[1, 2, 5, 8]", "[5, 2, 8, 1]", "[3, 2, 1]",
    ], out


def test_sorted_primitive_regression(tmp_path):
    # int / str lists must keep the py_obj_sorted path.
    out = _run(tmp_path,
        "def main():\n"
        "    print(sorted([3, 1, 2]))\n"                      # [1, 2, 3]
        "    print(sorted([3, 1, 2], reverse=True))\n"        # [3, 2, 1]
        "    print(sorted(['banana', 'apple', 'cherry']))\n"  # alphabetical
        "main()\n")
    assert out.split("\n")[:3] == [
        "[1, 2, 3]", "[3, 2, 1]", "['apple', 'banana', 'cherry']",
    ], out
