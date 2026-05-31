"""max(iterable, default=X) / min(iterable, default=X) under no-libpython.

The min/max(iterable) dispatch was gated on ``not expr.kwargs``, so the
``default=`` kwarg form fell through to libpython (errored under
--python-libpython=off) — even though _maybe_emit_min_max_iter already consumes
the ``default`` kwarg (seeding the accumulator when the iterable is empty). Fix:
drop the ``not expr.kwargs`` gate so the default= form reaches it; any other
kwarg (e.g. key=) still returns None and falls through.

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


def test_minmax_default_native_no_libpython(tmp_path):
    out = _run_pcc_program(
        tmp_path,
        "def main():\n"
        "    print(max([], default=-1))\n"           # -1 (empty -> default)
        "    print(max([5, 2, 8], default=-1))\n"    # 8 (non-empty -> max)
        "    print(min([], default=99))\n"           # 99 (empty -> default)
        "    print(min([3, 1, 2], default=99))\n"    # 1 (non-empty -> min)
        "    print(max([7], default=0))\n"           # 7
        "main()\n",
    )
    assert out.split("\n")[:5] == [
        "-1",
        "8",
        "99",
        "1",
        "7",
    ], out
