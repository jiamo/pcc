"""Float literals with exponents round-trip precisely, no-libpython.

The parsers (`py_parse.py` / `py_lift.py`) computed a float literal as
``mantissa * 10.0**exp`` via repeated float multiplication, accumulating error
(``1e100`` parsed to ``1.0000000000000006e+100``; ``6.022e23`` to
``6.0219999999999996e+23``). Fix scales the integer mantissa by an EXACT
``10**net`` and rounds once via ``float(bignum)`` (correctly rounded), for the
common magnitude range (|exponent| <= 308). Extreme tails (overflow->inf,
subnormal underflow like 5e-324) keep the graceful imprecise fallback.
"""
from __future__ import annotations

import subprocess
import sys
import textwrap


def test_float_literal_precision_matches_cpython(tmp_path, monkeypatch):
    monkeypatch.setenv("PCC_RUNTIME_CC", "cc")
    from pcc.py_frontend.pipeline import compile_python

    src = tmp_path / "fl.py"
    exe = tmp_path / "fl.out"
    src.write_text(textwrap.dedent("""
        def main() -> None:
            print(1e100)
            print(1e-100)
            print(6.022e23)
            print(1.6e-19)
            print(2.5e10)
            print(1.5e-3)
            print(1E5)
            print(9.999e99)
            print(1.23456789e50)
            print(123456789.123456789)
            print(6.62607015e-34)
            # regressions: plain decimals + small exponents
            print(3.14)
            print(0.1)
            print(3.0)
            print(0.0)
            print(100.0)
            print(2.718281828459045)

        if __name__ == "__main__":
            main()
        """).lstrip(), encoding="utf-8")
    compile_python(
        str(src), str(exe),
        ir_scaffold_mode="on", libpython_mode="off", backend="self",
    )
    cpython = subprocess.run(
        [sys.executable, str(src)], capture_output=True, text=True, timeout=30,
    ).stdout
    result = subprocess.run(
        [str(exe)], capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == cpython
