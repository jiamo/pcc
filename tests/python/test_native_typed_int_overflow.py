"""typed-int unboxed arithmetic must overflow to bignum, like CPython.

KNOWN BUG (xfail) — see docs/investigations/typed-int-unboxed-overflow-silent-
wraparound.md. Explicit ``int``-typed function params force the unboxed-i64
fast path (`_emit_binop_int` emits raw `builder.add/sub/mul`), so arithmetic
that overflows i64 SILENTLY WRAPS instead of producing CPython's arbitrary-
precision result. e.g. ``def mul(a:int,b:int): return a*b; mul(2**40, 2**40)``
gives 0 (2^80 mod 2^64) instead of 1208925819614629174706176. This violates
obligation 2 (the fast path's assumption fails — the result no longer fits i64 —
and the slow path must preserve Python semantics, i.e. promote to bignum).

The fix is design-sensitive (unboxed-i64 cannot represent the bignum overflow
result; the typed-int result representation must become tagged-int/boxed), so it
is tracked as a focused-session P0, not a quick patch. This test is xfail until
that lands; when it does, the xfail flips to xpass -> remove the marker.

Marked strict=False so it neither blocks CI nor silently rots: it documents the
known-wrong behavior and will surface (xpass) the moment the fix lands.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest


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


@pytest.mark.xfail(
    reason="typed-int unboxed i64 arithmetic wraps on overflow; see "
    "docs/investigations/typed-int-unboxed-overflow-silent-wraparound.md",
    strict=False,
)
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


# --- Additional overflow-surface cases (Phase 0: define the fix's acceptance
# criteria). Probed 2026-05-31: ``-`` and ``a*b > literal`` ALREADY box
# correctly; the cases below (``*`` chained into ``+``, the overflow value
# carried through a return ABI / a local slot, and ``<<``) still wrap, so they
# capture the part of the bug the conservative fix must close. xfail until the
# fix lands (then they flip to xpass -> remove the markers). ---


@pytest.mark.xfail(
    reason="typed-int * overflow must keep participating in a chained + (a*b "
    "wraps to i64 then + adds to the wrong value); see "
    "docs/investigations/typed-int-unboxed-overflow-silent-wraparound.md",
    strict=False,
)
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


@pytest.mark.xfail(
    reason="typed-int overflow result must survive the function return ABI "
    "(currently returned as wrapped i64); see investigation doc",
    strict=False,
)
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


@pytest.mark.xfail(
    reason="typed-int overflow result must survive storage in a local slot "
    "(currently stored as wrapped i64); see investigation doc",
    strict=False,
)
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


@pytest.mark.xfail(
    reason="typed-int left shift must promote to bignum (raw i64 shl masks the "
    "count and wraps); see investigation doc",
    strict=False,
)
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
