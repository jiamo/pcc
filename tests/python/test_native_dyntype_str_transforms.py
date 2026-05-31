"""DynType-receiver str transforms (capitalize/swapcase/title/casefold) under
strict no-libpython (run-based).

``_maybe_emit_str_method_via_dyn`` (the DynType-receiver str dispatch) already
had the runtime mapping for the no-arg transforms ``capitalize`` / ``swapcase``
/ ``title`` / ``casefold`` (py_str_capitalize/...), but they were missing from
the ``_STR_METHOD_NATIVE`` gate set, so a DynType receiver (e.g. a value from
``zip()`` / iteration / a DynType container) raised ``AttributeError:
capitalize`` instead of dispatching natively. ``upper`` / ``lower`` / ``strip``
already worked because they were in the set.

Found by a realistic-program CPython diff: ``for n, a in zip(names, ages):
n.capitalize()``. Fix is frontend-only: add the four transforms to
``_STR_METHOD_NATIVE``.

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


def test_dyntype_str_transforms_no_libpython(tmp_path):
    # The receiver `n` comes from zip() unpacking -> DynType, exercising the
    # _maybe_emit_str_method_via_dyn path (not the StrType fast path).
    out = _run_pcc_program(
        tmp_path,
        "def main():\n"
        "    names = ['alice BOB', 'mixedCase']\n"
        "    vals = [1, 2]\n"
        "    for n, v in zip(names, vals):\n"
        "        print(n.capitalize())\n"
        "        print(n.title())\n"
        "        print(n.swapcase())\n"
        "        print(n.casefold())\n"
        "        print(n.upper())\n"
        "main()\n",
    )
    assert out.split("\n")[:10] == [
        "Alice bob",      # 'alice BOB'.capitalize()
        "Alice Bob",      # .title()
        "ALICE bob",      # .swapcase()
        "alice bob",      # .casefold()
        "ALICE BOB",      # .upper()
        "Mixedcase",      # 'mixedCase'.capitalize()
        "Mixedcase",      # .title()
        "MIXEDcASE",      # .swapcase()
        "mixedcase",      # .casefold()
        "MIXEDCASE",      # .upper()
    ], out
