"""f-string format spec is taken literally (not stripped) under no-libpython.

The f-string field parser in py_parse.py stripped the format spec
(`text[i+1:].strip()`), discarding a leading space — so `f"{42: d}"` lost the
space-sign option and rendered "42" instead of " 42". (`f"{42: 5d}"` accidentally
worked because the width padding masked the missing space.) CPython takes the
format spec literally after the `:`.

Fix: do not `.strip()` the format spec in either f-string field parser
(`_split_fstring_expr_parts` and the inline `_FStringFormat` builder); only the
expression part is stripped. Completes the space-sign option for f-strings
(the runtime `format_int_builtin` half is fix #31; `format_float_builtin`
already had it).

Runs under ``--backend self --python-libpython=off`` in DEFAULT runtime mode.
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
            "uv",
            "run",
            "pcc",
            "--backend",
            "self",
            "--python-libpython=off",
            "--ir-scaffold=on",
            str(src),
            "-o",
            str(exe),
        ],
        text=True,
        capture_output=True,
        timeout=420,
        env=env,
    )
    assert build.returncode == 0, build.stderr
    run = subprocess.run(
        [str(exe)], text=True, capture_output=True, timeout=30, env=env
    )
    assert run.returncode == 0, run.stderr
    return run.stdout


def test_fstring_spec_literal_space_sign_no_libpython(tmp_path):
    out = _run_pcc_program(
        tmp_path,
        "def main():\n"
        "    print('[' + f'{42: d}' + ']')\n"  # [ 42] space-sign
        "    print('[' + f'{7: d}' + ']')\n"  # [ 7]
        "    print('[' + f'{-42: d}' + ']')\n"  # [-42]
        "    print('[' + f'{3.14: .2f}' + ']')\n"  # [ 3.14]
        "    print('[' + f'{42:>5}' + ']')\n"  # [   42] normal spec regression
        "    print('[' + f'{3.14:.2f}' + ']')\n"  # [3.14]
        "    print('[' + f'{42:#06x}' + ']')\n"  # [0x002a]
        "    print('[' + f'{42:+d}' + ']')\n"  # [+42]
        "    print('[' + f'{42:^7}' + ']')\n"  # [  42   ]
        "main()\n",
    )
    assert out.split("\n")[:9] == [
        "[ 42]",
        "[ 7]",
        "[-42]",
        "[ 3.14]",
        "[   42]",
        "[3.14]",
        "[0x002a]",
        "[+42]",
        "[  42   ]",
    ], out


def test_fstring_debug_eq_uses_repr_no_libpython(tmp_path):
    """``f"{x=}"`` self-documenting form defaults the value to repr (CPython:
    ``f"{x=}"`` is ``f"x={x!r}"``), while an explicit conversion or format spec
    overrides that default. py_parse.py builds the ``x=`` literal prefix but
    left ``conversion=None`` (-> str); the fix defaults it to ``"r"`` only when
    there is no explicit conversion and no format spec."""
    out = _run_pcc_program(
        tmp_path,
        "def main():\n"
        "    x = 'bob'\n"
        "    n = 42\n"
        "    v = 3.14159\n"
        "    xs = [1, 2]\n"
        "    print(f'{x=}')\n"  # x='bob'   (repr)
        "    print(f'{n=}')\n"  # n=42
        "    print(f'{n + 1=}')\n"  # n + 1=43  (expression text preserved)
        "    print(f'{v=:.2f}')\n"  # v=3.14    (spec wins, not repr)
        "    print(f'{x=!s}')\n"  # x=bob     (explicit !s wins)
        "    print(f'{xs=}')\n"  # xs=[1, 2]
        "main()\n",
    )
    assert out.split("\n")[:6] == [
        "x='bob'",
        "n=42",
        "n + 1=43",
        "v=3.14",
        "x=bob",
        "xs=[1, 2]",
    ], out


def test_bare_typed_string_fstring_stays_native(tmp_path):
    """An exact typed str with an empty spec needs no generic formatter."""
    out = _run_pcc_program(
        tmp_path,
        "def symbol(module: str, name: str) -> str:\n"
        "    clean = module.replace('.', '_').replace('-', '_')\n"
        "    return f'user_{clean}_{name}'\n"
        "\n"
        "print(symbol('pkg.mod-name', 'entry'))\n",
    )
    assert out == "user_pkg_mod_name_entry\n"
