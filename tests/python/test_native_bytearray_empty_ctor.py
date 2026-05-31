"""bytearray() (0-arg empty constructor) under strict no-libpython.

bytearray(b'...') (1-arg) worked but bytearray() (0-arg) forced the libpython
fallback (the lowering only handled len(args)==1). Fix (frontend): a 0-arg case
building an empty bytearray from an empty bytes object (py_bytes_new(NULL,0) +
py_bytearray_from_obj), mirroring the bytes() 0-arg path.

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


def test_bytearray_empty_constructor(tmp_path):
    out = _run(tmp_path,
        "def main():\n"
        "    ba = bytearray()\n"
        "    print(ba)\n"                       # bytearray(b'')
        "    print(len(ba))\n"                  # 0
        "    print(ba == bytearray(b''))\n"     # True
        "    print(bytearray(b'xy'))\n"         # bytearray(b'xy') (1-arg regression)
        "main()\n")
    assert out.split("\n")[:4] == [
        "bytearray(b'')", "0", "True", "bytearray(b'xy')",
    ], out
