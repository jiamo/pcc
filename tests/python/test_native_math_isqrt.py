"""``math.isqrt(n)`` — bignum-correct integer square root under strict no-libpython.

``math.isqrt`` was a libpython fallback: ``_emit_native_math_value_call`` handled
only {prod,gcd,factorial,floor,ceil,sqrt,pow,trunc}, so ``math.isqrt(n)`` fell
through to the CPython fallback (rejected under ``--python-libpython=off``).

Fix: a C-only runtime helper (``py_int_isqrt`` in ``py_int_modexp.c``, an
OBJ_PY_CC_HELPERS file with no pcc-Python port — the same pattern as
``py_int_pow_mod``) that runs an integer Newton iteration on boxed PyObject ints
via the ``py_int_*`` helpers. It never converts to float, so it is exact for
arbitrary-precision operands (``isqrt((10**30)+1)`` would be wrong through a
double). The frontend routes both ``math.isqrt(n)`` and ``from math import
isqrt`` to it, and checks ``py_err_occurred()`` after the call so the
ValueError for a negative argument reaches the handler.

Runs under ``--backend self --python-libpython=off`` in DEFAULT runtime mode
(the C-only helper is linked into the no-libpython archive regardless of tier).
"""
from __future__ import annotations

import math
import os
import subprocess
from pathlib import Path


def _run(tmp_path, source):
    src = tmp_path / "q.py"
    src.write_text(source, encoding="utf-8")
    exe = tmp_path / "q_bin"
    env = os.environ.copy()
    env.pop("LC_ALL", None)
    b = subprocess.run(
        [
            "uv", "run", "pcc",
            "--backend", "self",
            "--python-libpython=off",
            "--ir-scaffold=on",
            str(src), "-o", str(exe),
        ],
        text=True, capture_output=True, timeout=420, env=env,
    )
    assert b.returncode == 0, b.stderr
    r = subprocess.run([str(exe)], text=True, capture_output=True, timeout=30, env=env)
    assert r.returncode == 0, r.stderr
    return r.stdout


def test_isqrt_small_and_boundaries(tmp_path):
    out = _run(tmp_path,
        "import math\n"
        "def main():\n"
        "    print(math.isqrt(0))\n"    # 0
        "    print(math.isqrt(1))\n"    # 1
        "    print(math.isqrt(2))\n"    # 1
        "    print(math.isqrt(3))\n"    # 1
        "    print(math.isqrt(4))\n"    # 2
        "    print(math.isqrt(8))\n"    # 2
        "    print(math.isqrt(9))\n"    # 3
        "    print(math.isqrt(15))\n"   # 3
        "    print(math.isqrt(16))\n"   # 4
        "    print(math.isqrt(17))\n"   # 4
        "    print(math.isqrt(99))\n"   # 9
        "    print(math.isqrt(100))\n"  # 10
        "main()\n")
    expected = [str(math.isqrt(n)) for n in
                (0, 1, 2, 3, 4, 8, 9, 15, 16, 17, 99, 100)]
    assert out.split("\n")[:len(expected)] == expected, out


def test_isqrt_bignum_exact(tmp_path):
    """Above 2**53 a float sqrt loses precision; the boxed-int Newton iteration
    must stay exact. isqrt((10**30)+1) == isqrt(10**30) == 10**15, and the
    perfect-square / just-below cases pin the floor boundary at bignum scale."""
    out = _run(tmp_path,
        "import math\n"
        "def main():\n"
        "    print(math.isqrt(10 ** 30))\n"        # 10**15
        "    print(math.isqrt((10 ** 30) + 1))\n"  # 10**15 (still floor)
        "    print(math.isqrt((10 ** 30) - 1))\n"  # 10**15 - 1
        "    print(math.isqrt(2 ** 64))\n"         # 2**32
        "    print(math.isqrt((2 ** 64) - 1))\n"   # 2**32 - 1
        "    print(math.isqrt(2 ** 128))\n"        # 2**64
        "    print(math.isqrt((1 << 200) + 12345))\n"
        "main()\n")
    ns = [10 ** 30, (10 ** 30) + 1, (10 ** 30) - 1,
          2 ** 64, (2 ** 64) - 1, 2 ** 128, (1 << 200) + 12345]
    expected = [str(math.isqrt(n)) for n in ns]
    assert out.split("\n")[:len(expected)] == expected, out


def test_isqrt_negative_raises_valueerror(tmp_path):
    """CPython raises ``ValueError: isqrt() argument must be nonnegative`` for a
    negative argument. The C helper raises a matching-typed ValueError (via
    py_raise) so a ``try/except ValueError`` catches it and execution recovers;
    the caught message must match CPython exactly."""
    out = _run(tmp_path,
        "import math\n"
        "def safe(n):\n"
        "    try:\n"
        "        return str(math.isqrt(n))\n"
        "    except ValueError as ex:\n"
        "        return 'VE:' + str(ex)\n"
        "def main():\n"
        "    print(safe(25))\n"   # 5 (normal case)
        "    print(safe(-1))\n"   # VE:isqrt() argument must be nonnegative
        "    print(safe(-100))\n" # same VE message
        "    print(safe(26))\n"   # 5 (recovery after the error path)
        "main()\n")
    assert out.split("\n")[:4] == [
        "5",
        "VE:isqrt() argument must be nonnegative",
        "VE:isqrt() argument must be nonnegative",
        "5",
    ], out


def test_isqrt_from_import_alias(tmp_path):
    """``from math import isqrt`` must route through the same native helper
    (the import-alias path in native_modules.py), not fall back to libpython."""
    out = _run(tmp_path,
        "from math import isqrt\n"
        "def main():\n"
        "    print(isqrt(144))\n"          # 12
        "    print(isqrt(145))\n"          # 12
        "    print(isqrt(10 ** 30))\n"     # 10**15
        "main()\n")
    expected = [str(math.isqrt(n)) for n in (144, 145, 10 ** 30)]
    assert out.split("\n")[:len(expected)] == expected, out
