"""list() of a generator (iterator-only DynType) under strict no-libpython.

Companion to test_native_comprehension_over_generator: the comprehension site
was fixed first; this covers the ``list(<generator>)`` builtin. Before the fix,
``list(gen())`` returned ``[]`` because ``_maybe_emit_list_builtin``'s DynType
arm iterated via ``py_obj_len`` + integer ``py_obj_getitem`` (a generator has no
length / ``__getitem__`` -> zero iterations). The DynType arm now consumes via
the iterator protocol (``_emit_list_append_via_iter`` -> ``py_obj_iter`` /
``py_obj_next``, clearing a terminal StopIteration), matching the statement
for-loop and the comprehension path. TupleType / ClassType keep the len+getitem
path.

See docs/investigations/sequence-builtins-len-getitem-not-iterator-protocol.md.
Includes regression cases (list of a list / tuple / range / dict keys) to guard
the DynType routing change. ``list(<str>)`` is intentionally omitted: it is a
separate, pre-existing libpython fallback unrelated to this fix.

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


def test_list_of_generator_no_libpython(tmp_path):
    out = _run_pcc_program(
        tmp_path,
        "def gen(n):\n"
        "    i = 0\n"
        "    while i < n:\n"
        "        yield i * i\n"
        "        i += 1\n"
        "def main():\n"
        "    print(list(gen(4)))\n"                  # [0, 1, 4, 9]
        "    print(list(gen(0)))\n"                  # []
        "    print(list([1, 2, 3]))\n"              # [1, 2, 3]
        "    print(list((4, 5, 6)))\n"              # [4, 5, 6]
        "    print(list(range(3)))\n"                # [0, 1, 2]
        "    d = {'x': 1, 'y': 2}\n"
        "    print(sorted(list(d)))\n"               # ['x', 'y']
        "    print(list(gen(3)) + list(gen(2)))\n"   # [0, 1, 4, 0, 1]
        "    print(len(list(gen(5))))\n"             # 5
        "main()\n",
    )
    assert out.split("\n")[:8] == [
        "[0, 1, 4, 9]",
        "[]",
        "[1, 2, 3]",
        "[4, 5, 6]",
        "[0, 1, 2]",
        "['x', 'y']",
        "[0, 1, 4, 0, 1]",
        "5",
    ], out
