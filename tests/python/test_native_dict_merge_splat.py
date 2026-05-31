"""dict literal with ** unpacking ({**d, **e}) under strict no-libpython.

`{**d, **e}` (and `{**d, "k": v}`) raised a runtime `NameError: name '**' is not
defined`: the lift encodes a `**mapping` splat as a pair whose key is the
sentinel Name("**"), but `_emit_dict_literal` emitted that sentinel as a real
key lookup. Fix: a splat-aware dict builder (`_emit_dict_literal_with_splat`)
that py_dict_update-merges each splat and py_dict_set's ordinary pairs in source
order. Very common modern idiom (dict merging).
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


def test_dict_merge_splat_no_libpython(tmp_path):
    out = _run(tmp_path,
        "def main():\n"
        "    d = {'a': 1}\n"
        "    e = {'b': 2}\n"
        "    print(sorted({**d, **e}.items()))\n"          # [('a', 1), ('b', 2)]
        "    print(sorted({**d, 'c': 3}.items()))\n"        # [('a', 1), ('c', 3)]
        "    print(sorted({'x': 0, **d, **e}.items()))\n"   # [('a', 1), ('b', 2), ('x', 0)]
        "    print({**d, 'a': 99})\n"                        # {'a': 99}  (later wins)
        "    print({**{'a': 1}, **{'a': 2}})\n"             # {'a': 2}
        "    base = {'host': 'x', 'port': 80}\n"
        "    print(sorted({**base, 'port': 443}.items()))\n"  # [('host', 'x'), ('port', 443)]
        "    print(sorted({'k': 1, 'm': 2}.items()))\n"     # [('k', 1), ('m', 2)] (regression)
        "main()\n")
    assert out.split("\n")[:7] == [
        "[('a', 1), ('b', 2)]",
        "[('a', 1), ('c', 3)]",
        "[('a', 1), ('b', 2), ('x', 0)]",
        "{'a': 99}",
        "{'a': 2}",
        "[('host', 'x'), ('port', 443)]",
        "[('k', 1), ('m', 2)]",
    ], out
