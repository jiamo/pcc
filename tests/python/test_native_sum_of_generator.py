"""sum() of a generator (iterator-only DynType) under strict no-libpython.

Companion to test_native_list_of_generator / test_native_comprehension_over_generator
(same root cause: DynType iterables were consumed via py_obj_len + integer
py_obj_getitem, which yields nothing for a generator). ``sum(gen())`` returned
0; the DynType arm of ``_maybe_emit_sum_literal`` now consumes via the iterator
protocol (``_emit_sum_via_iter`` -> py_obj_iter/py_obj_next, accumulating int
elements into an i64, clearing a terminal StopIteration). ListType / TupleType
keep the len+getitem path.

See docs/investigations/sequence-builtins-len-getitem-not-iterator-protocol.md.
Includes regression cases (sum of a list / tuple, and the ``start`` argument).

Runs under ``--backend self --python-libpython=off`` in DEFAULT runtime mode.
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


def test_sum_of_generator_no_libpython(tmp_path):
    out = _run_pcc_program(
        tmp_path,
        "def gen(n):\n"
        "    i = 0\n"
        "    while i < n:\n"
        "        yield i * i\n"
        "        i += 1\n"
        "def main():\n"
        "    print(sum(gen(4)))\n"            # 0+1+4+9 = 14
        "    print(sum(gen(0)))\n"            # 0
        "    print(sum(gen(5), 100))\n"       # 0+1+4+9+16 + 100 = 130
        "    print(sum([1, 2, 3]))\n"         # 6
        "    print(sum((4, 5, 6)))\n"         # 15
        "    print(sum([1, 2, 3], 10))\n"     # 16
        "main()\n",
    )
    assert out.split("\n")[:6] == ["14", "0", "130", "6", "15", "16"], out
