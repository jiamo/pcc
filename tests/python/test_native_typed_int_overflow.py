"""typed-int annotations must keep Python bignum overflow semantics.

Default ``int`` annotations use the boxed/tagged Python-int ABI. The raw i64
function ABI is a mode-labeled diagnostic escape, not the default meaning of
``int``.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path


def _run_pcc_program(tmp_path: Path, source: str) -> str:
    src = tmp_path / "prog.py"
    src.write_text(source, encoding="utf-8")
    exe = tmp_path / "prog_bin"
    env = os.environ.copy()
    env.pop("LC_ALL", None)
    build = subprocess.run(
        [
            "uv", "run", "pcc", "--backend", "self", "--python-libpython=off",
            "--ir-scaffold=on", str(src), "-o", str(exe),
        ],
        text=True, capture_output=True, timeout=420, env=env,
    )
    assert build.returncode == 0, build.stderr
    run = subprocess.run(
        [str(exe)], text=True, capture_output=True, timeout=30, env=env
    )
    assert run.returncode == 0, run.stderr
    return run.stdout


def test_typed_int_param_overflow_promotes_to_bignum(tmp_path):
    out = _run_pcc_program(
        tmp_path,
        "def mul(a: int, b: int) -> int:\n"
        "    return a * b\n"
        "def addf(a: int, b: int) -> int:\n"
        "    return a + b\n"
        "def main():\n"
        "    print(mul(1099511627776, 1099511627776))\n"       # 2^40*2^40 = 2^80
        "    print(addf(9223372036854775807, 5))\n"            # 2^63-1 + 5
        "    print(mul(3037000500, 3037000500))\n"
        "main()\n",
    )
    assert out.split("\n")[:3] == [
        "1208925819614629174706176",
        "9223372036854775812",
        "9223372037000250000",
    ], out


# --- Additional overflow-surface cases. Probed 2026-05-31: ``-`` and
# ``a*b > literal`` already used the boxed path. The cases below capture the
# former raw-i64 failure surface for explicit ``int`` annotations. ---


def test_typed_int_chained_overflow_propagates(tmp_path):
    out = _run_pcc_program(
        tmp_path,
        "def f(a: int, b: int, c: int) -> int:\n"
        "    return a * b + c\n"
        "def main():\n"
        "    print(f(1099511627776, 1099511627776, 7))\n"   # 2^80 + 7
        "main()\n",
    )
    assert out.split("\n")[0] == "1208925819614629174706183", out


def test_typed_int_overflow_through_return_abi(tmp_path):
    out = _run_pcc_program(
        tmp_path,
        "def mul(a: int, b: int) -> int:\n"
        "    return a * b\n"
        "def main():\n"
        "    x = mul(1099511627776, 1099511627776)\n"
        "    print(x + 1)\n"                                  # 2^80 + 1
        "main()\n",
    )
    assert out.split("\n")[0] == "1208925819614629174706177", out


def test_typed_int_overflow_through_local_slot(tmp_path):
    out = _run_pcc_program(
        tmp_path,
        "def g(a: int, b: int) -> int:\n"
        "    x = a * b\n"
        "    return x + 1\n"
        "def main():\n"
        "    print(g(1099511627776, 1099511627776))\n"        # 2^80 + 1
        "main()\n",
    )
    assert out.split("\n")[0] == "1208925819614629174706177", out


def test_typed_int_left_shift_promotes_to_bignum(tmp_path):
    out = _run_pcc_program(
        tmp_path,
        "def sh(a: int) -> int:\n"
        "    return a << 100\n"
        "def main():\n"
        "    print(sh(1))\n"                                  # 2^100
        "main()\n",
    )
    assert out.split("\n")[0] == "1267650600228229401496703205376", out
