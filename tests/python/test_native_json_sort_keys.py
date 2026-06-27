"""json.dumps(obj, sort_keys=True) actually sorts dict keys (run-based).

Before this slice, native ``json.dumps`` accepted the ``sort_keys`` kwarg at
codegen time but dropped it: ``py_json_dumps`` took no sort flag and emitted
dict entries in insertion order, so ``json.dumps(d, sort_keys=True)`` produced
output that disagreed with CPython for any dict whose insertion order was not
already sorted.

The fix routes ``sort_keys=True`` to a native ``py_json_dumps_ex(obj, 1)``
helper that sorts dict keys (at every nesting level) by code point — a
byte-wise compare of the UTF-8 key bytes, which matches CPython's default
code-point ordering. ``sort_keys=False`` (and the no-kwarg form) keep the
insertion-order ``py_json_dumps`` path.

Compiles + runs under ``--backend self --python-libpython=off`` in DEFAULT
runtime mode (pcc-Python ports + the C-only ``py_json.c`` helper — the goal
mode). Output is diffed against CPython.
"""
from __future__ import annotations

import json
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


def test_json_dumps_sort_keys_no_libpython(tmp_path):
    out = _run_pcc_program(
        tmp_path,
        "import json\n"
        "def main():\n"
        # non-sorted insertion order -> must come out sorted
        "    print(json.dumps({'banana': 1, 'apple': 2, 'cherry': 3}, sort_keys=True))\n"
        # nested dict must sort recursively; list values keep element order
        "    print(json.dumps({'z': {'y': 1, 'a': 2}, 'a': [3, 1, 2], 'm': 5}, sort_keys=True))\n"
        # uppercase sorts before lowercase (code-point order)
        "    print(json.dumps({'B': 1, 'a': 2, 'A': 3}, sort_keys=True))\n"
        # empty dict
        "    print(json.dumps({}, sort_keys=True))\n"
        # sort_keys=False keeps insertion order
        "    print(json.dumps({'b': 1, 'a': 2}, sort_keys=False))\n"
        # no kwarg keeps insertion order
        "    print(json.dumps({'b': 1, 'a': 2}))\n"
        "main()\n",
    )
    expected = [
        json.dumps({"banana": 1, "apple": 2, "cherry": 3}, sort_keys=True),
        json.dumps({"z": {"y": 1, "a": 2}, "a": [3, 1, 2], "m": 5}, sort_keys=True),
        json.dumps({"B": 1, "a": 2, "A": 3}, sort_keys=True),
        json.dumps({}, sort_keys=True),
        json.dumps({"b": 1, "a": 2}, sort_keys=False),
        json.dumps({"b": 1, "a": 2}),
    ]
    assert out.split("\n")[: len(expected)] == expected, out
