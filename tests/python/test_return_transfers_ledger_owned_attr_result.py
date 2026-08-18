"""PY-P0-CONSUMER-BOUNDARY-OWNERSHIP-LEDGER: ``return obj.attr`` must transfer
the owned ``py_obj_getattr`` result, not retain a second reference.

The attribute emitter registers the getattr result in the value ledger
(``_note_owned_dynamic_call_value``) because the AST-shape classifier says an
``Attr`` is not owned.  Every borrowing consumer consults that ledger through
``_gc_release_if_owned`` -- except the return path, whose
``_return_value_needs_retain`` re-derived ownership from the AST alone, saw
"not owned", retained a second reference and returned that copy, leaking the
original.  Observable: the attribute object's finalizer never runs.

CPython-oracle finalizer differential: the output order below is CPython's;
pcc must match it. The bare-statement and binop shapes are the controls that
were already correct (their consumers consult the ledger).
"""
from __future__ import annotations

import os
import subprocess
import sys

PROGRAM = """
class Fin:
    def __init__(self, tag: str) -> None:
        self.tag = tag

    def __del__(self) -> None:
        print("fin " + self.tag)


class T:
    def __init__(self, tag: str) -> None:
        self.n = Fin(tag)


def ret(t):
    return t.n


def stmt(t) -> int:
    t.n
    return 0


def binop_len(t) -> int:
    return len(t.n.tag) + 0


def scope_ret() -> None:
    a = T("ret")
    r = ret(a)
    print("in-ret " + r.tag)


def scope_stmt() -> None:
    b = T("stmt")
    stmt(b)


def scope_binop() -> None:
    c = T("binop")
    binop_len(c)


def main() -> None:
    scope_ret()
    print("after-ret")
    scope_stmt()
    print("after-stmt")
    scope_binop()
    print("after-binop")
    print("end")


main()
"""


def _run(cmd, **kw):
    env = os.environ.copy()
    env.pop("LC_ALL", None)
    return subprocess.run(cmd, text=True, capture_output=True, env=env, **kw)


def test_return_of_attribute_frees_the_attribute_like_cpython(tmp_path):
    src = tmp_path / "prog.py"
    src.write_text(PROGRAM, encoding="utf-8")

    oracle = _run([sys.executable, str(src)], timeout=30)
    assert oracle.returncode == 0, oracle.stderr
    expected = oracle.stdout.strip().splitlines()
    # Pin the oracle shape so a silent CPython change cannot weaken the test.
    assert expected == [
        "in-ret ret", "fin ret", "after-ret",
        "fin stmt", "after-stmt",
        "fin binop", "after-binop",
        "end",
    ], expected

    exe = tmp_path / "prog_bin"
    build = _run(
        [
            "uv", "run", "pcc", "--backend", "self", "--python-libpython=off",
            "--ir-scaffold=on", str(src), "-o", str(exe),
        ],
        timeout=600,
    )
    assert build.returncode == 0, build.stderr
    run = _run([str(exe)], timeout=30)
    assert run.returncode == 0, run.stderr
    assert run.stdout.strip().splitlines() == expected, (
        "pcc finalizer order differs from CPython (a missing 'fin ret' means "
        "`return t.n` leaked the getattr result):\n" + run.stdout
    )
