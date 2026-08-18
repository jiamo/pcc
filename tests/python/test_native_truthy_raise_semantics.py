"""Truthiness that dispatches a raising ``__bool__``/``__len__`` must propagate.

``py_obj_truthy`` dispatches ``py_user_bool_dispatch`` /
``py_user_len_dispatch`` (and ``pcc_capi_cext_truthy`` for extension types) and
still returns a truth value when they raise -- the runtime uses the
return-code exception model, so the caller owes a ``py_err_occurred()`` check.
``_truthy`` guarded only its ``py_cpy_truthy`` path; every native
``py_obj_truthy`` call was unguarded.

Expression contexts (``not``/``and``/``or``/``bool()``) happened to propagate
because their result feeds a checked consumer.  Condition contexts feed a
``cbranch`` directly, so ``if`` and ``while`` silently swallowed the exception
and took the false branch.

Runs under --backend self --python-libpython=off on both runtime tiers: the
default tier links the pcc-Python port archive, the cc tier links the C
sources.
"""

from __future__ import annotations

import os
import subprocess

import pytest

PROGRAM = """
class BoomBool:
    def __bool__(self):
        raise ValueError('bool boom')


class BoomLen:
    def __len__(self):
        raise ValueError('len boom')


try:
    if BoomBool():
        print('NO RAISE if-true')
    else:
        print('NO RAISE if-false')
except ValueError:
    print('ValueError if')

try:
    while BoomBool():
        print('NO RAISE while-body')
    print('NO RAISE while-exit')
except ValueError:
    print('ValueError while')

try:
    r = not BoomBool()
    print('NO RAISE not', r)
except ValueError:
    print('ValueError not')

try:
    r = BoomBool() and 1
    print('NO RAISE and', r)
except ValueError:
    print('ValueError and')

try:
    r = BoomBool() or 1
    print('NO RAISE or', r)
except ValueError:
    print('ValueError or')

try:
    r = bool(BoomBool())
    print('NO RAISE bool()', r)
except ValueError:
    print('ValueError bool()')

try:
    if BoomLen():
        print('NO RAISE len-true')
    else:
        print('NO RAISE len-false')
except ValueError:
    print('ValueError len')

print('done')
"""

# Verified against CPython 3 as the oracle.
EXPECTED = [
    "ValueError if",
    "ValueError while",
    "ValueError not",
    "ValueError and",
    "ValueError or",
    "ValueError bool()",
    "ValueError len",
    "done",
]


@pytest.mark.parametrize("runtime_cc", ["port", "cc"])
def test_raising_bool_and_len_propagate_from_every_truthy_context(
    tmp_path, runtime_cc
):
    src = tmp_path / "prog.py"
    src.write_text(PROGRAM, encoding="utf-8")
    exe = tmp_path / "prog_bin"
    env = os.environ.copy()
    env.pop("LC_ALL", None)
    if runtime_cc == "cc":
        env["PCC_RUNTIME_CC"] = "cc"
    else:
        env.pop("PCC_RUNTIME_CC", None)
    build = subprocess.run(
        [
            "uv", "run", "pcc", "--backend", "self", "--python-libpython=off",
            "--ir-scaffold=on", str(src), "-o", str(exe),
        ],
        text=True, capture_output=True, timeout=600, env=env,
    )
    assert build.returncode == 0, build.stderr
    run = subprocess.run(
        [str(exe)], text=True, capture_output=True, timeout=30, env=env
    )
    assert run.returncode == 0, run.stderr
    assert run.stdout.strip().splitlines() == EXPECTED
