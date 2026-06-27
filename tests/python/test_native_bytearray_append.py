"""bytearray.append(int) under strict no-libpython.

bytearray.extend(...) worked but .append(int) was unwired: the ExprStmt
lowering only special-cased .extend, so a `ba.append(100)` statement fell
through to generic attribute-method dispatch and the runtime raised
`AttributeError: append`. Fix: py_bytearray_append (C runtime + pcc-Python
port, registered in runtime_abi) appends one byte (ValueError when out of
range(0,256), TypeError for a non-int arg) and returns a new bytearray;
frontend `_maybe_emit_bytearray_append_stmt` mirrors extend for both the
statically-ByteArrayType local and the Dyn-typed self.<attr> paths, storing
the result back into the target.

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


def test_bytearray_append_statement_updates_local_no_libpython(tmp_path):
    out = _run(tmp_path,
        "def main():\n"
        "    ba = bytearray(b'abc')\n"
        "    ba.append(100)\n"                 # -> b'abcd'
        "    ba.append(0)\n"
        "    ba.append(255)\n"
        "    print(ba)\n"
        "    print(bytes(ba))\n"
        "    print(len(ba))\n"
        "main()\n")
    assert out.split("\n")[:3] == [
        "bytearray(b'abcd\\x00\\xff')",
        "b'abcd\\x00\\xff'",
        "6",
    ], out


def test_bytearray_append_on_empty_no_libpython(tmp_path):
    out = _run(tmp_path,
        "def main():\n"
        "    ba = bytearray()\n"
        "    ba.append(72)\n"
        "    ba.append(105)\n"
        "    print(bytes(ba))\n"               # b'Hi'
        "main()\n")
    assert out.strip() == "b'Hi'", out


def test_bytearray_append_statement_updates_attr_no_libpython(tmp_path):
    out = _run(tmp_path,
        "class Buf:\n"
        "    def __init__(self):\n"
        "        self.data = bytearray()\n"
        "    def push(self, x):\n"
        "        self.data.append(x)\n"
        "\n"
        "def main():\n"
        "    b = Buf()\n"
        "    b.push(120)\n"
        "    b.push(121)\n"
        "    b.push(122)\n"
        "    print(b.data)\n"
        "main()\n")
    assert out.strip() == "bytearray(b'xyz')", out


def test_bytearray_append_out_of_range_raises_valueerror_no_libpython(tmp_path):
    out = _run(tmp_path,
        "def main():\n"
        "    ba = bytearray(b'ab')\n"
        "    try:\n"
        "        ba.append(256)\n"
        "    except ValueError:\n"
        "        print('ValueError-256')\n"
        "    try:\n"
        "        ba.append(-1)\n"
        "    except ValueError:\n"
        "        print('ValueError-neg')\n"
        "    ba.append(99)\n"                  # still usable after caught errors
        "    print(bytes(ba))\n"
        "main()\n")
    assert out.split("\n")[:3] == [
        "ValueError-256", "ValueError-neg", "b'abc'",
    ], out
