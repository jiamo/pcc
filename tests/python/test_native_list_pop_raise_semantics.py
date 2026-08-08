"""list.pop / del list[i] raise IndexError instead of silent NULL.

py_list_pop returned NULL on empty/out-of-range pops WITHOUT raising (the
last '/* TODO(phase3): raise ... */' family in py_list.c), so `[].pop()`
produced NULL-adjacent garbage through the typed marshal path and
try/except IndexError never fired. The fix raises in both the C runtime and
the pcc-Python port (grow-failure branches raise MemoryError the same way)
and adds the frontend post-call err-check on every generated py_list_pop
call site (typed method, dyn method, dyn dispatch, del-subscript).

Runs under --backend self --python-libpython=off on both runtime tiers:
the default tier links the pcc-Python port archive, the cc tier links the
C sources (they had the same gap and must stay mirrored).
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

PROGRAM = """
a = [1, 2, 3]
try:
    a.pop(10)
    print('NO RAISE pos')
except IndexError:
    print('IndexError pos')
try:
    a.pop(-10)
    print('NO RAISE neg')
except IndexError:
    print('IndexError neg')
b = []
try:
    b.pop()
    print('NO RAISE empty')
except IndexError:
    print('IndexError empty')
c = [7]
try:
    del c[5]
    print('NO RAISE del')
except IndexError:
    print('IndexError del')


def as_dyn(x):
    return x


d = as_dyn([4, 5])
try:
    d.pop(9)
    print('NO RAISE dyn')
except IndexError:
    print('IndexError dyn')
print('dyn popped', d.pop())
print('popped', a.pop())
print('len', len(a))
"""

EXPECTED = [
    "IndexError pos",
    "IndexError neg",
    "IndexError empty",
    "IndexError del",
    "IndexError dyn",
    "dyn popped 5",
    "popped 3",
    "len 2",
]


@pytest.mark.parametrize("runtime_cc", ["port", "cc"])
def test_list_pop_and_del_raise_indexerror_no_libpython(tmp_path, runtime_cc):
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
        text=True, capture_output=True, timeout=300, env=env,
    )
    assert build.returncode == 0, build.stderr
    run = subprocess.run(
        [str(exe)], text=True, capture_output=True, timeout=30, env=env
    )
    assert run.returncode == 0, run.stderr
    assert run.stdout.strip().splitlines() == EXPECTED
