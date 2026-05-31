"""Space ` ` sign option in format() for ints (e.g. format(42, " d") -> " 42").

`format_int_builtin` (py_format.c) parsed only the `+` sign option, not the
space ` ` option, so `format(42, " d")` raised "unsupported format specifier".
The float formatter (`format_float_builtin`) already handled space-sign. Fix:
parse a leading ` ` as the space-sign option (mirroring `+`) and prepend a space
for non-negative values.

Scope note: this covers the runtime `format(x, " d")` path (and the float
`format(x, " .2f")` which already worked). The f-string form `f"{x: d}"`
separately DROPS the leading space at the compile-time spec-extraction stage
(shared by int and float) — a distinct f-string-lowering issue documented in
docs/investigations/fstring-format-spec-gaps-altform-exponent-spacesign.md, not
fixed here.

Runs under ``--backend self --python-libpython=off`` in DEFAULT runtime mode
(py_format.c is C in both modes).
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


def test_format_space_sign_no_libpython(tmp_path):
    out = _run_pcc_program(
        tmp_path,
        "def main():\n"
        "    print('[' + format(42, ' d') + ']')\n"      # [ 42]
        "    print('[' + format(-42, ' d') + ']')\n"     # [-42] (negative: no extra space)
        "    print('[' + format(7, ' 5d') + ']')\n"      # [   42->   7] width+space
        "    print('[' + format(3.14, ' .2f') + ']')\n"  # [ 3.14] (float already worked)
        "    print('[' + format(42, '+d') + ']')\n"      # [+42] sign_plus regression
        "    print('[' + format(42, 'd') + ']')\n"       # [42] no-sign regression
        "main()\n",
    )
    assert out.split("\n")[:6] == [
        "[ 42]",
        "[-42]",
        "[    7]",
        "[ 3.14]",
        "[+42]",
        "[42]",
    ], out
