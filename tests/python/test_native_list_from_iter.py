"""list() over a generator / non-indexable iterable under strict no-libpython.

The list() builtin materialized DynType/Tuple/Class args via py_obj_len +
py_obj_getitem (integer indexing), so list(generator) gave [] (a generator has
no len) and could not drive an iterator. Now the general-iterable arm calls the
runtime py_obj_to_list, which uses the iterator protocol (py_obj_iter +
py_obj_next) with a len+getitem fallback for __getitem__-only containers.

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
    env["PCC_RUNTIME_CC"] = "cc"
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


def test_list_from_generator_and_iterables_native_no_libpython(tmp_path):
    out = _run_pcc_program(
        tmp_path,
        "def gen(n):\n"
        "    i = 0\n"
        "    while i < n:\n"
        "        yield i * i\n"
        "        i = i + 1\n"
        "print(list(gen(4)))\n"               # generator (was [])
        "print(sum(list(gen(5))), len(list(gen(10))))\n"
        "print(list(range(5)))\n"             # range
        "print(sorted(list({'a': 1, 'b': 2})))\n"   # dict keys
        "print(list([1, 2, 3]), list((4, 5, 6)))\n"  # list/tuple unchanged
        "print(sorted(list({9, 3, 6})))\n",  # set unchanged
    )
    assert out.split("\n")[:6] == [
        "[0, 1, 4, 9]",
        "30 10",
        "[0, 1, 2, 3, 4]",
        "['a', 'b']",
        "[1, 2, 3] [4, 5, 6]",
        "[3, 6, 9]",
    ], out
