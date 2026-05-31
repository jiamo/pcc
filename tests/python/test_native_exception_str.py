"""str(exc) / print(exc) for builtin exceptions under strict no-libpython.

Before this fix, ``print(exc)`` under ``--python-libpython=off`` rendered the
opaque ``<object tag=12>`` (PY_TYPE_EXC) instead of the exception message: the
print formatter ``_format`` (py_print_fmt.py port / py_print_fmt.c) had no
PY_TYPE_EXC case and fell through to the unknown-tag fallback. This is a silent
WRONG-OUTPUT no-libpython correctness gap (the program compiled and ran), not a
fallback — reproduced outside any single idiom (``raise ValueError(...)``,
``except E as e: print(e)``, ``str(e)``, ``print('x:', e)``).

Fix: ``_format`` now has a PY_TYPE_EXC case that writes ``str`` of the
exception's message via py_exc_get_message (borrowed ref; an arg-less exception
renders as the empty string). ``str(exc)`` already routed through py_obj_str's
existing EXC case; this aligns ``print(exc)`` with it.

Scope note: KeyError's CPython str repr-quotes the key (``str(KeyError('m'))``
== ``"'m'"``); pcc stores a single message and renders it plainly. That
repr-quoting is a separate, pre-existing gap shared with the unhandled-exception
traceback printer (``KeyError: m`` vs ``KeyError: 'm'``), so this test
deliberately covers the string-message exceptions (ValueError / RuntimeError /
TypeError), not KeyError.

Runs under ``--backend self --python-libpython=off`` in DEFAULT runtime mode
(pcc-Python ports — the goal mode; py_print_fmt is a PY_MODULES port).
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


def test_print_and_str_of_builtin_exception_no_libpython(tmp_path):
    out = _run_pcc_program(
        tmp_path,
        "def main():\n"
        "    try:\n"
        "        raise ValueError('bad thing')\n"
        "    except ValueError as e:\n"
        "        print(e)\n"                       # bad thing
        "        print('caught:', e)\n"            # caught: bad thing
        "        print(str(e))\n"                  # bad thing
        "    try:\n"
        "        raise RuntimeError('boom 42')\n"
        "    except RuntimeError as e:\n"
        "        print(e)\n"                       # boom 42
        "    try:\n"
        "        raise TypeError('need int')\n"
        "    except Exception as e:\n"
        "        print('E:', e)\n"                 # E: need int
        "    try:\n"
        "        raise ValueError()\n"             # arg-less -> empty str
        "    except ValueError as e:\n"
        "        print('[' + str(e) + ']')\n"      # []
        "main()\n",
    )
    assert out.split("\n")[:6] == [
        "bad thing",
        "caught: bad thing",
        "bad thing",
        "boom 42",
        "E: need int",
        "[]",
    ], out
