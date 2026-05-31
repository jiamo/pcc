"""Literal ``textwrap.dedent`` lowers without libpython fallback."""
from __future__ import annotations

import re
import textwrap
from pathlib import Path

import pytest


_REPO_ROOT = Path(__file__).absolute().parents[2]
_BUILD = _REPO_ROOT / "build"
_BUILD.mkdir(parents=True, exist_ok=True)


def _compile_to_ll(source: str, name: str, *, mode: str) -> str:
    from pcc.py_frontend.pipeline import compile_python

    src = _BUILD / f"{name}.py"
    out = _BUILD / f"{name}.ll"
    src.write_text(source, encoding="utf-8")
    compile_python(
        str(src),
        str(out),
        emit_llvm_only=True,
        ir_scaffold_mode=mode,
        libpython_mode="off",
    )
    return out.read_text(encoding="utf-8")


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
def test_textwrap_dedent_literal_dispatches_without_libpython(mode):
    program = textwrap.dedent(
        '''
        import textwrap

        def f() -> str:
            return textwrap.dedent("""
                alpha
                  beta
                """)
        '''
    )
    ir = _compile_to_ll(program, f"textwrap_dedent_attr_{mode}", mode=mode)
    body = _function_body(ir, "f")
    assert body is not None
    assert "py_cpy_" not in body, body
    assert "@.cpy.attr.dedent" not in ir, ir
    assert "@.cpy.mod.textwrap" not in ir, ir


@pytest.mark.parametrize("mode", ["off", "on"])
def test_textwrap_dedent_import_alias_literal_dispatches_without_libpython(mode):
    program = textwrap.dedent(
        '''
        from textwrap import dedent

        def f() -> str:
            return dedent("""
                gamma
                  delta
                """)
        '''
    )
    ir = _compile_to_ll(program, f"textwrap_dedent_alias_{mode}", mode=mode)
    body = _function_body(ir, "f")
    assert body is not None
    assert "py_cpy_" not in body, body
    assert "@.cpy.attr.dedent" not in ir, ir
    assert "@.cpy.mod.textwrap" not in ir, ir


def test_textwrap_dedent_literal_algorithm_matches_cpython_shape():
    from pcc.py_frontend.codegen.native_text_modules import (
        NativeTextModulesLoweringMixin,
    )

    class Dummy(NativeTextModulesLoweringMixin):
        pass

    source = """
        alpha
          beta
        """
    assert Dummy()._textwrap_dedent_literal_value(source) == textwrap.dedent(source)
