"""PY-P0-CONSUMER-BOUNDARY-OWNERSHIP-LEDGER (str instance): ``str(x)`` on the
generic ``py_obj_str`` path borrows its operand, so an owned temporary produced
by the argument expression must be released exactly once -- symmetric with the
already-fixed ``repr``/``ascii``/``hash`` single-argument builtins. A borrowed
operand (a plain name) must gain no release (that would be a double free).
"""
from __future__ import annotations

import os
import re
import subprocess


IR_PROGRAM = """
class T:
    def kind(self) -> int:
        return 1


def direct() -> str:
    return str(T())


def bound(x) -> str:
    return str(x)
"""


def _run(cmd, **kw):
    env = os.environ.copy()
    env.pop("LC_ALL", None)
    return subprocess.run(cmd, text=True, capture_output=True, env=env, **kw)


def test_str_releases_owned_argument_and_not_borrowed(tmp_path):
    src = tmp_path / "ir.py"
    src.write_text(IR_PROGRAM, encoding="utf-8")
    out = tmp_path / "ir.ll"
    build = _run(
        [
            "uv", "run", "pcc", "--backend", "self", "--python-libpython=off",
            "--ir-scaffold=on", "--python-library", f"--emit-llvm={out}",
            str(src),
        ],
        timeout=600,
    )
    assert build.returncode == 0, build.stderr
    ir_text = out.read_text(encoding="utf-8")

    def body(suffix: str) -> str:
        m = re.search(
            r"^define [^\n]*@user_\w*_" + suffix + r"\((?:.|\n)*?^\}",
            ir_text,
            re.MULTILINE,
        )
        assert m is not None, f"no definition for {suffix}"
        return m.group(0)

    # direct(): str(T()) borrows an owned instance through py_obj_str -> the
    # boxed operand must be released exactly once.
    direct = body("direct")
    strcall = re.search(r"@py_obj_str\(ptr (%[\w.]+)\)", direct)
    assert strcall is not None, direct
    operand = re.escape(strcall.group(1))
    assert re.search(r"@pcc_gc_release\(ptr " + operand + r"\)", direct), (
        "str(T()) leaked its owned argument (no release of the py_obj_str "
        "operand):\n" + direct
    )

    # bound(x): the operand is a borrowed parameter; the py_obj_str operand
    # must NOT be released (double free).
    bound = body("bound")
    strcall_b = re.search(r"@py_obj_str\(ptr (%[\w.]+)\)", bound)
    assert strcall_b is not None, bound
    operand_b = re.escape(strcall_b.group(1))
    assert not re.search(r"@pcc_gc_release\(ptr " + operand_b + r"\)", bound), (
        "str(borrowed) wrongly released its operand (double free):\n" + bound
    )
