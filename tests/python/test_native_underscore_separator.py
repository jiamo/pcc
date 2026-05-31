"""'_' grouping separator in format specs under strict no-libpython.

f"{1000000:_}" / "{:_}".format(x) / format(x, "_") used to raise ValueError:
py_format.c only recognised ',' as a grouping option and py_int_format_decimal
hard-coded the ',' byte. Fix: the ``comma`` int param now carries the
separator *byte* (0 = none, 44 = ',', 95 = '_'); py_format.c (int+float specs,
add_float_commas) and the frontend fast-path (format_lowering.py) emit the
byte instead of a bare 1, and py_int_format_decimal (C + pcc-Python port)
inserts chr(comma). The ',' path is preserved (it now passes 44).

Runs under ``--backend self --python-libpython=off`` in DEFAULT runtime mode.
"""
from __future__ import annotations
import os, subprocess


def _run(tmp_path, source):
    src = tmp_path / "p.py"; src.write_text(source, encoding="utf-8")
    exe = tmp_path / "p_bin"; env = os.environ.copy(); env.pop("LC_ALL", None)
    b = subprocess.run(["uv","run","pcc","--backend","self","--python-libpython=off","--ir-scaffold=on",str(src),"-o",str(exe)], text=True, capture_output=True, timeout=420, env=env)
    assert b.returncode == 0, b.stderr
    r = subprocess.run([str(exe)], text=True, capture_output=True, timeout=30, env=env)
    assert r.returncode == 0, r.stderr
    return r.stdout


def test_underscore_separator(tmp_path):
    out = _run(tmp_path,
        "def main():\n"
        "    print(f\"{1000000:_}\")\n"           # 1_000_000
        "    print(f\"{1234567:_}\")\n"           # 1_234_567
        "    print(f\"{-1234567:_}\")\n"          # -1_234_567
        "    print(f\"{1234567:_d}\")\n"          # 1_234_567 (frontend _d fast-path)
        "    print(\"{:_}\".format(1000000))\n"   # 1_000_000
        "    print(format(1234567, \"_\"))\n"     # 1_234_567
        "    print(f\"{1234567.5:_}\")\n"         # 1_234_567.5 (float)
        "    print(f\"{12:_}\")\n"                # 12 (no sep, <4 digits)
        "main()\n")
    assert out.split("\n")[:8] == [
        "1_000_000", "1_234_567", "-1_234_567", "1_234_567",
        "1_000_000", "1_234_567", "1_234_567.5", "12",
    ], out


def test_comma_separator_still_works(tmp_path):
    # Regression: the ',' grouping path must keep emitting ',' (not chr(1))
    # after the separator-byte refactor.
    out = _run(tmp_path,
        "def main():\n"
        "    print(f\"{1234567:,}\")\n"           # 1,234,567
        "    print(\"{:,}\".format(1000000))\n"   # 1,000,000
        "    print(format(1234567, \",\"))\n"     # 1,234,567
        "    print(f\"{1234567.5:,}\")\n"         # 1,234,567.5 (float)
        "    print(f\"{1234567:,d}\")\n"          # 1,234,567 (frontend ,d fast-path)
        "main()\n")
    assert out.split("\n")[:5] == [
        "1,234,567", "1,000,000", "1,234,567", "1,234,567.5", "1,234,567",
    ], out
