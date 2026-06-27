from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

# Regression for S-P0-SELF: a dict-subclass instance (collections.Counter) had
# no dict-inherited item storage or methods under the pcc1-compiled no-libpython
# stdlib. `Counter('aabbbc')` failed because `self[item] = ...` in Counter.update
# hit py_user_setitem_dispatch with no __setitem__ override (TypeError), and
# `c['z']` -> Counter.__getitem__ -> self.get('z', 0) could not resolve the
# inherited dict.get (AttributeError). The runtime now routes __setitem__ /
# __getitem__ / get / __missing__ / __len__ / __contains__ on a class flagged
# PY_CLASS_FLAG_DICT_SUBCLASS to a backing dict held in the instance's __dict__.
#
# CPython reference (`python3`): Counter('aabbbc'); print(c['b'], c['a'], c['z'])
#   -> "3 2 0"


def _build_and_run(tmp_path: Path, program: str) -> str:
    src = tmp_path / "prog.py"
    src.write_text(program, encoding="utf-8")
    exe = tmp_path / "prog"
    env = os.environ.copy()
    env.pop("LC_ALL", None)
    build = subprocess.run(
        [
            "uv",
            "run",
            "pcc",
            "--backend",
            "self",
            "--python-libpython=off",
            "--ir-scaffold=on",
            str(src),
            "-o",
            str(exe),
        ],
        text=True,
        capture_output=True,
        timeout=420,
        env=env,
    )
    assert build.returncode == 0, build.stderr
    run = subprocess.run(
        [str(exe)],
        text=True,
        capture_output=True,
        timeout=30,
        env=env,
    )
    assert run.returncode == 0, run.stderr
    return run.stdout


def test_counter_missing_key_returns_zero_no_libpython(tmp_path: Path):
    program = (
        "import collections\n"
        "def main():\n"
        "    c = collections.Counter('aabbbc')\n"
        "    print(c['b'], c['a'], c['z'])\n"
        "main()\n"
    )
    got = _build_and_run(tmp_path, program)
    # Diff against the live CPython reference so this stays honest if the
    # semantics ever shift.
    ref = subprocess.run(
        [sys.executable, "-c",
         "import collections;"
         "c=collections.Counter('aabbbc');"
         "print(c['b'], c['a'], c['z'])"],
        text=True,
        capture_output=True,
        timeout=30,
    )
    assert ref.returncode == 0, ref.stderr
    assert got == ref.stdout
    assert got.splitlines() == ["3 2 0"]


def test_counter_setitem_get_and_membership_no_libpython(tmp_path: Path):
    # Exercises the dict-inherited __setitem__ (no user override), get with an
    # explicit default, membership (__contains__), and len (__len__) on the
    # dict-subclass instance.
    program = (
        "import collections\n"
        "def main():\n"
        "    c = collections.Counter()\n"
        "    c['x'] = 5\n"
        "    print(c['x'], c['y'])\n"
        "    print(c.get('x', -1), c.get('z', -1))\n"
        "    print('x' in c, 'z' in c)\n"
        "    print(len(c))\n"
        "main()\n"
    )
    got = _build_and_run(tmp_path, program)
    ref = subprocess.run(
        [sys.executable, "-c",
         "import collections\n"
         "c = collections.Counter()\n"
         "c['x'] = 5\n"
         "print(c['x'], c['y'])\n"
         "print(c.get('x', -1), c.get('z', -1))\n"
         "print('x' in c, 'z' in c)\n"
         "print(len(c))\n"],
        text=True,
        capture_output=True,
        timeout=30,
    )
    assert ref.returncode == 0, ref.stderr
    assert got == ref.stdout
    assert got.splitlines() == ["5 0", "5 -1", "True False", "1"]
