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


def test_bytearray_slice_delete_no_libpython(tmp_path):
    out = _run(tmp_path,
        "def main():\n"
        "    ba = bytearray(b'abcdef')\n"
        "    del ba[:2]\n"
        "    print(ba)\n"
        "    del ba[1::2]\n"
        "    print(ba)\n"
        "    del ba[::-2]\n"
        "    print(ba)\n"
        "main()\n")
    assert out.split("\n")[:3] == [
        "bytearray(b'cdef')", "bytearray(b'ce')", "bytearray(b'c')",
    ], out


def test_bytearray_extend_statement_updates_local_no_libpython(tmp_path):
    out = _run(tmp_path,
        "def main():\n"
        "    ba = bytearray()\n"
        "    ba.extend(b'ab')\n"
        "    ba.extend(bytearray(b'c'))\n"
        "    ba.extend([100, 101])\n"
        "    print(ba)\n"
        "    print(bytes(ba))\n"
        "    print(ba.decode('utf-8'))\n"
        "main()\n")
    assert out.split("\n")[:3] == [
        "bytearray(b'abcde')", "b'abcde'", "abcde",
    ], out


def test_bytearray_extend_statement_updates_attr_no_libpython(tmp_path):
    out = _run(tmp_path,
        "class Box:\n"
        "    def __init__(self):\n"
        "        self.buf = bytearray()\n"
        "    def add(self, data):\n"
        "        self.buf.extend(data)\n"
        "\n"
        "def main():\n"
        "    b = Box()\n"
        "    b.add(b'xy')\n"
        "    b.add(bytearray(b'z'))\n"
        "    print(b.buf)\n"
        "main()\n")
    assert out.strip() == "bytearray(b'xyz')", out


def test_bytes_and_bytearray_find_no_libpython(tmp_path):
    out = _run(tmp_path,
        "def main():\n"
        "    data = b'GET /abc\\r\\n\\r\\n'\n"
        "    print(data.find(b'GET'))\n"
        "    print(data.find(b'\\r\\n\\r\\n'))\n"
        "    print(data.find(b'missing'))\n"
        "    print(data.find(47))\n"
        "    ba = bytearray(data)\n"
        "    print(ba.find(b'abc'))\n"
        "main()\n")
    assert out.splitlines() == ["0", "8", "-1", "4", "5"], out
