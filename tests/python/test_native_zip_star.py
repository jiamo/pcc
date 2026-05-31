"""zip(*matrix) transpose under strict no-libpython.

zip(*matrix) failed at runtime with "NameError: name '*'": the static zip
lowering (_maybe_emit_zip_builtin) fixes the result-tuple width at
len(expr.args) and emits each arg, so the *m splat marker became a Name("*")
lookup. zip(*matrix) needs the runtime number of rows as the iterables.

Fix: a runtime helper py_zip_star(rows) (py_call_splat.c, reusing the
pcc_sequence_* splat accessors) transposes the splat sequence's elements into a
list of tuples, truncated to the shortest row. The frontend routes a single
*splat zip arg to it. Normal zip(a, b, ...) keeps the static path.

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


def test_zip_star_transpose(tmp_path):
    out = _run(tmp_path,
        "def main():\n"
        "    matrix = [[1, 2, 3], [4, 5, 6]]\n"
        "    print([list(col) for col in zip(*matrix)])\n"   # [[1,4],[2,5],[3,6]]
        "    print(list(zip(*matrix)))\n"                     # [(1,4),(2,5),(3,6)]
        "    print(list(zip(*[[1, 2, 3], [4, 5]])))\n"        # ragged -> [(1,4),(2,5)]
        "    print(list(zip(*[[1, 2], [3, 4], [5, 6]])))\n"   # 3 rows
        "main()\n")
    assert out.split("\n")[:4] == [
        "[[1, 4], [2, 5], [3, 6]]",
        "[(1, 4), (2, 5), (3, 6)]",
        "[(1, 4), (2, 5)]",
        "[(1, 3, 5), (2, 4, 6)]",
    ], out


def test_zip_star_unzip_and_regression(tmp_path):
    out = _run(tmp_path,
        "def main():\n"
        "    pairs = [(1, 'x'), (2, 'y'), (3, 'z')]\n"
        "    nums, lets = zip(*pairs)\n"
        "    print(list(nums), list(lets))\n"               # [1,2,3] ['x','y','z']
        "    print(list(zip([1, 2], ['a', 'b'])))\n"        # normal zip regression
        "main()\n")
    assert out.split("\n")[:2] == [
        "[1, 2, 3] ['x', 'y', 'z']",
        "[(1, 'a'), (2, 'b')]",
    ], out
