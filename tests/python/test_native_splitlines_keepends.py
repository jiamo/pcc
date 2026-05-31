"""str.splitlines(keepends) with a POSITIONAL keepends arg under no-libpython.

splitlines() (no arg) and splitlines(keepends=...) (kwarg) worked, but the
common positional form splitlines(True) bailed to the libpython fallback —
the dispatch guard required ``not expr.args``. Fix (frontend): accept a single
positional arg, reading the keepends bool from args[0] (or the kwarg);
splitlines(True) routes to py_str_splitlines_keepends like the kwarg form.

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


def test_splitlines_positional_keepends(tmp_path):
    out = _run(tmp_path,
        "def main():\n"
        "    print('a\\nb\\nc'.splitlines(True))\n"     # ['a\n', 'b\n', 'c']
        "    print('a\\nb\\nc'.splitlines(False))\n"    # ['a', 'b', 'c']
        "    print('a\\nb\\n'.splitlines(1))\n"         # ['a\n', 'b\n']
        "    print('a\\nb'.splitlines())\n"             # ['a', 'b']
        "    print('x\\r\\ny'.splitlines(True))\n"      # ['x\r\n', 'y']
        "    print('line'.splitlines(keepends=True))\n" # ['line']
        "main()\n")
    assert out.split("\n")[:6] == [
        r"['a\n', 'b\n', 'c']",
        "['a', 'b', 'c']",
        r"['a\n', 'b\n']",
        "['a', 'b']",
        r"['x\r\n', 'y']",
        "['line']",
    ], out
