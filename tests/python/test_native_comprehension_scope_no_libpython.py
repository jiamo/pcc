"""Comprehension loop-variable scope under strict no-libpython (run-based).

Python 3 gives comprehensions their own scope: the loop target must NOT leak
into the enclosing function scope, and in particular must NOT overwrite an
outer variable of the same name that is read after the comprehension. Before
this fix ``comprehension_lowering._emit_comprehension`` lowered every loop
target directly into the flat enclosing ``self.env`` (no save/restore / fresh
slot), so::

    def main():
        x = 99
        ys = [x for x in range(5)]
        print(x, ys)

printed ``5 [0, 1, 2, 3, 4]`` under ``--backend self --python-libpython=off``
instead of CPython's ``99 [0, 1, 2, 3, 4]`` — the last iteration value leaked
over the outer binding.

The fix saves the outer ``self.env`` / ``_cpy_env_flags`` / ``_exact_int_env_flags``
bindings for every comprehension target name (including tuple-unpack element
names), drops them so each loop path allocates a fresh slot, and restores them
after the whole comprehension is emitted. This covers list/set/dict/generator
comprehensions and the range / list-index / string-char / tuple-unpack loop
paths.

Runs under ``--backend self --python-libpython=off`` in DEFAULT runtime mode
and diffs the program output against ``python3`` (CPython) as the oracle.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


_PROGRAM = (
    "def main():\n"
    # 1. range list-comp shadowing an outer int read afterwards (the bug).
    "    x = 99\n"
    "    ys = [x for x in range(5)]\n"
    "    print(x, ys)\n"
    # 2. list-literal iter shadowing an outer value.
    "    n = 7\n"
    "    a = [n for n in [10, 20, 30]]\n"
    "    print(n, a)\n"
    # 3. set-comp shadowing.
    "    s = 3\n"
    "    st = {s for s in range(3)}\n"
    "    print(s, sorted(st))\n"
    # 4. dict-comp with two generators shadowing two outer names.
    "    k = 5\n"
    "    v = 6\n"
    "    d = {k: v for k in range(2) for v in range(2)}\n"
    "    print(k, v, sorted(d.items()))\n"
    # 5. string-char iter shadowing.
    "    c = 'orig'\n"
    "    cs = [c for c in 'ab']\n"
    "    print(c, cs)\n"
    # 6. tuple-unpack target shadowing outer names.
    "    p = 100\n"
    "    q = 200\n"
    "    pairs = [(1, 2), (3, 4)]\n"
    "    sums = [p + q for (p, q) in pairs]\n"
    "    print(p, q, sums)\n"
    # 7. outer iterable references the target name: still uses the outer list.
    "    w = [10, 20, 30]\n"
    "    ww = [w for w in w]\n"
    "    print(w, ww)\n"
    # 8. nested comprehension: the inner target shadows the outer target;
    #    both must restore cleanly and the post-comp outer read is intact.
    "    m = 42\n"
    "    grid = [[m for m in range(2)] for m in range(3)]\n"
    "    print(m, grid)\n"
    "main()\n"
)


def _run_pcc_program(tmp_path: Path, source: str) -> str:
    src = tmp_path / "comp_scope.py"
    src.write_text(source, encoding="utf-8")
    exe = tmp_path / "comp_scope_bin"
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


def _run_cpython(tmp_path: Path, source: str) -> str:
    src = tmp_path / "comp_scope_ref.py"
    src.write_text(source, encoding="utf-8")
    env = os.environ.copy()
    env.pop("LC_ALL", None)
    run = subprocess.run(
        [sys.executable, str(src)],
        text=True, capture_output=True, timeout=30, env=env,
    )
    assert run.returncode == 0, run.stderr
    return run.stdout


def test_comprehension_loop_var_does_not_leak_no_libpython(tmp_path):
    pcc_out = _run_pcc_program(tmp_path, _PROGRAM)
    cpython_out = _run_cpython(tmp_path, _PROGRAM)
    assert pcc_out == cpython_out, (
        "comprehension loop target leaked into enclosing scope:\n"
        f"pcc:\n{pcc_out}\ncpython:\n{cpython_out}"
    )
    # Guard the specific regression values explicitly (independent of the
    # oracle) so a silently-wrong CPython run cannot mask a leak.
    lines = pcc_out.split("\n")
    assert lines[0] == "99 [0, 1, 2, 3, 4]", pcc_out
    assert lines[7] == "42 [[0, 1], [0, 1], [0, 1]]", pcc_out
