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


def _run(tmp_path, source, *, python_libpython="off"):
    src = tmp_path / "p.py"; src.write_text(source, encoding="utf-8")
    exe = tmp_path / "p_bin"; env = os.environ.copy(); env.pop("LC_ALL", None)
    b = subprocess.run(["uv","run","pcc","--backend","self",f"--python-libpython={python_libpython}","--ir-scaffold=on",str(src),"-o",str(exe)], text=True, capture_output=True, timeout=420, env=env)
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


def test_sorted_key_sum_of_index_then_slice(tmp_path):
    out = _run(tmp_path,
        "def main():\n"
        "    items = [('low', [1, 1]), ('high', [5, 4]), ('mid', [3, 2])]\n"
        "    top = sorted(items, key=lambda item: sum(item[1]), reverse=True)[:2]\n"
        "    print(top)\n"
        "main()\n")
    assert out.split("\n")[0] == "[('high', [5, 4]), ('mid', [3, 2])]", out


def test_sorted_key_captured_lambda_libpython_fallback(tmp_path):
    out = _run(
        tmp_path,
        "def main():\n"
        "    group_of = [1, 0, 1, 0]\n"
        "    print(sorted(range(4), key=lambda vv: (group_of[vv], vv)))\n"
        "main()\n",
        python_libpython="auto",
    )
    assert out.split("\n")[0] == "[1, 3, 0, 2]", out


def test_sorted_key_captured_index_lambda_in_nested_function(tmp_path):
    out = _run(
        tmp_path,
        "def main():\n"
        "    nvec = 4\n"
        "    group_of = [1, 0, 1, 0]\n"
        "    def plan_epoch(u):\n"
        "        out = []\n"
        "        for w in sorted(range(nvec), key=lambda vv: -group_of[vv]):\n"
        "            if w != u:\n"
        "                out.append(w)\n"
        "        return out\n"
        "    print(plan_epoch(1))\n"
        "main()\n",
        python_libpython="auto",
    )
    assert out.split("\n")[0] == "[0, 2, 3]", out


def test_sorted_key_captured_index_tuple_lambda(tmp_path):
    out = _run(
        tmp_path,
        "def main():\n"
        "    group_of = [1, 0, 1, 0]\n"
        "    print(sorted(range(4), key=lambda vv: (group_of[vv], vv)))\n"
        "main()\n",
        python_libpython="auto",
    )
    assert out.split("\n")[0] == "[1, 3, 0, 2]", out


def test_sorted_key_captured_lambda_ternary_result_slice(tmp_path):
    out = _run(
        tmp_path,
        "def main():\n"
        "    group_of = [1, 0, 1, 0]\n"
        "    ordered = sorted(range(4), key=lambda vv: (group_of[vv], vv)) if len(group_of) == 4 else list(range(4))\n"
        "    print(ordered[:2])\n"
        "main()\n",
        python_libpython="auto",
    )
    assert out.split("\n")[0] == "[1, 3]", out


def test_cpython_loop_flag_does_not_leak_into_nested_function_param(tmp_path):
    out = _run(
        tmp_path,
        "def main():\n"
        "    group_of = [1, 0, 1, 0]\n"
        "    ordered = sorted(range(4), key=lambda v: (group_of[v], v))\n"
        "    for v in ordered:\n"
        "        pass\n"
        "    def inner(v):\n"
        "        print(v * 2)\n"
        "    inner(3)\n"
        "main()\n",
        python_libpython="auto",
    )
    assert out.split("\n")[0] == "6", out


def test_cpython_for_target_flag_cleared_by_range_rebind(tmp_path):
    out = _run(
        tmp_path,
        "def main():\n"
        "    group_of = [1, 0, 1, 0]\n"
        "    ordered = sorted(range(4), key=lambda v: (group_of[v], v))\n"
        "    for v in ordered:\n"
        "        pass\n"
        "    for v in range(3):\n"
        "        print(v % 2)\n"
        "main()\n",
        python_libpython="auto",
    )
    assert out.split("\n")[:3] == ["0", "1", "0"], out


def test_cpython_for_target_can_index_pcc_list(tmp_path):
    out = _run(
        tmp_path,
        "def main():\n"
        "    values = [10, 20, 30, 40]\n"
        "    group_of = [0, 1, 0, 1]\n"
        "    ordered = sorted(range(4), key=lambda u: (group_of[u], u))\n"
        "    for u in ordered:\n"
        "        print(values[u])\n"
        "main()\n",
        python_libpython="auto",
    )
    assert out.split("\n")[:4] == ["10", "30", "20", "40"], out


def test_cpython_for_target_passed_to_user_function_as_pcc_arg(tmp_path):
    out = _run(
        tmp_path,
        "def main():\n"
        "    values = [10, 20, 30, 40]\n"
        "    group_of = [0, 1, 0, 1]\n"
        "    def show(w):\n"
        "        print(values[w])\n"
        "    ordered = sorted(range(4), key=lambda w: (group_of[w], w))\n"
        "    for w in ordered:\n"
        "        show(w)\n"
        "main()\n",
        python_libpython="auto",
    )
    assert out.split("\n")[:4] == ["10", "30", "20", "40"], out


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
