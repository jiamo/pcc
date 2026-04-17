"""Membership tests stay native when only the needle is CPython-backed.

The old lowering checked ``_expr_looks_cpython(lhs) or
_expr_looks_cpython(rhs)`` before emitting either side. That was too broad:
native ``getattr(obj, name, default)`` expressions look CPython-ish because
``getattr`` is also a fallback builtin, so ``x in getattr(...)`` routed the
whole membership test through CPython ``__contains__``.
"""
from __future__ import annotations

import re
import textwrap
from pathlib import Path

import pytest


_REPO_ROOT = Path(__file__).resolve().parent.parent
_BUILD = _REPO_ROOT / "build"
_BUILD.mkdir(parents=True, exist_ok=True)


def _compile_to_ll(source: str, name: str, *, mode: str) -> str:
    from pcc.py_frontend.pipeline import compile_python

    src = _BUILD / f"{name}.py"
    out = _BUILD / f"{name}.ll"
    src.write_text(source)
    compile_python(
        str(src),
        str(out),
        emit_llvm_only=True,
        ir_scaffold_mode=mode,
    )
    return out.read_text()


def _function_body(ir_text: str, fn_name_suffix: str) -> str | None:
    pattern = re.compile(
        r"define\s+[^\n]*?@[A-Za-z0-9_]*"
        + re.escape(fn_name_suffix)
        + r"\s*\([^)]*\)[^{]*\{(.+?)\n\}",
        re.DOTALL,
    )
    m = pattern.search(ir_text)
    return m.group(1) if m else None


@pytest.mark.parametrize("mode", ["off", "on"])
def test_getattr_membership_uses_native_obj_contains(mode):
    program = textwrap.dedent(
        """
        class Box:
            def __init__(self) -> None:
                self.items = ("a", "b")

        def f(box: Box, name: str) -> bool:
            return name in getattr(box, "items", ())
        """
    )
    ir = _compile_to_ll(program, f"membership_getattr_{mode}", mode=mode)
    body = _function_body(ir, "f")
    assert body is not None
    assert "@py_obj_contains" in body, body
    assert "cpy.fn.__contains__" not in body, body
    assert "cpy.contains" not in body, body


@pytest.mark.parametrize("mode", ["off", "on"])
def test_cpy_needle_in_native_list_bridges(mode):
    program = textwrap.dedent(
        """
        import tempfile

        def f(values: list[str]) -> bool:
            with tempfile.TemporaryDirectory() as tmp:
                return tmp in values
        """
    )
    ir = _compile_to_ll(program, f"membership_cpy_needle_{mode}", mode=mode)
    body = _function_body(ir, "f")
    assert body is not None
    assert "@py_list_contains" in body, body
    assert "@py_cpy_to_pcc_obj" in body, body
    assert "cpy.fn.__contains__" not in body, body


def test_cpy_container_still_uses_cpython_contains():
    program = textwrap.dedent(
        """
        import os

        def f(name: str) -> bool:
            return name in os.environ.keys()
        """
    )
    ir = _compile_to_ll(program, "membership_cpy_container", mode="off")
    body = _function_body(ir, "f")
    assert body is not None
    assert "cpy.fn.__contains__" in body, body
    assert "@py_obj_contains" not in body, body
