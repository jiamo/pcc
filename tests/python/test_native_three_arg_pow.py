"""pow(b, e, mod) — 3-arg modular exponentiation under strict no-libpython.

3-arg pow raised "NameError: name 'pow' is not defined" (call_expression_lowering
handled only 2-arg pow). Fix: a runtime square-and-multiply helper
(py_int_pow_mod in py_int_modexp.c, an OBJ_PY_CC_HELPERS C file linked in the
no-libpython archive) that reduces modulo `mod` at every step — never
materialising b**e, so it is usable for crypto-size exponents (a boxed
(b**e)%mod would OOM). The frontend routes the int 3-arg pow to it.

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


def test_three_arg_pow_modexp(tmp_path):
    out = _run(tmp_path,
        "def main():\n"
        "    print(pow(2, 10, 1000))\n"        # 24
        "    print(pow(5, 3, 13))\n"           # 8
        "    print(pow(7, 0, 5))\n"            # 1
        "    print(pow(10, 1, 7))\n"           # 3
        "    print(pow(2, 100, 1000000007))\n" # 976371285 (large exp, no blow-up)
        "    print(pow(3, 1000, 97))\n"        # 3**1000 mod 97
        "    print(pow(2, 10))\n"              # 1024 (2-arg regression)
        "main()\n")
    assert out.split("\n")[:7] == [
        "24", "8", "1", "3",
        str(pow(2, 100, 1000000007)),
        str(pow(3, 1000, 97)),
        "1024",
    ], out
