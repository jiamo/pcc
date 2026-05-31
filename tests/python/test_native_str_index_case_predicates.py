"""str.index() / str.isupper() / str.islower() under strict no-libpython.

These three str methods had no native lowering, so under
``--backend self --python-libpython=off`` they fell back to py_cpy_* helpers
and the compile hard-errored (PCC-PY-COMPILE-001). Added:
  - runtime py_str_isupper / py_str_islower (py_str_accessors.c + port .py):
    ASCII cased-character predicates (True iff >=1 cased char and none of the
    opposite case, matching CPython which ignores non-cased chars);
  - runtime py_str_index_of (named *_of to avoid the existing py_str_index s[i]
    subscript helper): find() + ValueError when the substring is absent;
  - frontend string_method_lowering dispatch for isupper/islower (the is*
    predicate group) and index (alongside find), in both the StrType fast path
    and the DynType path.

Compiles + runs under ``--backend self --python-libpython=off`` in DEFAULT
runtime mode (pcc-Python ports — the goal mode) and asserts CPython-exact output.
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
    # DEFAULT mode (pcc_py ports) — the no-libpython goal mode. py_str_isupper/
    # islower/index_of are mirrored in py_str_accessors.c and the port .py.
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


def test_str_index_isupper_islower_native_no_libpython(tmp_path):
    out = _run_pcc_program(
        tmp_path,
        "def main():\n"
        "    print('hello'.index('l'))\n"                 # 2
        "    print('hello world'.index('world'))\n"       # 6
        "    print('abcabc'.index('c'))\n"                # 2
        "    print('HELLO'.isupper(), 'Hello'.isupper(), 'hello'.isupper())\n"  # True False False
        "    print('HELLO1'.isupper(), '123'.isupper())\n"  # True False
        "    print('hello'.islower(), 'Hello'.islower(), 'HELLO'.islower())\n"  # True False False
        "    print('hello1'.islower(), '123'.islower())\n"  # True False
        "main()\n",
    )
    assert out.split("\n")[:7] == [
        "2",
        "6",
        "2",
        "True False False",
        "True False",
        "True False False",
        "True False",
    ], out


def test_str_index_not_found_raises_valueerror_no_libpython(tmp_path):
    # str.index of an absent substring raises ValueError. We assert the *raise*
    # (program exits non-zero with the ValueError message) rather than a
    # try/except catch: catching a runtime-raised exception from a
    # discarded-expression statement needs an inserted py_err_occurred() check
    # after the call, which is the separate broad "narrow err-check" follow-on
    # (also affects dict KeyError / list IndexError). py_str_index_of itself
    # raises correctly, which is what this slice fixes.
    src = tmp_path / "prog.py"
    src.write_text(
        "def main():\n"
        "    print('hello'.index('z'))\n"
        "main()\n",
        encoding="utf-8",
    )
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
    run = subprocess.run([str(exe)], text=True, capture_output=True, timeout=30, env=env)
    assert run.returncode != 0, "expected ValueError exit"
    assert "ValueError" in run.stderr, run.stderr
