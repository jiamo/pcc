"""Alt-form `#` hex/oct/bin format spec with zero-padding (e.g. f"{42:#06x}").

`f"{42:#06x}"` raised `ValueError: unsupported format specifier`: the runtime
int format parser (py_format.c `format_int_builtin`) bailed on `alt && zero_pad`
("pad-after-prefix: fall back"). `#x` without zero-pad already worked.

Fix: when alt + zero_pad + right-align, zero-pad AFTER the 0x/0o/0b prefix (and
any sign), CPython-style: `0x002a` not `000x2a`.

(`e`/`E`/`g`/`G` exponent specs for floats already worked via
`format_float_builtin`; the ` ` space-sign option is a separate minor gap.)

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


def test_format_spec_altform_zeropad_no_libpython(tmp_path):
    out = _run_pcc_program(
        tmp_path,
        "def main():\n"
        "    print(f'{42:#06x}', f'{42:#010x}')\n"   # 0x002a 0x0000002a
        "    print(f'{255:#06X}', f'{42:#X}')\n"     # 0X00FF 0X2A
        "    print(f'{-42:#06x}')\n"                 # -0x02a
        "    print(f'{42:#x}', f'{42:#o}', f'{42:#b}')\n"  # 0x2a 0o52 0b101010 (no-pad regression)
        "    print(f'{42:06x}', f'{42:x}')\n"        # 00002a 2a (plain regression)
        "    print(f'{3.14159:.3e}', f'{42:+d}')\n"  # 3.142e+00 +42 (exp/sign regression)
        "main()\n",
    )
    assert out.split("\n")[:6] == [
        "0x002a 0x0000002a",
        "0X00FF 0X2A",
        "-0x02a",
        "0x2a 0o52 0b101010",
        "00002a 2a",
        "3.142e+00 +42",
    ], out
