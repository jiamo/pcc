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
runtime mode, including the pcc-Python ``py_json_runtime.py`` production
owner. Output is diffed against CPython.
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


def test_json_dumps_ensure_ascii_false_no_libpython(tmp_path):
    out = _run_pcc_program(
        tmp_path,
        "import json\n"
        "def main():\n"
        "    print(json.dumps({'内容': '中文😀'}, ensure_ascii=False))\n"
        "    print(json.dumps({'乙': 2, '甲': 1}, ensure_ascii=False, sort_keys=True))\n"
        "main()\n",
    )
    expected = [
        json.dumps({"内容": "中文😀"}, ensure_ascii=False),
        json.dumps({"乙": 2, "甲": 1}, ensure_ascii=False, sort_keys=True),
    ]
    assert out.splitlines() == expected


def test_json_pcc_python_owner_edge_semantics_no_libpython(tmp_path):
    out = _run_pcc_program(
        tmp_path,
        "import json\n"
        "def main():\n"
        "    big = 1234567890123456789012345678901234567890\n"
        "    text = '{\"big\": ' + str(big) + ', \"one\": 1.0, \"yes\": true, \"nothing\": null}'\n"
        "    decoded = json.loads(text)\n"
        "    print(decoded['big'] == big)\n"
        "    print(json.dumps([decoded['big'], decoded['one'], decoded['yes'], decoded['nothing']]))\n"
        "    print(json.loads('\"\\\\u00e9\"') == chr(233))\n"
        "    print(json.loads('\"\\\\ud83d\\\\ude00\"') == chr(128512))\n"
        "main()\n",
    )
    assert out.splitlines() == [
        "True",
        "[1234567890123456789012345678901234567890, 1.0, true, null]",
        "True",
        "True",
    ]
