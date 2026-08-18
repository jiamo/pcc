"""Comprehensions over a generator (iterator-only DynType) under strict
no-libpython (run-based).

Before this fix, ``[x for x in gen()]`` produced ``[]`` under
``--python-libpython=off``: ``comprehension_lowering._emit_comprehension_generator``
routed a ``DynType`` iterable to ``_emit_comprehension_obj_indexed`` (a
``py_obj_len`` + integer ``py_obj_getitem`` loop), which runs zero times for an
iterator-only object such as a generator (no length / ``__getitem__``). The
statement ``for``-loop worked because ``for_loop_lowering`` uses the iterator
protocol (``py_obj_iter``/``py_obj_next``); the ``ClassType`` arm already used
it too. The fix routes the ``DynType`` arm through the same
``_emit_comprehension_obj_iterator`` path.

See docs/investigations/sequence-builtins-len-getitem-not-iterator-protocol.md.
(The list()/sum()/sorted()/set()/tuple() builtins share the same root cause and
are fixed in follow-up slices; this test covers the comprehension site.)

Includes regression cases (comprehension over a list literal and a string) to
guard the broadened DynType -> iterator-protocol routing. Runs under
``--backend self --python-libpython=off`` in DEFAULT runtime mode.
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


def test_comprehension_over_generator_no_libpython(tmp_path):
    out = _run_pcc_program(
        tmp_path,
        "def gen(n):\n"
        "    i = 0\n"
        "    while i < n:\n"
        "        yield i * i\n"
        "        i += 1\n"
        "def main():\n"
        "    print([x for x in gen(4)])\n"                      # [0, 1, 4, 9]
        "    print([x + 1 for x in gen(3)])\n"                  # [1, 2, 5]
        "    print([x for x in gen(5) if x % 2 == 0])\n"        # [0, 4, 16]
        "    print(sorted({x: x * 2 for x in gen(3)}.items()))\n"  # [(0,0),(1,2),(4,8)]
        "    total = 0\n"
        "    for x in gen(4):\n"                                # stmt for-loop regression
        "        total += x\n"
        "    print(total)\n"                                    # 14
        "    print([y for y in [10, 20, 30]])\n"               # list-literal regression
        "    print([c for c in 'abc'])\n"                       # string regression
        "main()\n",
    )
    assert out.split("\n")[:7] == [
        "[0, 1, 4, 9]",
        "[1, 2, 5]",
        "[0, 4, 16]",
        "[(0, 0), (1, 2), (4, 8)]",
        "14",
        "[10, 20, 30]",
        "['a', 'b', 'c']",
    ], out


def test_dict_comprehension_enumerate_set_uses_iterator_protocol(tmp_path):
    out = _run_pcc_program(
        tmp_path,
        "def main():\n"
        "    names = set()\n"
        "    names.add('_pcc_gc_pin')\n"
        "    names.add('_py_int_add')\n"
        "    names.add('_user_main')\n"
        "    known = {name: index for index, name in enumerate(names)}\n"
        "    print(len(known))\n"
        "    print('_pcc_gc_pin' in known)\n"
        "    print(sorted(known))\n"
        "main()\n",
    )
    assert out.splitlines() == [
        "3",
        "True",
        "['_pcc_gc_pin', '_py_int_add', '_user_main']",
    ]
