"""min()/max() with 3+ positional args under strict no-libpython (run-based).

``min``/``max`` only had native lowering for exactly 2 positional args and for a
single iterable arg; ``max(3, 7, 2)`` (3+ scalars) fell through every native
branch and, under ``--python-libpython=off``, became ``NameError: name 'max' is
not defined``. Found by a realistic-program CPython diff.

Fix (frontend-only): ``_emit_min_max_variadic`` folds the N args pairwise with
the same compare+select as the 2-arg path. All-int/bool folds as i64; if any arg
is a float, int/bool args are promoted to double via an i64 first (a plain
``_emit_expr`` may hand back a boxed void* that can't ``sitofp`` — the self
backend rejects ``sitofp void* -> double``). A DynType arg mixed with floats
bails to the fallback.

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


def test_minmax_variadic_no_libpython(tmp_path):
    out = _run_pcc_program(
        tmp_path,
        "def main():\n"
        "    print(max(3, 7, 2))\n"            # 7
        "    print(min(3, 7, 2))\n"            # 2
        "    print(max(1, 2, 3, 4, 5))\n"      # 5
        "    print(min(5, 4, 3, 2, 1))\n"      # 1
        "    print(max(1.5, 2.5, 0.5))\n"      # 2.5
        "    print(min(1.5, 2.5, 0.5))\n"      # 0.5
        "    print(max(3, 7.0, 2))\n"          # 7.0 (mixed int/float)
        "    print(min(3, 1.5, 2))\n"          # 1.5
        "    print(max(-1, -5, -3))\n"         # -1
        "main()\n",
    )
    assert out.split("\n")[:9] == [
        "7", "2", "5", "1", "2.5", "0.5", "7.0", "1.5", "-1",
    ], out
