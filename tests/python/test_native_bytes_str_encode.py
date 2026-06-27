"""``bytes(str, encoding)`` / ``bytearray(str, encoding)`` two-arg constructors
under strict no-libpython.

The one-arg / zero-arg ``bytes``/``bytearray`` constructors were already lowered
natively, but the two-arg ``(str, encoding-literal)`` form returned ``None`` from
``_emit_bytes_family_builtin`` and so forced the libpython fallback
(PCC-PY-COMPILE-001 "requires libpython fallback"). Fix (frontend-only): add
two-arg arms that encode the str argument via the existing
``py_str_utf8_encode`` / ``py_str_latin1_encode`` runtime helpers (already
registered), then wrap the resulting bytes with ``py_bytearray_from_obj`` for
the ``bytearray`` form. Only literal ``utf-8`` / ``latin-1`` encodings lower
natively; other encodings still fall through to libpython.

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


def test_bytes_str_utf8_two_arg_ctor(tmp_path):
    out = _run(tmp_path,
        "def main():\n"
        "    print(bytes('hi', 'utf-8'))\n"           # b'hi'
        "    print(bytearray('hi', 'utf-8'))\n"       # bytearray(b'hi')
        "    print(bytes('hi', 'utf-8') == b'hi')\n"  # True
        "    print(len(bytes('hi', 'utf-8')))\n"      # 2
        "main()\n")
    assert out.splitlines()[:4] == [
        "b'hi'", "bytearray(b'hi')", "True", "2",
    ], out


def test_bytes_str_encoding_utf8_vs_latin1(tmp_path):
    # A non-ASCII codepoint (U+00E9) encodes to two bytes in utf-8 but one byte
    # in latin-1 — proves the encoding argument is actually consulted, not
    # ignored.
    out = _run(tmp_path,
        "def main():\n"
        "    s = chr(233)\n"
        "    print(bytes(s, 'utf-8'))\n"        # b'\\xc3\\xa9'
        "    print(bytes(s, 'latin-1'))\n"      # b'\\xe9'
        "    print(bytearray(s, 'latin1'))\n"   # bytearray(b'\\xe9')
        "    print(len(bytes(s, 'utf-8')))\n"   # 2
        "    print(len(bytes(s, 'latin-1')))\n" # 1
        "main()\n")
    assert out.splitlines()[:5] == [
        r"b'\xc3\xa9'", r"b'\xe9'", r"bytearray(b'\xe9')", "2", "1",
    ], out


def test_bytes_str_two_arg_from_local_variable(tmp_path):
    # The str argument is a plain local (not a literal) — exercises the
    # StrType arg path through _emit_expr_as_pcc_object.
    out = _run(tmp_path,
        "def main():\n"
        "    text = 'abc'\n"
        "    enc = bytes(text, 'utf-8')\n"
        "    ba = bytearray(text, 'utf-8')\n"
        "    print(enc)\n"                      # b'abc'
        "    print(ba)\n"                       # bytearray(b'abc')
        "    print(enc == b'abc')\n"            # True
        "main()\n")
    assert out.splitlines()[:3] == [
        "b'abc'", "bytearray(b'abc')", "True",
    ], out
