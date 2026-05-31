"""repr/str/print of bytes escapes non-ASCII + control bytes as \\xNN under
strict no-libpython.

The bytes formatters only escaped c < 32 and c == 127, leaving high bytes
(>=128) raw -> repr(b'\\xcf\\x80') printed b'<raw UTF-8>' instead of b'\\xcf\\x80'.
CPython shows only printable ASCII (32..126) raw; everything else as \\xNN.
Fix: c == 127 -> c >= 127 in all three bytes formatters: py_format_bytes (C
py_print_fmt.c + pcc-Python port py_print_fmt.py, used by print) and
_format_bytes_str (port py_obj_stubs.py, used by repr/str).

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


def test_bytes_repr_escapes_high_bytes(tmp_path):
    out = _run(tmp_path,
        "def main():\n"
        "    print('π'.encode('utf-8'))\n"        # b'\xcf\x80'  (print path)
        "    print(repr(b'caf\\xc3\\xa9'))\n"      # b'caf\xc3\xa9' (repr path)
        "    print(str(b'\\xcf\\x80'))\n"          # b'\xcf\x80'  (str path)
        "    print(b'\\x7f\\x80\\xff')\n"          # b'\x7f\x80\xff'
        "    print(b'hello')\n"                    # b'hello' (printable raw)
        "    print(b'a\\tb\\nc')\n"                # b'a\tb\nc'
        "    print('ASCII'.encode())\n"           # b'ASCII'
        "main()\n")
    assert out.split("\n")[:7] == [
        r"b'\xcf\x80'",
        r"b'caf\xc3\xa9'",
        r"b'\xcf\x80'",
        r"b'\x7f\x80\xff'",
        "b'hello'",
        r"b'a\tb\nc'",
        "b'ASCII'",
    ], out
