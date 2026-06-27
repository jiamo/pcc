"""User-visible list subscript STORES raise catchable IndexError (no-libpython).

py_list_set is intentionally an internal non-raising setter (sort/insert
shifts, generator frames index within bounds by construction), but the
user-visible exact and generic list subscript assignments routed through it,
so an out-of-range ``items[index] = value`` returned silently. The public
raising store contract is ``py_list_setitem`` (C) mirrored in ``py_list.py``
(port), wired through:

- the exact-ListType store branch in subscript_lowering,
- ``py_obj_setitem`` / ``py_obj_setitem_i64`` list branches (dyn receivers),

covering ordinary assignment, unpack-target assignment, and augmented
assignment. Both runtime tiers are exercised: the default build links the
pcc-Python ports, ``PCC_RUNTIME_CC=cc`` links the C runtime — a fix mirrored
in only one tier passes the other and regresses silently.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

_TIERS = ("port", "cc")


def _run_pcc_program(tmp_path: Path, source: str, tier: str) -> str:
    src = tmp_path / "prog.py"
    src.write_text(source, encoding="utf-8")
    exe = tmp_path / "prog_bin"
    env = os.environ.copy()
    env.pop("LC_ALL", None)
    if tier == "cc":
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
    return run.stdout


_STORE_PROGRAM = (
    "a = [10, 20, 30]\n"
    "a[0] = 11\n"
    "a[-1] = 33\n"
    "print(a)\n"
    "for bad in [3, 5, 100, -4, -100]:\n"
    "    try:\n"
    "        a[bad] = 99\n"
    "        print('NO RAISE', bad)\n"
    "    except IndexError:\n"
    "        print('IndexError', bad)\n"
    "print(a)\n"
    "try:\n"
    "    a[3] = 99\n"
    "except IndexError as e:\n"
    "    print(str(e))\n"
    "try:\n"
    "    a[4], b = 1, 2\n"
    "    print('NO RAISE unpack')\n"
    "except IndexError:\n"
    "    print('IndexError unpack')\n"
    "a[1] += 5\n"
    "print(a[1])\n"
    "try:\n"
    "    a[9] += 1\n"
    "    print('NO RAISE aug')\n"
    "except IndexError:\n"
    "    print('IndexError aug')\n"
)

_STORE_EXPECTED = [
    "[11, 20, 33]",
    "IndexError 3",
    "IndexError 5",
    "IndexError 100",
    "IndexError -4",
    "IndexError -100",
    "[11, 20, 33]",
    "list assignment index out of range",
    "IndexError unpack",
    "25",
    "IndexError aug",
]


@pytest.mark.parametrize("tier", _TIERS)
def test_exact_list_store_out_of_range_raises_indexerror(tmp_path, tier):
    out = _run_pcc_program(tmp_path, _STORE_PROGRAM, tier)
    assert out.split("\n")[: len(_STORE_EXPECTED)] == _STORE_EXPECTED, out


_DYN_PROGRAM = (
    "def store(container, index, value):\n"
    "    container[index] = value\n"
    "\n"
    "a = [1, 2, 3]\n"
    "store(a, 1, 22)\n"
    "print(a)\n"
    "try:\n"
    "    store(a, 7, 99)\n"
    "    print('NO RAISE dyn')\n"
    "except IndexError:\n"
    "    print('IndexError dyn')\n"
    "d = {}\n"
    "store(d, 7, 99)\n"
    "print(d[7])\n"
)

_DYN_EXPECTED = [
    "[1, 22, 3]",
    "IndexError dyn",
    "99",
]


@pytest.mark.parametrize("tier", _TIERS)
def test_dyn_receiver_list_store_out_of_range_raises_indexerror(tmp_path, tier):
    out = _run_pcc_program(tmp_path, _DYN_PROGRAM, tier)
    assert out.split("\n")[: len(_DYN_EXPECTED)] == _DYN_EXPECTED, out


_INTERNAL_PROGRAM = (
    "a = [3, 1, 2]\n"
    "a.sort()\n"
    "print(a)\n"
    "a.insert(1, 9)\n"
    "print(a)\n"
    "a.reverse()\n"
    "print(a)\n"
)

_INTERNAL_EXPECTED = [
    "[1, 2, 3]",
    "[1, 9, 2, 3]",
    "[3, 2, 9, 1]",
]


@pytest.mark.parametrize("tier", _TIERS)
def test_internal_list_setters_unbroken_by_raising_store(tmp_path, tier):
    out = _run_pcc_program(tmp_path, _INTERNAL_PROGRAM, tier)
    assert out.split("\n")[: len(_INTERNAL_EXPECTED)] == _INTERNAL_EXPECTED, out
