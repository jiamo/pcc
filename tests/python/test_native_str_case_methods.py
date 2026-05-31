"""Native str.title()/swapcase()/casefold() under strict no-libpython (run-based).

These ASCII case-transform methods had no native lowering, so they fell back to
libpython (rejected under --python-libpython=off). Now lowered like
upper/lower/capitalize: runtime py_str_title/swapcase/casefold (py_str_accessors.c)
+ dispatch in string_method_lowering.py.

Compiles + runs under --backend self --python-libpython=off and asserts exact
output. (ASCII-only, the same scope as the existing py_str_upper/lower/capitalize.)
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
    # DEFAULT runtime mode (pcc_py / ports) — the no-libpython goal mode — NOT
    # PCC_RUNTIME_CC=cc (which would only exercise the C oracle path). py_str_
    # swapcase/title/casefold are mirrored in both py_str_accessors.c and the
    # pcc-Python port py_str_accessors.py, so this works in the default mode.
    build = subprocess.run(
        [
            "uv", "run", "pcc", "--backend", "self", "--python-libpython=off",
            "--ir-scaffold=on", str(src), "-o", str(exe),
        ],
        text=True, capture_output=True, timeout=300, env=env,
    )
    assert build.returncode == 0, build.stderr
    run = subprocess.run(
        [str(exe)], text=True, capture_output=True, timeout=30, env=env
    )
    assert run.returncode == 0, run.stderr
    return run.stdout


def test_str_case_methods_native_no_libpython(tmp_path):
    out = _run_pcc_program(
        tmp_path,
        "print('hello world'.title())\n"
        "print('a=b c'.title())\n"
        "print(\"they're here\".title())\n"
        "print('abc123def'.title())\n"
        "print('Hello World'.swapcase())\n"
        "print('MixedCase'.swapcase())\n"
        "print('HELLO'.casefold())\n"
        "print('hello'.casefold())\n",
    )
    assert out.split("\n")[:8] == [
        "Hello World",
        "A=B C",
        "They'Re Here",
        "Abc123Def",
        "hELLO wORLD",
        "mIXEDcASE",
        "hello",
        "hello",
    ], out
