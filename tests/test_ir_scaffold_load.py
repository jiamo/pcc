"""Phase 3 Task 8: scaffold lowering for ``builder.load``.

``self.builder.load(ptr)`` becomes
``call ptr @user_pcc_llvm_capi_ir_IRBuilder_load(ptr, ptr)`` in ON mode (no py_cpy_*).

Mirrors the structure of test_ir_scaffold_store.py; see that file for
the rationale.
"""
from __future__ import annotations

import re
import textwrap
from pathlib import Path

import pytest


_REPO_ROOT = Path(__file__).resolve().parent.parent
_BUILD = _REPO_ROOT / "build"
_BUILD.mkdir(parents=True, exist_ok=True)
_PTR = r"(?:ptr|i8\s*\*)"


def _compile_to_ll(source: str, name: str, *, mode: str) -> str:
    from pcc.py_frontend.pipeline import compile_python

    src = _BUILD / f"{name}.py"
    out = _BUILD / f"{name}.ll"
    src.write_text(source)
    compile_python(
        str(src), str(out),
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


_USE_LOAD_PROGRAM = textwrap.dedent(
    """
    def use_load(builder, ptr):
        return builder.load(ptr)
    """
)


def test_on_mode_emits_load_extern():
    ir_text = _compile_to_ll(_USE_LOAD_PROGRAM, "load_on", mode="on")
    assert "@user_pcc_llvm_capi_ir_IRBuilder_load" in ir_text, (
        "ON mode must emit user_pcc_llvm_capi_ir_IRBuilder_load extern; got:\n" + ir_text
    )
    decl = re.compile(
        r"declare[^\n]+" + _PTR + r"\s+@user_pcc_llvm_capi_ir_IRBuilder_load\s*\(\s*"
        + _PTR + r"\s*,\s*" + _PTR + r"\s*\)"
    )
    assert decl.search(ir_text), (
        f"extern declaration must match `{_PTR} ({_PTR}, {_PTR})`:\n"
        + ir_text
    )


def test_on_mode_use_load_body_has_no_py_cpy():
    ir_text = _compile_to_ll(
        _USE_LOAD_PROGRAM, "load_on_clean", mode="on",
    )
    body = _function_body(ir_text, "use_load")
    assert body is not None, ir_text
    assert "@user_pcc_llvm_capi_ir_IRBuilder_load" in body, body
    assert "py_cpy_" not in body, (
        "ON mode use_load body must have ZERO py_cpy_*; got:\n" + body
    )


def test_off_mode_still_uses_py_cpy_for_load():
    ir_text = _compile_to_ll(
        _USE_LOAD_PROGRAM, "load_off", mode="off",
    )
    assert "@user_pcc_llvm_capi_ir_IRBuilder_load" not in ir_text
    body = _function_body(ir_text, "use_load")
    assert body is not None
    assert "py_cpy_" in body


def test_load_arity_check():
    """builder.load must take exactly 1 arg (the pointer)."""
    from pcc.py_frontend.codegen.layer1 import ScaffoldUnsupportedError
    from pcc.py_frontend.pipeline import (
        PyPipelineError,
        compile_python,
    )

    src = _BUILD / "load_bad_arity.py"
    out = _BUILD / "load_bad_arity.ll"
    src.write_text(textwrap.dedent(
        """
        def f(builder, a, b):
            return builder.load(a, b)
        """
    ))
    with pytest.raises((ScaffoldUnsupportedError, PyPipelineError)):
        compile_python(
            str(src), str(out),
            emit_llvm_only=True,
            ir_scaffold_mode="on",
        )
