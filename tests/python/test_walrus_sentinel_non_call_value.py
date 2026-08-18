"""An annotated int assignment from a non-Call right-hand side must compile.

``_is_walrus_sentinel`` reads ``expr.func`` to recognise the internal
``_walrus``/``__walrus__`` marker.  Four of its eight call sites guarded with
``isinstance(..., Call)`` first and four did not, so an annotated assignment
with a boxed-int target and a subscript right-hand side reached the predicate
with a ``Subscript`` node:

    error: PCC-PY-COMPILE-001: [python-frontend]
           'Subscript' object has no attribute 'func'

The predicate is now total over expressions rather than requiring every caller
to remember the guard.
"""

from __future__ import annotations

import os
import subprocess

PROGRAM = """
class Box:
    def __init__(self):
        self.n = 7


def from_subscript(a: list) -> None:
    x: int = a[0]
    print(x)


def from_attribute(b) -> None:
    y: int = b.n
    print(y)


def from_name(p: int) -> None:
    z: int = p
    print(z)


def from_call(t: str) -> None:
    w: int = int(t)
    print(w)


from_subscript([11])
from_attribute(Box())
from_name(13)
from_call("17")
"""

EXPECTED = ["11", "7", "13", "17"]


def test_annotated_int_assignment_from_non_call_rhs(tmp_path):
    src = tmp_path / "prog.py"
    src.write_text(PROGRAM, encoding="utf-8")
    exe = tmp_path / "prog_bin"
    env = os.environ.copy()
    env.pop("LC_ALL", None)
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
