"""tuple.count(x) / tuple.index(x) under strict no-libpython.

Tuples had no method dispatch, so t.count(x) / t.index(x) forced the libpython
fallback (hard error under --python-libpython=off). Fix: runtime helpers
py_tuple_count / py_tuple_index (py_tuple_methods.c, OBJ_PY_CC_HELPERS — compare
via py_obj_eq; index raises ValueError when absent) + a frontend tuple-method
dispatch (_maybe_emit_tuple_method).

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


def test_tuple_count_index(tmp_path):
    out = _run(tmp_path,
        "def main():\n"
        "    t = (1, 2, 2, 3, 2)\n"
        "    print(t.count(2))\n"                  # 3
        "    print(t.count(99))\n"                 # 0
        "    print(t.index(3))\n"                  # 3
        "    print(t.index(2))\n"                  # 1
        "    print(('a', 'b', 'a').count('a'))\n"  # 2
        "    print(('x', 'y', 'z').index('y'))\n"  # 1
        "    try:\n"
        "        (1, 2).index(99)\n"
        "        print('no-error')\n"
        "    except ValueError:\n"
        "        print('ValueError')\n"            # ValueError
        "main()\n")
    assert out.split("\n")[:7] == [
        "3", "0", "3", "1", "2", "1", "ValueError",
    ], out
