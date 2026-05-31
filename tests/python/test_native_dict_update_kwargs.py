"""dict.update(**kwargs) (keyword form) under strict no-libpython.

`d.update({...})` (positional) worked but `d.update(x=10)` fell back:
`_maybe_emit_dict_method` bailed on ANY kwargs. Fix: allow `update` through the
kwargs guard and, in the update handler, set each named keyword pair via
py_dict_set (after an optional positional mapping). ** splats fall back.
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


def test_dict_update_kwargs_no_libpython(tmp_path):
    out = _run(tmp_path,
        "def main():\n"
        "    d = {}\n"
        "    d.update(x=10, y=20)\n"
        "    print(sorted(d.items()))\n"          # [('x', 10), ('y', 20)]
        "    d2 = {'a': 1}\n"
        "    d2.update({'b': 2}, c=3)\n"
        "    print(sorted(d2.items()))\n"         # [('a', 1), ('b', 2), ('c', 3)]
        "    d3 = {'k': 1}\n"
        "    d3.update(k=99)\n"
        "    print(d3['k'])\n"                     # 99
        "    d4 = {'a': 1}\n"
        "    d4.update({'b': 2})\n"
        "    print(sorted(d4.items()))\n"         # [('a', 1), ('b', 2)]
        "main()\n")
    assert out.split("\n")[:4] == [
        "[('x', 10), ('y', 20)]", "[('a', 1), ('b', 2), ('c', 3)]", "99",
        "[('a', 1), ('b', 2)]",
    ], out
