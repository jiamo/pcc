"""sorted() over non-indexable iterables under strict no-libpython (run-based).

py_obj_sorted (py_obj_ops_compare.c) special-cased set but otherwise built its
working list via py_obj_getitem(x, i) (integer indexing). That returns NULL for
a dict (0/1/... are not keys), so `sorted({...})` produced [<null>, ...]; it
also could not drive a generator/range. Now non-list/tuple/set iterables use the
iterator protocol (py_obj_iter + py_obj_next), so sorted() works for dict (keys),
generator, range, etc.; list/tuple/set keep their existing paths.

Compiles + runs under --backend self --python-libpython=off and asserts output.
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
    # DEFAULT mode (pcc_py ports) — the no-libpython goal mode. py_obj_sorted is
    # in both py_obj_ops_compare.c (iterator protocol, all iterables) and the
    # port py_obj_ops_compare.py (dict via py_dict_keys + indexables). sorted of
    # a generator/range is cc-only for now (the port handles the common dict +
    # indexable cases; full-iterator port is a follow-on).
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


def test_sorted_dict_and_iterables_native_no_libpython(tmp_path):
    # DEFAULT mode: dict (-> sorted keys, was [<null>,...]) + the indexable
    # iterables. sorted(generator)/sorted(range) work in cc mode but are a
    # port follow-on for default mode (see helper note).
    out = _run_pcc_program(
        tmp_path,
        "d = {'banana': 1, 'apple': 2, 'cherry': 3}\n"
        "print(sorted(d))\n"                 # dict -> sorted keys (was <null>)
        "print(sorted([3, 1, 2]), sorted((5, 2, 8)), sorted({9, 3, 6}))\n"
        "print(sorted('dcba'))\n"
        "e = {3: 'x', 1: 'y', 2: 'z'}\n"
        "print(sorted(e))\n",
    )
    assert out.split("\n")[:4] == [
        "['apple', 'banana', 'cherry']",
        "[1, 2, 3] [2, 5, 8] [3, 6, 9]",
        "['a', 'b', 'c', 'd']",
        "[1, 2, 3]",
    ], out
