"""bytearray.insert(i, b) and bytearray.pop([i]) under strict no-libpython.

.append/.extend were wired but .insert / .pop were not: `b.insert(1, 120)`
raised `AttributeError: insert` and `b.pop()` had no native path. Fix:
py_bytearray_insert / py_bytearray_pop (C runtime + pcc-Python port in
py_obj_stubs, registered in runtime_abi).

  * insert grows the inline data[] buffer (no spare capacity), so the runtime
    rebuilds a fresh object and the frontend re-binds the target — same model
    as append/extend. Index is CPython-clamped: negative adds len, then clamp
    into [0, len]. Byte out of range(0,256) raises ValueError.
  * pop removes and returns the byte (default the last) as an int, shrinking
    the receiver in place (memmove tail down + decrement byte_len). Empty ->
    IndexError; out-of-range -> IndexError. pop() == pop(-1).

Frontend: `_maybe_emit_bytearray_insert_stmt` (statement form, re-binds the
target) + a ByteArrayType `pop` branch in method_call_expression_lowering
(expression form, returns the popped tagged int). Runs under
``--backend self --python-libpython=off`` in DEFAULT runtime mode (which links
the pcc-Python port), so the port must implement the helpers too.
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


def test_bytearray_insert_and_pop_no_libpython(tmp_path):
    out = _run(tmp_path,
        "def main():\n"
        "    b = bytearray(b'abc')\n"
        "    b.insert(1, 120)\n"                  # b'axbc'
        "    print(bytes(b))\n"
        "    x = b.pop()\n"                       # 99, b'axb'
        "    print(x, bytes(b))\n"
        "main()\n")
    assert out.split("\n")[:2] == [
        "b'axbc'",
        "99 b'axb'",
    ], out


def test_bytearray_insert_index_clamping_no_libpython(tmp_path):
    # negative index adds len then clamps to 0; too-large clamps to len.
    out = _run(tmp_path,
        "def main():\n"
        "    b = bytearray(b'axb')\n"
        "    b.insert(0, 90)\n"                   # b'Zaxb'
        "    b.insert(100, 88)\n"                 # b'ZaxbX'
        "    b.insert(-1, 89)\n"                  # b'ZaxbYX'
        "    print(bytes(b))\n"
        "main()\n")
    assert out.strip() == "b'ZaxbYX'", out


def test_bytearray_pop_index_and_negative_no_libpython(tmp_path):
    out = _run(tmp_path,
        "def main():\n"
        "    b = bytearray(b'axbYX')\n"
        "    print(b.pop(0), bytes(b))\n"         # 97, b'xbYX'
        "    print(b.pop(-2), bytes(b))\n"        # 89, b'xbX'
        "main()\n")
    assert out.split("\n")[:2] == [
        "97 b'xbYX'",
        "89 b'xbX'",
    ], out


def test_bytearray_pop_empty_and_oob_raise_indexerror_no_libpython(tmp_path):
    out = _run(tmp_path,
        "def main():\n"
        "    b = bytearray()\n"
        "    try:\n"
        "        b.pop()\n"
        "    except IndexError:\n"
        "        print('IndexError-empty')\n"
        "    b = bytearray(b'ab')\n"
        "    try:\n"
        "        b.pop(5)\n"
        "    except IndexError:\n"
        "        print('IndexError-oob')\n"
        "    print(bytes(b))\n"                   # still usable
        "main()\n")
    assert out.split("\n")[:3] == [
        "IndexError-empty", "IndexError-oob", "b'ab'",
    ], out


def test_bytearray_insert_out_of_range_byte_raises_valueerror_no_libpython(tmp_path):
    out = _run(tmp_path,
        "def main():\n"
        "    b = bytearray(b'ab')\n"
        "    try:\n"
        "        b.insert(0, 256)\n"
        "    except ValueError:\n"
        "        print('ValueError-insert')\n"
        "    b.insert(0, 65)\n"                   # still usable -> b'Aab'
        "    print(bytes(b))\n"
        "main()\n")
    assert out.split("\n")[:2] == [
        "ValueError-insert", "b'Aab'",
    ], out
