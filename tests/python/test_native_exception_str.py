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

KeyError is special: CPython's ``KeyError.__str__`` repr-quotes the key
(``str(KeyError('m'))`` == ``"'m'"``), unlike other exceptions which render the
message plainly. Both the ``str(exc)`` path (py_obj_str / py_obj_stubs) and the
``print(exc)`` path (py_print_fmt) now special-case KeyError via
``py_exc_matches(o, py_exc_builtin_class(PY_EXC_KEYERROR))`` -> ``repr(key)``.
(The unhandled-exception traceback printer ``KeyError: m`` and ``repr(exc)``
itself remain separate follow-ups.)

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


def test_keyerror_str_and_print_repr_quotes_key_no_libpython(tmp_path):
    """``KeyError.__str__`` is ``repr(key)`` (CPython): both ``str(e)`` and
    ``print(e)`` quote a string key, while a non-string key uses its repr too,
    and other exceptions stay plain."""
    out = _run_pcc_program(
        tmp_path,
        "def main():\n"
        "    try:\n"
        "        d = {}\n"
        "        _ = d['missing']\n"
        "    except KeyError as e:\n"
        "        print(e)\n"                       # 'missing'
        "        print('caught:', e)\n"            # caught: 'missing'
        "        print(str(e))\n"                  # 'missing'
        "    try:\n"
        "        d2 = {'a': 1}\n"
        "        d2.pop('x')\n"
        "    except KeyError as e:\n"
        "        print(e)\n"                       # 'x'
        "    try:\n"
        "        d3 = {}\n"
        "        _ = d3[7]\n"                       # int key
        "    except KeyError as e:\n"
        "        print(e)\n"                       # 7
        "    try:\n"
        "        raise ValueError('plain')\n"      # non-KeyError stays plain
        "    except ValueError as e:\n"
        "        print(e)\n"                       # plain
        "main()\n",
    )
    assert out.split("\n")[:6] == [
        "'missing'",
        "caught: 'missing'",
        "'missing'",
        "'x'",
        "7",
        "plain",
    ], out


def test_exception_repr_no_libpython(tmp_path):
    """``repr(exc)`` is ``ClassName(repr(arg))`` (``ClassName()`` arg-less) —
    previously returned ``<null>`` for every exception. Covered for the common
    string-message and arg-less cases across ``repr()``, container repr, and
    ``print([exc])``. (pcc stores a single stringified message, so a non-string
    arg like ``KeyError(5)`` renders ``KeyError('5')`` — a documented limit that
    needs the original args tuple to fix.)"""
    out = _run_pcc_program(
        tmp_path,
        "def main():\n"
        "    print(repr(KeyError('missing')))\n"        # KeyError('missing')
        "    print(repr(ValueError('bad value')))\n"    # ValueError('bad value')
        "    print(repr(ValueError()))\n"               # ValueError()
        "    print(repr(RuntimeError('boom')))\n"       # RuntimeError('boom')
        "    print([KeyError('a'), ValueError('b')])\n"  # [KeyError('a'), ValueError('b')]
        "    try:\n"
        "        raise TypeError('need int')\n"
        "    except TypeError as e:\n"
        "        print(repr(e))\n"                       # TypeError('need int')
        "        print([e])\n"                           # [TypeError('need int')]
        "main()\n",
    )
    assert out.split("\n")[:7] == [
        "KeyError('missing')",
        "ValueError('bad value')",
        "ValueError()",
        "RuntimeError('boom')",
        "[KeyError('a'), ValueError('b')]",
        "TypeError('need int')",
        "[TypeError('need int')]",
    ], out
