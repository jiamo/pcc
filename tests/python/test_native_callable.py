"""Native callable() builtin under strict no-libpython (run-based).

``callable`` was listed in the frontend's builtin name-allowlist but had no
direct-call lowering, so ``callable(x)`` hit the CPython fallback and was
rejected under ``--python-libpython=off``. It is now lowered to the runtime
helper ``py_builtin_callable`` (py_dunder.c + py_dunder.py port), which mirrors
``py_obj_call``'s dispatch classification: functions, classes and weakrefs are
callable, an instance is callable iff its class defines ``__call__``, and every
other object (tagged int, str, list, tuple, None, float) is not.

Compiles + runs under ``--backend self --python-libpython=off`` and asserts the
exact CPython-matching output.
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
    # DEFAULT mode (pcc_py ports) — goal mode; py_builtin_callable mirrored
    # in both py_dunder.c and py_dunder.py.
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


def test_callable_native_no_libpython(tmp_path):
    out = _run_pcc_program(
        tmp_path,
        "def f(x):\n"
        "    return x\n"
        "\n"
        "def g():\n"
        "    return 2\n"
        "\n"
        "class Callable:\n"
        "    def __call__(self):\n"
        "        return 1\n"
        "\n"
        "class Plain:\n"
        "    pass\n"
        "\n"
        "print(callable(f), callable(g))\n"
        "print(callable(Callable), callable(Plain))\n"
        "print(callable(Callable()), callable(Plain()))\n"
        "print(callable(3), callable('abc'))\n"
        "print(callable([1, 2, 3]), callable((1, 2)))\n"
        "print(callable(None), callable(3.5))\n"
        "if callable(f):\n"
        "    print('f-callable')\n"
        "if not callable(42):\n"
        "    print('int-not-callable')\n"
        "inst = Callable()\n"
        "if callable(inst):\n"
        "    print('inst-callable')\n",
    )
    assert out.split("\n")[:9] == [
        "True True",
        "True True",
        "True False",
        "False False",
        "False False",
        "False False",
        "f-callable",
        "int-not-callable",
        "inst-callable",
    ], out
