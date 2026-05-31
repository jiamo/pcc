"""Codegen-focused regression for nested closure + comprehension scope.

This covers the case where the same comprehension target name appears inside a
nested function. If free-name analysis is wrong, pcc emits a synthesized closure
that expects that target in an outer scope and then fails with
``reference to unbound name <target>``.
"""
from __future__ import annotations

import subprocess
import textwrap
from pathlib import Path


def _compile_and_run(tmp_path: Path, source: str) -> list[str]:
    from pcc.py_frontend.pipeline import compile_python

    src = tmp_path / "probe.py"
    exe = tmp_path / "probe.out"
    src.write_text(textwrap.dedent(source).lstrip(), encoding="utf-8")
    compile_python(
        str(src),
        str(exe),
        ir_scaffold_mode="on",
        libpython_mode="off",
        backend="self",
    )
    proc = subprocess.run([str(exe)], text=True, capture_output=True, timeout=30)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    return proc.stdout.strip().splitlines()


def test_nested_genexpr_target_named_h_is_not_captured(tmp_path):
    lines = _compile_and_run(
        tmp_path,
        """
        def outer(xs):
            offset = 1

            def inner():
                return tuple(h + offset for h in xs)

            return inner()

        vals = outer([1, 2, 3])
        print(vals[0], vals[1], vals[2])
        """,
    )
    assert lines == ["2 3 4"]


def test_nested_list_dict_set_comp_target_named_h_is_not_captured(tmp_path):
    lines = _compile_and_run(
        tmp_path,
        """
        def outer(xs):
            scale = 2

            def inner():
                ys = [h * scale for h in xs]
                d = {h: h * scale for h in xs}
                s = {h % 2 for h in xs}
                print(len(ys))
                print(d[3])
                print(len(s))
                return ys

            return inner()

        outer([1, 2, 3])
        """,
    )
    assert lines == ["3", "6", "2"]
