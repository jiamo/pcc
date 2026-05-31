"""dict[missing] -> KeyError and list[oob] -> IndexError under no-libpython.

The statically-typed subscript path (subscript_lowering.py DictType/ListType
branches) called py_dict_get / py_list_get, which return NULL silently on a
missing key / out-of-range index. So `d['missing']` / `a[9]` produced "<null>"
and a surrounding try/except could not catch anything (no exception was
raised).

Fix: new raising subscript variants py_dict_getitem (KeyError carrying the key,
via py_exc_new_with_value) and py_list_getitem (IndexError "list index out of
range"), mirrored in C (py_dict.c / py_list.c) and the pcc-Python ports
(py_dict.py / py_list.py). subscript_lowering routes d[k]/a[i] to them and emits
the post-call err check so try/except catches the raise. py_dict_get /
py_list_get stay non-raising for dict.get()/pop()/setdefault() and other
internal callers.

Compiles + runs under ``--backend self --python-libpython=off`` in DEFAULT
runtime mode (pcc-Python ports — the goal mode).
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


def test_dict_keyerror_list_indexerror_catch_no_libpython(tmp_path):
    out = _run_pcc_program(
        tmp_path,
        "def main():\n"
        "    d = {'a': 1, 'b': 2}\n"
        "    a = [10, 20, 30]\n"
        "    print(d['a'], a[1], a[-1])\n"        # present/valid/negative -> 1 20 30
        "    try:\n"
        "        print(d['z'])\n"
        "    except KeyError:\n"
        "        print('caught-KeyError')\n"
        "    try:\n"
        "        print(a[9])\n"
        "    except IndexError:\n"
        "        print('caught-IndexError')\n"
        "    # no-regression: dict.get/pop/setdefault keep using non-raising py_dict_get\n"
        "    print(d.get('z', 99), d.get('a'))\n"
        "    print(d.pop('b'), d.setdefault('c', 3))\n"
        "main()\n",
    )
    assert out.split("\n")[:5] == [
        "1 20 30",
        "caught-KeyError",
        "caught-IndexError",
        "99 1",
        "2 3",
    ], out
