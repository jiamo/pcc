"""Division / modulo by zero must raise a catchable ZeroDivisionError under
strict no-libpython, instead of silently yielding 0 / inf / NULL.

The integer ``//`` / ``%`` lowerings emitted a bare ``sdiv`` / ``srem`` (ARM64
SDIV-by-zero yields 0), the float ``//`` / ``%`` emitted ``fdiv`` / ``fmod``
(→ inf / nan), and the boxed runtime helpers (``py_int_floordiv`` /
``py_int_mod`` / ``py_obj_mod``) returned NULL *without* raising — so a zero
divisor produced a wrong value (or an uncatchable late crash) rather than
``ZeroDivisionError``. This violates Python semantics (a defensive
``try/except ZeroDivisionError`` silently failed).

Fix guards every division lowering path: unboxed i64 (_python_floordiv/mod_i64),
boxed runtime binop (_emit_runtime_int_binop_value), exact-int
(exact_int_lowering), dyn (py_obj_mod null-check / py_obj_truediv raises),
float (_emit_binop_float + static `/`), and the low_ir pure-leaf scaffold
(variable-divisor `//`/`%`/`/` bails to the guarded full path). Message matches
Python 3.14's unified "division by zero".

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


def test_zero_division_inline_dyn(tmp_path):
    # dyn operands (tuple-unpack), inline in the same function's try
    out = _run(tmp_path,
        "def main():\n"
        "    for a, b in [(10, 0), (10, 3), (7, 0)]:\n"
        "        try:\n"
        "            print('fd', a // b)\n"
        "        except ZeroDivisionError as e:\n"
        "            print('fd ZDE', str(e))\n"
        "        try:\n"
        "            print('md', a % b)\n"
        "        except ZeroDivisionError:\n"
        "            print('md ZDE')\n"
        "        try:\n"
        "            print('td', a / b)\n"
        "        except ZeroDivisionError:\n"
        "            print('td ZDE')\n"
        "main()\n")
    assert out.split("\n")[:9] == [
        "fd ZDE division by zero", "md ZDE", "td ZDE",
        "fd 3", "md 1", "td 3.3333333333333335",
        "fd ZDE division by zero", "md ZDE", "td ZDE",
    ], out


def test_zero_division_typed_and_cross_function(tmp_path):
    # typed-int helper (bigint-capable boxed path), caught in CALLER (cross-fn)
    # + a bigint divisor, + float //, + same-function try
    out = _run(tmp_path,
        "def fdiv(a: int, b: int) -> int:\n"
        "    return a // b\n"
        "def safe(a: int, b: int) -> int:\n"
        "    try:\n"
        "        return a % b\n"
        "    except ZeroDivisionError:\n"
        "        return -1\n"
        "def main():\n"
        "    try:\n"
        "        print(fdiv(10, 0))\n"
        "    except ZeroDivisionError:\n"
        "        print('cross ZDE')\n"
        "    print(fdiv(10, 3))\n"
        "    print(safe(10, 0))\n"
        "    print(safe(10, 3))\n"
        "    try:\n"
        "        print((2 ** 70) // 0)\n"
        "    except ZeroDivisionError:\n"
        "        print('bigint ZDE')\n"
        "    try:\n"
        "        print(5.0 // 0.0)\n"
        "    except ZeroDivisionError:\n"
        "        print('floorf ZDE')\n"
        "main()\n")
    assert out.split("\n")[:6] == [
        "cross ZDE", "3", "-1", "1", "bigint ZDE", "floorf ZDE",
    ], out


def test_nonzero_constant_divisor_still_works(tmp_path):
    # nonzero-literal divisor stays on the fast path and must remain correct
    out = _run(tmp_path,
        "def f(x: int) -> int:\n"
        "    return x // 2 + x % 3\n"
        "def g(x: float) -> float:\n"
        "    return x / 4.0\n"
        "def main():\n"
        "    print(f(17))\n"          # 8 + 2 = 10
        "    print(g(10.0))\n"        # 2.5
        "    print(100 // 7, 100 % 7)\n"  # 14 2
        "main()\n")
    assert out.split("\n")[:3] == ["10", "2.5", "14 2"], out
