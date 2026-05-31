"""repr/str/print of bytearray -> bytearray(b'...') under strict no-libpython.

print(bytearray(...)) showed <object tag=18> and repr/str gave <null>: no
bytearray formatter (PY_TYPE_BYTEARRAY=18) existed. Fix: py_format_bytearray =
'bytearray(' + the bytes formatter + ')' (bytes and bytearray share layout) in
py_print_fmt.c + port (print path) and _format_bytearray_str in py_obj_stubs.py
(repr/str path). Non-ASCII bytes escape as \\xNN (inherited from #49).

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


def test_bytearray_repr(tmp_path):
    out = _run(tmp_path,
        "def main():\n"
        "    ba = bytearray(b'Hello')\n"
        "    print(ba)\n"                          # bytearray(b'Hello')
        "    print(repr(ba))\n"                     # bytearray(b'Hello')
        "    print(str(ba))\n"                      # bytearray(b'Hello')
        "    print([ba])\n"                         # [bytearray(b'Hello')]
        "    print(bytearray(b'\\xcf\\x80'))\n"     # bytearray(b'\xcf\x80')
        "    print(bytearray(b''))\n"               # bytearray(b'')
        "    print(bytearray(b'a\\tb'))\n"          # bytearray(b'a\tb')
        "main()\n")
    assert out.split("\n")[:7] == [
        "bytearray(b'Hello')",
        "bytearray(b'Hello')",
        "bytearray(b'Hello')",
        "[bytearray(b'Hello')]",
        r"bytearray(b'\xcf\x80')",
        "bytearray(b'')",
        r"bytearray(b'a\tb')",
    ], out
